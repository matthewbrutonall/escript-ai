"""
ORM side of hosted AI budget reservations (docs/budget-reservations.md).

Every transaction that touches more than one billing row locks AIBudgetLock
rows first (instance, then user), then job rows, then call and hold rows.
The instance row is therefore a single mutex for multi-row billing writes,
which rules out lock-order deadlocks. Single-row updates of one call
(mark_sent, settle_*) take no other lock.
"""
from __future__ import annotations

import logging
import uuid
from datetime import timedelta

from django.apps import apps
from django.conf import settings
from django.db import transaction
from django.db.models import Case, DecimalField, Exists, F, OuterRef, Sum, Value, When
from django.utils import timezone

from .billing import (
    ZERO,
    BudgetExceeded,
    JobCancelled,
    JobClaimLost,
    call_bound_usd,
    describe_error,
    extra_needed,
    failure_state,
    fits,
    price_cost,
    scope_usage,
    usd,
)
from .budget import month_start

logger = logging.getLogger(__name__)

DEFAULT_LEASE_SECONDS = 1800
INSTANCE_SCOPE = 'instance'


class JobNotClaimed(Exception):
    """This delivery must not run the job. `reason` says why."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def _m(name):
    return apps.get_model('ai', name)


def lease_seconds():
    return int(getattr(settings, 'AI_JOB_LEASE_SECONDS', None)
               or DEFAULT_LEASE_SECONDS)


def instance_cap():
    return getattr(settings, 'AI_INSTANCE_MONTHLY_BUDGET_USD', None)


def user_cap(user):
    from .dispatch import user_monthly_cap
    return user_monthly_cap(user)


def _user_id(user):
    return getattr(user, 'pk', user)


def _scopes(user_id):
    scopes = [INSTANCE_SCOPE]
    if user_id is not None:
        scopes.append(f'user:{user_id}')
    return scopes


def _lock_scopes(user_id):
    """Create then lock scope rows in one fixed order. Inside atomic()."""
    Lock = _m('AIBudgetLock')
    scopes = _scopes(user_id)
    for scope in scopes:
        Lock.objects.get_or_create(scope=scope)
    list(Lock.objects.select_for_update()
         .filter(scope__in=scopes).order_by('scope'))


def _accounted():
    """What a call accounts for: confirmed cost, held reservation, or 0."""
    Call = _m('AIBillableCall')
    return Case(
        When(state=Call.STATE_CONFIRMED, then=F('confirmed_usd')),
        When(state__in=Call.HELD_STATES, then=F('reserved_usd')),
        default=Value(ZERO),
        output_field=DecimalField(max_digits=14, decimal_places=6))


def hold_drawn(hold_id):
    Call = _m('AIBillableCall')
    total = (Call.objects.filter(hold_id=hold_id)
             .aggregate(s=Sum(_accounted()))['s'])
    return usd(total)


def month_usage(user=None, now=None):
    """Usage for a user, or for the whole instance when user is None."""
    Ledger = _m('AIUsageLedger')
    Call = _m('AIBillableCall')
    Hold = _m('AIBudgetHold')
    start = month_start(now or timezone.now())
    ledger = Ledger.objects.filter(created_at__gte=start)
    calls = Call.objects.filter(state__in=Call.HELD_STATES,
                                created_at__gte=start)
    holds = Hold.objects.filter(state=Hold.STATE_OPEN)
    if user is not None:
        uid = _user_id(user)
        ledger = ledger.filter(user_id=uid)
        calls = calls.filter(user_id=uid)
        holds = holds.filter(user_id=uid)
    confirmed = ledger.aggregate(s=Sum('actual_cost'))['s']
    held = calls.aggregate(s=Sum('reserved_usd'))['s']
    open_holds = [(h.amount_usd, hold_drawn(h.pk)) for h in holds]
    return scope_usage(confirmed, held, open_holds)


def _require_fit(user, delta, now=None):
    """Raise BudgetExceeded unless delta fits under every applicable cap."""
    checks = [('instance', instance_cap(), None)]
    if user is not None:
        checks.append(('user', user_cap(user), user))
    for label, cap, who in checks:
        used = month_usage(who, now)
        if not fits(used, delta, cap):
            raise BudgetExceeded(
                f"The {label} monthly AI budget does not allow this: "
                f"used or held {used} USD, needs {usd(delta)} USD more, "
                f"cap {cap} USD.")


def budget_allows_now(user, est_cost):
    """Advisory, unlocked check used at submission. Hosted backends only."""
    try:
        _require_fit(user, est_cost)
    except BudgetExceeded:
        return False
    return True


def job_cancelled(job):
    if not getattr(job, 'task_id', None):
        return False
    TaskReport = apps.get_model('reporting', 'TaskReport')
    return TaskReport.objects.filter(
        task_id=job.task_id,
        workflow_state=TaskReport.WORKFLOW_STATE_CANCELED).exists()


def _stop_job_billing(job, hold, reason, now):
    """Close a hold and mark its in-flight calls. Never releases a sent call."""
    Call = _m('AIBillableCall')
    Hold = _m('AIBudgetHold')
    AIJob = _m('AIJob')
    calls = Call.objects.filter(state=Call.STATE_RESERVED)
    calls = calls.filter(hold=hold) if hold is not None else calls.filter(job=job)
    calls.filter(sent=False).update(
        state=Call.STATE_RELEASED, settled_at=now,
        error=f'not sent; {reason}'[:255])
    unknown = calls.filter(sent=True).update(
        state=Call.STATE_UNKNOWN, settled_at=now, error=reason[:255])
    if hold is not None:
        Hold.objects.filter(pk=hold.pk, state=Hold.STATE_OPEN).update(
            state=Hold.STATE_CLOSED, closed_at=now, close_reason=reason[:64])
    if job is not None:
        message = f'AI job stopped: {reason}.'
        if unknown:
            message += (f' {unknown} provider call(s) have an unknown billing '
                        'outcome and stay held until an operator resolves them.')
        AIJob.objects.filter(
            pk=job.pk, status__in=[AIJob.STATUS_PENDING, AIJob.STATUS_RUNNING],
        ).update(status=AIJob.STATUS_ERROR, error=message[:2000])
    return unknown


def reconcile_stale(now=None):
    """Stop billing for jobs whose worker is gone. Returns holds closed.

    Run it through reconcile_now(), which holds the instance lock.
    """
    Call = _m('AIBillableCall')
    Hold = _m('AIBudgetHold')
    AIJob = _m('AIJob')
    now = now or timezone.now()
    cutoff = now - timedelta(seconds=lease_seconds())
    TaskReport = apps.get_model('reporting', 'TaskReport')
    open_holds = (Hold.objects.filter(state=Hold.STATE_OPEN)
                  .select_related('job')
                  .annotate(cancelled=Exists(TaskReport.objects.filter(
                      task_id=OuterRef('job__task_id'),
                      workflow_state=TaskReport.WORKFLOW_STATE_CANCELED))))
    closed = 0
    for hold in open_holds:
        job = hold.job
        if job is None:
            reason = 'job deleted'
        elif hold.cancelled:
            reason = 'task cancelled'
        elif job.status in (AIJob.STATUS_DONE, AIJob.STATUS_ERROR):
            reason = 'job already finished'
        elif (job.heartbeat_at or hold.created_at) < cutoff:
            reason = 'worker heartbeat expired'
        else:
            continue
        _stop_job_billing(job, hold, reason, now)
        closed += 1
    orphans = Call.objects.filter(
        state=Call.STATE_RESERVED, hold__isnull=True, created_at__lt=cutoff)
    orphans.filter(sent=False).update(
        state=Call.STATE_RELEASED, settled_at=now, error='not sent; stale')
    orphans.filter(sent=True).update(
        state=Call.STATE_UNKNOWN, settled_at=now, error='stale reservation')
    return closed


def claim_job(job_pk, task_id, now=None):
    """Take ownership of a job for this delivery, or raise JobNotClaimed."""
    AIJob = _m('AIJob')
    Hold = _m('AIBudgetHold')
    now = now or timezone.now()
    # Decide inside the transaction, raise after it commits: raising inside
    # atomic() would roll back the reconciliation of an interrupted run.
    refusal = None
    with transaction.atomic():
        _lock_scopes(None)
        job = AIJob.objects.select_for_update().get(pk=job_pk)
        if job.status in (AIJob.STATUS_DONE, AIJob.STATUS_ERROR):
            refusal = f'job already {job.status}'
        elif job.status == AIJob.STATUS_RUNNING and job.claim:
            cutoff = now - timedelta(seconds=lease_seconds())
            hold = Hold.objects.filter(job=job, state=Hold.STATE_OPEN).first()
            if job.heartbeat_at is not None and job.heartbeat_at >= cutoff:
                refusal = 'another delivery owns this job'
            elif hold is None:
                refusal = 'earlier delivery finished billing'
            else:
                _stop_job_billing(
                    job, hold, 'worker stopped; task redelivered', now)
                refusal = 'earlier delivery was interrupted'
        else:
            job.claim = uuid.uuid4().hex
            job.heartbeat_at = now
            job.status = AIJob.STATUS_RUNNING
            job.task_id = task_id
            job.save(update_fields=['claim', 'heartbeat_at', 'status',
                                    'task_id', 'updated_at'])
            return job.claim
    raise JobNotClaimed(refusal)


def _owned_job(job_pk, claim):
    """Lock the job row and check this worker still owns it."""
    AIJob = _m('AIJob')
    job = AIJob.objects.select_for_update().get(pk=job_pk)
    if job.status != AIJob.STATUS_RUNNING or job.claim != claim:
        if job_cancelled(job):
            raise JobCancelled('The AI task was cancelled.')
        raise JobClaimLost(
            'This worker no longer owns the AI job; no further calls are sent.')
    if job_cancelled(job):
        raise JobCancelled('The AI task was cancelled.')
    return job


def open_hold(job, claim, user, amount, now=None):
    Hold = _m('AIBudgetHold')
    now = now or timezone.now()
    amount = usd(amount)
    # Own transaction, so a refusal below cannot roll it back.
    reconcile_now(now)
    with transaction.atomic():
        _lock_scopes(_user_id(user))
        job_row = _owned_job(job.pk, claim)
        _require_fit(user, amount, now)
        job_row.heartbeat_at = now
        job_row.save(update_fields=['heartbeat_at'])
        return Hold.objects.create(job=job_row, user=user, amount_usd=amount)


def close_hold(hold, reason='finished', now=None):
    Hold = _m('AIBudgetHold')
    if hold is None:
        return
    Hold.objects.filter(pk=hold.pk, state=Hold.STATE_OPEN).update(
        state=Hold.STATE_CLOSED, closed_at=now or timezone.now(),
        close_reason=reason[:64])


def reserve_call(*, job, claim, hold, user, document, config, bound, now=None):
    """Hold one call's upper bound. Committed before the request is sent."""
    Call = _m('AIBillableCall')
    Hold = _m('AIBudgetHold')
    now = now or timezone.now()
    bound = usd(bound)
    # Own transaction, so a refusal below cannot roll it back.
    reconcile_now(now)
    with transaction.atomic():
        _lock_scopes(_user_id(user))
        delta = bound
        if job is not None:
            job_row = _owned_job(job.pk, claim)
            job_row.heartbeat_at = now
            job_row.save(update_fields=['heartbeat_at'])
        if hold is not None:
            hold_row = Hold.objects.get(pk=hold.pk)
            if hold_row.state != Hold.STATE_OPEN:
                raise JobClaimLost('The job budget hold is closed.')
            delta = extra_needed(bound, hold_row.amount_usd, hold_drawn(hold.pk))
        _require_fit(user, delta, now)
        return Call.objects.create(
            job=job, hold=hold, user=user, document=document,
            provider=config.provider, model_id=config.model_id,
            reserved_usd=bound)


