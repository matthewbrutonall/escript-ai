"""Calamari line workflow against disposable rows.

The predict process is replaced. The HTTP service, import, and review gate
are the real ones. The stand-in text is not a recognition result.
"""
import json
import os
import shutil
import socket
import subprocess
import threading
import time
import uuid
from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from PIL import Image

from ai.external_engines.calamari.predictor import inspect_model_dir
from ai.external_engines.calamari.backend import PredictCalamariBackend
from ai.external_engines.calamari.server import serve
from ai.external_htr_run import execute_external_htr_run
from ai.gate import LayerNotEligible, assert_training_eligible
from ai.models import AIDocumentPolicy, AILayerGate, ExternalHTREngineConfig, ExternalHTRRun
from core.models import Document, DocumentPart, Line, LineTranscription, Project, Transcription
from users.models import User

MEDIA = "/tmp/escript-calamari-run-media"
SECRET = "calamari-stand-in-text"
MANUAL_TEXT = "manual-kept-text"


def _png():
    buffer = __import__("io").BytesIO()
    Image.new("RGB", (60, 40), (240, 240, 240)).save(buffer, "PNG")
    payload = buffer.getvalue()
    return SimpleUploadedFile("page.png", payload, "image/png"), len(payload)


def _port():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _wait(port):
    for _ in range(50):
        try:
            sock = socket.create_connection(("127.0.0.1", port), 0.2)
        except OSError:
            time.sleep(0.05)
            continue
        sock.close()
        return
    raise AssertionError("calamari wrapper did not listen")


