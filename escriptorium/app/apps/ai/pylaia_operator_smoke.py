"""Optional process check for a local PyLaia decode service.

This module is not part of ``python3 -m unittest ai.tests`` or
``python manage.py test ai.django_integration_tests``. It runs only when
``PYLAIA_OPERATOR_SMOKE`` is ``1`` and a decode service is already
listening. It does not start that service, download a model, or install
PyLaia. Empty recognized text is valid. The check does not print that
text and it is not an accuracy result. It does not run training.

``PYLAIA_SMOKE_ENDPOINT`` defaults to ``http://127.0.0.1:8766``.
``PYLAIA_SMOKE_MODEL_ID`` defaults to ``huginmunin``. That model id is the
label configured on the server, not a file path and not a model-card id.
The service reports ``licence`` ``unspecified`` and ``source``
``configured``. Those are configured labels. The public card for
``Teklia/pylaia-norhand-v1`` is labelled MIT, and
``Teklia/pylaia-huginmunin`` redirects there. This check does not verify
that card or the files in the model directory.
"""
import ipaddress
import json
import os
import re
import shutil
import unittest
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from io import BytesIO, StringIO
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone
from PIL import Image

from ai.external_engines.pylaia.backend import ENGINE_NAME, _MODEL_VERSION
from ai.external_htr_writeback_plan import _layer_name, provenance_stamp
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
from core.models import (
    Document,
    DocumentPart,
    Line,
    LineTranscription,
    Project,
    Transcription,
)
from users.models import User

MEDIA = "/tmp/htr-pylaia-operator-media"
SECRET_TITLE = "pylaia-smoke-secret-title"
SECRET_FILE = "pylaia-smoke-secret-page.png"
MANUAL_TEXT = "manual-kept-text"
DECOY_EXTERNAL_ID = "decoy-external-id"
DEFAULT_ENDPOINT = "http://127.0.0.1:8766"
DEFAULT_MODEL_ID = "huginmunin"
_LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_CONFIGURED_LICENCE = "unspecified"
_CONFIGURED_SOURCE = "configured"


def _endpoint():
    return os.environ.get("PYLAIA_SMOKE_ENDPOINT", DEFAULT_ENDPOINT).strip()


def _model_id():
    return os.environ.get("PYLAIA_SMOKE_MODEL_ID", DEFAULT_MODEL_ID).strip()


def _loopback_base(value):
    """True when value is an http loopback base URL with no userinfo."""
    try:
        parts = urllib.parse.urlsplit(value)
        port = parts.port
        host = parts.hostname
    except ValueError:
        return False
    if (
        parts.scheme != "http"
        or parts.username
        or parts.password
        or parts.query
        or parts.fragment
        or parts.path not in ("", "/")
    ):
        return False
    if host == "localhost":
        return port is not None
    if not isinstance(host, str):
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    if getattr(address, "ipv4_mapped", None) is not None:
        return False
    return bool(address.is_loopback and port is not None)


def _png():
    buffer = BytesIO()
    Image.new("RGB", (640, 300), (180, 180, 180)).save(buffer, "PNG")
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
        if code is None:
            code = 0
    else:
        code = 0
    return code, stdout.getvalue(), stderr.getvalue()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _get_json(url):
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _NoRedirect,
    )
    try:
        with opener.open(url, timeout=5) as response:
            status = response.status
            raw = response.read(65536)
    except urllib.error.HTTPError as exc:
        raise AssertionError("local engine status %s" % exc.code) from None
    except (OSError, urllib.error.URLError):
        raise AssertionError("local engine was not reachable") from None
    if status != 200:
        raise AssertionError("local engine status %s" % status)
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        raise AssertionError("local engine record was not json") from None
    if not isinstance(payload, dict):
        raise AssertionError("local engine record was not an object")
    return payload