def mark_sent(call_pk):
    Call = _m('AIBillableCall')
    Call.objects.filter(pk=call_pk).update(sent=True)


def confirm_call(call_pk, tokens_in, tokens_out, cost, now=None):
    """Provider-reported usage: write the one ledger row for this call."""
    Call = _m('AIBillableCall')
    Ledger = _m('AIUsageLedger')
    AIJob = _m('AIJob')
    now = now or timezone.now()
    cost = usd(cost)
    user_id = Call.objects.filter(pk=call_pk).values_list(
        'user_id', flat=True).first()
    with transaction.atomic():
        _lock_scopes(user_id)
        call = Call.objects.select_for_update().get(pk=call_pk)
        if call.ledger_id is not None or call.state not in (
                Call.STATE_RESERVED, Call.STATE_UNKNOWN):
            logger.warning('ai: usage for call %s ignored (state %s)',
                           call.pk, call.state)
            return call
        if cost > call.reserved_usd:
            logger.warning('ai: call %s cost %s exceeds its bound %s',
                           call.pk, cost, call.reserved_usd)
        ledger = Ledger.objects.create(
            job_id=call.job_id, provider=call.provider, model_id=call.model_id,
            tokens_in=tokens_in, tokens_out=tokens_out,
            est_cost=float(call.reserved_usd), actual_cost=float(cost),
            user_id=call.user_id, document_id=call.document_id)
        call.state = Call.STATE_CONFIRMED
        call.confirmed_usd = cost
        call.tokens_in = tokens_in
        call.tokens_out = tokens_out
        call.ledger = ledger
        call.settled_at = now
        call.save()
        if call.job_id:
            AIJob.objects.filter(pk=call.job_id).update(
                actual_cost=F('actual_cost') + float(cost),
                tokens_in=F('tokens_in') + tokens_in,
                tokens_out=F('tokens_out') + tokens_out)
        return call


