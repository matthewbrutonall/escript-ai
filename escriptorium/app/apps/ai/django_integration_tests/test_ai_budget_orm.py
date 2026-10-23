"""PostgreSQL checks for hosted AI budget reservations.

docs/budget-reservations.md is the specification. Providers are mocked at
requests.post, so the real backend classes report usage. No paid API is
called. Concurrency cases use committed rows and separate connections.
"""
import json as jsonlib
import os
import re
import threading
import uuid
from datetime import timedelta
from decimal import Decimal
from io import BytesIO
from unittest.mock import MagicMock, patch

import requests
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from PIL import Image

from ai import billing_store
from ai.billing import BudgetExceeded, JobClaimLost, Price, usd
from ai.billing_store import (
    JobNotClaimed, claim_job, confirm_call, mark_sent, month_usage,
    open_hold, reconcile_now, reserve_call, resolve_call, resolve_price,
)
from ai.models import (
    AIBackendConfig, AIBillableCall, AIBudgetHold, AIJob, AIModelPrice,
    AIUsageLedger, AIUserQuota,
)
from ai.tasks import ai_seg_review, ai_transcribe
from core.models import (
    Document, DocumentPart, Line, LineTranscription, Project, Transcription,
)
from reporting.models import TaskReport
from users.models import User

MEDIA = "/tmp/ai-budget-orm-media"
# The fixture model is priced at 0 input and 100 USD per 1M output tokens,
# and sends max_tokens 1000. The real bound function therefore gives every
# call a reservation of exactly 1000 * 100 / 1M = 0.10 USD, whatever the
# crop size or prompt, and a call reporting 300 output tokens costs 0.03.
PRICE = Price("0", "100")
MAX_TOKENS = 1000
BOUND = Decimal("0.100000")
CALL_USAGE = {"input_tokens": 10000, "output_tokens": 300}
CALL_COST = Decimal("0.030000")
LEASE = 1800


def _png(w=600, h=400):
    buffer = BytesIO()
    Image.new("RGB", (w, h), (240, 240, 240)).save(buffer, "PNG")
    payload = buffer.getvalue()
    return SimpleUploadedFile("page.png", payload, "image/png"), len(payload)


def _keys(prompt):
    m = re.search(r"The colour keys on this crop are: ([^.]*)\.", prompt)
    return [k.strip() for k in m.group(1).split(",")] if m else ["text"]


class FakeProvider:
    """Stands in for the provider HTTP API at requests.post.

    `script` entries are applied to successive calls; missing entries
    answer normally with CALL_USAGE.
    """

    def __init__(self, script=None, on_call=None):
        self.script = list(script or [])
        self.on_call = on_call
        self.calls = 0
        self.lock = threading.Lock()

    def __call__(self, url, headers=None, json=None, timeout=None):
        with self.lock:
            self.calls += 1
            n = self.calls
            action = self.script.pop(0) if self.script else "ok"
        if self.on_call:
            self.on_call(n)
        if isinstance(action, BaseException):
            raise action
        prompt = self._prompt(json)
        if action == "nokeys":
            text = "{}"
        else:
            text = jsonlib.dumps({k: f"line {n}" for k in _keys(prompt)})
        resp = MagicMock()
        resp.ok = True
        resp.status_code = 200
        resp.raise_for_status = lambda: None
        if "messages" in (json or {}) and "anthropic" in url:
            body = {"content": [{"type": "text", "text": text}]}
            if action == "badbody":
                body["content"] = "not a list"
            if action != "nousage":
                body["usage"] = dict(CALL_USAGE)
        else:  # OpenAI-compatible chat completions (local)
            body = {"choices": [{"message": {"content": text}}],
                    "usage": {"prompt_tokens": 7, "completion_tokens": 3}}
        resp.json.return_value = body
        return resp

    @staticmethod
    def _prompt(payload):
        content = payload["messages"][0]["content"]
        return content[0]["text"]


class HttpSwitch:
    """Installed once per test as requests.post. Never unpatched mid-test,
    so a thread cannot restore the real function under another thread.
    Any request with no provider set fails the test."""

    def __init__(self):
        self.target = None

    def __call__(self, *args, **kwargs):
        if self.target is None:
            raise AssertionError("unexpected HTTP request in budget tests")
        return self.target(*args, **kwargs)


class Outcome:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error

    def failed(self):
        return self.error is not None


def _apply(task, kwargs, task_id=None):
    """Run a task eagerly. test_settings makes eager errors propagate."""
    try:
        res = task.apply(kwargs=kwargs, task_id=task_id or uuid.uuid4().hex)
    except Exception as exc:
        return Outcome(error=exc)
    return Outcome(result=res.result)


