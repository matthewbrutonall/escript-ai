"""HTRflow page workflow against disposable rows. No engine process is called."""
import io
import json
import os
import shutil
import uuid
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from PIL import Image

from ai.external_htr_dispatch import dispatch_external_htr_run
from ai.external_htr_user import import_run, list_models, start_run
from ai.gate import LayerNotEligible, assert_training_eligible
from ai.htrflow_client import HtrflowClientError
from ai.htrflow_contract import PageModel, PageModelList
from ai.htrflow_run import execute_htrflow_run
from ai.htrflow_run_import import import_htrflow_run
from ai.models import (
    AIDocumentPolicy,
    AIJob,
    AILayerGate,
    ExternalHTREngineConfig,
    ExternalHTRJob,
    ExternalHTRLineResult,
    ExternalHTRRun,
)
from core.models import (
    Block,
    Document,
    DocumentPart,
    Line,
    LineTranscription,
    Project,
    Transcription,
)
from users.models import User

MEDIA = "/tmp/escript-htrflow-run-media"
SECRET_TITLE = "htrflow-secret-title"
SECRET_FILE = "htrflow-secret-page.png"
SECRET_PATH = "htrflow-secret-path"
KEPT = "kept"
MANUAL_TEXT = "manual-kept-text"
KRAKEN_TEXT = "kraken-kept-text"
MODEL_ID = "handwriting-english-large"

ALTO = """<?xml version="1.0" encoding="UTF-8"?>
<alto xmlns="http://www.loc.gov/standards/alto/ns-v4#">
  <Description>
    <sourceImageInformation><fileName>%s</fileName></sourceImageInformation>
    <OCRProcessing>
      <ocrProcessingStep>
        <processingStepSettings>model: example/regions model_class: YOLO model_version: version-regions</processingStepSettings>
        <processingSoftware><softwareVersion>0.2.6</softwareVersion></processingSoftware>
      </ocrProcessingStep>
      <ocrProcessingStep>
        <processingStepSettings>model: example/lines model_class: YOLO model_version: version-lines</processingStepSettings>
      </ocrProcessingStep>
      <ocrProcessingStep>
        <processingStepSettings>model: example/text model_class: TrOCR model_version: version-text</processingStepSettings>
      </ocrProcessingStep>
    </OCRProcessing>
  </Description>
  <Layout>
    <Page WIDTH="120" HEIGHT="40">
      <TextBlock ID="block-a" HPOS="10" VPOS="8" WIDTH="80" HEIGHT="24">
        <Shape><Polygon POINTS="10,8 90,8 90,32 10,32"/></Shape>
        <TextLine ID="line-a" HPOS="10" VPOS="8" WIDTH="80" HEIGHT="12">
          <Shape><Polygon POINTS="10,8 90,8 90,20 10,20"/></Shape>
          <String CONTENT="%s" WC="0.5"/>
        </TextLine>
        <TextLine ID="line-b" HPOS="10" VPOS="20" WIDTH="80" HEIGHT="12">
          <Shape><Polygon POINTS="10,20 90,20 90,32 10,32"/></Shape>
          <String CONTENT=""/>
        </TextLine>
        <TextLine ID="line-c"><String CONTENT="skip-me"/></TextLine>
      </TextBlock>
    </Page>
  </Layout>
</alto>
""" % (SECRET_FILE, KEPT)

EMPTY_PAGE = """<alto><Layout><Page WIDTH="20" HEIGHT="10"></Page></Layout></alto>"""


def _png():
    buffer = io.BytesIO()
    Image.new("RGB", (40, 20), (240, 240, 240)).save(buffer, "PNG")
    payload = buffer.getvalue()
    return SimpleUploadedFile(SECRET_FILE, payload, "image/png"), len(payload)


def _forbid_image(_part):
    raise AssertionError("page image was opened")


def _listed(*models):
    return PageModelList("htrflow", models)


def _model(model_id=MODEL_ID, beams=1, present=True):
    return PageModel(model_id, model_id, "", False, beams, present)


