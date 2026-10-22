"""Operator workflow against the local reference engine.

Synthetic page, disposable media, and a loopback HTTP test double.
``LINE <line_id>`` is that double's fixed text. It is not recognition
and it is not an accuracy result. The server binds to 127.0.0.1 and is
shut down when the test ends, including when an assertion fails.
"""
import os
import shutil
import socket
import threading
import uuid
from contextlib import contextmanager
from http.server import HTTPServer
from io import BytesIO, StringIO
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone
from PIL import Image

from ai.gate import (
    ACK_PHRASE,
    LayerNotEligible,
    acknowledge_sample,
    assert_training_eligible,
    build_gate_sample,
    mark_training_eligible,
)
from ai.models import (
    AIDocumentPolicy,
    AILayerGate,
    AILineDisagreement,
    ExternalHTREngineConfig,
    ExternalHTRJob,
    ExternalHTRLineResult,
)
from ai.reference_engine.engine import ENGINE_NAME, MODEL_ID, MODEL_VERSION
from ai.reference_engine.server import ReferenceHandler
from core.models import (
    Document,
    DocumentPart,
    Line,
    LineTranscription,
    Project,
    Transcription,
)
from users.models import User

MEDIA = "/tmp/htr-operator-workflow-media"
SECRET_TITLE = "workflow-secret-title"
SECRET_FILE = "workflow-secret-page.png"
MANUAL_TEXT = "manual-kept-text"
DECOY_EXTERNAL_ID = "decoy-external-id"
REMOTE_ENDPOINT = "http://10.0.0.8:9/secret-endpoint"
REFERENCE_WARNING = "reference engine ignored the image"


def _png():
    buffer = BytesIO()
    Image.new("RGB", (60, 60), (20, 20, 20)).save(buffer, "PNG")
    payload = buffer.getvalue()
    return SimpleUploadedFile(SECRET_FILE, payload, "image/png"), len(payload)


