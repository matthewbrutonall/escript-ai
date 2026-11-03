"""WordLevelTrOCR line text uses the verified word join. No engine process is called."""
import io
import os
import shutil
import uuid

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from PIL import Image

from ai.external_htr_user import import_run
from ai.gate import LayerNotEligible, assert_training_eligible
from ai.htrflow_contract import PageModel, PageModelList
from ai.htrflow_run import execute_htrflow_run
from ai.htrflow_run_import import import_htrflow_run
from ai.models import (
    AIDocumentPolicy,
    AILayerGate,
    ExternalHTREngineConfig,
    ExternalHTRLineResult,
    ExternalHTRRun,
)
from core.models import Document, DocumentPart, Line, LineTranscription, Project, Transcription
from users.models import User

MEDIA = "/tmp/escript-htrflow-wordlevel-run-media"
MODEL_ID = "wordlevel-trocr-iam"
TROCR_ID = "handwriting-english-large"
MANUAL_TEXT = "manual-kept-text"
LINE_TEXT = "world Hello"

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
        <processingStepSettings>model: microsoft/trocr-base-handwritten model_class: WordLevelTrOCR model_version: eaacaf452b06415df8f10bb6fad3a4c11e609406</processingStepSettings>
      </ocrProcessingStep>
    </OCRProcessing>
  </Description>
  <Layout>
    <Page WIDTH="120" HEIGHT="40">
      <TextBlock ID="region-a" HPOS="10" VPOS="8" WIDTH="90" HEIGHT="16">
        <TextLine ID="line-a" HPOS="10" VPOS="8" WIDTH="80" HEIGHT="16">
          <String HPOS="40" VPOS="8" WIDTH="30" HEIGHT="16" CONTENT="world"/>
          <String HPOS="10" VPOS="8" WIDTH="24" HEIGHT="16" CONTENT="Hello"/>
        </TextLine>
      </TextBlock>
    </Page>
  </Layout>
</alto>
"""


def _png():
    buffer = io.BytesIO()
    Image.new("RGB", (40, 20), (240, 240, 240)).save(buffer, "PNG")
    payload = buffer.getvalue()
    return SimpleUploadedFile("wordlevel-page.png", payload, "image/png"), len(payload)


@override_settings(MEDIA_ROOT=MEDIA)
class HtrflowWordLevelRunOrmTests(TestCase):
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
            name="wordlevel",
            slug=uuid.uuid4().hex[:12],
            owner=self.owner,
        )
        self.document = Document.objects.create(
            name="wordlevel-document",
            owner=self.owner,
            project=self.project,
        )
        AIDocumentPolicy.objects.create(document=self.document, never_send_offsite=True)
        upload, size = _png()
        self.part = DocumentPart.objects.create(
            document=self.document,
            image=upload,
            image_file_size=size,
            original_filename="wordlevel-page.png",
        )
        self.kept = Line.objects.create(
            document_part=self.part,
            baseline=[[4, 12], [30, 12]],
            mask=[[4, 6], [30, 6], [30, 16], [4, 16]],
        )
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

    def test_import_joins_words_once_and_leaves_the_manual_layer(self):
        config = ExternalHTREngineConfig.objects.create(
            name="wordlevel-%s" % uuid.uuid4().hex[:12],
            endpoint_url="http://127.0.0.1:9/wordlevel/",
            enabled=True,
            tier=ExternalHTREngineConfig.TIER_RESEARCH,
            experimental=True,
            timeout_seconds=5,
            metadata={"task": "recognize_page", "engine": "htrflow"},
        )
        run = ExternalHTRRun.objects.create(
            document=self.document,
            config=config,
            created_by=self.owner,
            model_id=MODEL_ID,
            status=ExternalHTRRun.STATUS_QUEUED,
            part_ids=[self.part.pk],
            pages=[],
        )
        seen = {}

        def recognize(_config, *, model_id, image):
            seen["model_id"] = model_id
            seen["image"] = image
            return ALTO

        def fetch(_config):
            return PageModelList("htrflow", (
                PageModel(MODEL_ID, "WordLevelTrOCR (English handwriting, IAM)", "mit", True, 1, True),
                PageModel(TROCR_ID, "English handwriting", "", False, 1, True),
            ))

        finished = execute_htrflow_run(
            run.pk,
            recognize=recognize,
            fetch_models=fetch,
            open_image=lambda _part: b"synthetic-page",
        )
        self.assertEqual(seen["model_id"], MODEL_ID)
        self.assertNotEqual(seen["model_id"], TROCR_ID)
        self.assertEqual(finished.status, "completed")
        stored = ExternalHTRLineResult.objects.get(job__run=finished)
        self.assertEqual(stored.text, LINE_TEXT)
        self.assertNotEqual(stored.text, "worldHello")
        self.assertNotEqual(stored.text, "Hello world")
        before_lines = Line.objects.filter(document_part=self.part).count()
        before_layers = Transcription.objects.filter(document=self.document).count()
        result = import_htrflow_run(finished.pk)
        self.assertEqual(result.code, "writeback_imported")
        self.assertEqual(Line.objects.filter(document_part=self.part).count(), before_lines + 1)
        layer = Transcription.objects.get(pk=result.transcription_id)
        self.assertEqual(layer.name, "external-htr:htrflow:%s:run-%s" % (MODEL_ID, finished.pk))
        written = LineTranscription.objects.get(transcription=layer)
        self.assertEqual(written.content, LINE_TEXT)
        self.assertEqual(written.version_source, "external-htr:htrflow:%s" % MODEL_ID)
        gate = AILayerGate.objects.get(transcription=layer)
        self.assertEqual(gate.state, AILayerGate.STATE_RAW)
        with self.assertRaises(LayerNotEligible):
            assert_training_eligible(layer)
        repeat = import_run(self.document, self.owner, finished.pk)
        self.assertEqual(repeat.data["code"], "already_imported")
        self.assertEqual(Transcription.objects.filter(document=self.document).count(), before_layers + 1)
        self.assertEqual(Line.objects.filter(document_part=self.part).count(), before_lines + 1)
        self.manual_row.refresh_from_db()
        self.assertEqual(self.manual_row.content, MANUAL_TEXT)
        self.assertEqual(self.manual_row.version_source, "manual")
        self.assertFalse(
            LineTranscription.objects.filter(transcription=self.manual, content=LINE_TEXT).exists()
        )