class BudgetFixture(TransactionTestCase):
    """Committed fixture rows so several connections can see and lock them."""

    def setUp(self):
        os.makedirs(MEDIA, exist_ok=True)
        self.settings_cm = override_settings(
            MEDIA_ROOT=MEDIA, AI_INSTANCE_MONTHLY_BUDGET_USD=None,
            AI_MONTHLY_BUDGET_USD=None, AI_JOB_LEASE_SECONDS=LEASE)
        self.settings_cm.enable()
        self.addCleanup(lambda: self.settings_cm.disable())
        for target in ("ai.tasks.send_event", "ai.tasks.notify_user",
                       "reporting.tasks.send_event"):
            p = patch(target)
            p.start()
            self.addCleanup(p.stop)
        self.http = HttpSwitch()
        for target, value in (("requests.post", self.http),
                              ("requests.get", self.http)):
            p = patch(target, value)
            p.start()
            self.addCleanup(p.stop)
        self.user = self._user()
        self.project = Project.objects.create(
            name="budget", slug=uuid.uuid4().hex[:12], owner=self.user)
        self.document = Document.objects.create(
            name="doc", owner=self.user, project=self.project)
        self.part = self._part(self.document, lines=6)
        self.layer = Transcription.objects.create(
            document=self.document, name="AI layer")
        self.config = AIBackendConfig.objects.create(
            name="claude-" + uuid.uuid4().hex[:8], provider="anthropic",
            model_id="claude-test", key_ref="AI_BUDGET_TEST_KEY",
            prompt_template="", params={"max_tokens": MAX_TOKENS})
        AIModelPrice.objects.get_or_create(
            provider="anthropic", model_id="claude-test",
            defaults=dict(input_usd_per_1m=PRICE.in_usd_per_1m,
                          output_usd_per_1m=PRICE.out_usd_per_1m,
                          source="test fixture"))
        os.environ["AI_BUDGET_TEST_KEY"] = "sk-test-not-real"
        self.addCleanup(os.environ.pop, "AI_BUDGET_TEST_KEY", None)

    # -- helpers --------------------------------------------------------
    def _user(self):
        tag = uuid.uuid4().hex[:10]
        return User.objects.create_user(
            username=f"budget-{tag}", email=f"{tag}@example.test",
            password="unused-test-password")

    def _part(self, document, lines):
        upload, size = _png()
        part = DocumentPart.objects.create(
            document=document, image=upload, image_file_size=size,
            original_filename="page.png")
        for i in range(lines):
            top = 20 + 50 * i
            Line.objects.create(
                document_part=part, order=i,
                baseline=[[20, top + 30], [560, top + 30]],
                mask=[[20, top], [560, top], [560, top + 36], [20, top + 36]])
        return part

    def _cap(self, user=None, usd_cap=None):
        if user is None:
            self.settings_cm.disable()
            self.settings_cm = override_settings(
                MEDIA_ROOT=MEDIA, AI_INSTANCE_MONTHLY_BUDGET_USD=usd_cap,
                AI_MONTHLY_BUDGET_USD=None, AI_JOB_LEASE_SECONDS=LEASE)
            self.settings_cm.enable()
        else:
            AIUserQuota.objects.update_or_create(
                user=user, defaults={"monthly_usd": usd_cap})

    def _job(self, est="0", config=None, user=None, mode=AIJob.MODE_REGION):
        return AIJob.objects.create(
            backend=config or self.config, document=self.document,
            transcription=self.layer if mode == AIJob.MODE_REGION else None,
            mode=mode, est_cost=float(est), created_by=user or self.user)

    def _run(self, job, provider, task_id=None, per_crop=6, parts=None):
        self.http.target = provider
        return _apply(ai_transcribe, dict(
            instance_pks=[p.pk for p in (parts or [self.part])],
            ai_config_pk=job.backend_id, transcription_pk=self.layer.pk,
            user_pk=self.user.pk, job_pk=job.pk, per_crop=per_crop),
            task_id=task_id or uuid.uuid4().hex)

    def _threads(self, targets):
        errors = []

        def wrap(fn):
            def inner():
                try:
                    fn()
                except Exception as exc:  # recorded and asserted by caller
                    errors.append(exc)
                finally:
                    connection.close()
            return inner

        threads = [threading.Thread(target=wrap(t)) for t in targets]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)
            self.assertFalse(t.is_alive(), "thread did not finish")
        return errors

    def _ledger_total(self, **filters):
        return sum((usd(r.actual_cost) for r in
                    AIUsageLedger.objects.filter(**filters)), Decimal(0))


class ConcurrencyTests(BudgetFixture):
    def test_simultaneous_calls_never_exceed_user_cap(self):
        self._cap(self.user, 1.0)
        barrier = threading.Barrier(8)
        results = []

        def one():
            barrier.wait(timeout=10)
            try:
                reserve_call(job=None, claim=None, hold=None, user=self.user,
                             document=self.document, config=self.config,
                             price=PRICE, bound=Decimal("0.30"))
                results.append("ok")
            except BudgetExceeded:
                results.append("refused")

        errors = self._threads([one] * 8)
        self.assertEqual(errors, [])
        self.assertEqual(results.count("ok"), 3)
        self.assertEqual(results.count("refused"), 5)
        self.assertEqual(month_usage(self.user), Decimal("0.90"))

    def test_simultaneous_jobs_respect_instance_cap_across_users(self):
        self._cap(None, 1.0)
        other = self._user()
        jobs = [(self._job("0.6"), self.user), (self._job("0.6", user=other), other)]
        claims = [claim_job(j.pk, f"t{i}") for i, (j, _) in enumerate(jobs)]
        barrier = threading.Barrier(2)
        outcomes = []

        def opener(i):
            def run():
                job, user = jobs[i]
                barrier.wait(timeout=10)
                try:
                    open_hold(job, claims[i], user, job.est_cost)
                    outcomes.append("held")
                except BudgetExceeded:
                    outcomes.append("refused")
            return run

        self.assertEqual(self._threads([opener(0), opener(1)]), [])
        self.assertEqual(sorted(outcomes), ["held", "refused"])
        self.assertEqual(month_usage(None), Decimal("0.60"))

    def test_open_hold_blocks_other_users_until_closed(self):
        self._cap(None, 1.0)
        job = self._job("0.8")
        claim = claim_job(job.pk, "t1")
        hold = open_hold(job, claim, self.user, job.est_cost)
        other = self._user()
        with self.assertRaises(BudgetExceeded):
            reserve_call(job=None, claim=None, hold=None, user=other,
                         document=self.document, config=self.config,
                         price=PRICE, bound=Decimal("0.3"))
        billing_store.close_hold(hold)
        reserve_call(job=None, claim=None, hold=None, user=other,
                     document=self.document, config=self.config,
                     price=PRICE, bound=Decimal("0.3"))
        self.assertEqual(month_usage(None), Decimal("0.3"))

    def test_concurrent_full_jobs_stop_at_the_cap(self):
        # Two real jobs on separate parts share one user cap of 0.30 USD.
        # Each call holds 0.10 and settles at 0.03. Both jobs fall back to
        # per-line calls, so 14 calls are wanted (0.42 USD): some must be
        # refused, and usage must never pass the cap while they run.
        cap = Decimal("0.3")
        self._cap(self.user, float(cap))
        part_b = self._part(self.document, lines=6)
        job_a, job_b = self._job("0"), self._job("0")
        seen = []

        def check(n):
            seen.append(month_usage(self.user))

        provider = FakeProvider(script=["nokeys"] * 2, on_call=check)
        barrier = threading.Barrier(2)

        def runner(job, part):
            def run():
                barrier.wait(timeout=10)
                self._run(job, provider, parts=[part])
            return run

        self.assertEqual(self._threads(
            [runner(job_a, self.part), runner(job_b, part_b)]), [])
        self.assertTrue(seen)
        self.assertLessEqual(max(seen), cap)
        self.assertLess(provider.calls, 14)
        self.assertLessEqual(self._ledger_total(user=self.user), cap)
        self.assertLessEqual(month_usage(self.user), cap)
        self.assertEqual(AIBillableCall.objects.filter(
            state=AIBillableCall.STATE_CONFIRMED).count(), provider.calls)
        self.assertEqual(AIBillableCall.objects.count(), provider.calls)
        self.assertFalse(AIBudgetHold.objects.filter(
            state=AIBudgetHold.STATE_OPEN).exists())
        errors = AIJob.objects.filter(
            pk__in=[job_a.pk, job_b.pk], status=AIJob.STATUS_ERROR)
        self.assertTrue(errors.exists())
        for job in errors:
            self.assertIn("budget", job.error)