def settle_estimated(call_pk, estimated, tokens_in, tokens_out, now=None):
    """Answered without usage: billed, amount unknown. Stays held."""
    Call = _m('AIBillableCall')
    Call.objects.filter(pk=call_pk, state=Call.STATE_RESERVED).update(
        state=Call.STATE_ESTIMATED, estimated_usd=usd(estimated),
        tokens_in=tokens_in, tokens_out=tokens_out,
        settled_at=now or timezone.now())


def settle_failure(call_pk, exc, sent, now=None):
    Call = _m('AIBillableCall')
    state = failure_state(exc, sent)
    Call.objects.filter(pk=call_pk, state=Call.STATE_RESERVED).update(
        state=state, error=describe_error(exc)[:255],
        settled_at=now or timezone.now())
    return state


def resolve_call(call, *, by, charged, amount=None, note='', now=None):
    """Operator decision on an estimated or unknown call."""
    Call = _m('AIBillableCall')
    Ledger = _m('AIUsageLedger')
    now = now or timezone.now()
    with transaction.atomic():
        _lock_scopes(call.user_id)
        call = Call.objects.select_for_update().get(pk=call.pk)
        if call.state not in (Call.STATE_ESTIMATED, Call.STATE_UNKNOWN):
            raise ValueError(f'call {call.pk} is {call.state}, not resolvable')
        call.resolved_by = by
        call.resolved_at = now
        call.resolution_note = note
        if charged:
            amount = usd(amount)
            call.ledger = Ledger.objects.create(
                job_id=call.job_id, provider=call.provider,
                model_id=call.model_id, tokens_in=call.tokens_in or 0,
                tokens_out=call.tokens_out or 0,
                est_cost=float(call.reserved_usd), actual_cost=float(amount),
                user_id=call.user_id, document_id=call.document_id)
            call.state = Call.STATE_CONFIRMED
            call.confirmed_usd = amount
        else:
            call.state = Call.STATE_RELEASED
        call.save()
        return call


