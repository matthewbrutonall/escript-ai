"""External HTR action against disposable rows and the loopback reference engine.

``LINE <line_id>`` is that test double's fixed text. It is not recognition
and it is not an accuracy result. The server binds to 127.0.0.1 and is
shut down when the test ends.
"""
import base64
import io
import json
import os
import shutil
import socket
import threading
import uuid
from contextlib import contextmanager
from http.server import HTTPServer
from types import SimpleNamespace
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from PIL import Image

from ai.external_htr_live import ExternalHtrLiveResult, run_external_htr_live
from ai.external_htr_run import execute_external_htr_run
from ai.external_htr_run_import import import_external_htr_run
from ai.external_htr_user import start_run
from ai.external_htr_writeback_plan import provenance_stamp
from ai.gate import LayerNotEligible, assert_training_eligible
from ai.models import (
    AIDocumentPolicy,
    AIJob,
    AILayerGate,
    ExternalHTREngineConfig,
    ExternalHTRJob,
    ExternalHTRLineResult,
    ExternalHTRRun,
)
from ai.reference_engine.engine import ENGINE_NAME, MODEL_ID, MODEL_VERSION
from ai.reference_engine.server import ReferenceHandler
from ai.tasks import ai_transcribe
from core.models import (
    Document,
    DocumentPart,
    Line,
    LineTranscription,
    Project,
    Transcription,
)
from users.models import User

MEDIA = "/tmp/escript-stage3-run-media"
SECRET_TITLE = "stage3-secret-title"
SECRET_TOKEN = "stage3-secret-token"
SECRET_FILE = "stage3-secret-page.png"
MANUAL_TEXT = "manual-kept-text"
KRAKEN_TEXT = "kraken-kept-text"
ACK_PHRASE = "I reviewed the sample"


def _png():
    buffer = __import__("io").BytesIO()
    Image.new("RGB", (60, 60), (20, 20, 20)).save(buffer, "PNG")
    payload = buffer.getvalue()
    return SimpleUploadedFile(SECRET_FILE, payload, "image/png"), len(payload)


@contextmanager
def _reference_server():
    server = HTTPServer(("127.0.0.1", 0), ReferenceHandler)
    host, port = server.server_address[:2]
    if host != "127.0.0.1":
        server.server_close()
        raise RuntimeError("reference engine was not bound to loopback")
    thread = threading.Thread(target=server.serve_forever, name="stage3-reference", daemon=True)
    thread.start()
    try:
        last_error = None
        for _ in range(50):
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    break
            except OSError as exc:
                last_error = exc
        else:
            raise RuntimeError("reference engine did not accept a connection") from last_error
        yield "http://127.0.0.1:%d/" % port
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _forbid_image(part):
    raise AssertionError("page image was opened")