@unittest.skipUnless(
    os.environ.get("PYLAIA_OPERATOR_SMOKE") == "1",
    "requires a local PyLaia decode service; set PYLAIA_OPERATOR_SMOKE=1",
)
@override_settings(MEDIA_ROOT=MEDIA)
class PyLaiaOperatorSmokeTests(TestCase):
    def setUp(self):
        if os.path.isdir(MEDIA):
            shutil.rmtree(MEDIA)
        os.makedirs(MEDIA)
        self.evidence = {}
        self.owner = User.objects.create_user(
            username="pylaia-smoke-%s" % os.urandom(4).hex(),
            email="pylaia-smoke-%s@example.test" % os.urandom(4).hex(),
            password="unused-test-password",
        )
        self.project = Project.objects.create(
            name="pylaia-operator-smoke",
            slug=os.urandom(6).hex(),
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
        created = [
            self._line(DECOY_EXTERNAL_ID, 8, 136, 72),
            self._line(None, 160, 288, 224),
        ]
        self.lines = list(self.part.lines.order_by("order", "pk"))
        if [line.pk for line in self.lines] != [line.pk for line in created]:
            raise AssertionError("synthetic lines were not stored in order")
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

    def test_audit_preview_apply_and_review_gate(self):
        endpoint = _endpoint()
        model_id = _model_id()
        self.assertTrue(_loopback_base(endpoint), "smoke endpoint must be loopback http")
        self.assertTrue(_LABEL.fullmatch(model_id), "smoke model id must be a plain label")
        self.assertEqual(ENGINE_NAME, "pylaia")
        self.assertEqual(_MODEL_VERSION, "0")
        record = _get_json(endpoint.rstrip("/") + "/v1/models/" + model_id)
        self.assertEqual(record.get("engine"), ENGINE_NAME)
        self.assertEqual(record.get("model_id"), model_id)
        self.assertEqual(record.get("model_version"), _MODEL_VERSION)
        self.assertEqual(record.get("licence"), _CONFIGURED_LICENCE)
        self.assertEqual(record.get("source"), _CONFIGURED_SOURCE)
        self.assertIs(record.get("experimental"), True)
        self._record(
            http_engine=record.get("engine"),
            http_model_id=record.get("model_id"),
            http_model_version=record.get("model_version"),
            http_licence=record.get("licence"),
            http_source=record.get("source"),
            http_experimental=record.get("experimental") is True,
        )

        layers_before = set(Transcription.objects.values_list("pk", flat=True))
        line_rows_before = LineTranscription.objects.count()
        self.assertEqual(layers_before, {self.manual.pk})
        self.assertEqual(line_rows_before, 1)
        self.assertEqual(ExternalHTRJob.objects.count(), 0)
        self.assertEqual(AILayerGate.objects.count(), 0)

        config = ExternalHTREngineConfig.objects.create(
            name="PyLaia smoke",
            endpoint_url=endpoint.rstrip("/"),
            enabled=True,
            tier=ExternalHTREngineConfig.TIER_RESEARCH,
            experimental=True,
            timeout_seconds=180,
            metadata={},
        )
        try:
            self._exercise(config, endpoint, model_id, layers_before, line_rows_before)
        finally:
            config.enabled = False
            config.save(update_fields=["enabled", "updated_at"])
            self.assertFalse(
                ExternalHTREngineConfig.objects.get(pk=config.pk).enabled,
            )
            self._record(config_disabled=True)

    def _exercise(self, config, endpoint, model_id, layers_before, line_rows_before):
        code, stdout, stderr = _run(
            "run_external_htr_audit",
            str(config.pk),
            str(self.part.pk),
            "--engine",
            ENGINE_NAME,
            "--model-id",
            model_id,
        )
        self._assert_quiet(stdout, stderr, endpoint)
        self.assertEqual(code, 0, "audit command failed: %s" % stdout.strip())
        job = ExternalHTRJob.objects.get()
        self.assertEqual(
            stdout,
            "OK: external HTR audit job %s completed results=2 skipped=0\n" % job.pk,
        )
        self._assert_completed_audit(job, config, model_id)
        self.assertEqual(
            set(Transcription.objects.values_list("pk", flat=True)),
            layers_before,
        )
        self.assertEqual(LineTranscription.objects.count(), line_rows_before)
        self.assertEqual(AILayerGate.objects.count(), 0)
        self.assertEqual(self._manual_state(), self.manual_state)
        self._record(
            audit_completed=True,
            audit_engine=job.engine,
            audit_model_id=job.model_id,
            audit_model_version=job.model_version,
            audit_api_version=job.api_version,
            result_count=job.result_count,
            empty_text_count=job.empty_text_count,
            text_empty=job.empty_text_count == job.result_count,
            skipped_line_count=job.skipped_line_count,
        )

        self.assertFalse(job.imported)
        audited_at = job.updated_at
        audited_lines = list(job.lines.order_by("position", "pk"))

        with self._no_engine_io():
            code, stdout, stderr = _run("import_external_htr_audit", str(job.pk))
        job.refresh_from_db()
        self._assert_quiet(stdout, stderr, endpoint)
        self.assertEqual(code, 0, "preview command failed: %s" % stdout.strip())
        self.assertEqual(
            stdout,
            "OK: external HTR import preview job=%s lines=2\n" % job.pk,
        )
        self.assertEqual(job.updated_at, audited_at)
        self.assertFalse(job.imported)
        self.assertIsNone(job.imported_transcription_id)
        preview_lines = list(job.lines.order_by("position", "pk"))
        self.assertEqual(len(preview_lines), len(audited_lines))
        self.assertTrue(
            all(
                left.pk == right.pk
                and left.position == right.position
                and left.line_id == right.line_id
                and left.status == right.status
                and left.text == right.text
                and list(left.warnings) == list(right.warnings)
                for left, right in zip(preview_lines, audited_lines)
            ),
            "preview changed an audit row",
        )
        self.assertEqual(
            set(Transcription.objects.values_list("pk", flat=True)),
            layers_before,
        )
        self.assertEqual(LineTranscription.objects.count(), line_rows_before)
        self.assertEqual(AILayerGate.objects.count(), 0)
        self.assertEqual(self._manual_state(), self.manual_state)
        self._record(preview_writes=False, preview_exit=code)

        with self._no_engine_io():
            code, stdout, stderr = _run(
                "import_external_htr_audit", str(job.pk), "--apply",
            )
        job.refresh_from_db()
        self._assert_quiet(stdout, stderr, endpoint)
        self.assertEqual(code, 0, "apply command failed: %s" % stdout.strip())
        self.assertTrue(job.imported)
        layer = job.imported_transcription
        self.assertIsNotNone(layer)
        self.assertEqual(
            stdout,
            "OK: external HTR import applied job=%s layer=%s lines=2\n" % (
                job.pk, layer.pk,
            ),
        )
        self.assertEqual(
            set(Transcription.objects.values_list("pk", flat=True)),
            layers_before | {layer.pk},
        )
        self.assertNotEqual(layer.pk, self.manual.pk)
        self.assertEqual(layer.document_id, self.document.pk)
        self.assertEqual(
            layer.name, _layer_name(job.pk, ENGINE_NAME, model_id),
        )
        self._assert_imported_text(job, layer)
        gate = AILayerGate.objects.get(transcription=layer)
        written = list(
            LineTranscription.objects.filter(transcription=layer).order_by("pk")
        )
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
        self._record(
            apply_exit=code,
            layer_created=True,
            gate_state=gate.state,
            sample_nonempty=bool(gate.sample_line_pks),
            import_marker=job.imported is True,
            training_blocked=True,
            manual_unchanged=True,
            text_exact=True,
        )

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
        self.assertEqual(
            reloaded.ai_gate.sample_line_pks, [line.pk for line in self.lines],
        )
        self.assertEqual(self._manual_state(), self.manual_state)
        self._record(training_check_passed=True, training_ran=False)

        layers_after = set(Transcription.objects.values_list("pk", flat=True))
        with self._no_engine_io():
            code, stdout, stderr = _run(
                "import_external_htr_audit", str(job.pk), "--apply",
            )
        job.refresh_from_db()
        self._assert_quiet(stdout, stderr, endpoint)
        self.assertEqual(code, 1, "repeat apply should be refused")
        self.assertEqual(stdout, "FAILED: external HTR import already_imported\n")
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
        self._record(repeat_apply_exit=code, repeat_apply_code="already_imported")

    def _line(self, external_id, top, bottom, baseline_y):
        line = Line.objects.create(
            document_part=self.part,
            baseline=[[10, baseline_y], [630, baseline_y]],
            mask=[
                [10, top],
                [630, top],
                [630, bottom],
                [10, bottom],
            ],
            external_id=external_id,
        )
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

    def _assert_completed_audit(self, job, config, model_id):
        self.assertEqual(job.status, ExternalHTRJob.STATUS_COMPLETED)
        self.assertEqual(job.document_id, self.document.pk)
        self.assertEqual(job.part_id, self.part.pk)
        self.assertEqual(job.config_id, config.pk)
        self.assertEqual(job.engine, ENGINE_NAME)
        self.assertEqual(job.model_id, model_id)
        self.assertEqual(job.model_version, _MODEL_VERSION)
        self.assertEqual(job.api_version, "1")
        self.assertEqual(
            job.layer_source, provenance_stamp(ENGINE_NAME, model_id),
        )
        self.assertEqual(job.requested_line_count, 2)
        self.assertEqual(job.skipped_line_count, 0)
        self.assertEqual(job.result_count, 2)
        self.assertEqual(job.warning_count, 0)
        self.assertEqual(job.code, "")
        self.assertEqual(job.message, "response would be written")
        self.assertFalse(job.imported)
        self.assertIsNone(job.imported_transcription_id)
        rows = list(job.lines.order_by("position", "pk"))
        self.assertEqual(len(rows), 2)
        empty = 0
        for position, (row, line) in enumerate(zip(rows, self.lines)):
            self.assertEqual(row.job_id, job.pk)
            self.assertEqual(row.position, position)
            self.assertEqual(row.line_id, str(line.pk))
            self.assertNotEqual(row.line_id, line.external_id)
            self.assertNotEqual(row.line_id, DECOY_EXTERNAL_ID)
            self.assertEqual(row.status, ExternalHTRLineResult.STATUS_INCLUDED)
            self.assertIsInstance(row.text, str)
            self.assertIsNone(row.confidence)
            self.assertEqual(row.timing_ms, 0)
            self.assertEqual(row.warnings, [])
            if row.text == "":
                empty += 1
        self.assertEqual(job.empty_text_count, empty)

    def _assert_imported_text(self, job, layer):
        written = {
            row.line_id: row
            for row in LineTranscription.objects.filter(transcription=layer)
        }
        self.assertEqual(set(written), {line.pk for line in self.lines})
        for line in self.lines:
            stored = written[line.pk]
            returned = job.lines.get(line_id=str(line.pk)).text
            self.assertTrue(
                stored.content == returned,
                "imported text does not match the stored audit row",
            )
            self.assertTrue(
                stored.content != MANUAL_TEXT,
                "imported text matched the manual canary",
            )
            self.assertEqual(stored.version_source, job.layer_source)
            self.assertEqual(stored.version_author, "")
            self.assertNotEqual(str(line.pk), line.external_id)

    def _assert_quiet(self, stdout, stderr, endpoint):
        self.assertEqual(stderr, "")
        blob = stdout + stderr
        for item in (
            SECRET_TITLE,
            SECRET_FILE,
            MANUAL_TEXT,
            DECOY_EXTERNAL_ID,
            endpoint,
            "iVBOR",
            "Traceback",
            "weights.ckpt",
            "/models",
            MEDIA,
            "unused-test-password",
        ):
            self.assertNotIn(item, blob, item)

    @contextmanager
    def _no_engine_io(self):
        """Fail if preview or apply opens an image or the network.

        Do not patch socket.create_connection. That breaks the database.
        """
        def reject(label):
            def _reject(*args, **kwargs):
                raise AssertionError(label)
            return _reject

        with patch("urllib.request.urlopen", side_effect=reject("urlopen")), \
                patch("urllib.request.build_opener", side_effect=reject("opener")), \
                patch(
                    "ai.htr_engine_client.urllib.request.urlopen",
                    side_effect=reject("client-urlopen"),
                ), \
                patch(
                    "ai.htr_engine_client.urllib.request.build_opener",
                    side_effect=reject("client-opener"),
                ), \
                patch("requests.get", side_effect=reject("get")), \
                patch("requests.post", side_effect=reject("post")), \
                patch("PIL.Image.open", side_effect=reject("image")):
            yield

    def _record(self, **fields):
        safe = {}
        for key, value in fields.items():
            if not isinstance(key, str) or not isinstance(value, (bool, int, str)):
                raise AssertionError("evidence field was not a scalar")
            if isinstance(value, str) and len(value) > 64:
                continue
            safe[key] = value
        self.evidence.update(safe)
        path = os.environ.get("PYLAIA_SMOKE_EVIDENCE")
        if not path:
            return
        temporary = path + ".tmp"
        try:
            with open(temporary, "w", encoding="utf-8") as handle:
                json.dump(self.evidence, handle, sort_keys=True)
                handle.write("\n")
            os.replace(temporary, path)
        except OSError:
            return