def reconcile_now(now=None):
    """Stand-alone reconcile under the instance lock (management command)."""
    with transaction.atomic():
        _lock_scopes(None)
        return reconcile_stale(now)


class _CallContext:
    """Receives a backend's sent()/usage() for one reserved call."""

    def __init__(self, call_pk, backend_cls):
        self.call_pk = call_pk
        self.backend_cls = backend_cls
        self.was_sent = False
        self.confirmed = False

    def sent(self):
        if not self.was_sent:
            mark_sent(self.call_pk)
            self.was_sent = True

    def usage(self, tokens_in, tokens_out):
        if self.confirmed:
            return
        confirm_call(self.call_pk, tokens_in, tokens_out,
                     price_cost(self.backend_cls, tokens_in, tokens_out))
        self.confirmed = True


class HostedMeter:
    """Reserve, send, and settle each hosted call. One per job or ai_fix."""

    def __init__(self, *, config, user=None, document=None, job=None,
                 claim=None, hold=None):
        self.config = config
        self.user = user
        self.document = document
        self.job = job
        self.claim = claim
        self.hold = hold

    def call(self, backend, image, keys, prompt):
        bound = call_bound_usd(type(backend), self.config, image.size, prompt)
        call = reserve_call(
            job=self.job, claim=self.claim, hold=self.hold, user=self.user,
            document=self.document, config=self.config, bound=bound)
        ctx = _CallContext(call.pk, type(backend))
        backend.meter = ctx
        try:
            result = backend.transcribe_region(image, keys, prompt)
        except BaseException as exc:
            if not ctx.confirmed:
                try:
                    settle_failure(call.pk, exc, ctx.was_sent)
                except Exception:
                    logger.exception('ai: could not settle call %s', call.pk)
            raise
        finally:
            backend.meter = None
        if not ctx.confirmed:
            settle_estimated(
                call.pk, price_cost(type(backend), result.tokens_in,
                                    result.tokens_out),
                result.tokens_in, result.tokens_out)
        return result

    def close(self, reason='finished'):
        close_hold(self.hold, reason)


class LocalMeter:
    """Local engines: no dollars, no reservations. Tokens go on the job."""

    def __init__(self, job=None):
        self.job = job

    def call(self, backend, image, keys, prompt):
        result = backend.transcribe_region(image, keys, prompt)
        if self.job is not None:
            _m('AIJob').objects.filter(pk=self.job.pk).update(
                tokens_in=F('tokens_in') + int(result.tokens_in or 0),
                tokens_out=F('tokens_out') + int(result.tokens_out or 0))
        return result

    def close(self, reason='finished'):
        pass


def open_job_meter(job, claim, config, user, document):
    """Open the job hold for est_cost, or a no-dollar meter for local."""
    if config.is_local:
        return LocalMeter(job)
    hold = open_hold(job, claim, user, job.est_cost)
    return HostedMeter(config=config, user=user, document=document,
                       job=job, claim=claim, hold=hold)


def open_call_meter(config, user, document):
    """A meter for one-off calls with no job (ai_fix)."""
    if config.is_local:
        return LocalMeter(None)
    return HostedMeter(config=config, user=user, document=document)
