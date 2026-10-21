"""PostgreSQL checks for the external HTR writeback helper.

The helper creates one layer from a stored audit. Engine IO is mocked.
No production rows are read. The concurrency test uses two database
connections and records the second waiting on the audit-row lock.
"""
import os
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import timedelta
from io import BytesIO
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import close_old_connections, connection
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from PIL import Image

from ai.external_htr_writeback import (
    CODE_ALREADY,
    CODE_COLLISION,
    CODE_IMPORTED,
    MESSAGE_ALREADY,
    MESSAGE_COLLISION,
    MESSAGE_IMPORTED,
    import_external_htr_writeback,
)
from ai.external_htr_writeback_plan import (
    load_external_htr_writeback_plan,
    CODE_CANCELLED,
    CODE_FAILED as CODE_AUDIT_FAILED,
    CODE_INCOMPLETE,
    CODE_LINE,
    CODE_PLANNED,
    CODE_SENT,
    CODE_TEXT,
    CONTENT_LIMIT,
    MESSAGE_CANCELLED,
    MESSAGE_FAILED as MESSAGE_AUDIT_FAILED,
    MESSAGE_INCOMPLETE,
    MESSAGE_LINE,
    MESSAGE_PLANNED,
    MESSAGE_SENT,
    MESSAGE_TEXT,
)
from ai.models import (
    AILayerGate,
    ExternalHTREngineConfig,
    ExternalHTRJob,
    ExternalHTRLineResult,
)
from core.models import (
    Document,
    DocumentPart,
    Line,
    LineTranscription,
    Project,
    Transcription,
)
from users.models import User

MEDIA = "/tmp/htr-writeback-import-media"
ENDPOINT = "http://10.0.0.8:9/secret-endpoint"


def _png():
    buffer = BytesIO()
    Image.new("RGB", (60, 60), (20, 20, 20)).save(buffer, "PNG")
    payload = buffer.getvalue()
    return SimpleUploadedFile("page.png", payload, "image/png"), len(payload)


@contextmanager
def _no_io():
    # Do not patch socket.create_connection. That breaks the database.
    with patch("urllib.request.build_opener", side_effect=AssertionError("opener")), \
            patch("urllib.request.urlopen", side_effect=AssertionError("urlopen")), \
            patch("requests.get", side_effect=AssertionError("get")), \
            patch("requests.post", side_effect=AssertionError("post")), \
            patch("PIL.Image.open", side_effect=AssertionError("image")), \
            patch("core.models.send_event", side_effect=AssertionError("event")):
        yield


