"""PostgreSQL checks for the external HTR import command.

Preview is read-only. ``--apply`` calls the writer. Engine IO is mocked.
No production rows are read.
"""
from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from ai.django_integration_tests.test_external_htr_writeback_orm import (
    ENDPOINT,
    MEDIA,
    _WritebackFixture,
    _no_io,
)
from ai.external_htr_writeback_plan import load_external_htr_writeback_plan
from ai.gate import LayerNotEligible, assert_training_eligible
from ai.models import AILayerGate, ExternalHTRJob
from core.models import Document, LineTranscription, Transcription

SECRET_TEXT = "secret-line-text"
SECRET_TITLE = "secret-doc-title"


def _run(*args):
    stdout, stderr = StringIO(), StringIO()
    try:
        call_command(
            "import_external_htr_audit",
            *args,
            stdout=stdout,
            stderr=stderr,
            skip_checks=True,
        )
    except SystemExit as exc:
        code = exc.code
    else:
        code = 0
    return code, stdout.getvalue(), stderr.getvalue()


@override_settings(MEDIA_ROOT=MEDIA)
class ExternalHtrImportCommandOrmTests(TestCase):
    _audit = _WritebackFixture._audit
    _result = _WritebackFixture._result
    _line = _WritebackFixture._line
    _page = _WritebackFixture._page
    _part = _WritebackFixture._part
    _assert_raw_gate = _WritebackFixture._assert_raw_gate

    def setUp(self):
        _WritebackFixture.setUp(self)
        Document.objects.filter(pk=self.document.pk).update(name=SECRET_TITLE)

    def test_preview_prints_the_summary_and_writes_nothing(self):
        second = self._line(self.part)
        job = self._audit(
            result_count=2,
            requested_line_count=3,
            skipped_line_count=1,
            empty_text_count=1,
        )
        self._result(job, str(second.pk), "", position=0)
        self._result(job, str(self.line.pk), SECRET_TEXT, position=1)
        self._result(job, "900000001", "skipped-secret", position=2, status="skipped")
        old = timezone.now() - timedelta(days=1)
        ExternalHTRJob.objects.filter(pk=job.pk).update(updated_at=old)
        layers = set(Transcription.objects.values_list("pk", flat=True))
        lines = LineTranscription.objects.count()
        gates = AILayerGate.objects.count()

        with _no_io():
            code, stdout, stderr = _run(str(job.pk))

        job.refresh_from_db()
        self.assertEqual(code, 0, stdout)
        self.assertEqual(
            stdout,
            "OK: external HTR import preview job=%s lines=2\n" % job.pk,
        )
        self._assert_quiet(stdout, stderr, "skipped-secret")
        self.assertFalse(job.imported)
        self.assertIsNone(job.imported_transcription_id)
        self.assertEqual(job.updated_at, old)
        self.assertEqual(job.status, ExternalHTRJob.STATUS_COMPLETED)
        self.assertEqual(set(Transcription.objects.values_list("pk", flat=True)), layers)
        self.assertEqual(LineTranscription.objects.count(), lines)
        self.assertEqual(AILayerGate.objects.count(), gates)
        self.assertEqual(job.lines.get(position=1).text, SECRET_TEXT)

    def test_apply_creates_one_layer_with_text_gate_sample_and_marker(self):
        second = self._line(self.part)
        job = self._audit(result_count=2, empty_text_count=1)
        self._result(job, str(self.line.pk), SECRET_TEXT, position=0)
        self._result(job, str(second.pk), "", position=1)
        before = set(Transcription.objects.values_list("pk", flat=True))

        with _no_io():
            code, stdout, stderr = _run(str(job.pk), "--apply")

        job.refresh_from_db()
        self.assertEqual(code, 0)
        self.assertTrue(job.imported)
        layer = job.imported_transcription
        self.assertEqual(
            stdout,
            "OK: external HTR import applied job=%s layer=%s lines=2\n" % (job.pk, layer.pk),
        )
        self._assert_quiet(stdout, stderr)
        self.assertEqual(
            set(Transcription.objects.values_list("pk", flat=True)),
            before | {layer.pk},
        )
        rows = {
            row.line_id: row.content
            for row in LineTranscription.objects.filter(transcription=layer)
        }
        self.assertEqual(rows, {self.line.pk: SECRET_TEXT, second.pk: ""})
        gate = self._assert_raw_gate(layer, [self.line.pk, second.pk])
        self.assertEqual(gate.sample_line_pks, [self.line.pk, second.pk])
        with self.assertRaises(LayerNotEligible):
            assert_training_eligible(Transcription.objects.get(pk=layer.pk))
        self.assertEqual(AILayerGate.objects.count(), 1)

        with _no_io():
            again, again_out, again_err = _run(str(job.pk), "--apply")
        self.assertEqual(again, 1)
        self.assertEqual(again_out, "FAILED: external HTR import already_imported\n")
        self._assert_quiet(again_out, again_err)
        self.assertEqual(Transcription.objects.filter(pk=layer.pk).count(), 1)
        self.assertEqual(AILayerGate.objects.count(), 1)

    def test_missing_invalid_unfinished_and_imported_audits_fail(self):
        with _no_io():
            for args in ((), ("0",), ("-3",), ("nope",), ("1.5",), ("999999999",)):
                code, stdout, stderr = _run(*args)
                self.assertEqual(code, 1, args)
                self.assertEqual(stdout, "FAILED: external HTR import audit_incomplete\n", args)
                self._assert_quiet(stdout, stderr)

        planned = self._audit(result_count=1, status=ExternalHTRJob.STATUS_PLANNED)
        self._result(planned, str(self.line.pk), SECRET_TEXT, position=0)
        with _no_io():
            code, stdout, stderr = _run(str(planned.pk))
        self.assertEqual(stdout, "FAILED: external HTR import audit_planned\n")
        self._assert_quiet(stdout, stderr)
        self.assertEqual(code, 1)

        ready = self._audit(result_count=1)
        self._result(ready, str(self.line.pk), SECRET_TEXT, position=0)
        plan = load_external_htr_writeback_plan(ready.pk)
        self.assertTrue(plan.allowed)
        ExternalHTRJob.objects.filter(pk=ready.pk).update(imported=True)
        layers = set(Transcription.objects.values_list("pk", flat=True))
        with _no_io():
            preview, preview_out, preview_err = _run(str(ready.pk))
            applied, applied_out, applied_err = _run(str(ready.pk), "--apply")
        self.assertEqual(preview_out, "FAILED: external HTR import already_imported\n")
        self.assertEqual(applied_out, "FAILED: external HTR import already_imported\n")
        self.assertEqual(preview, 1)
        self.assertEqual(applied, 1)
        self._assert_quiet(preview_out, preview_err)
        self._assert_quiet(applied_out, applied_err)
        ready.refresh_from_db()
        self.assertTrue(ready.imported)
        self.assertIsNone(ready.imported_transcription_id)
        self.assertEqual(set(Transcription.objects.values_list("pk", flat=True)), layers)
        self.assertEqual(LineTranscription.objects.count(), 0)
        self.assertEqual(AILayerGate.objects.count(), 0)

    def test_name_collision_leaves_the_existing_layer(self):
        job = self._audit(result_count=1)
        self._result(job, str(self.line.pk), SECRET_TEXT, position=0)
        name = "%s:job-%s" % (job.layer_source, job.pk)
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
            code, stdout, stderr = _run(str(job.pk), "--apply")

        job.refresh_from_db()
        existing.refresh_from_db()
        original.refresh_from_db()
        self.assertEqual(code, 1)
        self.assertEqual(stdout, "FAILED: external HTR import layer_name_taken\n")
        self._assert_quiet(stdout, stderr, name)
        self.assertFalse(job.imported)
        self.assertIsNone(job.imported_transcription_id)
        self.assertEqual(existing.updated_at, existing_updated)
        self.assertEqual(original.content, "already there")
        self.assertEqual(original.version_updated_at, original_updated)
        self.assertEqual(set(Transcription.objects.values_list("pk", flat=True)), layers)
        self.assertEqual(AILayerGate.objects.count(), 0)

    def test_apply_revalidates_current_lines_after_preview(self):
        job = self._audit(result_count=1)
        self._result(job, str(self.line.pk), SECRET_TEXT, position=0)
        old = timezone.now() - timedelta(days=1)
        ExternalHTRJob.objects.filter(pk=job.pk).update(updated_at=old)
        layers = set(Transcription.objects.values_list("pk", flat=True))

        with _no_io():
            preview, preview_out, preview_err = _run(str(job.pk))
            self.line.delete()
            applied, applied_out, applied_err = _run(str(job.pk), "--apply")

        job.refresh_from_db()
        self.assertEqual(preview, 0)
        self.assertIn("lines=1", preview_out)
        self._assert_quiet(preview_out, preview_err)
        self.assertEqual(applied, 1)
        self.assertEqual(applied_out, "FAILED: external HTR import line_not_on_part\n")
        self._assert_quiet(applied_out, applied_err)
        self.assertFalse(job.imported)
        self.assertEqual(job.updated_at, old)
        self.assertEqual(set(Transcription.objects.values_list("pk", flat=True)), layers)
        self.assertEqual(LineTranscription.objects.count(), 0)
        self.assertEqual(AILayerGate.objects.count(), 0)
        self.assertEqual(job.lines.get().text, SECRET_TEXT)

    def test_unexpected_writer_failure_is_fixed_and_leaves_nothing(self):
        job = self._audit(result_count=1)
        self._result(job, str(self.line.pk), SECRET_TEXT, position=0)
        old = timezone.now() - timedelta(days=1)
        ExternalHTRJob.objects.filter(pk=job.pk).update(updated_at=old)
        layers = set(Transcription.objects.values_list("pk", flat=True))

        def fail_sample(*args, **kwargs):
            raise RuntimeError("Traceback " + ENDPOINT + " " + SECRET_TEXT)

        with _no_io(), patch(
            "ai.external_htr_writeback.build_gate_sample", fail_sample,
        ):
            code, stdout, stderr = _run(str(job.pk), "--apply")

        job.refresh_from_db()
        self.assertEqual(code, 1)
        self.assertEqual(stdout, "FAILED: external HTR import import_internal\n")
        self.assertNotIn("Traceback", stderr)
        self._assert_quiet(stdout, stderr)
        self.assertFalse(job.imported)
        self.assertIsNone(job.imported_transcription_id)
        self.assertEqual(job.updated_at, old)
        self.assertEqual(set(Transcription.objects.values_list("pk", flat=True)), layers)
        self.assertEqual(LineTranscription.objects.count(), 0)
        self.assertEqual(AILayerGate.objects.count(), 0)

    def test_audit_with_no_included_lines_previews_and_apply_rolls_back(self):
        job = self._audit(result_count=0, requested_line_count=0)
        layers = set(Transcription.objects.values_list("pk", flat=True))
        with _no_io():
            preview, preview_out, preview_err = _run(str(job.pk))
            applied, applied_out, applied_err = _run(str(job.pk), "--apply")
        job.refresh_from_db()
        self.assertEqual(preview, 0)
        self.assertEqual(
            preview_out,
            "OK: external HTR import preview job=%s lines=0\n" % job.pk,
        )
        self.assertEqual(applied, 1)
        self.assertEqual(applied_out, "FAILED: external HTR import review_sample_empty\n")
        self._assert_quiet(preview_out, preview_err)
        self._assert_quiet(applied_out, applied_err)
        self.assertFalse(job.imported)
        self.assertEqual(set(Transcription.objects.values_list("pk", flat=True)), layers)
        self.assertEqual(LineTranscription.objects.count(), 0)
        self.assertEqual(AILayerGate.objects.count(), 0)

    def _assert_quiet(self, stdout, stderr, *extra):
        blob = stdout + stderr
        self.assertEqual(stdout.count("\n"), 1)
        self.assertNotIn("Traceback", blob)
        self.assertNotIn("RuntimeError", blob)
        for secret in (ENDPOINT, SECRET_TEXT, SECRET_TITLE, "example-model", "page.png", *extra):
            self.assertNotIn(secret, blob, secret)