class JobAccountingTests(BudgetFixture):
    def test_each_call_is_confirmed_and_the_hold_closes(self):
        self.part = self._part(self.document, lines=12)
        job = self._job("0.5")
        provider = FakeProvider()
        self._run(job, provider)
        job.refresh_from_db()
        self.assertEqual(job.status, AIJob.STATUS_DONE)
        self.assertEqual(provider.calls, 2)
        calls = AIBillableCall.objects.filter(job=job)
        self.assertEqual(
            set(calls.values_list("state", flat=True)),
            {AIBillableCall.STATE_CONFIRMED})
        self.assertEqual(AIUsageLedger.objects.filter(job=job).count(), 2)
        self.assertEqual(usd(job.actual_cost), 2 * CALL_COST)
        self.assertEqual(job.tokens_in, 2 * CALL_USAGE["input_tokens"])
        hold = AIBudgetHold.objects.get(job=job)
        self.assertEqual(hold.state, AIBudgetHold.STATE_CLOSED)
        self.assertEqual(month_usage(self.user), 2 * CALL_COST)

    def test_partial_page_failure_keeps_charges(self):
        self.part = self._part(self.document, lines=12)
        job = self._job("0.5")
        provider = FakeProvider(script=["ok", "badbody"])
        result = self._run(job, provider)
        self.assertTrue(result.failed())
        job.refresh_from_db()
        self.assertEqual(job.status, AIJob.STATUS_ERROR)
        # The failing call reported usage before parsing: both charged.
        self.assertEqual(AIUsageLedger.objects.filter(job=job).count(), 2)
        self.assertEqual(self._ledger_total(job=job), 2 * CALL_COST)
        self.assertEqual(usd(job.actual_cost), 2 * CALL_COST)
        self.assertEqual(AIBudgetHold.objects.get(job=job).state,
                         AIBudgetHold.STATE_CLOSED)
        self.assertEqual(job.lines_written, 6)

    def test_unknown_outcome_stays_held(self):
        self.part = self._part(self.document, lines=12)
        job = self._job("0.5")
        provider = FakeProvider(script=["ok", requests.ReadTimeout("slow")])
        self.assertTrue(self._run(job, provider).failed())
        unknown = AIBillableCall.objects.get(
            job=job, state=AIBillableCall.STATE_UNKNOWN)
        self.assertTrue(unknown.sent)
        self.assertEqual(unknown.error, "ReadTimeout")
        self.assertIsNone(unknown.ledger)
        self.assertEqual(AIUsageLedger.objects.filter(job=job).count(), 1)
        # Confirmed 0.03 plus the unknown call's full hold of 0.10.
        self.assertEqual(month_usage(self.user), CALL_COST + BOUND)

    def test_auth_rejection_is_released(self):
        job = self._job("0.5")
        resp = MagicMock(ok=False, status_code=401, text="no")
        post = MagicMock(return_value=resp)
        self.http.target = post
        result = _apply(ai_transcribe, dict(
            instance_pks=[self.part.pk], ai_config_pk=self.config.pk,
            transcription_pk=self.layer.pk, user_pk=self.user.pk,
            job_pk=job.pk))
        self.assertTrue(result.failed())
        call = AIBillableCall.objects.get(job=job)
        self.assertEqual(call.state, AIBillableCall.STATE_RELEASED)
        self.assertEqual(month_usage(self.user), Decimal(0))
        self.assertEqual(post.call_count, 1)

    def test_answer_without_usage_is_estimated_and_held(self):
        job = self._job("0.5")
        self._run(job, FakeProvider(script=["nousage"]))
        call = AIBillableCall.objects.get(job=job)
        self.assertEqual(call.state, AIBillableCall.STATE_ESTIMATED)
        self.assertIsNotNone(call.estimated_usd)
        self.assertFalse(AIUsageLedger.objects.filter(job=job).exists())
        self.assertEqual(month_usage(self.user), BOUND)

    def test_fallback_calls_reserve_before_sending(self):
        # Hold 0. Cap 0.20. Each call holds 0.10 and settles at 0.03.
        # Calls are admitted while confirmed + 0.10 <= 0.20: four calls.
        self._cap(self.user, 0.2)
        job = self._job("0")
        provider = FakeProvider(script=["nokeys"])  # crop call -> 6 per-line
        result = self._run(job, provider)
        self.assertTrue(result.failed())
        self.assertEqual(provider.calls, 4)
        job.refresh_from_db()
        self.assertEqual(job.status, AIJob.STATUS_ERROR)
        self.assertIn("budget", job.error)
        self.assertEqual(AIBillableCall.objects.filter(job=job).count(), 4)
        self.assertEqual(self._ledger_total(job=job), 4 * CALL_COST)
        self.assertLessEqual(month_usage(self.user), Decimal("0.2"))
        # The three lines written before the stop are kept and counted.
        self.assertEqual(job.lines_written, 3)
        self.assertEqual(LineTranscription.objects.filter(
            transcription=self.layer).count(), 3)

    def test_fallbacks_draw_from_hold_then_need_free_budget(self):
        # Hold 0.25 under a cap of 0.27. While the hold's remainder covers a
        # call's 0.10 bound, usage stays 0.25. Call 7 needs 0.03 beyond the
        # hold: 0.28 > 0.27, so it is refused before sending.
        self._cap(self.user, 0.27)
        job = self._job("0.25")
        provider = FakeProvider(script=["nokeys"])
        self.assertTrue(self._run(job, provider).failed())
        self.assertEqual(provider.calls, 6)
        job.refresh_from_db()
        self.assertIn("budget", job.error)
        self.assertEqual(self._ledger_total(job=job), 6 * CALL_COST)
        self.assertEqual(month_usage(self.user), 6 * CALL_COST)

    def test_fallbacks_within_hold_complete(self):
        self._cap(self.user, 0.30)
        job = self._job("0.25")
        provider = FakeProvider(script=["nokeys"])
        self._run(job, provider)
        job.refresh_from_db()
        self.assertEqual(job.status, AIJob.STATUS_DONE)
        self.assertEqual(provider.calls, 7)
        self.assertEqual(job.lines_written, 6)
        self.assertEqual(month_usage(self.user), 7 * CALL_COST)

    def test_job_estimate_must_fit_before_any_call(self):
        self._cap(self.user, 0.1)
        job = self._job("0.5")
        provider = FakeProvider()
        self.assertTrue(self._run(job, provider).failed())
        self.assertEqual(provider.calls, 0)
        job.refresh_from_db()
        self.assertEqual(job.status, AIJob.STATUS_ERROR)
        self.assertFalse(AIBudgetHold.objects.filter(job=job).exists())

    def test_seg_review_is_metered(self):
        job = self._job("0.5", mode=AIJob.MODE_SEG_REVIEW)
        provider = FakeProvider()
        self.http.target = provider
        _apply(ai_seg_review, dict(
            instance_pks=[self.part.pk], ai_config_pk=self.config.pk,
            user_pk=self.user.pk, job_pk=job.pk))
        job.refresh_from_db()
        self.assertEqual(job.status, AIJob.STATUS_DONE)
        self.assertEqual(provider.calls, 1)
        self.assertEqual(self._ledger_total(job=job), CALL_COST)
        self.assertEqual(usd(job.actual_cost), CALL_COST)

    def test_local_backend_is_outside_dollar_accounting(self):
        self._cap(None, 0)
        self._cap(self.user, 0)
        local = AIBackendConfig.objects.create(
            name="local-" + uuid.uuid4().hex[:8], provider="local",
            model_id="llava", endpoint="http://127.0.0.1:11434/v1",
            prompt_template="", params={})
        job = self._job("0", config=local)
        provider = FakeProvider()
        self._run(job, provider)
        job.refresh_from_db()
        self.assertEqual(job.status, AIJob.STATUS_DONE)
        self.assertEqual(provider.calls, 1)
        self.assertFalse(AIBillableCall.objects.exists())
        self.assertFalse(AIBudgetHold.objects.exists())
        self.assertFalse(AIUsageLedger.objects.exists())
        self.assertEqual(job.tokens_in, 7)
        self.assertEqual(job.actual_cost, 0)