class _WritebackFixture:
    def setUp(self):
        os.makedirs(MEDIA, exist_ok=True)
        self.owner = User.objects.create_user(
            username="wb-%s" % uuid.uuid4().hex[:12],
            email="wb-%s@example.test" % uuid.uuid4().hex[:12],
            password="unused-test-password",
        )
        self.project = Project.objects.create(
            name="writeback-import",
            slug=uuid.uuid4().hex[:12],
            owner=self.owner,
        )
        self.document, self.part, self.line = self._page("page")
        self.other_line = self._line(self._part(self.document))
        self.config = ExternalHTREngineConfig.objects.create(
            name="engine-%s" % uuid.uuid4().hex[:12],
            endpoint_url=ENDPOINT,
            enabled=False,
            tier=ExternalHTREngineConfig.TIER_RESEARCH,
            timeout_seconds=5,
            metadata={},
        )

    def test_import_preserves_text_order_and_provenance(self):
        second = self._line(self.part)
        kept = "é" * CONTENT_LIMIT
        self.assertEqual(len(kept), 2048)
        self.assertEqual(len("𠀋".encode("utf-8")), 4)
        supplementary = "𠀋" * 3
        job = self._audit(
            result_count=3,
            warning_count=1,
            empty_text_count=1,
            requested_line_count=5,
            skipped_line_count=4,
        )
        self._result(job, str(self.line.pk), kept, position=1, warnings=["soft"], confidence=0.25, timing_ms=15)
        self._result(job, str(second.pk), "", position=0)
        third = self._line(self.part)
        self._result(job, str(third.pk), supplementary, position=2)
        self._result(
            job, "900000001", "é" * (CONTENT_LIMIT + 40), position=3,
            status="skipped", code="no-mask",
        )
        old = timezone.now() - timedelta(days=1)
        ExternalHTRJob.objects.filter(pk=job.pk).update(updated_at=old)
        layers_before = Transcription.objects.count()

        with _no_io():
            result = import_external_htr_writeback(job.pk)

        job.refresh_from_db()
        self.assertTrue(result.imported)
        self.assertEqual(result.code, CODE_IMPORTED)
        self.assertEqual(result.message, MESSAGE_IMPORTED)
        self.assertEqual(result.job_id, job.pk)
        self.assertEqual(result.transcription_id, job.imported_transcription_id)
        self.assertTrue(job.imported)
        self.assertEqual(job.status, ExternalHTRJob.STATUS_COMPLETED)
        self.assertGreater(job.updated_at, old)
        self.assertEqual(Transcription.objects.count(), layers_before + 1)
        layer = job.imported_transcription
        self.assertEqual(layer.document_id, self.document.pk)
        self.assertTrue(layer.name.endswith(":job-%s" % job.pk))
        self.assertNotEqual(layer.name, job.layer_source)
        self.assertLessEqual(len(layer.name), Transcription._meta.get_field("name").max_length)
        rows = list(
            LineTranscription.objects.filter(transcription=layer).order_by("pk")
        )
        self.assertEqual(
            [(row.line_id, row.content) for row in rows],
            [(second.pk, ""), (self.line.pk, kept), (third.pk, supplementary)],
        )
        self.assertEqual(len(rows[1].content), CONTENT_LIMIT)
        self.assertEqual(rows[1].content, kept)
        for row in rows:
            self.assertEqual(row.version_source, job.layer_source)
            self.assertEqual(row.version_author, self.owner.username)
            self.assertIsNone(row.avg_confidence)
            self.assertIsNone(row.graphs)
        stored = job.lines.get(line_id=str(self.line.pk))
        self.assertEqual(stored.confidence, 0.25)
        self.assertEqual(stored.timing_ms, 15)
        self.assertEqual(stored.warnings, ["soft"])
        self.assertEqual(stored.text, kept)
        self.assertEqual(AILayerGate.objects.count(), 0)
        self.assertNotIn(ENDPOINT, result.message)
        self.assertNotIn(kept[:8], result.message)

    def test_over_limit_and_inconsistent_audits_create_nothing(self):
        over = self._audit(result_count=1)
        refused = ("é" * CONTENT_LIMIT) + "é"
        self._result(over, str(self.line.pk), refused, position=0)
        self._assert_unchanged(over, CODE_TEXT, MESSAGE_TEXT)
        self.assertEqual(over.lines.get().text, refused)

        planned = self._audit(result_count=1, status=ExternalHTRJob.STATUS_PLANNED)
        self._result(planned, str(self.line.pk), "kept", position=0)
        self._assert_unchanged(planned, CODE_PLANNED, MESSAGE_PLANNED)
        planned.refresh_from_db()
        self.assertEqual(planned.status, ExternalHTRJob.STATUS_PLANNED)

        mismatched = self._audit(result_count=2)
        self._result(mismatched, str(self.line.pk), "kept", position=0)
        self._assert_unchanged(mismatched, CODE_INCOMPLETE, MESSAGE_INCOMPLETE)

        drifted = self._audit(result_count=1, layer_source="external-htr:other:model")
        self._result(drifted, str(self.line.pk), "kept", position=0)
        self._assert_unchanged(drifted, CODE_INCOMPLETE, MESSAGE_INCOMPLETE)

        foreign = self._audit(result_count=1)
        self._result(foreign, str(self.other_line.pk), "other-part", position=0)
        self._assert_unchanged(foreign, CODE_LINE, MESSAGE_LINE)

        deleted = self._audit(result_count=1)
        extra = self._line(self.part)
        self._result(deleted, str(extra.pk), "gone", position=0)
        extra_pk = extra.pk
        extra.delete()
        self.assertFalse(Line.objects.filter(pk=extra_pk).exists())
        self._assert_unchanged(deleted, CODE_LINE, MESSAGE_LINE)

        with _no_io():
            missing = import_external_htr_writeback(999999999)
            bad = import_external_htr_writeback(True)
        self.assertFalse(missing.imported)
        self.assertEqual(missing.code, CODE_INCOMPLETE)
        self.assertEqual(missing.message, MESSAGE_INCOMPLETE)
        self.assertIsNone(missing.transcription_id)
        self.assertEqual(bad.code, CODE_INCOMPLETE)
        self.assertEqual(LineTranscription.objects.count(), 0)

    def test_name_collision_leaves_the_existing_layer(self):
        job = self._audit(result_count=1)
        self._result(job, str(self.line.pk), "fresh", position=0)
        stamp = job.layer_source
        name = "%s:job-%s" % (stamp, job.pk)
        existing = Transcription.objects.create(document=self.document, name=name)
        original = LineTranscription.objects.create(
            transcription=existing,
            line=self.line,
            content="already there",
            version_source="manual",
            version_author="keeper",
        )
        existing_updated = existing.updated_at
        original_updated = original.version_updated_at
        layers = set(Transcription.objects.values_list("pk", flat=True))

        with _no_io():
            result = import_external_htr_writeback(job.pk)

        job.refresh_from_db()
        existing.refresh_from_db()
        original.refresh_from_db()
        self.assertFalse(result.imported)
        self.assertEqual(result.code, CODE_COLLISION)
        self.assertEqual(result.message, MESSAGE_COLLISION)
        self.assertIsNone(result.transcription_id)
        self.assertFalse(job.imported)
        self.assertIsNone(job.imported_transcription_id)
        self.assertEqual(existing.name, name)
        self.assertEqual(existing.updated_at, existing_updated)
        self.assertEqual(original.content, "already there")
        self.assertEqual(original.version_updated_at, original_updated)
        self.assertEqual(original.version_source, "manual")
        self.assertEqual(original.version_author, "keeper")
        self.assertEqual(set(Transcription.objects.values_list("pk", flat=True)), layers)
        self.assertEqual(LineTranscription.objects.filter(transcription=existing).count(), 1)
        self.assertNotIn("fresh", result.message)
        self.assertNotIn(ENDPOINT, result.message)

    def test_repeat_import_creates_nothing_else(self):
        job = self._audit(result_count=1, empty_text_count=1)
        self._result(job, str(self.line.pk), "", position=0)
        with _no_io():
            first = import_external_htr_writeback(job.pk)
        job.refresh_from_db()
        layer_id = job.imported_transcription_id
        row_id = LineTranscription.objects.get(transcription_id=layer_id).pk
        marked_at = job.updated_at
        layers = Transcription.objects.count()

        with _no_io():
            second = import_external_htr_writeback(job.pk)

        job.refresh_from_db()
        self.assertTrue(first.imported)
        self.assertFalse(second.imported)
        self.assertEqual(second.code, CODE_ALREADY)
        self.assertEqual(second.message, MESSAGE_ALREADY)
        self.assertIsNone(second.transcription_id)
        self.assertEqual(job.imported_transcription_id, layer_id)
        self.assertEqual(job.updated_at, marked_at)
        self.assertEqual(Transcription.objects.count(), layers)
        row = LineTranscription.objects.get(pk=row_id)
        self.assertEqual(row.content, "")
        self.assertEqual(LineTranscription.objects.filter(transcription_id=layer_id).count(), 1)

    def test_long_username_is_not_truncated_into_the_author_field(self):
        limit = LineTranscription._meta.get_field("version_author").max_length
        self.assertEqual(limit, 128)
        long_user = User.objects.create_user(
            username="n" * 150,
            email="long-%s@example.test" % uuid.uuid4().hex[:12],
            password="unused-test-password",
        )
        job = self._audit(result_count=1, created_by=long_user)
        self._result(job, str(self.line.pk), "kept", position=0)
        with _no_io():
            result = import_external_htr_writeback(job.pk)
        self.assertTrue(result.imported)
        row = LineTranscription.objects.get(transcription_id=result.transcription_id)
        self.assertEqual(row.content, "kept")
        self.assertEqual(row.version_author, "")
        self.assertNotIn("n" * 8, row.version_author)

    def test_mid_write_failure_rolls_back_the_layer_and_marker(self):
        second = self._line(self.part)
        job = self._audit(result_count=2)
        self._result(job, str(self.line.pk), "one", position=0)
        self._result(job, str(second.pk), "two", position=1)
        old = timezone.now() - timedelta(days=1)
        ExternalHTRJob.objects.filter(pk=job.pk).update(updated_at=old)
        layers = set(Transcription.objects.values_list("pk", flat=True))
        real_create = LineTranscription.objects.create

        def fail_second(*args, **kwargs):
            fail_second.calls += 1
            if fail_second.calls >= 2:
                raise RuntimeError("forced-mid-write")
            return real_create(*args, **kwargs)

        fail_second.calls = 0
        with _no_io(), patch.object(LineTranscription.objects, "create", fail_second):
            with self.assertRaisesRegex(RuntimeError, "forced-mid-write"):
                import_external_htr_writeback(job.pk)

        job.refresh_from_db()
        self.assertFalse(job.imported)
        self.assertIsNone(job.imported_transcription_id)
        self.assertEqual(job.updated_at, old)
        self.assertEqual(set(Transcription.objects.values_list("pk", flat=True)), layers)
        self.assertEqual(LineTranscription.objects.count(), 0)
        self.assertEqual(job.lines.get(position=0).text, "one")
        self.assertEqual(job.lines.get(position=1).text, "two")

    def test_failed_sent_and_cancelled_audits_create_nothing(self):
        for status, code, message in (
            (ExternalHTRJob.STATUS_FAILED, CODE_AUDIT_FAILED, MESSAGE_AUDIT_FAILED),
            (ExternalHTRJob.STATUS_SENT, CODE_SENT, MESSAGE_SENT),
            (ExternalHTRJob.STATUS_CANCELLED, CODE_CANCELLED, MESSAGE_CANCELLED),
        ):
            job = self._audit(result_count=1, status=status)
            self._result(job, str(self.line.pk), "kept", position=0)
            self._assert_unchanged(job, code, message)
            job.refresh_from_db()
            self.assertEqual(job.status, status)

    def test_completed_audit_with_no_included_lines_creates_an_empty_layer(self):
        job = self._audit(result_count=0, requested_line_count=0)
        layers_before = Transcription.objects.count()
        with _no_io():
            result = import_external_htr_writeback(job.pk)
        job.refresh_from_db()
        self.assertTrue(result.imported)
        self.assertEqual(result.code, CODE_IMPORTED)
        self.assertEqual(result.message, MESSAGE_IMPORTED)
        self.assertEqual(result.transcription_id, job.imported_transcription_id)
        self.assertEqual(Transcription.objects.count(), layers_before + 1)
        self.assertEqual(
            LineTranscription.objects.filter(
                transcription_id=result.transcription_id,
            ).count(),
            0,
        )
        self.assertEqual(job.status, ExternalHTRJob.STATUS_COMPLETED)
        self.assertFalse(job.code)

    def test_removing_the_layer_does_not_allow_another_import(self):
        job = self._audit(result_count=1, empty_text_count=1)
        self._result(job, str(self.line.pk), "", position=0)
        with _no_io():
            first = import_external_htr_writeback(job.pk)
        layer_id = first.transcription_id
        Transcription.objects.get(pk=layer_id).delete()
        job.refresh_from_db()
        self.assertTrue(job.imported)
        self.assertIsNone(job.imported_transcription_id)
        layers = Transcription.objects.count()
        with _no_io():
            second = import_external_htr_writeback(job.pk)
        job.refresh_from_db()
        self.assertFalse(second.imported)
        self.assertEqual(second.code, CODE_ALREADY)
        self.assertEqual(second.message, MESSAGE_ALREADY)
        self.assertIsNone(second.transcription_id)
        self.assertTrue(job.imported)
        self.assertEqual(Transcription.objects.count(), layers)
        self.assertFalse(Transcription.objects.filter(pk=layer_id).exists())

    def test_document_delete_removes_the_imported_audit_and_layer(self):
        job = self._audit(result_count=1)
        self._result(job, str(self.line.pk), "kept", position=0)
        with _no_io():
            result = import_external_htr_writeback(job.pk)
        self.assertTrue(result.imported)
        layer_id = result.transcription_id
        job_id = job.pk
        document_id = self.document.pk
        with patch("core.models.send_event"):
            self.document.delete()
        self.assertFalse(Document.objects.filter(pk=document_id).exists())
        self.assertFalse(ExternalHTRJob.objects.filter(pk=job_id).exists())
        self.assertFalse(Transcription.objects.filter(pk=layer_id).exists())
        self.assertFalse(ExternalHTRLineResult.objects.filter(job_id=job_id).exists())
        self.assertFalse(LineTranscription.objects.filter(transcription_id=layer_id).exists())

    def _assert_unchanged(self, job, code, message):
        old = timezone.now() - timedelta(days=1)
        ExternalHTRJob.objects.filter(pk=job.pk).update(updated_at=old)
        layers = set(Transcription.objects.values_list("pk", flat=True))
        lines = LineTranscription.objects.count()
        with _no_io():
            result = import_external_htr_writeback(job.pk)
        job.refresh_from_db()
        self.assertFalse(result.imported)
        self.assertEqual(result.code, code)
        self.assertEqual(result.message, message)
        self.assertIsNone(result.transcription_id)
        self.assertNotIn(ENDPOINT, result.message)
        self.assertFalse(job.imported)
        self.assertIsNone(job.imported_transcription_id)
        self.assertEqual(job.updated_at, old)
        self.assertEqual(set(Transcription.objects.values_list("pk", flat=True)), layers)
        self.assertEqual(LineTranscription.objects.count(), lines)
        return result

    def _audit(self, **overrides):
        engine = overrides.get("engine", "pylaia")
        model_id = overrides.get("model_id", "example-model")
        status = overrides.get("status", ExternalHTRJob.STATUS_COMPLETED)
        layer_source = overrides.get(
            "layer_source",
            ("external-htr:%s:%s" % (engine, model_id))[:128],
        )
        if status != ExternalHTRJob.STATUS_COMPLETED and "layer_source" not in overrides:
            layer_source = None
        return ExternalHTRJob.objects.create(
            document=self.document,
            part=self.part,
            config=self.config,
            created_by=overrides.get("created_by", self.owner),
            model_id=model_id,
            status=status,
            layer_source=layer_source,
            engine=engine,
            model_version=overrides.get("model_version", "1"),
            api_version=overrides.get("api_version", "1"),
            requested_line_count=overrides.get("requested_line_count", 1),
            skipped_line_count=overrides.get("skipped_line_count", 0),
            result_count=overrides.get("result_count", 0),
            warning_count=overrides.get("warning_count", 0),
            empty_text_count=overrides.get("empty_text_count", 0),
            code=overrides.get("code", ""),
            message=overrides.get("message", "response would be written"),
        )

    def _result(self, job, line_id, text, *, position, warnings=None, status="included",
                code="", confidence=None, timing_ms=0):
        return ExternalHTRLineResult.objects.create(
            job=job,
            line_id=line_id,
            position=position,
            text=text,
            warnings=[] if warnings is None else warnings,
            status=status,
            code=code,
            confidence=confidence,
            timing_ms=timing_ms,
        )

    def _page(self, name):
        document = Document.objects.create(
            name=name, owner=self.owner, project=self.project,
        )
        part = self._part(document)
        return document, part, self._line(part)

    def _part(self, document):
        upload, size = _png()
        return DocumentPart.objects.create(
            document=document,
            image=upload,
            image_file_size=size,
            original_filename="page.png",
        )

    def _line(self, part, external_id=None):
        line = Line.objects.create(
            document_part=part,
            baseline=[[10, 30], [50, 30]],
            mask=[[10, 20], [50, 20], [50, 40], [10, 40]],
        )
        if external_id is not None:
            line.external_id = external_id
            line.save(update_fields=["external_id"])
        return line