def _run(command, *args):
    stdout, stderr = StringIO(), StringIO()
    try:
        call_command(
            command,
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


@contextmanager
def _reference_server():
    """Serve the fake engine on an ephemeral IPv4 loopback port."""
    server = HTTPServer(("127.0.0.1", 0), ReferenceHandler)
    host, port = server.server_address[:2]
    if host != "127.0.0.1":
        server.server_close()
        raise RuntimeError("reference engine was not bound to loopback")
    thread = threading.Thread(
        target=server.serve_forever,
        name="reference-engine-test",
        daemon=True,
    )
    thread.start()
    try:
        _wait_until_listening(port)
        yield "http://127.0.0.1:%d/" % port
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        if thread.is_alive():
            raise RuntimeError("reference engine thread did not stop")
        _assert_stopped(port)


def _wait_until_listening(port):
    last_error = None
    for _ in range(50):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError as exc:
            last_error = exc
    raise RuntimeError("reference engine did not accept a connection") from last_error


def _assert_stopped(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.2):
            pass
    except OSError:
        return
    raise RuntimeError("reference engine is still listening on %s" % port)


@contextmanager
def _no_engine_io():
    """Fail if preview or apply opens an image or the network.

    Do not patch socket.create_connection. That breaks the database.
    """
    def reject(label):
        def _reject(*args, **kwargs):
            raise AssertionError(label)
        return _reject

    with patch("urllib.request.urlopen", side_effect=reject("urlopen")), \
            patch("urllib.request.build_opener", side_effect=reject("opener")), \
            patch("ai.htr_engine_client.urllib.request.urlopen", side_effect=reject("client-urlopen")), \
            patch("ai.htr_engine_client.urllib.request.build_opener", side_effect=reject("client-opener")), \
            patch("requests.get", side_effect=reject("get")), \
            patch("requests.post", side_effect=reject("post")), \
            patch("PIL.Image.open", side_effect=reject("image")):
        yield


@override_settings(MEDIA_ROOT=MEDIA)
class ExternalHtrOperatorWorkflowOrmTests(TestCase):
    def setUp(self):
        if os.path.isdir(MEDIA):
            shutil.rmtree(MEDIA)
        os.makedirs(MEDIA)
        self.owner = User.objects.create_user(
            username="wf-%s" % uuid.uuid4().hex[:12],
            email="wf-%s@example.test" % uuid.uuid4().hex[:12],
            password="unused-test-password",
        )
        self.project = Project.objects.create(
            name="operator-workflow",
            slug=uuid.uuid4().hex[:12],
            owner=self.owner,
        )
        self.document = Document.objects.create(
            name=SECRET_TITLE,
            owner=self.owner,
            project=self.project,
        )
        AIDocumentPolicy.objects.create(
            document=self.document,
            never_send_offsite=True,
        )
        upload, size = _png()
        self.part = DocumentPart.objects.create(
            document=self.document,
            image=upload,
            image_file_size=size,
            original_filename=SECRET_FILE,
        )
        first = self._line(self.part, DECOY_EXTERNAL_ID)
        second = self._line(self.part, None)
        self.lines = list(self.part.lines.order_by("order", "pk"))
        self.assertEqual([line.pk for line in self.lines], [first.pk, second.pk])
        self.assertLess(self.lines[0].order, self.lines[1].order)
        self.assertTrue(os.path.isfile(self.part.image.path))
        self.manual = Transcription.objects.get(
            document=self.document,
            name=Transcription.DEFAULT_NAME,
        )
        self.manual_row = LineTranscription.objects.create(
            transcription=self.manual,
            line=self.lines[0],
            content=MANUAL_TEXT,
            version_source="manual",
            version_author="keeper",
        )
        self.manual_state = self._manual_state()

    def tearDown(self):
        shutil.rmtree(MEDIA, ignore_errors=True)

    def test_audit_preview_and_apply_use_the_reference_engine(self):
        self.assertEqual(ACK_PHRASE, "I reviewed the sample")
        layers_before = set(Transcription.objects.values_list("pk", flat=True))
        line_rows_before = LineTranscription.objects.count()
        self.assertEqual(layers_before, {self.manual.pk})
        self.assertEqual(ExternalHTRJob.objects.count(), 0)
        self.assertEqual(AILayerGate.objects.count(), 0)

        with _reference_server() as endpoint:
            config = self._config(endpoint)
            code, stdout, stderr = _run(
                "run_external_htr_audit",
                str(config.pk),
                str(self.part.pk),
                "--engine",
                ENGINE_NAME,
                "--model-id",
                MODEL_ID,
            )
            self._assert_quiet(stdout, stderr, endpoint)
            self.assertEqual(code, 0, stdout)
            job = ExternalHTRJob.objects.get()
            self.assertEqual(
                stdout,
                "OK: external HTR audit job %s completed results=2 skipped=0\n" % job.pk,
            )
            self._assert_completed_audit(job, config, endpoint)
            self.assertEqual(
                set(Transcription.objects.values_list("pk", flat=True)),
                layers_before,
            )
            self.assertEqual(LineTranscription.objects.count(), line_rows_before)
            self.assertEqual(AILayerGate.objects.count(), 0)
            self.assertEqual(self._manual_state(), self.manual_state)

        self.assertFalse(job.imported)
        audited_at = job.updated_at
        audited_lines = [
            (row.pk, row.position, row.line_id, row.text, row.warnings)
            for row in job.lines.order_by("position", "pk")
        ]

        with _no_engine_io():
            code, stdout, stderr = _run("import_external_htr_audit", str(job.pk))
        job.refresh_from_db()
        self.assertEqual(code, 0, stdout)
        self.assertEqual(
            stdout,
            "OK: external HTR import preview job=%s lines=2\n" % job.pk,
        )
        self._assert_quiet(stdout, stderr)
        self.assertEqual(job.updated_at, audited_at)
        self.assertFalse(job.imported)
        self.assertIsNone(job.imported_transcription_id)
        self.assertEqual(
            [
                (row.pk, row.position, row.line_id, row.text, row.warnings)
                for row in job.lines.order_by("position", "pk")
            ],
            audited_lines,
        )
        self.assertEqual(
            set(Transcription.objects.values_list("pk", flat=True)),
            layers_before,
        )
        self.assertEqual(LineTranscription.objects.count(), line_rows_before)
        self.assertEqual(AILayerGate.objects.count(), 0)
        self.assertEqual(self._manual_state(), self.manual_state)

        with _no_engine_io():
            code, stdout, stderr = _run(
                "import_external_htr_audit", str(job.pk), "--apply",
            )
        job.refresh_from_db()
        self.assertEqual(code, 0, stdout)
        self.assertTrue(job.imported)
        layer = job.imported_transcription
        self.assertEqual(
            stdout,
            "OK: external HTR import applied job=%s layer=%s lines=2\n" % (
                job.pk, layer.pk,
            ),
        )
        self._assert_quiet(stdout, stderr, "LINE ")
        self.assertEqual(
            set(Transcription.objects.values_list("pk", flat=True)),
            layers_before | {layer.pk},
        )
        self.assertNotEqual(layer.pk, self.manual.pk)
        self.assertEqual(layer.document_id, self.document.pk)
        self.assertEqual(layer.name, "%s:job-%s" % (job.layer_source, job.pk))
        written = list(
            LineTranscription.objects.filter(transcription=layer).order_by("pk")
        )
        expected = [
            (line.pk, "LINE %s" % line.pk) for line in self.lines
        ]
        self.assertEqual([(row.line_id, row.content) for row in written], expected)
        for row, line in zip(written, self.lines):
            self.assertEqual(row.content, "LINE %s" % line.pk)
            self.assertNotEqual(row.content, "LINE %s" % DECOY_EXTERNAL_ID)
            self.assertEqual(row.version_source, job.layer_source)
            self.assertEqual(row.version_author, "")
        gate = AILayerGate.objects.get(transcription=layer)
        summary = build_gate_sample(
            [], written_pks=[row.line_id for row in written], comparison=None,
        )
        self.assertEqual(gate.state, AILayerGate.STATE_RAW)
        self.assertEqual(gate.sample_line_pks, summary["sample_line_pks"])
        self.assertTrue(gate.sample_line_pks)
        self.assertEqual(gate.sample_line_pks, [line.pk for line in self.lines])
        self.assertIsNone(gate.mean_cer)
        self.assertIsNone(gate.comparison_id)
        self.assertIsNone(gate.job_id)
        self.assertEqual(gate.held_out_part_pks, [])
        self.assertIsNone(gate.acknowledged_at)
        self.assertEqual(AILineDisagreement.objects.filter(gate=gate).count(), 0)
        self.assertEqual(AILayerGate.objects.count(), 1)
        with self.assertRaises(LayerNotEligible):
            assert_training_eligible(layer)
        assert_training_eligible(Transcription.objects.get(pk=self.manual.pk))
        self.assertEqual(self._manual_state(), self.manual_state)

        acknowledge_sample(
            gate, user=self.owner, phrase=ACK_PHRASE, now=timezone.now(),
        )
        gate.save()
        with self.assertRaises(LayerNotEligible):
            assert_training_eligible(Transcription.objects.get(pk=layer.pk))
        mark_training_eligible(gate)
        gate.save()
        reloaded = Transcription.objects.get(pk=layer.pk)
        assert_training_eligible(reloaded)
        self.assertEqual(
            reloaded.ai_gate.state, AILayerGate.STATE_TRAINING_ELIGIBLE,
        )
        self.assertEqual(reloaded.ai_gate.sample_line_pks, [line.pk for line in self.lines])
        self.assertEqual(self._manual_state(), self.manual_state)

        layers_after = set(Transcription.objects.values_list("pk", flat=True))
        with _no_engine_io():
            code, stdout, stderr = _run(
                "import_external_htr_audit", str(job.pk), "--apply",
            )
        job.refresh_from_db()
        self.assertEqual(code, 1, stdout)
        self.assertEqual(stdout, "FAILED: external HTR import already_imported\n")
        self._assert_quiet(stdout, stderr, "LINE ")
        self.assertTrue(job.imported)
        self.assertEqual(job.imported_transcription_id, layer.pk)
        self.assertEqual(
            set(Transcription.objects.values_list("pk", flat=True)),
            layers_after,
        )
        self.assertEqual(AILayerGate.objects.count(), 1)
        self.assertEqual(
            LineTranscription.objects.filter(transcription=layer).count(),
            2,
        )
        self.assertEqual(self._manual_state(), self.manual_state)

    def test_restricted_remote_endpoint_is_blocked_before_network(self):
        config = self._config(REMOTE_ENDPOINT)
        events = []

        def reject(label):
            def _reject(*args, **kwargs):
                events.append(label)
                raise AssertionError(label)
            return _reject

        jobs = ExternalHTRJob.objects.count()
        results = ExternalHTRLineResult.objects.count()
        layers = set(Transcription.objects.values_list("pk", flat=True))
        line_rows = LineTranscription.objects.count()
        with patch(
            "ai.management.commands.run_external_htr_audit.Image.open",
            side_effect=reject("image"),
        ), patch(
            "ai.htr_engine_client.urllib.request.build_opener",
            side_effect=reject("opener"),
        ), patch(
            "ai.htr_engine_client.urllib.request.urlopen",
            side_effect=reject("urlopen"),
        ), patch(
            "requests.get", side_effect=reject("get"),
        ), patch(
            "requests.post", side_effect=reject("post"),
        ):
            code, stdout, stderr = _run(
                "run_external_htr_audit",
                str(config.pk),
                str(self.part.pk),
                "--engine",
                ENGINE_NAME,
                "--model-id",
                MODEL_ID,
            )
        self.assertEqual(
            (code, stdout, stderr),
            (1, "FAILED: external HTR audit offsite_forbidden\n", ""),
            events,
        )
        self.assertEqual(events, [])
        self._assert_quiet(stdout, stderr, REMOTE_ENDPOINT)
        self.assertEqual(ExternalHTRJob.objects.count(), jobs)
        self.assertEqual(ExternalHTRLineResult.objects.count(), results)
        self.assertEqual(set(Transcription.objects.values_list("pk", flat=True)), layers)
        self.assertEqual(LineTranscription.objects.count(), line_rows)
        self.assertEqual(self._manual_state(), self.manual_state)

    def _config(self, endpoint):
        return ExternalHTREngineConfig.objects.create(
            name="engine-%s" % uuid.uuid4().hex[:12],
            endpoint_url=endpoint,
            enabled=True,
            tier=ExternalHTREngineConfig.TIER_RESEARCH,
            experimental=True,
            timeout_seconds=10,
            metadata={},
        )

    def _line(self, part, external_id):
        line = Line.objects.create(
            document_part=part,
            baseline=[[10, 30], [50, 30]],
            mask=[[10, 20], [50, 20], [50, 40], [10, 40]],
        )
        if external_id is not None:
            line.external_id = external_id
            line.save(update_fields=["external_id"])
        return line

    def _manual_state(self):
        manual = Transcription.objects.get(pk=self.manual.pk)
        row = LineTranscription.objects.get(pk=self.manual_row.pk)
        return (
            manual.pk,
            manual.name,
            manual.updated_at,
            row.content,
            row.version_source,
            row.version_author,
            row.version_updated_at,
        )

    def _assert_completed_audit(self, job, config, endpoint):
        self.assertEqual(job.status, ExternalHTRJob.STATUS_COMPLETED)
        self.assertEqual(job.document_id, self.document.pk)
        self.assertEqual(job.part_id, self.part.pk)
        self.assertEqual(job.config_id, config.pk)
        self.assertEqual(job.engine, ENGINE_NAME)
        self.assertEqual(job.model_id, MODEL_ID)
        self.assertEqual(job.model_version, MODEL_VERSION)
        self.assertEqual(job.api_version, "1")
        self.assertEqual(job.layer_source, "external-htr:reference:reference-line")
        self.assertEqual(job.requested_line_count, 2)
        self.assertEqual(job.skipped_line_count, 0)
        self.assertEqual(job.result_count, 2)
        self.assertEqual(job.warning_count, 2)
        self.assertEqual(job.empty_text_count, 0)
        self.assertEqual(job.code, "")
        self.assertEqual(job.message, "response would be written")
        self.assertFalse(job.imported)
        self.assertIsNone(job.imported_transcription_id)
        self.assertNotIn(endpoint, job.message)
        self.assertNotIn(SECRET_TITLE, job.message)
        rows = list(job.lines.order_by("position", "pk"))
        self.assertEqual(len(rows), 2)
        for position, (row, line) in enumerate(zip(rows, self.lines)):
            self.assertEqual(row.position, position)
            self.assertEqual(row.line_id, str(line.pk))
            self.assertNotEqual(row.line_id, DECOY_EXTERNAL_ID)
            self.assertEqual(row.status, ExternalHTRLineResult.STATUS_INCLUDED)
            self.assertEqual(row.text, "LINE %s" % line.pk)
            self.assertEqual(row.warnings, [REFERENCE_WARNING])
            self.assertIsNone(row.confidence)
            self.assertEqual(row.timing_ms, 0)
            self.assertNotIn(endpoint, row.text)

    def _assert_quiet(self, stdout, stderr, *extra):
        self.assertEqual(stderr, "")
        blob = stdout + stderr
        banned = (
            SECRET_TITLE,
            SECRET_FILE,
            MANUAL_TEXT,
            DECOY_EXTERNAL_ID,
            REFERENCE_WARNING,
            REMOTE_ENDPOINT,
            "10.0.0.8",
            "iVBOR",
            "Traceback",
            "secret-endpoint",
            MEDIA,
        ) + tuple(extra)
        for item in banned:
            self.assertNotIn(item, blob, item)