class CancellationAndCrashTests(BudgetFixture):
    def test_cancelled_task_sends_no_further_call(self):
        self.part = self._part(self.document, lines=12)
        job = self._job("0.5")
        task_id = uuid.uuid4().hex
        report = TaskReport.objects.create(
            label="ai", user=self.user, task_id=task_id,
            workflow_state=TaskReport.WORKFLOW_STATE_STARTED)

        def cancel_after_first(n):
            if n == 1:
                TaskReport.objects.filter(pk=report.pk).update(
                    workflow_state=TaskReport.WORKFLOW_STATE_CANCELED)

        provider = FakeProvider(on_call=cancel_after_first)
        self.assertTrue(self._run(job, provider, task_id=task_id).failed())
        self.assertEqual(provider.calls, 1)
        job.refresh_from_db()
        self.assertEqual(job.status, AIJob.STATUS_ERROR)
        self.assertEqual(AIBudgetHold.objects.get(job=job).state,
                         AIBudgetHold.STATE_CLOSED)
        self.assertEqual(self._ledger_total(job=job), CALL_COST)

    def _crashed_job(self, sent=True):
        job = self._job("0.5")
        claim = claim_job(job.pk, "crashed-task")
        hold = open_hold(job, claim, self.user, job.est_cost)
        call = reserve_call(job=job, claim=claim, hold=hold, user=self.user,
                            document=self.document, config=self.config,
                            price=PRICE, bound=BOUND)
        if sent:
            mark_sent(call.pk, claim)
        return job, hold, call

    def test_killed_worker_is_reconciled_as_unknown(self):
        job, hold, sent_call = self._crashed_job(sent=True)
        unsent = reserve_call(
            job=job, claim=AIJob.objects.get(pk=job.pk).claim, hold=hold,
            user=self.user, document=self.document, config=self.config,
            price=PRICE, bound=BOUND)
        later = timezone.now() + timedelta(seconds=LEASE + 1)
        self.assertEqual(reconcile_now(later), 1)
        sent_call.refresh_from_db()
        unsent.refresh_from_db()
        self.assertEqual(sent_call.state, AIBillableCall.STATE_UNKNOWN)
        self.assertEqual(unsent.state, AIBillableCall.STATE_RELEASED)
        job.refresh_from_db()
        self.assertEqual(job.status, AIJob.STATUS_ERROR)
        self.assertIn("unknown billing", job.error)
        self.assertEqual(AIBudgetHold.objects.get(pk=hold.pk).state,
                         AIBudgetHold.STATE_CLOSED)
        # Held, not released, and not in the ledger.
        self.assertEqual(month_usage(self.user), BOUND)
        self.assertFalse(AIUsageLedger.objects.exists())

    def test_late_answer_from_presumed_dead_worker_confirms(self):
        job, hold, call = self._crashed_job(sent=True)
        claim = AIJob.objects.get(pk=job.pk).claim
        reconcile_now(timezone.now() + timedelta(seconds=LEASE + 1))
        confirm_call(call.pk, 10000, 300)
        call.refresh_from_db()
        self.assertEqual(call.state, AIBillableCall.STATE_CONFIRMED)
        self.assertEqual(month_usage(self.user), CALL_COST)
        # Its owner can send nothing more.
        with self.assertRaises(JobClaimLost):
            reserve_call(job=job, claim=claim, hold=hold, user=self.user,
                         document=self.document, config=self.config,
                         price=PRICE, bound=BOUND)

    def test_operator_resolution(self):
        _, _, charged = self._crashed_job(sent=True)
        reconcile_now(timezone.now() + timedelta(seconds=LEASE + 1))
        _, _, free = self._crashed_job(sent=True)
        reconcile_now(timezone.now() + timedelta(seconds=LEASE + 1))
        resolve_call(charged, by=self.user, charged=True,
                     amount=Decimal("0.04"), note="invoice line 7")
        resolve_call(free, by=self.user, charged=False, note="not on invoice")
        charged.refresh_from_db()
        free.refresh_from_db()
        self.assertEqual(charged.state, AIBillableCall.STATE_CONFIRMED)
        self.assertEqual(usd(charged.ledger.actual_cost), Decimal("0.04"))
        self.assertEqual(free.state, AIBillableCall.STATE_RELEASED)
        self.assertEqual(month_usage(self.user), Decimal("0.04"))
        with self.assertRaises(ValueError):
            resolve_call(free, by=self.user, charged=True, amount=1, note="x")