@override_settings(MEDIA_ROOT=MEDIA)
class HtrflowRunOrmTests(TestCase):
    def setUp(self):
        self.addCleanup(shutil.rmtree, MEDIA, True)
        if os.path.isdir(MEDIA):
            shutil.rmtree(MEDIA)
        os.makedirs(MEDIA)
        self.owner = User.objects.create_user(
            username="owner-%s" % uuid.uuid4().hex[:12],
            email="owner-%s@example.test" % uuid.uuid4().hex[:12],
            password="unused-test-password",
        )
        self.stranger = User.objects.create_user(
            username="stranger-%s" % uuid.uuid4().hex[:12],
            email="stranger-%s@example.test" % uuid.uuid4().hex[:12],
            password="unused-test-password",
        )
        self.project = Project.objects.create(
            name="htrflow",
            slug=uuid.uuid4().hex[:12],
            owner=self.owner,
        )
        self.document = Document.objects.create(
            name=SECRET_TITLE,
            owner=self.owner,
            project=self.project,
        )
        AIDocumentPolicy.objects.create(document=self.document, never_send_offsite=True)
        self.part, self.kept, self.other = self._part()
        self.manual = Transcription.objects.get(
            document=self.document, name=Transcription.DEFAULT_NAME,
        )
        self.manual_row = LineTranscription.objects.create(
            transcription=self.manual,
            line=self.kept,
            content=MANUAL_TEXT,
            version_source="manual",
            version_author="keeper",
        )
        self.kraken_row = LineTranscription.objects.create(
            transcription=self.manual,
            line=self.other,
            content=KRAKEN_TEXT,
            version_source="kraken",
            version_author="keeper",
        )
        self.client = Client()
        self.client.force_login(self.owner)

    def test_permission_offsite_hosted_and_beams_are_refused_before_an_image_opens(self):
        denied = start_run(
            self.document, self.stranger,
            {"config_id": 1, "model_id": MODEL_ID, "part_ids": [self.part.pk]},
            queue=lambda _run_id: (_ for _ in ()).throw(AssertionError("queued")),
        )
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(denied.data["code"], "permission_denied")
        offsite = self._config("http://203.0.113.9/%s/" % SECRET_PATH)
        hosted = self._config(
            "http://127.0.0.1:9/%s/" % SECRET_PATH,
            tier=ExternalHTREngineConfig.TIER_API,
        )
        disabled = self._config("http://127.0.0.1:9/%s/" % SECRET_PATH, enabled=False)
        with patch("urllib.request.urlopen", side_effect=AssertionError("urlopen")), patch(
            "ai.htrflow_client.fetch_page_models", side_effect=AssertionError("models"),
        ), patch("ai.htrflow_run._open_part_bytes", side_effect=AssertionError("image")):
            offsite_response = start_run(
                self.document, self.owner,
                {"config_id": offsite.pk, "model_id": MODEL_ID, "part_ids": [self.part.pk]},
            )
            hosted_response = start_run(
                self.document, self.owner,
                {"config_id": hosted.pk, "model_id": MODEL_ID, "part_ids": [self.part.pk]},
            )
            disabled_response = start_run(
                self.document, self.owner,
                {"config_id": disabled.pk, "model_id": MODEL_ID, "part_ids": [self.part.pk]},
            )
            wide = self._config("http://127.0.0.1:9/%s/" % SECRET_PATH)
            with patch("ai.htrflow_client.fetch_page_models", return_value=_listed(_model(beams=4))):
                beam_response = start_run(
                    self.document, self.owner,
                    {"config_id": wide.pk, "model_id": MODEL_ID, "part_ids": [self.part.pk]},
                    queue=lambda _run_id: (_ for _ in ()).throw(AssertionError("queued")),
                )
            missing = self._config("http://127.0.0.1:9/%s/" % SECRET_PATH)
            with patch("ai.htrflow_client.fetch_page_models", return_value=_listed(_model("other"))):
                missing_response = start_run(
                    self.document, self.owner,
                    {"config_id": missing.pk, "model_id": MODEL_ID, "part_ids": [self.part.pk]},
                )
        self.assertEqual(offsite_response.status_code, 400)
        self.assertEqual(offsite_response.data["code"], "offsite_forbidden")
        self.assertEqual(hosted_response.data["code"], "tier_refused")
        self.assertEqual(disabled_response.data["code"], "disabled")
        self.assertEqual(beam_response.status_code, 400)
        self.assertEqual(beam_response.data["code"], "beam_width_refused")
        self.assertEqual(missing_response.data["code"], "model_not_found")
        self.assertEqual(ExternalHTRRun.objects.count(), 0)
        self.assertNotIn(SECRET_PATH, json.dumps(offsite_response.data))
        queued = self._queued(self._config("http://203.0.113.9/%s/" % SECRET_PATH))
        with patch("ai.htrflow_client.fetch_page_models", side_effect=AssertionError("models")), patch(
            "ai.htrflow_run._open_part_bytes", _forbid_image,
        ):
            refused = execute_htrflow_run(queued.pk, open_image=_forbid_image, recognize=_forbid_recognize)
        refused.refresh_from_db()
        self.assertEqual(refused.status, "failed")
        self.assertEqual(refused.code, "offsite_forbidden")
        self.assertEqual(ExternalHTRJob.objects.count(), 0)

    def test_retry_then_success_import_gate_and_preserved_transcriptions(self):
        config = self._config("http://127.0.0.1:9/%s/" % SECRET_PATH)
        calls = {"n": 0, "images": []}

        def recognize(_config, *, model_id, image):
            self.assertEqual(model_id, MODEL_ID)
            calls["n"] += 1
            calls["images"].append(image)
            if calls["n"] < 3:
                raise HtrflowClientError("unavailable", "recognition request was not completed")
            return ALTO

        pauses = {"n": 0}
        run = self._queued(config)
        with self.assertLogs("ai.htrflow_run", level="INFO") as captured:
            finished = execute_htrflow_run(
                run.pk,
                recognize=recognize,
                fetch_models=lambda _config: _listed(_model()),
                open_image=lambda _part: b"synthetic-page",
                pause=lambda: pauses.__setitem__("n", pauses["n"] + 1),
            )
        finished.refresh_from_db()
        self.assertEqual(finished.status, "completed")
        self.assertEqual(finished.engine, "htrflow")
        self.assertEqual(calls["n"], 3)
        self.assertEqual(pauses["n"], 2)
        self.assertEqual(calls["images"], [b"synthetic-page"] * 3)
        page = finished.pages[0]
        self.assertEqual(page["status"], "completed")
        self.assertEqual(page["attempts"], 3)
        self.assertEqual(
            {(line["line_id"], line["status"], line["code"], line["empty"], line["text_length"])
             for line in page["lines"]},
            {("line-a", "included", "", False, len(KEPT)),
             ("line-b", "included", "", True, 0),
             ("line-c", "skipped", "no_geometry", False, 0)},
        )
        for line in page["lines"]:
            self.assertEqual(set(line), {"line_id", "status", "code", "empty", "text_length"})
        blob = "\n".join(captured.output)
        self.assertIn("htrflow run %s completed" % finished.pk, blob)
        for secret in (KEPT, "skip-me", SECRET_FILE, SECRET_PATH, SECRET_TITLE, "synthetic-page"):
            self.assertNotIn(secret, blob)
            self.assertNotIn(secret, json.dumps(finished.pages))
        job = ExternalHTRJob.objects.get(run=finished)
        layout = json.dumps(job.layout)
        self.assertNotIn(KEPT, layout)
        self.assertNotIn("skip-me", layout)
        self.assertNotIn(SECRET_FILE, layout)
        self.assertNotIn(SECRET_PATH, layout)
        self.assertEqual(job.layout["software_version"], "0.2.6")
        self.assertEqual(
            [item["role"] for item in job.layout["models"]],
            ["regions", "lines", "recognition"],
        )
        self.assertEqual(job.model_version, "version-text")
        stored = {row.line_id: row for row in ExternalHTRLineResult.objects.filter(job=job)}
        self.assertEqual(stored["line-a"].text, KEPT)
        self.assertEqual(stored["line-b"].text, "")
        self.assertEqual(stored["line-c"].text, "")
        self.assertEqual(AIJob.objects.count(), 0)
        again = execute_htrflow_run(
            finished.pk,
            recognize=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("second")),
            fetch_models=lambda _config: (_ for _ in ()).throw(AssertionError("models")),
            open_image=_forbid_image,
        )
        self.assertEqual(again.status, "completed")
        before = Line.objects.filter(document_part=self.part).count()
        result = import_htrflow_run(finished.pk)
        self.assertEqual(result.code, "writeback_imported")
        self.assertGreater(Line.objects.filter(document_part=self.part).count(), before)
        created = Line.objects.get(document_part=self.part, external_id="line-a")
        self.assertGreaterEqual(len(created.mask), 3)
        self.assertEqual(created.block.external_id, "block-a")
        self.assertTrue(Block.objects.filter(document_part=self.part, external_id="block-a").exists())
        layer = Transcription.objects.get(pk=result.transcription_id)
        self.assertEqual(layer.name, "external-htr:htrflow:%s:run-%s" % (MODEL_ID, finished.pk))
        rows = {row.line.external_id: row for row in layer.linetranscription_set.select_related("line")}
        self.assertEqual(rows["line-a"].content, KEPT)
        self.assertEqual(rows["line-a"].version_source, "external-htr:htrflow:%s" % MODEL_ID)
        self.assertEqual(rows["line-a"].version_author, self.owner.username)
        self.assertEqual(rows["line-b"].content, "")
        self.assertNotIn("line-c", rows)
        layer = Transcription.objects.get(pk=layer.pk)
        gate = AILayerGate.objects.get(transcription=layer)
        self.assertEqual(gate.state, AILayerGate.STATE_RAW)
        self.assertTrue(gate.sample_line_pks)
        self.assertIsNone(gate.comparison_id)
        with self.assertRaises(LayerNotEligible):
            assert_training_eligible(layer)
        repeat = import_run(self.document, self.owner, finished.pk)
        self.assertEqual(repeat.status_code, 200)
        self.assertEqual(repeat.data["code"], "already_imported")
        self.assertEqual(
            Transcription.objects.filter(document=self.document).exclude(pk=self.manual.pk).count(),
            1,
        )
        self._assert_existing_text()
        self.assertEqual(AIJob.objects.count(), 0)

    def test_matched_external_id_does_not_create_a_duplicate_line(self):
        self.kept.external_id = "line-a"
        self.kept.save(update_fields=["external_id"])
        run = self._run_alto(self._config("http://127.0.0.1:9/%s/" % SECRET_PATH))
        before = Line.objects.filter(document_part=self.part).count()
        result = import_htrflow_run(run.pk)
        self.assertEqual(result.code, "writeback_imported")
        self.assertEqual(Line.objects.filter(document_part=self.part).count(), before)
        layer = Transcription.objects.get(pk=result.transcription_id)
        written = LineTranscription.objects.get(transcription=layer, line=self.kept)
        self.assertEqual(written.content, KEPT)
        self.assertFalse(LineTranscription.objects.filter(transcription=layer, line=self.other).exists())
        self._assert_existing_text()

    def test_partial_import_writes_only_the_completed_page(self):
        upload, size = _png()
        second = DocumentPart.objects.create(
            document=self.document,
            image=upload,
            image_file_size=size,
            original_filename=SECRET_FILE,
        )
        Line.objects.create(
            document_part=second,
            baseline=[[2, 8], [20, 8]],
            mask=[[2, 4], [20, 4], [20, 12], [2, 12]],
        )
        calls = {"n": 0}

        def recognize(_config, *, model_id, image):
            calls["n"] += 1
            if calls["n"] == 1:
                return ALTO
            raise HtrflowClientError("invalid_response", "engine response could not be read")

        run = self._queued(
            self._config("http://127.0.0.1:9/%s/" % SECRET_PATH),
            part_ids=[self.part.pk, second.pk],
        )
        finished = execute_htrflow_run(
            run.pk,
            recognize=recognize,
            fetch_models=lambda _config: _listed(_model()),
            open_image=lambda _part: b"synthetic-page",
            pause=lambda: (_ for _ in ()).throw(AssertionError("retried")),
        )
        finished.refresh_from_db()
        self.assertEqual(finished.status, "partial")
        self.assertEqual(calls["n"], 2)
        self.assertEqual([page["status"] for page in finished.pages], ["completed", "failed"])
        self.assertEqual(finished.pages[1]["code"], "invalid_response")
        result = import_htrflow_run(finished.pk)
        self.assertEqual(result.code, "writeback_imported")
        self.assertFalse(Line.objects.filter(document_part=second, external_id="line-a").exists())
        self.assertTrue(Line.objects.filter(document_part=self.part, external_id="line-a").exists())
        self._assert_existing_text()

    def test_empty_page_can_complete_and_a_terminal_failure_does_not_switch_engine(self):
        run = self._queued(self._config("http://127.0.0.1:9/%s/" % SECRET_PATH))
        finished = execute_htrflow_run(
            run.pk,
            recognize=lambda *_args, **_kwargs: EMPTY_PAGE,
            fetch_models=lambda _config: _listed(_model(present=False)),
            open_image=lambda _part: b"synthetic-page",
        )
        finished.refresh_from_db()
        self.assertEqual(finished.status, "completed")
        self.assertEqual(finished.pages[0]["lines"], [])
        empty = import_htrflow_run(finished.pk)
        self.assertEqual(empty.code, "no_included_lines")
        self.assertIsNone(empty.transcription_id)
        failed = self._queued(self._config("http://127.0.0.1:9/%s/" % SECRET_PATH))
        calls = {"n": 0}

        def once(_config, *, model_id, image):
            calls["n"] += 1
            raise HtrflowClientError("invalid_response", "engine response could not be read")

        with patch("ai.external_htr_run.execute_external_htr_run", side_effect=AssertionError("line")):
            outcome = execute_htrflow_run(
                failed.pk,
                recognize=once,
                fetch_models=lambda _config: _listed(_model()),
                open_image=lambda _part: b"synthetic-page",
                pause=lambda: (_ for _ in ()).throw(AssertionError("retried")),
            )
        outcome.refresh_from_db()
        self.assertEqual(calls["n"], 1)
        self.assertEqual(outcome.status, "failed")
        self.assertEqual(outcome.code, "invalid_response")
        self.assertEqual(AIJob.objects.count(), 0)

    def test_dispatcher_uses_the_metadata_marker_and_the_model_list_is_page_level(self):
        page = self._queued(self._config("http://127.0.0.1:9/%s/" % SECRET_PATH))
        line = self._queued(self._line_config("http://127.0.0.1:9/%s/" % SECRET_PATH))
        with patch("ai.htrflow_run.execute_htrflow_run", return_value="page") as page_worker, patch(
            "ai.external_htr_run.execute_external_htr_run", return_value="line",
        ) as line_worker:
            self.assertEqual(dispatch_external_htr_run(page.pk, task_id="t"), "page")
            self.assertEqual(dispatch_external_htr_run(line.pk, task_id="t"), "line")
        page_worker.assert_called_once()
        line_worker.assert_called_once()
        config = self._config("http://127.0.0.1:9/%s/" % SECRET_PATH)
        with patch(
            "ai.htrflow_client.fetch_page_models",
            return_value=_listed(_model(), _model("wide", beams=4)),
        ):
            listed = list_models(self.document, self.owner, config.pk)
        self.assertEqual(listed.data["engine"], "htrflow")
        self.assertEqual(listed.data["models"][0]["task"], "recognize_page")
        self.assertEqual(listed.data["models"][0]["num_beams"], 1)
        self.assertEqual(listed.data["models"][1]["num_beams"], 4)
        self.assertNotIn("endpoint", json.dumps(listed.data))
        self.assertNotIn(SECRET_PATH, json.dumps(listed.data))
        stranger = Client()
        stranger.force_login(self.stranger)
        self.assertEqual(stranger.get(self._url("external-htr/engines/")).status_code, 404)

    def test_worker_refuses_a_bad_beam_width_before_opening_the_page(self):
        run = self._queued(self._config("http://127.0.0.1:9/%s/" % SECRET_PATH))
        finished = execute_htrflow_run(
            run.pk,
            fetch_models=lambda _config: _listed(_model(beams=True)),
            recognize=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("recognize")),
            open_image=_forbid_image,
        )
        finished.refresh_from_db()
        self.assertEqual(finished.status, "failed")
        self.assertEqual(finished.code, "beam_width_refused")
        self.assertEqual(finished.engine, "")

    def test_default_pause_retries_transient_http_and_not_permanent_4xx(self):
        config = self._config("http://127.0.0.1:9/%s/" % SECRET_PATH)
        calls = {"n": 0}

        def transient(_config, *, model_id, image):
            calls["n"] += 1
            if calls["n"] < 3:
                raise HtrflowClientError("http_error", "engine returned HTTP 503", status=503)
            return ALTO

        run = self._queued(config)
        with patch("ai.htrflow_run.time.sleep") as slept:
            finished = execute_htrflow_run(
                run.pk,
                recognize=transient,
                fetch_models=lambda _config: _listed(_model()),
                open_image=lambda _part: b"synthetic-page",
            )
        finished.refresh_from_db()
        self.assertEqual(calls["n"], 3)
        self.assertEqual(finished.pages[0]["attempts"], 3)
        self.assertEqual([call.args for call in slept.call_args_list], [(1,), (1,)])

        calls["n"] = 0

        def permanent(_config, *, model_id, image):
            calls["n"] += 1
            raise HtrflowClientError("http_error", "engine returned HTTP 404", status=404)

        failed = self._queued(config)
        with patch("ai.htrflow_run.time.sleep", side_effect=AssertionError("slept")):
            outcome = execute_htrflow_run(
                failed.pk,
                recognize=permanent,
                fetch_models=lambda _config: _listed(_model()),
                open_image=lambda _part: b"synthetic-page",
            )
        outcome.refresh_from_db()
        self.assertEqual(calls["n"], 1)
        self.assertEqual(outcome.status, "failed")
        self.assertEqual(outcome.code, "http_error")
        self.assertEqual(outcome.pages[0]["attempts"], 1)
        self.assertEqual(ExternalHTRJob.objects.filter(run=failed).count(), 0)

    def test_missing_dimensions_and_out_of_bounds_geometry_are_not_imported(self):
        config = self._config("http://127.0.0.1:9/%s/" % SECRET_PATH)
        missing = self._queued(config)
        execute_htrflow_run(
            missing.pk,
            recognize=lambda *_args, **_kwargs: (
                '<alto><Layout><Page HEIGHT="10"><TextLine ID="a">'
                '<String CONTENT="kept"/></TextLine></Page></Layout></alto>'
            ),
            fetch_models=lambda _config: _listed(_model()),
            open_image=lambda _part: b"synthetic-page",
        )
        missing.refresh_from_db()
        self.assertEqual(missing.status, "failed")
        self.assertEqual(missing.code, "page_dimensions")
        self.assertEqual(ExternalHTRJob.objects.filter(run=missing).count(), 0)
        self.assertEqual(import_htrflow_run(missing.pk).code, "audit_incomplete")
        self._assert_existing_text()

        bounded = """<alto><Layout><Page WIDTH="30" HEIGHT="20">
          <TextBlock ID="inside" HPOS="1" VPOS="1" WIDTH="8" HEIGHT="6">
            <TextLine ID="good" HPOS="1" VPOS="1" WIDTH="8" HEIGHT="6">
              <String CONTENT="kept"/>
            </TextLine>
          </TextBlock>
          <TextLine ID="bad">
            <Shape><Polygon POINTS="1,1 40,1 40,8 1,8"/></Shape>
            <String CONTENT="outside-text"/>
          </TextLine>
        </Page></Layout></alto>"""
        run = self._queued(config)
        execute_htrflow_run(
            run.pk,
            recognize=lambda *_args, **_kwargs: bounded,
            fetch_models=lambda _config: _listed(_model()),
            open_image=lambda _part: b"synthetic-page",
        )
        run.refresh_from_db()
        self.assertEqual(run.status, "completed")
        job = ExternalHTRJob.objects.get(run=run)
        self.assertEqual([item["line_id"] for item in job.layout["lines"]], ["good"])
        self.assertNotIn("outside-text", json.dumps(job.layout))
        stored = {row.line_id: row for row in job.lines.all()}
        self.assertEqual(stored["good"].text, KEPT)
        self.assertEqual(stored["bad"].text, "")
        self.assertEqual(stored["bad"].code, "geometry_out_of_bounds")
        layout = json.loads(json.dumps(job.layout))
        layout["lines"][0]["polygon"] = [[1, 1], [40, 1], [40, 8], [1, 8]]
        job.layout = layout
        job.save(update_fields=["layout", "updated_at"])
        refused = import_htrflow_run(run.pk)
        self.assertEqual(refused.code, "no_included_lines")
        self.assertIsNone(refused.transcription_id)
        self.assertFalse(Line.objects.filter(document_part=self.part, external_id="good").exists())
        self.assertFalse(Line.objects.filter(document_part=self.part, external_id="bad").exists())
        self._assert_existing_text()

    def test_non_finite_coordinates_skip_the_line_and_the_next_page_still_runs(self):
        config = self._config("http://127.0.0.1:9/%s/" % SECRET_PATH)
        later, _kept, _other = self._part()
        bad_text = (
            "box-inf-text",
            "box-neginf-text",
            "box-huge-text",
            "poly-inf-text",
            "poly-neginf-text",
            "poly-huge-text",
        )
        malformed = """<alto><Layout><Page WIDTH="30" HEIGHT="20">
          <TextLine ID="box-inf" HPOS="inf" VPOS="1" WIDTH="8" HEIGHT="6">
            <String CONTENT="box-inf-text"/>
          </TextLine>
          <TextLine ID="box-neginf" HPOS="1" VPOS="-inf" WIDTH="8" HEIGHT="6">
            <String CONTENT="box-neginf-text"/>
          </TextLine>
          <TextLine ID="box-huge" HPOS="1" VPOS="1" WIDTH="1e309" HEIGHT="6">
            <String CONTENT="box-huge-text"/>
          </TextLine>
          <TextLine ID="poly-inf">
            <Shape><Polygon POINTS="1,1 inf,1 2,2"/></Shape>
            <String CONTENT="poly-inf-text"/>
          </TextLine>
          <TextLine ID="poly-neginf">
            <Shape><Polygon POINTS="1,1 -inf,1 2,2"/></Shape>
            <String CONTENT="poly-neginf-text"/>
          </TextLine>
          <TextLine ID="poly-huge">
            <Shape><Polygon POINTS="1,1 1e309,1 2,2"/></Shape>
            <String CONTENT="poly-huge-text"/>
          </TextLine>
        </Page></Layout></alto>"""
        following = """<alto><Layout><Page WIDTH="30" HEIGHT="20">
          <TextLine ID="good" HPOS="1" VPOS="1" WIDTH="8" HEIGHT="6">
            <String CONTENT="kept"/>
          </TextLine>
        </Page></Layout></alto>"""
        responses = [malformed, following]
        calls = {"n": 0}

        def recognize(_config, *, model_id, image):
            calls["n"] += 1
            return responses[calls["n"] - 1]

        run = self._queued(config, part_ids=[self.part.pk, later.pk])
        execute_htrflow_run(
            run.pk,
            recognize=recognize,
            fetch_models=lambda _config: _listed(_model()),
            open_image=lambda _part: b"synthetic-page",
        )
        run.refresh_from_db()
        self.assertEqual(calls["n"], 2)
        self.assertEqual(run.status, "completed")
        self.assertNotEqual(run.code, "internal")
        self.assertEqual([page["part_id"] for page in run.pages], [self.part.pk, later.pk])
        self.assertEqual([page["status"] for page in run.pages], ["completed", "completed"])
        first = ExternalHTRJob.objects.get(run=run, part=self.part)
        self.assertEqual(first.layout["lines"], [])
        self.assertNotIn("box-inf-text", json.dumps(first.layout))
        stored = {row.line_id: row for row in first.lines.all()}
        for line_id, text in zip(
            ("box-inf", "box-neginf", "box-huge", "poly-inf", "poly-neginf", "poly-huge"),
            bad_text,
        ):
            self.assertEqual(stored[line_id].status, "skipped")
            self.assertEqual(stored[line_id].code, "no_geometry")
            self.assertEqual(stored[line_id].text, "")
            self.assertNotEqual(stored[line_id].text, text)
        second = ExternalHTRJob.objects.get(run=run, part=later)
        self.assertEqual([item["line_id"] for item in second.layout["lines"]], ["good"])
        self.assertEqual(second.lines.get(line_id="good").text, KEPT)
        imported = import_htrflow_run(run.pk)
        self.assertEqual(imported.code, "writeback_imported")
        self.assertEqual(imported.lines, 1)
        self.assertIsNotNone(imported.transcription_id)
        self.assertFalse(Line.objects.filter(external_id__in=[
            "box-inf", "box-neginf", "box-huge", "poly-inf", "poly-neginf", "poly-huge",
        ]).exists())
        self.assertTrue(Line.objects.filter(document_part=later, external_id="good").exists())
        self.assertFalse(LineTranscription.objects.filter(content__in=bad_text).exists())
        created = LineTranscription.objects.get(
            transcription_id=imported.transcription_id, line__external_id="good",
        )
        self.assertEqual(created.content, KEPT)
        self._assert_existing_text()

    def _config(self, endpoint, *, enabled=True, tier=ExternalHTREngineConfig.TIER_RESEARCH):
        return ExternalHTREngineConfig.objects.create(
            name="htrflow-%s" % uuid.uuid4().hex[:12],
            endpoint_url=endpoint,
            enabled=enabled,
            tier=tier,
            experimental=True,
            timeout_seconds=5,
            metadata={"task": "recognize_page", "engine": "htrflow"},
        )

    def _line_config(self, endpoint):
        return ExternalHTREngineConfig.objects.create(
            name="line-%s" % uuid.uuid4().hex[:12],
            endpoint_url=endpoint,
            enabled=True,
            tier=ExternalHTREngineConfig.TIER_RESEARCH,
            experimental=True,
            timeout_seconds=5,
            metadata={"token": "stored-token"},
        )

    def _queued(self, config, *, part_ids=None, created_by=None):
        return ExternalHTRRun.objects.create(
            document=self.document,
            config=config,
            created_by=self.owner if created_by is None else created_by,
            model_id=MODEL_ID,
            status=ExternalHTRRun.STATUS_QUEUED,
            part_ids=part_ids or [self.part.pk],
            pages=[],
        )

    def _run_alto(self, config):
        run = self._queued(config)
        execute_htrflow_run(
            run.pk,
            recognize=lambda *_args, **_kwargs: ALTO,
            fetch_models=lambda _config: _listed(_model()),
            open_image=lambda _part: b"synthetic-page",
        )
        run.refresh_from_db()
        return run

    def _part(self):
        upload, size = _png()
        part = DocumentPart.objects.create(
            document=self.document,
            image=upload,
            image_file_size=size,
            original_filename=SECRET_FILE,
        )
        kept = Line.objects.create(
            document_part=part,
            baseline=[[4, 12], [30, 12]],
            mask=[[4, 6], [30, 6], [30, 16], [4, 16]],
        )
        other = Line.objects.create(
            document_part=part,
            baseline=[[4, 18], [30, 18]],
            mask=[[4, 16], [30, 16], [30, 24], [4, 24]],
        )
        return part, kept, other

    def _url(self, path):
        return "/api/documents/%s/%s" % (self.document.pk, path)

    def _assert_existing_text(self):
        self.manual_row.refresh_from_db()
        self.kraken_row.refresh_from_db()
        self.assertEqual(self.manual_row.content, MANUAL_TEXT)
        self.assertEqual(self.manual_row.version_source, "manual")
        self.assertEqual(self.kraken_row.content, KRAKEN_TEXT)
        self.assertEqual(self.kraken_row.version_source, "kraken")
        self.assertFalse(
            LineTranscription.objects.filter(transcription=self.manual, content=KEPT).exists()
        )


def _forbid_recognize(*_args, **_kwargs):
    raise AssertionError("recognize")
