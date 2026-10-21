"""ORM checks for the external HTR writeback planner.

The loader reads one audit and the current line primary keys on its part.
Engine IO is mocked. No production rows are read. Planning does not create
a transcription layer.
"""
import os
import uuid
from io import BytesIO
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from PIL import Image

from ai.external_htr_writeback_plan import (
    CODE_INCOMPLETE,
    CODE_LINE,
    CODE_MALFORMED,
    CODE_PART,
    CODE_PLANNED,
    CODE_READY,
    CODE_TEXT,
    CONTENT_LIMIT,
    MESSAGE_INCOMPLETE,
    MESSAGE_LINE,
    MESSAGE_MALFORMED,
    MESSAGE_PART,
    MESSAGE_PLANNED,
    MESSAGE_READY,
    MESSAGE_TEXT,
    load_external_htr_writeback_plan,
)
from ai.models import ExternalHTREngineConfig, ExternalHTRJob, ExternalHTRLineResult
from core.models import (
    Document,
    DocumentPart,
    Line,
    LineTranscription,
    Project,
    Transcription,
)
from users.models import User

MEDIA = "/tmp/htr-writeback-orm-media"
ENDPOINT = "http://10.0.0.8:9/secret-endpoint"


def _png():
    buffer = BytesIO()
    Image.new("RGB", (60, 60), (20, 20, 20)).save(buffer, "PNG")
    payload = buffer.getvalue()
    return SimpleUploadedFile("page.png", payload, "image/png"), len(payload)