class DuplicateDeliveryTests(BudgetFixture):
    def test_simultaneous_duplicate_delivery_runs_once(self):
        job = self._job("0.5")
        provider = FakeProvider()
        barrier = threading.Barrier(2)
        task_id = uuid.uuid4().hex
        results = []

        def deliver():
            barrier.wait(timeout=10)
            results.append(self._run(job, provider, task_id=task_id).result)

        self.assertEqual(self._threads([deliver, deliver]), [])
        self.assertEqual(provider.calls, 1)
        self.assertEqual(sum(1 for r in results if r.get("skipped")), 1)
        self.assertEqual(AIBudgetHold.objects.filter(job=job).count(), 1)
        self.assertEqual(self._ledger_total(job=job), CALL_COST)

    def test_redelivery_after_crash_does_not_resume(self):
        job, hold, call = CancellationAndCrashTests._crashed_job(self, sent=True)
        AIJob.objects.filter(pk=job.pk).update(
            heartbeat_at=timezone.now() - timedelta(seconds=LEASE + 1))
        provider = FakeProvider()
        result = self._run(job, provider, task_id="crashed-task")
        self.assertEqual(result.result.get("skipped"), True)
        self.assertEqual(provider.calls, 0)
        call.refresh_from_db()
        self.assertEqual(call.state, AIBillableCall.STATE_UNKNOWN)
        job.refresh_from_db()
        self.assertEqual(job.status, AIJob.STATUS_ERROR)

    def test_redelivery_of_live_or_finished_job_is_noop(self):
        job = self._job("0.5")
        claim_job(job.pk, "first")
        with self.assertRaises(JobNotClaimed):
            claim_job(job.pk, "first")
        AIJob.objects.filter(pk=job.pk).update(status=AIJob.STATUS_DONE)
        provider = FakeProvider()
        self.assertTrue(self._run(job, provider).result.get("skipped"))
        self.assertEqual(provider.calls, 0)