@override_settings(MEDIA_ROOT=MEDIA)
class ExternalHtrRunOrmTests(TestCase):
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
            name="stage3",
            slug=uuid.uuid4().hex[:12],
            owner=self.owner,
        )
        self.document = Document.objects.create(
            name=SECRET_TITLE,
            owner=self.owner,
            project=self.project,
        )
        AIDocumentPolicy.objects.create(document=self.document, never_send_offsite=True)
        self.part, self.kept, self.skipped = self._part_with_lines()
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
            line=self.skipped,
            content=KRAKEN_TEXT,
            version_source="kraken",
            version_author="keeper",
        )
        self.client = Client()
        self.client.force_login(self.owner)
        # Celery wraps the task, so the proxy module is celery.local.
        self.assertEqual(ai_transcribe.run.__module__, "ai.tasks")
        self.assertIn("ai_transcribe", ai_transcribe.name)

    def test_api_refuses_disabled_hosted_and_strangers_without_calling_an_engine(self):
        with patch("urllib.request.urlopen", side_effect=AssertionError("urlopen")), patch(
            "ai.htr_engine_client.urllib.request.urlopen",
            side_effect=AssertionError("client-urlopen"),
        ):
            disabled = self._config("http://127.0.0.1:9/", enabled=False)
            hosted = self._config("http://127.0.0.1:9/", tier=ExternalHTREngineConfig.TIER_API)
            denied = self.client.get(self._url("external-htr/engines/"))
            self.assertEqual(self.client.get(
                "/api/documents/%s/external-htr/engines/" % self.document.pk,
            ).status_code, 200, denied)
            stranger = Client()
            stranger.force_login(self.stranger)
            self.assertEqual(
                stranger.get(self._url("external-htr/engines/")).status_code, 404,
            )
            body = self.client.get(self._url("external-htr/engines/")).json()
            self.assertNotIn("endpoint_url", json.dumps(body))
            self.assertNotIn(SECRET_TOKEN, json.dumps(body))
            self.assertTrue(any(row["id"] == disabled.pk and row["available"] is False for row in body["engines"]))
            self.assertTrue(any(row["id"] == hosted.pk and row["available"] is False for row in body["engines"]))
            disabled_response = self._post("external-htr/runs/", {
                "config_id": disabled.pk, "model_id": MODEL_ID, "part_ids": [self.part.pk],
            })
            hosted_response = self._post("external-htr/runs/", {
                "config_id": hosted.pk, "model_id": MODEL_ID, "part_ids": [self.part.pk],
            })
        self.assertEqual(disabled_response.status_code, 400)
        self.assertEqual(disabled_response.json()["code"], "disabled")
        self.assertEqual(hosted_response.status_code, 400)
        self.assertEqual(hosted_response.json()["code"], "tier_refused")
        self.assertEqual(hosted_response.json()["message"], "hosted engines are not used for this action")
        self.assertEqual(ExternalHTRRun.objects.count(), 0)

    def test_queue_failure_is_terminal_and_does_not_include_the_exception(self):
        config = self._config("http://127.0.0.1:9/")

        def boom(_run_id):
            raise RuntimeError("broker-" + SECRET_TOKEN)

        with patch("ai.htr_engine_client.fetch_capabilities", side_effect=OSError("down")), patch(
            "ai.htr_engine_client.fetch_models", side_effect=OSError("down"),
        ):
            response = start_run(
                self.document, self.owner,
                {"config_id": config.pk, "model_id": MODEL_ID, "part_ids": [self.part.pk]},
                queue=boom,
            )
        self.assertEqual(response.status_code, 503)
        self.assertNotIn(SECRET_TOKEN, json.dumps(response.data))
        run = ExternalHTRRun.objects.get()
        self.assertEqual(run.status, "failed")
        self.assertEqual(run.code, "unavailable")
        self.assertEqual(run.message, "recognition job was not queued")

    def test_pylaia_menu_needs_no_row_and_a_saved_row_keeps_the_line_height_rule(self):
        htrflow = ExternalHTREngineConfig.objects.create(
            name="HTRflow",
            endpoint_url="http://127.0.0.1:9/",
            enabled=True,
            tier=ExternalHTREngineConfig.TIER_RESEARCH,
            experimental=True,
            timeout_seconds=10,
            metadata={"task": "recognize_page", "engine": "htrflow"},
        )
        before = ExternalHTREngineConfig.objects.count()
        with patch("urllib.request.urlopen", side_effect=AssertionError("urlopen")):
            body = self.client.get(self._url("external-htr/engines/")).json()
        self.assertEqual(ExternalHTREngineConfig.objects.count(), before)
        names = [row["name"] for row in body["engines"]]
        self.assertIn("HTRflow", names)
        self.assertEqual(names.count("PyLaia"), 1)
        self.assertEqual(names.count("Calamari"), 1)
        placeholder = next(row for row in body["engines"] if row["name"] == "PyLaia")
        self.assertIsNone(placeholder["id"])
        self.assertTrue(placeholder["placeholder"])
        self.assertFalse(placeholder["available"])
        self.assertFalse(placeholder["enabled"])
        calamari = next(row for row in body["engines"] if row["name"] == "Calamari")
        self.assertIsNone(calamari["id"])
        self.assertEqual(calamari["engine"], "calamari")
        self.assertTrue(calamari["placeholder"])
        self.assertFalse(calamari["available"])
        self.assertFalse(calamari["enabled"])
        self.assertNotIn("endpoint_url", json.dumps(body))
        htrflow_row = next(row for row in body["engines"] if row["id"] == htrflow.pk)
        self.assertTrue(htrflow_row["available"])
        self.assertNotIn("placeholder", htrflow_row)

        config = ExternalHTREngineConfig.objects.create(
            name="PyLaia",
            endpoint_url="http://127.0.0.1:9/",
            enabled=True,
            tier=ExternalHTREngineConfig.TIER_RESEARCH,
            experimental=True,
            timeout_seconds=10,
            metadata={},
        )
        listed = self.client.get(self._url("external-htr/engines/")).json()["engines"]
        self.assertEqual([row["name"] for row in listed].count("PyLaia"), 1)
        self.assertTrue(any(
            row["id"] == config.pk and row["name"] == "PyLaia" and row["available"] is True
            and not row.get("placeholder")
            for row in listed
        ))
        remaining = [row for row in listed if row.get("placeholder")]
        self.assertEqual([row["engine"] for row in remaining], ["calamari"])
        self.assertEqual(remaining[0]["name"], "Calamari")
        self.assertIsNone(remaining[0]["id"])
        self.assertNotIn("endpoint_url", json.dumps(listed))
        self.assertTrue(any(row["id"] == htrflow.pk and row["name"] == "HTRflow" for row in listed))

        bare = SimpleNamespace(
            engine="pylaia",
            models=(SimpleNamespace(
                model_id="huginmunin",
                display_name="Huginmunin",
                licence="unspecified",
                experimental=True,
                input=SimpleNamespace(line_height=None),
            ),),
        )
        ready = SimpleNamespace(
            engine="pylaia",
            models=(SimpleNamespace(
                model_id="huginmunin",
                display_name="Huginmunin",
                licence="unspecified",
                experimental=True,
                input=SimpleNamespace(line_height=128),
            ),),
        )
        caps = SimpleNamespace(engine="pylaia")
        with patch("ai.htr_engine_client.fetch_capabilities", return_value=caps), patch(
            "ai.htr_engine_client.fetch_models", return_value=bare,
        ), patch("urllib.request.urlopen", side_effect=AssertionError("urlopen")):
            refused_models = self.client.get(
                self._url("external-htr/engines/%s/models/" % config.pk),
            )
            refused_run = start_run(
                self.document, self.owner,
                {"config_id": config.pk, "model_id": "huginmunin", "part_ids": [self.part.pk]},
                queue=lambda _run_id: (_ for _ in ()).throw(AssertionError("queued")),
            )
        self.assertEqual(refused_models.status_code, 200)
        self.assertEqual(refused_models.json()["engine"], "pylaia")
        self.assertIsNone(refused_models.json()["models"][0]["line_height"])
        self.assertEqual(refused_run.status_code, 400)
        self.assertEqual(refused_run.data["code"], "model_line_height")
        self.assertEqual(ExternalHTRRun.objects.count(), 0)

        with patch("ai.htr_engine_client.fetch_capabilities", return_value=caps), patch(
            "ai.htr_engine_client.fetch_models", return_value=ready,
        ), patch("urllib.request.urlopen", side_effect=AssertionError("urlopen")):
            accepted = self.client.get(self._url("external-htr/engines/%s/models/" % config.pk))
        self.assertEqual(accepted.status_code, 200)
        model = accepted.json()["models"][0]
        self.assertEqual(model["model_id"], "huginmunin")
        self.assertEqual(model["line_height"], 128)
        self.assertEqual(ExternalHTRRun.objects.count(), 0)

    def test_pylaia_line_height_is_required_before_a_page_is_opened(self):
        config = self._config("http://127.0.0.1:9/")
        bare = SimpleNamespace(
            engine="pylaia",
            models=(SimpleNamespace(
                model_id="huginmunin",
                input=SimpleNamespace(line_height=None),
            ),),
        )
        caps = SimpleNamespace(engine="pylaia")
        with patch("ai.htr_engine_client.fetch_capabilities", return_value=caps), patch(
            "ai.htr_engine_client.fetch_models", return_value=bare,
        ):
            refused = start_run(
                self.document, self.owner,
                {"config_id": config.pk, "model_id": "huginmunin", "part_ids": [self.part.pk]},
                queue=lambda _run_id: (_ for _ in ()).throw(AssertionError("queued")),
            )
        self.assertEqual(refused.status_code, 400)
        self.assertEqual(refused.data["code"], "model_line_height")
        self.assertEqual(refused.data["message"], "model did not report a usable line height")
        self.assertEqual(ExternalHTRRun.objects.count(), 0)

        queued = self._queued(config, "huginmunin")

        def fail_if_called(*_args, **_kwargs):
            raise AssertionError("engine was called")

        execute_external_htr_run(
            queued.pk,
            fetch_capabilities=lambda _config: caps,
            fetch_models=lambda _config: bare,
            open_image=_forbid_image,
            live=fail_if_called,
            pause=lambda: None,
        )
        queued.refresh_from_db()
        self.assertEqual(queued.code, "model_line_height")
        self.assertEqual(ExternalHTRJob.objects.count(), 0)

        reported = SimpleNamespace(
            engine="pylaia",
            models=(SimpleNamespace(
                model_id="huginmunin",
                input=SimpleNamespace(line_height=128),
            ),),
        )
        seen = {}

        def remember(_config, request):
            seen["image"] = request["lines"][0]["image"]
            return SimpleNamespace(ok=False, code="unavailable", apply_plan=None)

        sized = self._queued(config, "huginmunin")
        execute_external_htr_run(
            sized.pk,
            fetch_capabilities=lambda _config: caps,
            fetch_models=lambda _config: reported,
            live=remember,
            pause=lambda: None,
        )
        opened = Image.open(io.BytesIO(base64.standard_b64decode(seen["image"])))
        opened.load()
        self.assertEqual(opened.size[1], 128)
        self.assertGreater(opened.size[0], 40)

    def test_worker_blocks_permission_offsite_and_unknown_models_before_the_image(self):
        config = self._config("http://10.0.0.8:9/secret-endpoint")
        run = self._queued(config, MODEL_ID, created_by=self.stranger)
        execute_external_htr_run(run.pk, open_image=_forbid_image, pause=lambda: None)
        run.refresh_from_db()
        self.assertEqual(run.code, "permission_denied")
        self.assertEqual(ExternalHTRJob.objects.count(), 0)

        owned = self._queued(config, MODEL_ID)
        execute_external_htr_run(owned.pk, open_image=_forbid_image, pause=lambda: None)
        owned.refresh_from_db()
        self.assertEqual(owned.code, "offsite_forbidden")
        self.assertNotIn("10.0.0.8", owned.message)
        self.assertEqual(ExternalHTRJob.objects.count(), 0)

        local = self._config("http://127.0.0.1:9/")
        with _reference_server() as endpoint:
            local.endpoint_url = endpoint
            local.save(update_fields=["endpoint_url", "updated_at"])
            missing = self._queued(local, "not-a-reported-model")
            execute_external_htr_run(missing.pk, open_image=_forbid_image, pause=lambda: None)
        missing.refresh_from_db()
        self.assertEqual(missing.code, "model_not_found")
        self.assertEqual(missing.message, "engine did not report that model")
        self.assertEqual(ExternalHTRJob.objects.count(), 0)

    def test_reference_run_imports_once_and_keeps_the_review_gate(self):
        with _reference_server() as endpoint:
            config = self._config(endpoint)
            with self.assertLogs("ai.external_htr_run", level="INFO") as logs:
                response = self._post("external-htr/runs/", {
                    "config_id": config.pk,
                    "model_id": MODEL_ID,
                    "part_ids": [self.part.pk],
                }, queue=self._inline)
            self.assertEqual(response.status_code, 202, response.content)
            run = ExternalHTRRun.objects.get()
            detail = self.client.get(self._url("external-htr/runs/%s/" % run.pk))
            self.assertEqual(detail.status_code, 200)
            payload = detail.json()
            rendered = "\n".join(logs.output)
            for secret in (SECRET_TITLE, SECRET_TOKEN, endpoint, "LINE ", SECRET_FILE):
                self.assertNotIn(secret, json.dumps(payload), secret)
                self.assertNotIn(secret, rendered, secret)
        self.assertEqual(run.status, "completed")
        self.assertEqual(run.engine, ENGINE_NAME)
        page = payload["pages"][0]
        self.assertEqual(page["status"], "completed")
        self.assertGreaterEqual(page["skipped"], 1)
        self.assertGreaterEqual(page["results"], 1)
        skipped = [line for line in page["lines"] if line["line_id"] == str(self.skipped.pk)]
        self.assertEqual(skipped[0]["status"], "skipped")
        self.assertEqual(skipped[0]["code"], "no_mask")
        self.assertNotIn("text", skipped[0])
        self.assertEqual(AIJob.objects.count(), 0)

        imported = self._post("external-htr/runs/%s/import/" % run.pk, {})
        self.assertEqual(imported.status_code, 200, imported.content)
        self.assertEqual(imported.json()["code"], "writeback_imported")
        layer = Transcription.objects.get(pk=imported.json()["transcription_id"])
        self.assertNotEqual(layer.pk, self.manual.pk)
        written = LineTranscription.objects.get(transcription=layer)
        self.assertEqual(written.line_id, self.kept.pk)
        self.assertTrue(written.content.startswith("LINE "))
        self.assertEqual(written.version_source, provenance_stamp(ENGINE_NAME, MODEL_ID))
        self.assertEqual(written.version_author, self.owner.username)
        self.assertFalse(
            LineTranscription.objects.filter(transcription=layer, line=self.skipped).exists(),
        )
        gate = AILayerGate.objects.get(transcription=layer)
        self.assertEqual(gate.state, AILayerGate.STATE_RAW)
        self.assertTrue(gate.sample_line_pks)
        with self.assertRaises(LayerNotEligible):
            assert_training_eligible(layer)
        assert_training_eligible(self.manual)
        self._assert_existing_text()

        again = self._post("external-htr/runs/%s/import/" % run.pk, {})
        self.assertEqual(again.status_code, 200)
        self.assertEqual(again.json()["code"], "already_imported")
        self.assertEqual(again.json()["transcription_id"], layer.pk)
        self.assertEqual(Transcription.objects.filter(document=self.document).count(), 2)
        self.assertEqual(AILayerGate.objects.count(), 1)

        acknowledged = self.client.post(
            "/api/documents/%s/ai-gates/%s/acknowledge/" % (self.document.pk, gate.pk),
            data=json.dumps({"phrase": ACK_PHRASE}),
            content_type="application/json",
        )
        self.assertEqual(acknowledged.status_code, 200, acknowledged.content)
        gate.refresh_from_db()
        self.assertEqual(gate.state, "sampled")
        with self.assertRaises(LayerNotEligible):
            assert_training_eligible(layer)
        marked = self.client.post(
            "/api/documents/%s/ai-gates/%s/mark_eligible/" % (self.document.pk, gate.pk),
        )
        self.assertEqual(marked.status_code, 200, marked.content)
        gate.refresh_from_db()
        self.assertEqual(gate.state, "training-eligible")
        # The earlier eligibility check cached the raw reverse relation.
        layer = Transcription.objects.get(pk=layer.pk)
        assert_training_eligible(layer)
        self._assert_existing_text()

    def test_partial_failure_imports_only_the_completed_page(self):
        failed_part, failed_line, _ignored = self._part_with_lines()
        os.remove(failed_part.image.path)
        with _reference_server() as endpoint:
            config = self._config(endpoint)
            run = self._queued(config, MODEL_ID, part_ids=[self.part.pk, failed_part.pk])
            execute_external_htr_run(run.pk, pause=lambda: None)
        run.refresh_from_db()
        self.assertEqual(run.status, "partial")
        self.assertEqual(run.code, "partial")
        by_part = {page["part_id"]: page for page in run.pages}
        self.assertEqual(by_part[self.part.pk]["status"], "completed")
        self.assertEqual(by_part[failed_part.pk]["status"], "failed")
        self.assertEqual(by_part[failed_part.pk]["code"], "image_unavailable")
        result = import_external_htr_run(run.pk)
        self.assertEqual(result.code, "writeback_imported")
        layer_lines = set(
            LineTranscription.objects.filter(transcription_id=result.transcription_id)
            .values_list("line_id", flat=True)
        )
        self.assertIn(self.kept.pk, layer_lines)
        self.assertNotIn(failed_line.pk, layer_lines)
        failed_job = ExternalHTRJob.objects.filter(run=run, part=failed_part).first()
        if failed_job is not None:
            self.assertFalse(failed_job.imported)
        self._assert_existing_text()

    def test_empty_engine_text_is_imported(self):
        from ai.reference_engine import engine as reference_engine

        real = reference_engine._recognize

        def blank(body):
            status, payload = real(body)
            if status == 200:
                for row in payload.get("results", []):
                    row["text"] = ""
            return status, payload

        with patch.object(reference_engine, "_recognize", blank), _reference_server() as endpoint:
            config = self._config(endpoint)
            run = self._queued(config, MODEL_ID)
            execute_external_htr_run(run.pk, pause=lambda: None)
        run.refresh_from_db()
        self.assertEqual(run.status, "completed")
        self.assertGreaterEqual(run.pages[0]["empty_text"], 1)
        empty = [line for line in run.pages[0]["lines"] if line["line_id"] == str(self.kept.pk)]
        self.assertTrue(empty[0]["empty"])
        self.assertEqual(empty[0]["text_length"], 0)
        result = import_external_htr_run(run.pk)
        self.assertEqual(result.code, "writeback_imported")
        written = LineTranscription.objects.get(
            transcription_id=result.transcription_id, line=self.kept,
        )
        self.assertEqual(written.content, "")
        self.assertEqual(
            AILayerGate.objects.get(transcription_id=result.transcription_id).state,
            AILayerGate.STATE_RAW,
        )

    def test_retry_then_terminal_failure_stays_on_the_same_engine(self):
        decoy = self._config("http://127.0.0.1:9/decoy-engine")
        calls = {"n": 0}

        def flaky(config, request):
            calls["n"] += 1
            if calls["n"] == 1:
                return ExternalHtrLiveResult(
                    False, "unavailable", "recognition request was not completed", None,
                )
            return run_external_htr_live(config, request)

        with _reference_server() as endpoint:
            config = self._config(endpoint)
            run = self._queued(config, MODEL_ID)
            execute_external_htr_run(run.pk, live=flaky, pause=lambda: None)
        run.refresh_from_db()
        self.assertEqual(calls["n"], 2)
        self.assertEqual(run.status, "completed")
        self.assertEqual(run.pages[0]["attempts"], 2)
        self.assertEqual(run.config_id, config.pk)
        self.assertEqual(ExternalHTRJob.objects.filter(config=decoy).count(), 0)

        calls["n"] = 0

        def refused(config, request):
            calls["n"] += 1
            self.assertEqual(config.pk, config.pk)
            return ExternalHtrLiveResult(
                False, "http_error", "recognition request was not completed", None,
            )

        with _reference_server() as endpoint:
            other = self._config(endpoint)
            failed = self._queued(other, MODEL_ID)
            execute_external_htr_run(failed.pk, live=refused, pause=lambda: None)
            execute_external_htr_run(failed.pk, live=refused, pause=lambda: None, task_id="again")
        failed.refresh_from_db()
        self.assertEqual(calls["n"], 1)
        self.assertEqual(failed.status, "failed")
        self.assertEqual(failed.code, "http_error")
        self.assertEqual(failed.config_id, other.pk)
        self.assertEqual(import_external_htr_run(failed.pk).code, "audit_incomplete")
        self.assertEqual(Transcription.objects.filter(document=self.document).count(), 1)

    def test_running_claim_and_all_skipped_import(self):
        config = self._config("http://127.0.0.1:9/")
        run = self._queued(config, MODEL_ID)
        run.status = ExternalHTRRun.STATUS_RUNNING
        run.task_id = "held"
        run.save(update_fields=["status", "task_id", "updated_at"])
        calls = {"n": 0}

        def live(config, request):
            calls["n"] += 1
            return ExternalHtrLiveResult(False, "http_error", "recognition request was not completed", None)

        execute_external_htr_run(run.pk, task_id="other", live=live, pause=lambda: None)
        run.refresh_from_db()
        self.assertEqual(calls["n"], 0)
        self.assertEqual(run.status, "running")
        self.assertEqual(run.task_id, "held")

        stamp = provenance_stamp(ENGINE_NAME, MODEL_ID)
        skipped_run = ExternalHTRRun.objects.create(
            document=self.document,
            config=config,
            created_by=self.owner,
            model_id=MODEL_ID,
            engine=ENGINE_NAME,
            status=ExternalHTRRun.STATUS_COMPLETED,
            part_ids=[self.part.pk],
            pages=[],
        )
        job = ExternalHTRJob.objects.create(
            document=self.document,
            part=self.part,
            config=config,
            run=skipped_run,
            model_id=MODEL_ID,
            engine=ENGINE_NAME,
            model_version=MODEL_VERSION,
            api_version="1",
            layer_source=stamp,
            status=ExternalHTRJob.STATUS_COMPLETED,
            requested_line_count=1,
            skipped_line_count=1,
            result_count=0,
            warning_count=0,
            empty_text_count=0,
            created_by=self.owner,
        )
        ExternalHTRLineResult.objects.create(
            job=job,
            line_id=str(self.skipped.pk),
            position=0,
            text="",
            status=ExternalHTRLineResult.STATUS_SKIPPED,
            code="no_mask",
        )
        result = import_external_htr_run(skipped_run.pk)
        self.assertEqual(result.code, "no_included_lines")
        self.assertIsNone(result.transcription_id)
        self.assertEqual(Transcription.objects.filter(document=self.document).count(), 1)
        self._assert_existing_text()

    def _inline(self, run_id):
        execute_external_htr_run(run_id, task_id="inline", pause=lambda: None)

    def _post(self, path, payload, queue=None):
        if queue is None:
            return self.client.post(
                self._url(path),
                data=json.dumps(payload),
                content_type="application/json",
            )
        with patch("ai.external_htr_user._default_queue", queue):
            return self.client.post(
                self._url(path),
                data=json.dumps(payload),
                content_type="application/json",
            )

    def _url(self, path):
        return "/api/documents/%s/%s" % (self.document.pk, path)

    def _config(self, endpoint, *, enabled=True, tier=ExternalHTREngineConfig.TIER_RESEARCH):
        return ExternalHTREngineConfig.objects.create(
            name="engine-%s" % uuid.uuid4().hex[:12],
            endpoint_url=endpoint,
            enabled=enabled,
            tier=tier,
            experimental=True,
            timeout_seconds=10,
            metadata={"token": SECRET_TOKEN},
        )

    def _queued(self, config, model_id, *, created_by=None, part_ids=None):
        return ExternalHTRRun.objects.create(
            document=self.document,
            config=config,
            created_by=self.owner if created_by is None else created_by,
            model_id=model_id,
            status=ExternalHTRRun.STATUS_QUEUED,
            part_ids=part_ids or [self.part.pk],
            pages=[],
        )

    def _part_with_lines(self):
        upload, size = _png()
        part = DocumentPart.objects.create(
            document=self.document,
            image=upload,
            image_file_size=size,
            original_filename=SECRET_FILE,
        )
        kept = Line.objects.create(
            document_part=part,
            baseline=[[10, 30], [50, 30]],
            mask=[[10, 20], [50, 20], [50, 40], [10, 40]],
        )
        skipped = Line.objects.create(
            document_part=part,
            baseline=[[10, 30], [50, 30]],
            mask=None,
        )
        return part, kept, skipped

    def _assert_existing_text(self):
        self.manual_row.refresh_from_db()
        self.kraken_row.refresh_from_db()
        self.assertEqual(self.manual_row.content, MANUAL_TEXT)
        self.assertEqual(self.manual_row.version_source, "manual")
        self.assertEqual(self.kraken_row.content, KRAKEN_TEXT)
        self.assertEqual(self.kraken_row.version_source, "kraken")


if __name__ == "__main__":
    import django
    from django.conf import settings
    from django.test.utils import get_runner

    django.setup()
    raise SystemExit(not get_runner(settings)().run_tests(
        ["ai.django_integration_tests.test_external_htr_run_orm"],
    ))