@override_settings(MEDIA_ROOT=MEDIA)
class CalamariRunOrmTests(TestCase):
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
            name="calamari",
            slug=uuid.uuid4().hex[:12],
            owner=self.owner,
        )
        self.document = Document.objects.create(
            name="calamari-disposable",
            owner=self.owner,
            project=self.project,
        )
        AIDocumentPolicy.objects.create(document=self.document, never_send_offsite=True)
        upload, size = _png()
        self.part = DocumentPart.objects.create(
            document=self.document,
            image=upload,
            image_file_size=size,
            original_filename="page.png",
        )
        self.kept = Line.objects.create(
            document_part=self.part,
            baseline=[[10, 30], [50, 30]],
            mask=[[10, 20], [50, 20], [50, 40], [10, 40]],
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
        self.client = Client()
        self.client.force_login(self.owner)

    def test_one_voter_imports_behind_the_raw_gate_and_a_repeat_does_not(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            models = root / "models"
            work = root / "work"
            models.mkdir()
            work.mkdir()
            (models / "licence.txt").write_text("MIT\n", encoding="utf-8")
            (models / "0.ckpt.json").write_text(
                '{"version": 6, "scenario": {"data": {"line_height": 48, "input_channels": 1}}}',
                encoding="utf-8",
            )
            folder = models / "0.ckpt"
            folder.mkdir()
            (folder / "saved_model.pb").write_bytes(b"not-a-network")
            self.assertEqual(inspect_model_dir(models).checkpoints[0].licence, "MIT")
            backend = PredictCalamariBackend(model_dir=models, work_root=work, timeout_seconds=30)
            port = _port()

            def fake_run(argv, **kwargs):
                self.assertFalse(kwargs["shell"])
                self.assertEqual(kwargs["env"]["CUDA_VISIBLE_DEVICES"], "")
                for image in argv:
                    if str(image).endswith(".png"):
                        Path(str(image)[:-4] + ".pred.txt").write_text(SECRET + "\n", encoding="utf-8")
                return subprocess.CompletedProcess(argv, 0, b"", b"")

            from unittest.mock import patch
            with patch("ai.external_engines.calamari.predictor.subprocess.run", side_effect=fake_run):
                thread = threading.Thread(
                    target=serve,
                    args=("127.0.0.1", port, backend),
                    daemon=True,
                )
                thread.start()
                _wait(port)
                with patch("ai.external_htr_user._default_queue", self._inline):
                    response = self.client.post(
                        "/api/documents/%s/external-htr/runs/" % self.document.pk,
                        data=json.dumps({
                            "config_id": self._config("http://127.0.0.1:%s" % port).pk,
                            "model_id": "0.ckpt",
                            "part_ids": [self.part.pk],
                        }),
                        content_type="application/json",
                    )
            self.assertEqual(response.status_code, 202, response.content)
            self.assertNotIn(SECRET, response.content.decode("utf-8"))
        run = ExternalHTRRun.objects.get()
        self.assertEqual(run.status, "completed")
        self.assertEqual(run.engine, "calamari")
        self.assertFalse(run.imported)
        imported = self.client.post(
            "/api/documents/%s/external-htr/runs/%s/import/" % (self.document.pk, run.pk),
            data="{}",
            content_type="application/json",
        )
        self.assertEqual(imported.status_code, 200, imported.content)
        layer = Transcription.objects.get(pk=imported.json()["transcription_id"])
        self.assertNotEqual(layer.pk, self.manual.pk)
        written = LineTranscription.objects.get(transcription=layer)
        self.assertEqual(written.content, SECRET)
        self.assertEqual(written.version_source, "external-htr:calamari:0.ckpt")
        gate = AILayerGate.objects.get(transcription=layer)
        self.assertEqual(gate.state, AILayerGate.STATE_RAW)
        with self.assertRaises(LayerNotEligible):
            assert_training_eligible(layer)
        again = self.client.post(
            "/api/documents/%s/external-htr/runs/%s/import/" % (self.document.pk, run.pk),
            data="{}",
            content_type="application/json",
        )
        self.assertEqual(again.json()["code"], "already_imported")
        self.assertEqual(Transcription.objects.filter(document=self.document).count(), 2)
        self.manual_row.refresh_from_db()
        self.assertEqual(self.manual_row.content, MANUAL_TEXT)

    def test_a_predict_failure_stays_on_calamari_and_imports_nothing(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            models = root / "models"
            work = root / "work"
            models.mkdir()
            work.mkdir()
            (models / "licence.txt").write_text("MIT\n", encoding="utf-8")
            (models / "0.ckpt.json").write_text(
                '{"version": 6, "scenario": {"data": {"line_height": 48}}}',
                encoding="utf-8",
            )
            folder = models / "0.ckpt"
            folder.mkdir()
            (folder / "saved_model.pb").write_bytes(b"not-a-network")
            backend = PredictCalamariBackend(model_dir=models, work_root=work, timeout_seconds=30)
            port = _port()

            def fake_run(argv, **kwargs):
                return subprocess.CompletedProcess(argv, 1, b"", b"")

            from unittest.mock import patch
            with patch("ai.external_engines.calamari.predictor.subprocess.run", side_effect=fake_run):
                thread = threading.Thread(
                    target=serve,
                    args=("127.0.0.1", port, backend),
                    daemon=True,
                )
                thread.start()
                _wait(port)
                config = self._config("http://127.0.0.1:%s" % port)
                run = ExternalHTRRun.objects.create(
                    document=self.document,
                    config=config,
                    created_by=self.owner,
                    model_id="0.ckpt",
                    status=ExternalHTRRun.STATUS_QUEUED,
                    part_ids=[self.part.pk],
                    pages=[],
                )
                execute_external_htr_run(run.pk, pause=lambda: None)
        run.refresh_from_db()
        self.assertEqual(run.engine, "calamari")
        self.assertIn(run.status, {"failed", "partial"})
        self.assertFalse(run.imported)
        self.assertEqual(Transcription.objects.filter(document=self.document).count(), 1)

    def _config(self, endpoint):
        return ExternalHTREngineConfig.objects.create(
            name="calamari-%s" % uuid.uuid4().hex[:8],
            endpoint_url=endpoint,
            enabled=True,
            tier=ExternalHTREngineConfig.TIER_RESEARCH,
            experimental=True,
            timeout_seconds=30,
            metadata={},
        )

    def _inline(self, run_id):
        execute_external_htr_run(run_id, task_id="inline", pause=lambda: None)