class SubmissionTests(BudgetFixture):
    def test_estimate_and_advisory_check(self):
        from ai.dispatch import assert_dispatch_allowed
        from ai.serializers import estimate_transcribe_cost
        est = estimate_transcribe_cost(self.config, [self.part], 6)
        self.assertGreater(est, 0)
        self._cap(self.user, est / 2)
        with self.assertRaises(RuntimeError) as ctx:
            assert_dispatch_allowed(self.document, self.config,
                                    user=self.user, est_cost=est)
        self.assertIn("budget", str(ctx.exception))
        self._cap(self.user, est * 2)
        assert_dispatch_allowed(self.document, self.config,
                                user=self.user, est_cost=est)

    def test_api_submission_stores_estimate_and_refuses_over_cap(self):
        from django.urls import reverse
        from rest_framework.test import APIClient
        client = APIClient()
        client.force_authenticate(self.user)
        uri = reverse('api:document-ai-transcribe', kwargs={'pk': self.document.pk})
        body = {"backend": self.config.pk, "parts": [self.part.pk],
                "transcription": self.layer.pk}
        with patch("ai.tasks.ai_transcribe.delay") as delay:
            self._cap(self.user, 0.000001)
            refused = client.post(uri, body, format="json")
            self.assertEqual(refused.status_code, 400, refused.content)
            self.assertIn(b"budget", refused.content)
            delay.assert_not_called()
            self.assertFalse(AIJob.objects.exists())

            self._cap(self.user, 10)
            ok = client.post(uri, body, format="json")
            self.assertEqual(ok.status_code, 200, ok.content)
            delay.assert_called_once()
        job = AIJob.objects.get()
        self.assertGreater(job.est_cost, 0)
        self.assertEqual(delay.call_args.kwargs["job_pk"], job.pk)
        # Nothing is held while the job waits in the queue.
        self.assertFalse(AIBudgetHold.objects.exists())
        self.assertEqual(month_usage(self.user), Decimal(0))

    def test_ai_fix_reserves_before_calling(self):
        from rest_framework import serializers as drf
        from ai.serializers import AIFixSerializer
        line = self.part.lines.first()
        self._cap(self.user, 0.05)
        provider = FakeProvider()
        ser = AIFixSerializer(
            data={"line": line.pk, "transcription": self.layer.pk,
                  "backend": self.config.pk},
            document=self.document, user=self.user)
        self.assertTrue(ser.is_valid(), ser.errors)
        self.http.target = provider
        with self.assertRaises(drf.ValidationError):
            ser.save()
        self.assertEqual(provider.calls, 0)
        self._cap(self.user, 1)
        out = ser.save()
        self.assertEqual(provider.calls, 1)
        self.assertEqual(out["text"], "line 1")
        self.assertEqual(month_usage(self.user), CALL_COST)

    def test_malformed_cap_setting_blocks(self):
        from escriptorium import settings as s
        os.environ["AI_TEST_BAD_CAP"] = "ten dollars"
        self.addCleanup(os.environ.pop, "AI_TEST_BAD_CAP", None)
        self.assertEqual(s._ai_budget_env("AI_TEST_BAD_CAP"), 0.0)
        os.environ["AI_TEST_BAD_CAP"] = "nan"
        self.assertEqual(s._ai_budget_env("AI_TEST_BAD_CAP"), 0.0)
        os.environ["AI_TEST_BAD_CAP"] = ""
        self.assertIsNone(s._ai_budget_env("AI_TEST_BAD_CAP"))