@override_settings(MEDIA_ROOT=MEDIA)
class ExternalHtrWritebackOrmTests(_WritebackFixture, TestCase):
    """Rolled-back checks. Concurrent imports are in the transaction case below."""


@override_settings(MEDIA_ROOT=MEDIA)
class ExternalHtrWritebackConcurrencyTests(TransactionTestCase):
    """Two database connections import one audit.

    The fixture is committed, so both connections can lock the same job.
    A TestCase transaction would hide those rows from the second connection.
    """

    def test_second_import_waits_on_the_held_audit_lock(self):
        host = ExternalHtrWritebackOrmTests(
            "test_import_preserves_text_order_and_provenance",
        )
        host.setUp()
        job = host._audit(result_count=1)
        host._result(job, str(host.line.pk), "same", position=0)
        job_id = job.pk
        document_id = host.document.pk
        line_id = host.line.pk
        before = set(
            Transcription.objects.filter(document_id=document_id).values_list(
                "pk", flat=True,
            )
        )
        held = threading.Event()
        release = threading.Event()
        second_started = threading.Event()
        outcomes = []
        errors = []
        observed = {}
        guard = threading.Lock()

        def blocking_plan(plan_job_id):
            # The helper calls this only after select_for_update. The first
            # caller still owns that transaction, so the second import waits
            # on the audit row until release is set.
            if not held.is_set():
                held.set()
                if not release.wait(timeout=15):
                    raise TimeoutError("audit lock was not released")
            return load_external_htr_writeback_plan(plan_job_id)

        def _session(name):
            close_old_connections()
            connection.ensure_connection()
            with connection.cursor() as cursor:
                cursor.execute("SELECT set_config('application_name', %s, false)", [name])

        def first():
            try:
                _session("htr-writeback-holder")
                result = import_external_htr_writeback(job_id)
                with guard:
                    outcomes.append(result)
            except Exception as exc:
                with guard:
                    errors.append(type(exc).__name__)
            finally:
                release.set()
                close_old_connections()

        def second():
            try:
                if not held.wait(timeout=15):
                    raise TimeoutError("holder did not take the audit lock")
                _session("htr-writeback-waiter")
                second_started.set()
                result = import_external_htr_writeback(job_id)
                with guard:
                    outcomes.append(result)
            except Exception as exc:
                with guard:
                    errors.append(type(exc).__name__)
            finally:
                close_old_connections()

        def watch():
            try:
                if not second_started.wait(timeout=15):
                    return
                close_old_connections()
                connection.ensure_connection()
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    row = _audit_lock_wait()
                    if row is not None:
                        with guard:
                            observed.update(row)
                        return
                    if release.wait(0.05):
                        return
            except Exception as exc:
                with guard:
                    errors.append(type(exc).__name__)
            finally:
                release.set()
                close_old_connections()

        threads = [
            threading.Thread(target=first),
            threading.Thread(target=second),
            threading.Thread(target=watch),
        ]
        try:
            with _no_io(), patch(
                "ai.external_htr_writeback.load_external_htr_writeback_plan",
                blocking_plan,
            ):
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join(timeout=20)
                    self.assertFalse(thread.is_alive())
        finally:
            release.set()
            held.set()
            for thread in threads:
                thread.join(timeout=5)

        self.assertEqual(errors, [])
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(
            (
                observed.get("wait_event_type"),
                observed.get("wait_event"),
                observed.get("locktype"),
                observed.get("granted"),
            ),
            ("Lock", "transactionid", "transactionid", False),
            observed,
        )
        self.assertNotEqual(observed.get("waiter_pid"), observed.get("holder_pid"))
        self.assertEqual(observed.get("holder_relation"), "ai_externalhtrjob")
        self.assertEqual(
            sorted(item.code for item in outcomes),
            [CODE_ALREADY, CODE_IMPORTED],
        )
        imported = [item for item in outcomes if item.imported]
        refused = [item for item in outcomes if not item.imported]
        self.assertEqual(len(imported), 1)
        self.assertEqual(imported[0].message, MESSAGE_IMPORTED)
        self.assertEqual(refused[0].message, MESSAGE_ALREADY)
        self.assertIsNone(refused[0].transcription_id)
        self.assertNotIn("same", refused[0].message)
        self.assertNotIn(ENDPOINT, refused[0].message)

        job.refresh_from_db()
        self.assertTrue(job.imported)
        self.assertEqual(job.status, ExternalHTRJob.STATUS_COMPLETED)
        created = set(
            Transcription.objects.filter(document_id=document_id).values_list(
                "pk", flat=True,
            )
        ) - before
        self.assertEqual(len(created), 1)
        self.assertEqual(created, {job.imported_transcription_id})
        rows = list(
            LineTranscription.objects.filter(transcription_id=job.imported_transcription_id)
        )
        self.assertEqual([(row.line_id, row.content) for row in rows], [(line_id, "same")])
        self.assertEqual(LineTranscription.objects.filter(line_id=line_id).count(), 1)


