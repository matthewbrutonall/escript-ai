"""SATRN uses the existing HTRflow page contract. No engine process is called."""
import io
import json
import os
import shutil
import uuid
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from PIL import Image

from ai.external_htr_user import import_run, list_models, start_run
from ai.gate import LayerNotEligible, assert_training_eligible
from ai.htrflow_client import HtrflowClientError
from ai.htrflow_contract import PageModel, PageModelList
from ai.htrflow_run import execute_htrflow_run
from ai.htrflow_run_import import import_htrflow_run
from ai.models import (
    AIDocumentPolicy,
    AILayerGate,
    ExternalHTREngineConfig,
    ExternalHTRJob,
    ExternalHTRRun,
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

MEDIA = "/tmp/escript-htrflow-satrn-run-media"
SATRN_ID = "satrn-riksarkivet"
TROCR_ID = "handwriting-english-large"
REVISION = "a40c7093232eaa47a83ce6469fc4abd033486bdc"
KEPT = "kept"

ALTO = """<?xml version="1.0" encoding="UTF-8"?>
<alto xmlns="http://www.loc.gov/standards/alto/ns-v4#">
  <Description>
    <OCRProcessing>
      <ocrProcessingStep>
        <processingStepSettings>model: Riksarkivet/yolov9-regions-1 model_class: YOLO model_version: 9e1a97431764db9b20e5c2afdfed2f6d36e3b456</processingStepSettings>
        <processingSoftware><softwareVersion>0.2.6</softwareVersion></processingSoftware>
      </ocrProcessingStep>
      <ocrProcessingStep>
        <processingStepSettings>model: Riksarkivet/yolov9-lines-within-regions-1 model_class: YOLO model_version: 1dda6137ce6cc4f9768c79d5625d007c24a0f8df</processingStepSettings>
      </ocrProcessingStep>
      <ocrProcessingStep>
        <processingStepSettings>model: Riksarkivet/satrn_htr model_class: Satrn model_version: %s</processingStepSettings>
      </ocrProcessingStep>
    </OCRProcessing>
  </Description>
  <Layout>
    <Page WIDTH="960" HEIGHT="320">
      <TextBlock ID="region-a" HPOS="261" VPOS="67" WIDTH="615" HEIGHT="134">
        <Shape><Polygon POINTS="261,67 876,67 876,201 261,201"/></Shape>
        <TextLine ID="line-a" HPOS="261" VPOS="67" WIDTH="615" HEIGHT="134">
          <Shape><Polygon POINTS="261,67 261,201 876,201 876,67"/></Shape>
          <String CONTENT="%s" WC="0.5"/>
        </TextLine>
      </TextBlock>
    </Page>
  </Layout>
</alto>
""" % (REVISION, KEPT)


def _png():
    buffer = io.BytesIO()
    Image.new("RGB", (40, 20), (240, 240, 240)).save(buffer, "PNG")
    payload = buffer.getvalue()
    return SimpleUploadedFile("satrn-page.png", payload, "image/png"), len(payload)


def _model(model_id, beams=1):
    return PageModel(model_id, model_id, "mit", model_id == SATRN_ID, beams, True)


def _listed(*models):
    return PageModelList("htrflow", models)


def _forbid_image(_part):
    raise AssertionError("page image was opened")


def _forbid_recognize(*_args, **_kwargs):
    raise AssertionError("recognition was called")


@override_settings(MEDIA_ROOT=MEDIA)
class HtrflowSatrnRunOrmTests(TestCase):
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
        self.project = Project.objects.create(
            name="satrn",
            slug=uuid.uuid4().hex[:12],
            owner=self.owner,
        )
        self.document = Document.objects.create(
            name="satrn-document",
            owner=self.owner,
            project=self.project,
        )
        AIDocumentPolicy.objects.create(document=self.document, never_send_offsite=True)
        upload, size = _png()
        self.part = DocumentPart.objects.create(
            document=self.document,
            image=upload,
            image_file_size=size,
            original_filename="satrn-page.png",
        )
        self.manual = Transcription.objects.get(
            document=self.document, name=Transcription.DEFAULT_NAME,
        )
        self.satrn = self._config("http://127.0.0.1:9/satrn/")
        self.trocr = self._config("http://127.0.0.1:9/trocr/")

    def test_listing_and_selection_do_not_substitute_trocr(self):
        catalogues = {
            self.satrn.endpoint_url: _listed(_model(SATRN_ID), _model(TROCR_ID)),
            self.trocr.endpoint_url: _listed(_model(TROCR_ID)),
        }

        def fetch(config):
            return catalogues[config.endpoint_url]

        with patch("ai.htrflow_client.fetch_page_models", fetch):
            satrn_list = list_models(self.document, self.owner, self.satrn.pk)
            trocr_list = list_models(self.document, self.owner, self.trocr.pk)
            queued = start_run(
                self.document, self.owner,
                {
                    "config_id": self.satrn.pk,
                    "model_id": SATRN_ID,
                    "part_ids": [self.part.pk],
                },
                queue=lambda _run_id: None,
            )
        self.assertEqual(
            [item["model_id"] for item in satrn_list.data["models"]],
            [SATRN_ID, TROCR_ID],
        )
        self.assertEqual(satrn_list.data["models"][0]["licence"], "mit")
        self.assertEqual(satrn_list.data["models"][0]["num_beams"], 1)
        self.assertEqual(satrn_list.data["models"][0]["task"], "recognize_page")
        self.assertTrue(satrn_list.data["models"][0]["experimental"])
        self.assertEqual(
            [item["model_id"] for item in trocr_list.data["models"]],
            [TROCR_ID],
        )
        self.assertEqual(queued.status_code, 202)
        run = ExternalHTRRun.objects.get(pk=queued.data["id"])
        self.assertEqual(run.model_id, SATRN_ID)
        seen = []

        def recognize(_config, *, model_id, image):
            seen.append(model_id)
            self.assertEqual(image, b"synthetic-page")
            return ALTO

        finished = execute_htrflow_run(
            run.pk,
            recognize=recognize,
            fetch_models=lambda _config: _listed(_model(SATRN_ID), _model(TROCR_ID)),
            open_image=lambda _part: b"synthetic-page",
        )
        finished.refresh_from_db()
        self.assertEqual(seen, [SATRN_ID])
        self.assertEqual(finished.status, "completed")
        self.assertEqual(finished.engine, "htrflow")
        job = ExternalHTRJob.objects.get(run=finished)
        self.assertEqual(job.model_id, SATRN_ID)
        self.assertEqual(job.model_version, REVISION)
        self.assertEqual(job.layout["page"], {"width": 960, "height": 320})
        self.assertEqual(job.layout["models"][2]["role"], "recognition")
        self.assertEqual(job.layout["models"][2]["model_class"], "Satrn")
        self.assertEqual(
            job.layout["lines"][0]["polygon"],
            [[261, 67], [261, 201], [876, 201], [876, 67]],
        )
        self.assertNotIn(KEPT, json.dumps(job.layout))
        self.assertEqual(job.lines.get(line_id="line-a").text, KEPT)
        imported = import_htrflow_run(finished.pk)
        self.assertEqual(imported.code, "writeback_imported")
        layer = Transcription.objects.get(pk=imported.transcription_id)
        self.assertEqual(
            layer.name, "external-htr:htrflow:%s:run-%s" % (SATRN_ID, finished.pk),
        )
        row = layer.linetranscription_set.select_related("line").get(line__external_id="line-a")
        self.assertEqual(row.content, KEPT)
        self.assertEqual(row.version_source, "external-htr:htrflow:%s" % SATRN_ID)
        self.assertNotEqual(row.version_source, "external-htr:htrflow:%s" % TROCR_ID)
        gate = AILayerGate.objects.get(transcription=layer)
        self.assertEqual(gate.state, AILayerGate.STATE_RAW)
        with self.assertRaises(LayerNotEligible):
            assert_training_eligible(layer)
        repeat = import_run(self.document, self.owner, finished.pk)
        self.assertEqual(repeat.data["code"], "already_imported")
        self.assertEqual(self.manual.linetranscription_set.count(), 0)

    def test_missing_model_bad_beams_and_unavailable_runtime_do_not_fall_back(self):
        with patch(
            "ai.htrflow_client.fetch_page_models",
            return_value=_listed(_model(TROCR_ID)),
        ), patch("ai.htrflow_run._open_part_bytes", side_effect=AssertionError("image")):
            missing = start_run(
                self.document, self.owner,
                {
                    "config_id": self.satrn.pk,
                    "model_id": SATRN_ID,
                    "part_ids": [self.part.pk],
                },
                queue=lambda _run_id: (_ for _ in ()).throw(AssertionError("queued")),
            )
        self.assertEqual(missing.status_code, 400)
        self.assertEqual(missing.data["code"], "model_not_found")
        with patch(
            "ai.htrflow_client.fetch_page_models",
            return_value=_listed(_model(SATRN_ID, beams=4)),
        ):
            wide = start_run(
                self.document, self.owner,
                {
                    "config_id": self.satrn.pk,
                    "model_id": SATRN_ID,
                    "part_ids": [self.part.pk],
                },
                queue=lambda _run_id: (_ for _ in ()).throw(AssertionError("queued")),
            )
        self.assertEqual(wide.status_code, 400)
        self.assertEqual(wide.data["code"], "beam_width_refused")
        self.assertEqual(ExternalHTRRun.objects.count(), 0)

        queued = ExternalHTRRun.objects.create(
            document=self.document,
            config=self.satrn,
            created_by=self.owner,
            model_id=SATRN_ID,
            status=ExternalHTRRun.STATUS_QUEUED,
            part_ids=[self.part.pk],
            pages=[],
        )
        hidden = execute_htrflow_run(
            queued.pk,
            recognize=_forbid_recognize,
            fetch_models=lambda _config: _listed(_model(TROCR_ID)),
            open_image=_forbid_image,
        )
        hidden.refresh_from_db()
        self.assertEqual(hidden.status, "failed")
        self.assertEqual(hidden.code, "model_not_found")
        self.assertEqual(ExternalHTRJob.objects.filter(run=hidden).count(), 0)

        down = ExternalHTRRun.objects.create(
            document=self.document,
            config=self.satrn,
            created_by=self.owner,
            model_id=SATRN_ID,
            status=ExternalHTRRun.STATUS_QUEUED,
            part_ids=[self.part.pk],
            pages=[],
        )

        def unavailable(_config):
            raise HtrflowClientError("unavailable", "engine request failed")

        failed = execute_htrflow_run(
            down.pk,
            recognize=_forbid_recognize,
            fetch_models=unavailable,
            open_image=_forbid_image,
        )
        failed.refresh_from_db()
        self.assertEqual(failed.status, "failed")
        self.assertEqual(failed.code, "unavailable")
        self.assertEqual(import_htrflow_run(failed.pk).code, "audit_incomplete")
        self.assertFalse(
            Transcription.objects.filter(document=self.document).exclude(pk=self.manual.pk).exists()
        )

    def test_recognition_failure_stays_a_failed_page(self):
        run = ExternalHTRRun.objects.create(
            document=self.document,
            config=self.satrn,
            created_by=self.owner,
            model_id=SATRN_ID,
            status=ExternalHTRRun.STATUS_QUEUED,
            part_ids=[self.part.pk],
            pages=[],
        )
        seen = []

        def recognize(_config, *, model_id, image):
            seen.append((model_id, image))
            raise HtrflowClientError("http_error", "engine returned HTTP 422", status=422)

        finished = execute_htrflow_run(
            run.pk,
            recognize=recognize,
            fetch_models=lambda _config: _listed(_model(SATRN_ID)),
            open_image=lambda _part: b"synthetic-page",
        )
        finished.refresh_from_db()
        self.assertEqual(seen, [(SATRN_ID, b"synthetic-page")])
        self.assertEqual(finished.status, "failed")
        self.assertEqual(finished.code, "http_error")
        self.assertEqual(finished.pages[0]["status"], "failed")
        self.assertEqual(finished.pages[0]["lines"], [])
        self.assertEqual(ExternalHTRJob.objects.filter(run=finished).count(), 0)
        self.assertEqual(import_htrflow_run(finished.pk).code, "audit_incomplete")
        self.assertEqual(Line.objects.filter(document_part=self.part).count(), 0)
        self.assertFalse(
            LineTranscription.objects.exclude(transcription=self.manual).exists()
        )

    def _config(self, endpoint):
        return ExternalHTREngineConfig.objects.create(
            name="htrflow-%s" % uuid.uuid4().hex[:12],
            endpoint_url=endpoint,
            enabled=True,
            tier=ExternalHTREngineConfig.TIER_RESEARCH,
            experimental=True,
            timeout_seconds=5,
            metadata={"task": "recognize_page", "engine": "htrflow"},
        )