class ReviewFixTests(BudgetFixture):
    """Corrections from the review of d3700bf. Real pricing and bounds."""

    def _held_call(self):
        job, hold, call = CancellationAndCrashTests._crashed_job(self, sent=True)
        reconcile_now(timezone.now() + timedelta(seconds=LEASE + 1))
        call.refresh_from_db()
        self.assertEqual(call.state, AIBillableCall.STATE_UNKNOWN)
        return job, call

    # -- pricing --------------------------------------------------------
    def test_unpriced_model_is_refused_before_any_request(self):
        unpriced = AIBackendConfig.objects.create(
            name="unpriced-" + uuid.uuid4().hex[:8], provider="anthropic",
            model_id="claude-unpriced", key_ref="AI_BUDGET_TEST_KEY",
            prompt_template="", params={"max_tokens": MAX_TOKENS})
        job = self._job("0.5", config=unpriced)
        provider = FakeProvider()
        outcome = self._run(job, provider)
        self.assertTrue(outcome.failed())
        self.assertIn("No price is configured", str(outcome.error))
        self.assertEqual(provider.calls, 0)
        self.assertFalse(AIBillableCall.objects.exists())
        self.assertFalse(AIBudgetHold.objects.exists())
        job.refresh_from_db()
        self.assertEqual(job.status, AIJob.STATUS_ERROR)

    def test_unpriced_model_is_refused_at_submission(self):
        from django.urls import reverse
        from rest_framework.test import APIClient
        unpriced = AIBackendConfig.objects.create(
            name="unpriced-" + uuid.uuid4().hex[:8], provider="mistral",
            model_id="pixtral-large-latest", key_ref="AI_BUDGET_TEST_KEY",
            prompt_template="", params={})
        client = APIClient()
        client.force_authenticate(self.user)
        uri = reverse('api:document-ai-transcribe', kwargs={'pk': self.document.pk})
        with patch("ai.tasks.ai_transcribe.delay") as delay:
            resp = client.post(uri, {"backend": unpriced.pk,
                                     "parts": [self.part.pk]}, format="json")
        self.assertEqual(resp.status_code, 400)
        self.assertIn(b"No price is configured", resp.content)
        delay.assert_not_called()

    def test_azure_needs_an_explicit_priced_model(self):
        from ai.billing import UnpricedModel
        azure = AIBackendConfig.objects.create(
            name="az-" + uuid.uuid4().hex[:8], provider="azure",
            model_id="my-deployment", endpoint="https://example.test",
            key_ref="AI_BUDGET_TEST_KEY", prompt_template="", params={})
        with self.assertRaises(UnpricedModel):
            resolve_price(azure)
        AIModelPrice.objects.create(
            provider="azure", model_id="my-deployment",
            input_usd_per_1m=Decimal("0.01"), output_usd_per_1m=Decimal("0.01"))
        with self.assertRaises(UnpricedModel):  # deployment name is not used
            resolve_price(azure)
        AIModelPrice.objects.create(
            provider="azure", model_id="gpt-big",
            input_usd_per_1m=Decimal("5"), output_usd_per_1m=Decimal("20"))
        azure.price_model_id = "gpt-big"
        azure.save()
        self.assertEqual(resolve_price(azure), Price("5", "20"))

    def test_expensive_model_settles_at_its_configured_rates(self):
        AIModelPrice.objects.filter(model_id="claude-test").update(
            input_usd_per_1m=Decimal("15"), output_usd_per_1m=Decimal("75"))
        job = self._job("5")

        def raise_price_mid_call(n):
            # A price edit during the job must not change this call's charge.
            AIModelPrice.objects.filter(model_id="claude-test").update(
                input_usd_per_1m=Decimal("99"), output_usd_per_1m=Decimal("99"))

        self._run(job, FakeProvider(on_call=raise_price_mid_call))
        call = AIBillableCall.objects.get(job=job)
        self.assertEqual(call.state, AIBillableCall.STATE_CONFIRMED)
        self.assertEqual(call.in_usd_per_1m, Decimal("15"))
        expected = usd(Decimal(10000) * 15 / 10**6 + Decimal(300) * 75 / 10**6)
        self.assertEqual(call.confirmed_usd, expected)
        self.assertEqual(self._ledger_total(job=job), expected)
        # The reservation used the same rates: output part alone is 1000 * 75.
        self.assertGreaterEqual(call.reserved_usd, usd("0.075"))

    def test_overrun_is_recorded_in_full_and_stops_the_job(self):
        self.part = self._part(self.document, lines=12)
        job = self._job("1")
        over = dict(CALL_USAGE, output_tokens=2000)  # 0.20 against 0.10

        provider = FakeProvider()
        orig = provider.__call__

        def overrun(url, headers=None, json=None, timeout=None):
            resp = orig(url, headers=headers, json=json, timeout=timeout)
            resp.json.return_value["usage"] = over
            return resp

        self.http.target = overrun
        outcome = _apply(ai_transcribe, dict(
            instance_pks=[self.part.pk], ai_config_pk=self.config.pk,
            transcription_pk=self.layer.pk, user_pk=self.user.pk,
            job_pk=job.pk))
        self.assertTrue(outcome.failed())
        self.assertIn("cost more than its reservation", str(outcome.error))
        self.assertEqual(provider.calls, 1)
        self.assertEqual(self._ledger_total(job=job), usd("0.20"))
        job.refresh_from_db()
        self.assertEqual(usd(job.actual_cost), usd("0.20"))

    # -- unresolved billing and months ---------------------------------
    def test_unresolved_call_survives_month_rollover_and_resolves_once(self):
        job, call = self._held_call()
        last_month = timezone.now().replace(day=1) - timedelta(days=3)
        AIBillableCall.objects.filter(pk=call.pk).update(created_at=last_month)
        # Still held in the current month, and in a later one.
        self.assertEqual(month_usage(self.user), BOUND)
        self.assertEqual(month_usage(
            self.user, timezone.now() + timedelta(days=40)), BOUND)
        resolve_call(call, by=self.user, charged=True, amount="0.04",
                     note="invoice")
        call.refresh_from_db()
        ledger = call.ledger
        self.assertEqual(ledger.accounting_month,
                         last_month.replace(day=1).date())
        # Counted in the month it was incurred, not in this one.
        self.assertEqual(month_usage(self.user), Decimal(0))
        self.assertEqual(month_usage(self.user, last_month), usd("0.04"))

    def test_confirmed_charges_from_last_month_do_not_count_now(self):
        job, call = self._held_call()
        last_month = timezone.now().replace(day=1) - timedelta(days=3)
        AIBillableCall.objects.filter(pk=call.pk).update(created_at=last_month)
        confirm_call(call.pk, 10000, 300)  # late answer, after the rollover
        self.assertEqual(month_usage(self.user), Decimal(0))
        self.assertEqual(month_usage(self.user, last_month), CALL_COST)

    def test_http_403_stays_held(self):
        job = self._job("0.5")
        self.http.target = MagicMock(return_value=MagicMock(
            ok=False, status_code=403, text="policy"))
        self.assertTrue(_apply(ai_transcribe, dict(
            instance_pks=[self.part.pk], ai_config_pk=self.config.pk,
            transcription_pk=self.layer.pk, user_pk=self.user.pk,
            job_pk=job.pk)).failed())
        call = AIBillableCall.objects.get(job=job)
        self.assertEqual(call.state, AIBillableCall.STATE_UNKNOWN)
        self.assertEqual(call.error, "ProviderHTTPError HTTP 403")
        self.assertEqual(month_usage(self.user), BOUND)

    # -- send transition ------------------------------------------------
    def test_released_reservation_cannot_be_sent(self):
        job, hold, call = CancellationAndCrashTests._crashed_job(self, sent=False)
        claim = AIJob.objects.get(pk=job.pk).claim
        AIBillableCall.objects.filter(pk=call.pk).update(
            state=AIBillableCall.STATE_RELEASED)
        with self.assertRaises(JobClaimLost):
            mark_sent(call.pk, claim)
        call.refresh_from_db()
        self.assertFalse(call.sent)

    def test_expired_lease_cannot_send(self):
        job, hold, call = CancellationAndCrashTests._crashed_job(self, sent=False)
        claim = AIJob.objects.get(pk=job.pk).claim
        AIJob.objects.filter(pk=job.pk).update(
            heartbeat_at=timezone.now() - timedelta(seconds=LEASE + 1))
        with self.assertRaises(JobClaimLost):
            mark_sent(call.pk, claim)
        self.assertFalse(AIBillableCall.objects.get(pk=call.pk).sent)

    def test_send_aborts_before_http_when_reservation_was_reconciled(self):
        job = self._job("0.5")
        provider = FakeProvider()

        def expire_before_send(*a, **kw):
            raise AssertionError("must not reach HTTP")

        # Reconcile the call between reservation and send.
        real_reserve = billing_store.reserve_call

        def reserve_then_expire(**kw):
            call = real_reserve(**kw)
            AIBillableCall.objects.filter(pk=call.pk).update(
                state=AIBillableCall.STATE_RELEASED)
            return call

        self.http.target = expire_before_send
        with patch("ai.billing_store.reserve_call", reserve_then_expire):
            outcome = _apply(ai_transcribe, dict(
                instance_pks=[self.part.pk], ai_config_pk=self.config.pk,
                transcription_pk=self.layer.pk, user_pk=self.user.pk,
                job_pk=job.pk))
        self.assertTrue(outcome.failed())
        self.assertIsInstance(outcome.error, JobClaimLost)
        self.assertEqual(provider.calls, 0)
        self.assertFalse(AIBillableCall.objects.get(job=job).sent)

    # -- lifecycle ------------------------------------------------------
    def test_stale_running_job_without_hold_is_stopped(self):
        job = self._job("0.5")
        claim_job(job.pk, "died-before-hold")
        AIJob.objects.filter(pk=job.pk).update(
            heartbeat_at=timezone.now() - timedelta(seconds=LEASE + 1))
        provider = FakeProvider()
        outcome = self._run(job, provider, task_id="died-before-hold")
        self.assertTrue(outcome.result.get("skipped"))
        self.assertEqual(provider.calls, 0)
        job.refresh_from_db()
        self.assertEqual(job.status, AIJob.STATUS_ERROR)

    def test_reconcile_stops_stale_job_without_hold(self):
        job = self._job("0.5")
        claim_job(job.pk, "t")
        self.assertEqual(reconcile_now(
            timezone.now() + timedelta(seconds=LEASE + 1)), 1)
        job.refresh_from_db()
        self.assertEqual(job.status, AIJob.STATUS_ERROR)

    def test_legacy_running_job_without_claim_never_runs(self):
        job = self._job("0.5")
        AIJob.objects.filter(pk=job.pk).update(
            status=AIJob.STATUS_RUNNING, claim="", heartbeat_at=None)
        provider = FakeProvider()
        outcome = self._run(job, provider)
        self.assertTrue(outcome.result.get("skipped"))
        self.assertEqual(provider.calls, 0)
        self.assertFalse(AIBudgetHold.objects.exists())
        # Once past the lease it is marked as stopped, still without running.
        AIJob.objects.filter(pk=job.pk).update(
            updated_at=timezone.now() - timedelta(seconds=LEASE + 1))
        reconcile_now()
        job.refresh_from_db()
        self.assertEqual(job.status, AIJob.STATUS_ERROR)

    def test_delivery_without_job_pk_is_refused_before_work(self):
        provider = FakeProvider()
        self.http.target = provider
        outcome = _apply(ai_transcribe, dict(
            instance_pks=[self.part.pk], ai_config_pk=self.config.pk,
            transcription_pk=self.layer.pk, user_pk=self.user.pk))
        self.assertTrue(outcome.failed())
        self.assertIn("job_pk", str(outcome.error))
        outcome = _apply(ai_seg_review, dict(
            instance_pks=[self.part.pk], ai_config_pk=self.config.pk,
            user_pk=self.user.pk))
        self.assertTrue(outcome.failed())
        self.assertEqual(provider.calls, 0)
        self.assertFalse(AIJob.objects.exists())
        self.assertFalse(AIBillableCall.objects.exists())

    # -- administration -------------------------------------------------
    def _staff(self, *perms):
        from django.contrib.auth.models import Permission
        staff = self._user()
        staff.is_staff = True
        staff.save()
        for codename in perms:
            staff.user_permissions.add(Permission.objects.get(
                content_type__app_label="ai", codename=codename))
        return staff

    def _post_action(self, staff, call, action, **extra):
        from django.test import Client
        from django.urls import reverse
        client = Client()
        client.force_login(staff)
        return client.post(
            reverse("admin:ai_aibillablecall_changelist"),
            dict(action=action, _selected_action=[call.pk], confirm="1",
                 note="billing record", **extra))

    def test_view_only_staff_cannot_resolve(self):
        _, call = self._held_call()
        viewer = self._staff("view_aibillablecall")
        for action in ("resolve_charged_action", "resolve_not_charged_action"):
            self._post_action(viewer, call, action, amount="0.05")
            call.refresh_from_db()
            self.assertEqual(call.state, AIBillableCall.STATE_UNKNOWN, action)
        self.assertFalse(AIUsageLedger.objects.exists())

    def test_change_permission_resolves_and_updates_job_once(self):
        job, call = self._held_call()
        before = usd(AIJob.objects.get(pk=job.pk).actual_cost)
        editor = self._staff("view_aibillablecall", "change_aibillablecall")
        self._post_action(editor, call, "resolve_charged_action", amount="0.05")
        self._post_action(editor, call, "resolve_charged_action", amount="0.05")
        call.refresh_from_db()
        self.assertEqual(call.state, AIBillableCall.STATE_CONFIRMED)
        self.assertEqual(call.resolved_by, editor)
        self.assertEqual(AIUsageLedger.objects.filter(job=job).count(), 1)
        self.assertEqual(usd(AIJob.objects.get(pk=job.pk).actual_cost),
                         before + usd("0.05"))
        with self.assertRaises(ValueError):
            resolve_call(call, by=editor, charged=True, amount="0.05", note="x")
        self.assertEqual(usd(AIJob.objects.get(pk=job.pk).actual_cost),
                         before + usd("0.05"))

    def test_non_finite_amounts_are_rejected(self):
        _, call = self._held_call()
        for bad in ("nan", "inf", "-1"):
            with self.assertRaises(ValueError):
                resolve_call(call, by=self.user, charged=True, amount=bad, note="x")
        self.assertEqual(AIBillableCall.objects.get(pk=call.pk).state,
                         AIBillableCall.STATE_UNKNOWN)

    def test_non_finite_quota_fails_closed(self):
        from django.core.exceptions import ValidationError
        quota = AIUserQuota(user=self.user, monthly_usd=float("inf"))
        with self.assertRaises(ValidationError):
            quota.full_clean()
        # A value stored before the validator existed blocks, not unlimited.
        for bad in (float("inf"), float("nan")):
            AIUserQuota.objects.update_or_create(
                user=self.user, defaults={"monthly_usd": bad})
            with self.assertRaises(BudgetExceeded):
                reserve_call(job=None, claim=None, hold=None, user=self.user,
                             document=self.document, config=self.config,
                             price=PRICE, bound=Decimal("0.01"))
        self.assertFalse(AIBillableCall.objects.exists())