def _audit_lock_wait():
    """Return the waiter while another session holds the audit-job row lock."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT waiter.pid,
                   waiter.wait_event_type,
                   waiter.wait_event,
                   blocked.locktype,
                   blocked.granted,
                   holder.pid,
                   holder_class.relname
            FROM pg_stat_activity waiter
            JOIN pg_locks blocked
              ON blocked.pid = waiter.pid
             AND NOT blocked.granted
            JOIN pg_stat_activity holder
              ON holder.application_name = 'htr-writeback-holder'
             AND holder.datname = waiter.datname
             AND holder.pid <> waiter.pid
            JOIN pg_locks held
              ON held.pid = holder.pid
             AND held.granted
            JOIN pg_class holder_class
              ON holder_class.oid = held.relation
             AND holder_class.relname = 'ai_externalhtrjob'
            WHERE waiter.application_name = 'htr-writeback-waiter'
              AND waiter.datname = current_database()
              AND waiter.wait_event_type = 'Lock'
              AND waiter.wait_event = 'transactionid'
              AND blocked.locktype = 'transactionid'
            LIMIT 1
            """
        )
        row = cursor.fetchone()
    if row is None:
        return None
    return {
        "waiter_pid": row[0],
        "wait_event_type": row[1],
        "wait_event": row[2],
        "locktype": row[3],
        "granted": row[4],
        "holder_pid": row[5],
        "holder_relation": row[6],
    }