@override_settings(MEDIA_ROOT=MEDIA)
class ExternalHtrWritebackPlanOrmTests(TestCase):
    def setUp(self):
        os.makedirs(MEDIA, exist_ok=True)
        self.owner = User.objects.create_user(
            username="wb-%s" % uuid.uuid4().hex[:12],
            email="wb-%s@example.test" % uuid.uuid4().hex[:12],
            password="unused-test-password",
        )
        self.project = Project.objects.create(
            name="writeback",
            slug=uuid.uuid4().hex[:12],
            owner=self.owner,
        )
        self.document, self.part, self.line = self._page("page")
        self.other_part = self._part(self.document)
        self.other_line = self._line(self.other_part)
        self.other_document, self.foreign_part, self.foreign_line = self._page("other")
        self.config = ExternalHTREngineConfig.objects.create(
            name="engine-%s" % uuid.uuid4().hex[:12],
            endpoint_url=ENDPOINT,
            enabled=False,
            tier=ExternalHTREngineConfig.TIER_RESEARCH,
            timeout_seconds=5,
            metadata={},
        )

    def test_completed_audit_plans_primary_keys_in_stored_order(self):
        self.line.external_id = "eSc_line_trap"
        self.line.save(update_fields=["external_id"])
        second = self._line(self.part, external_id="eSc_line_other")
        job = self._audit(
            self.document,
            self.part,
            result_count=2,
            warning_count=1,
            empty_text_count=1,
            requested_line_count=5,
            skipped_line_count=4,
        )
        self._result(job, str(self.line.pk), "kept", position=1, warnings=["soft"])
        self._result(job, str(second.pk), "", position=0)
        self._result(
            job, "900000001", "not-imported", position=2, status="skipped", code="no-mask",
        )
        stamp = "external-htr:pylaia:example-model"
        proposed = "%s:job-%s" % (stamp, job.pk)
        layers_before = Transcription.objects.count()
        Transcription.objects.create(document=self.document, name=stamp)
        Transcription.objects.create(document=self.document, name=proposed)
        before = self._fingerprint(job)

        plan = self._load(job.pk)

        self.assertTrue(plan.allowed)
        self.assertEqual(plan.code, CODE_READY)
        self.assertEqual(plan.message, MESSAGE_READY)
        self.assertEqual(plan.job_id, job.pk)
        self.assertEqual(plan.document_id, self.document.pk)
        self.assertEqual(plan.part_id, self.part.pk)
        self.assertEqual(
            [(item.line_id, item.text) for item in plan.lines],
            [(str(second.pk), ""), (str(self.line.pk), "kept")],
        )
        self.assertEqual(plan.included_count, 2)
        self.assertEqual(plan.skipped_count, 1)
        self.assertEqual(plan.empty_text_count, 1)
        self.assertNotEqual(self.line.external_id, str(self.line.pk))
        self.assertNotIn(self.line.external_id, [item.line_id for item in plan.lines])
        self.assertNotIn(second.external_id, [item.line_id for item in plan.lines])
        self.assertEqual(plan.provenance_stamp, stamp)
        self.assertEqual(plan.layer_name, proposed)
        self.assertNotEqual(plan.layer_name, plan.provenance_stamp)
        self.assertEqual(plan.engine, "pylaia")
        self.assertEqual(plan.model_id, "example-model")
        self.assertEqual(plan.model_version, "1")
        self.assertEqual(plan.api_version, "1")
        self.assertLessEqual(
            len(plan.provenance_stamp),
            LineTranscription._meta.get_field("version_source").max_length,
        )
        self.assertLessEqual(
            len(plan.layer_name),
            Transcription._meta.get_field("name").max_length,
        )
        self.assertNotIn("10.0.0.8", plan.message)
        self.assertNotIn(ENDPOINT, plan.layer_name)
        self.assertEqual(Transcription.objects.count(), layers_before + 2)
        self.assertEqual(LineTranscription.objects.count(), 0)
        self.assertEqual(self._fingerprint(job), before)
        self.assertEqual(self.config.endpoint_url, ENDPOINT)

    def test_external_id_deleted_foreign_and_mismatched_parts_have_no_candidates(self):
        owned = self._audit(self.document, self.part, result_count=1)
        self.line.external_id = "eSc_line_trap"
        self.line.save(update_fields=["external_id"])
        self._result(owned, self.line.external_id, "QUJDRA==", position=0)
        self._assert_blocked(owned, CODE_MALFORMED, MESSAGE_MALFORMED)

        deleted = self._audit(self.document, self.part, result_count=2)
        extra = self._line(self.part)
        self._result(deleted, str(self.line.pk), "kept", position=0)
        self._result(deleted, str(extra.pk), "gone", position=1)
        extra_pk = extra.pk
        extra.delete()
        plan = self._assert_blocked(deleted, CODE_LINE, MESSAGE_LINE)
        self.assertFalse(Line.objects.filter(pk=extra_pk).exists())
        self.assertEqual(plan.lines, ())
        self.assertEqual(deleted.lines.count(), 2)

        foreign = self._audit(self.document, self.part, result_count=1)
        self._result(foreign, str(self.other_line.pk), "other-part", position=0)
        self._assert_blocked(foreign, CODE_LINE, MESSAGE_LINE)

        mismatched = self._audit(self.document, self.foreign_part, result_count=1)
        self._result(mismatched, str(self.foreign_line.pk), "other-document", position=0)
        plan = self._assert_blocked(mismatched, CODE_PART, MESSAGE_PART)
        self.assertEqual(plan.document_id, self.document.pk)
        self.assertEqual(plan.part_id, self.foreign_part.pk)

    def test_deleted_part_and_unfinished_job_do_not_write(self):
        job = self._audit(self.document, self.part, result_count=1)
        self._result(job, str(self.line.pk), "kept", position=0)
        with patch("core.models.send_event") as send:
            self.part.delete()
        self.assertGreaterEqual(send.call_count, 1)
        job.refresh_from_db()
        self.assertIsNone(job.part_id)
        plan = self._assert_blocked(job, CODE_INCOMPLETE, MESSAGE_INCOMPLETE)
        self.assertIsNone(plan.part_id)
        self.assertEqual(job.lines.count(), 1)

        planned = self._audit(
            self.other_document,
            self.foreign_part,
            status=ExternalHTRJob.STATUS_PLANNED,
            layer_source=None,
            result_count=0,
        )
        self._assert_blocked(planned, CODE_PLANNED, MESSAGE_PLANNED)
        planned.refresh_from_db()
        self.assertEqual(planned.status, ExternalHTRJob.STATUS_PLANNED)

    def test_missing_job_is_a_fixed_refusal(self):
        before_jobs = ExternalHTRJob.objects.count()
        before_layers = Transcription.objects.count()
        before_lines = LineTranscription.objects.count()
        plan = self._load(999999999)
        self.assertFalse(plan.allowed)
        self.assertEqual(plan.code, CODE_INCOMPLETE)
        self.assertEqual(plan.message, MESSAGE_INCOMPLETE)
        self.assertEqual(plan.lines, ())
        self.assertIsNone(plan.job_id)
        self.assertEqual(ExternalHTRJob.objects.count(), before_jobs)
        self.assertEqual(Transcription.objects.count(), before_layers)
        self.assertEqual(LineTranscription.objects.count(), before_lines)

    def test_content_limit_matches_the_transcription_field(self):
        limit = LineTranscription._meta.get_field("content").max_length
        self.assertEqual(CONTENT_LIMIT, limit)
        self.assertEqual(limit, 2048)
        kept = "é" * limit
        refused_text = kept + "é"
        fit = self._audit(self.document, self.part, result_count=1)
        self._result(fit, str(self.line.pk), kept, position=0)
        plan = self._load(fit.pk)
        self.assertTrue(plan.allowed)
        self.assertEqual(plan.lines[0].text, kept)
        self.assertEqual(len(plan.lines[0].text), limit)
        self.assertEqual(LineTranscription.objects.count(), 0)

        over = self._audit(self.document, self.part, result_count=1)
        self._result(over, str(self.line.pk), refused_text, position=1)
        blocked = self._assert_blocked(over, CODE_TEXT, MESSAGE_TEXT)
        self.assertNotIn(refused_text[:12], blocked.message)
        self.assertEqual(over.lines.get().text, refused_text)

    def test_long_provenance_keeps_the_stored_engine_beside_the_stamp(self):
        engine = "e" * 80
        model_id = "m" * 80
        model_version = "v" * 40
        stamp = ("external-htr:%s:%s" % (engine, model_id))[:128]
        self.assertEqual(len(stamp), 128)
        job = self._audit(
            self.document,
            self.part,
            engine=engine,
            model_id=model_id,
            model_version=model_version,
            result_count=1,
        )
        self.assertEqual(job.layer_source, stamp)
        self._result(job, str(self.line.pk), "kept", position=0)
        plan = self._load(job.pk)
        self.assertTrue(plan.allowed)
        self.assertEqual(plan.provenance_stamp, stamp)
        self.assertEqual(plan.engine, engine)
        self.assertEqual(plan.model_id, model_id)
        self.assertEqual(plan.model_version, model_version)
        self.assertNotIn("job-%s" % job.pk, plan.provenance_stamp)
        self.assertTrue(plan.layer_name.endswith(":job-%s" % job.pk))
        self.assertNotEqual(plan.layer_name, plan.provenance_stamp)
        self.assertEqual(LineTranscription.objects.count(), 0)
        self.assertEqual(self.config.endpoint_url, ENDPOINT)

    def _assert_blocked(self, job, code, message):
        before = self._fingerprint(job)
        layers = Transcription.objects.count()
        plan = self._load(job.pk)
        self.assertFalse(plan.allowed)
        self.assertEqual(plan.code, code)
        self.assertEqual(plan.message, message)
        self.assertEqual(plan.lines, ())
        self.assertEqual(plan.included_count, 0)
        self.assertEqual(plan.skipped_count, 0)
        self.assertEqual(plan.empty_text_count, 0)
        self.assertEqual(plan.layer_name, "")
        self.assertEqual(plan.provenance_stamp, "")
        self.assertNotIn(ENDPOINT, plan.message)
        self.assertNotIn("QUJDRA==", plan.message)
        self.assertEqual(Transcription.objects.count(), layers)
        self.assertEqual(LineTranscription.objects.count(), 0)
        self.assertEqual(self._fingerprint(job), before)
        return plan

    def _load(self, job_id):
        # Do not patch socket.create_connection. That breaks the database.
        with patch("urllib.request.build_opener", side_effect=AssertionError("opener")), \
                patch("urllib.request.urlopen", side_effect=AssertionError("urlopen")), \
                patch("requests.get", side_effect=AssertionError("get")), \
                patch("requests.post", side_effect=AssertionError("post")):
            return load_external_htr_writeback_plan(job_id)

    def _audit(self, document, part, **overrides):
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
            document=document,
            part=part,
            config=self.config,
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

    def _result(self, job, line_id, text, *, position, warnings=None, status="included", code=""):
        return ExternalHTRLineResult.objects.create(
            job=job,
            line_id=line_id,
            position=position,
            text=text,
            warnings=[] if warnings is None else warnings,
            status=status,
            code=code,
        )

    def _fingerprint(self, job):
        job.refresh_from_db()
        rows = tuple(
            ExternalHTRLineResult.objects.filter(job=job).order_by("pk").values_list(
                "line_id", "position", "text", "status", "code", "warnings", "updated_at",
            )
        )
        return (
            job.status,
            job.code,
            job.message,
            job.layer_source,
            job.engine,
            job.model_id,
            job.model_version,
            job.api_version,
            job.result_count,
            job.warning_count,
            job.empty_text_count,
            job.skipped_line_count,
            job.requested_line_count,
            job.document_id,
            job.part_id,
            job.updated_at,
            rows,
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
