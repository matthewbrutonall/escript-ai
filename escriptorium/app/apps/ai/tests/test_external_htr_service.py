"""Dry-run external HTR audit flow. No Django, no network, no transcription write."""
import ast
import unittest
from pathlib import Path

from ai.external_htr_apply_plan import MESSAGE_READY
from ai.external_htr_audit import LINE_INCLUDED, LINE_SKIPPED, STATUS_COMPLETED, STATUS_FAILED, STATUS_PLANNED
from ai.external_htr_plan import (
    CODE_DISABLED,
    CODE_NOTHING,
    MESSAGE_DISABLED,
    MESSAGE_NOTHING,
    MESSAGE_READY as PLAN_READY,
)
from ai.external_htr_service import (
    CODE_LIVE,
    MESSAGE_LIVE,
    ExternalHtrServiceError,
    run_external_htr_dry_run,
)

AI_DIR = Path(__file__).resolve().parents[1]
MASK = [[0, 0], [10, 0], [10, 4], [0, 4]]
BASELINE = [[0, 2], [10, 2]]
IMAGE = "QUJDRA=="
ENDPOINT = "http://127.0.0.1:9/secret-endpoint"
TOKEN = "secret-token"
TITLE = "Secret Title"
SECRET_PATH = "/tmp/secret-document"
RAW = "raw-response-body"
USERNAME = "secret-user"
RUNTIME = (
    "tasks.py",
    "views.py",
    "serializers.py",
    "pipeline.py",
    "backends.py",
    "dispatch.py",
    "models.py",
    "admin.py",
)
JOB_TEXT = (
    "model_id", "status", "layer_source", "engine", "model_version", "api_version",
    "code", "message", "requested_line_count", "skipped_line_count", "result_count",
    "warning_count", "empty_text_count",
)
LINE_TEXT = (
    "line_id", "position", "text", "confidence", "timing_ms", "warnings", "status", "code",
)
RESULT_TEXT = (
    "job_id", "status", "code", "message", "requested_line_count", "skipped_line_count",
    "result_count", "warning_count", "empty_text_count", "skips",
)


class _Atomic:
    def __init__(self):
        self.entered = 0

    def __call__(self):
        return self

    def __enter__(self):
        self.entered += 1
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _Row:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)
        self.saved = 0
        self.update_fields = None

    def save(self, update_fields=None):
        self.saved += 1
        self.update_fields = list(update_fields)


class _Objects:
    def __init__(self):
        self.rows = []

    def create(self, **kwargs):
        row = _Row(**kwargs)
        row.pk = len(self.rows) + 1
        self.rows.append(row)
        return row

    def filter(self, **kwargs):
        return [
            row for row in self.rows
            if all(getattr(row, key) == value for key, value in kwargs.items())
        ]


class _Model:
    def __init__(self):
        self.objects = _Objects()


class _Line:
    def __init__(self, pk, mask=MASK, baseline=BASELINE):
        self.pk = pk
        self.mask = mask
        self.baseline = baseline


class _Service:
    def __init__(self, enabled=True):
        self.jobs = _Model()
        self.lines = _Model()
        self.atomic = _Atomic()
        self.calls = []
        self.document = type("Document", (), {
            "pk": 7, "title": TITLE, "path": SECRET_PATH,
        })()
        self.part = type("Part", (), {"pk": 8, "filename": SECRET_PATH})()
        self.config = type("Config", (), {
            "pk": 3,
            "name": "PyLaia smoke",
            "enabled": enabled,
            "endpoint_url": ENDPOINT,
            "metadata": {"api_key": TOKEN},
        })()
        self.user = type("User", (), {"pk": 4, "username": USERNAME})()

    def encode(self, line):
        self.calls.append(line.pk)
        return IMAGE

    def run(self, lines, **overrides):
        kwargs = {
            "config": self.config,
            "lines": lines,
            "document": self.document,
            "part": self.part,
            "engine": "pylaia",
            "model_id": "example-model",
            "encode_line": self.encode,
            "preprocessing": {"grayscale": True},
            "max_lines": None,
            "created_by": self.user,
            "job_model": self.jobs,
            "line_model": self.lines,
            "atomic": self.atomic,
        }
        kwargs.update(overrides)
        return run_external_htr_dry_run(**kwargs)


def _stored(job, lines, result=None):
    parts = [str(getattr(job, name)) for name in JOB_TEXT]
    for line in lines:
        parts.extend(str(getattr(line, name)) for name in LINE_TEXT)
    if result is not None:
        parts.extend(str(getattr(result, name)) for name in RESULT_TEXT)
    return "\n".join(parts)


def _response(line_ids, texts):
    return {
        "api_version": "1",
        "job_id": "plan",
        "engine": "pylaia",
        "model_id": "example-model",
        "model_version": "1",
        "results": [
            {
                "line_id": line_id,
                "text": text,
                "confidence": 0.5,
                "timing_ms": 4,
                "warnings": [],
            }
            for line_id, text in zip(line_ids, texts)
        ],
        "provenance": {
            "engine": "pylaia",
            "model_id": "example-model",
            "model_version": "1",
            "api_version": "1",
        },
        "timing_ms": 0,
        "resources": {},
    }


class DryRunTests(unittest.TestCase):
    def test_usable_lines_create_a_planned_audit_job(self):
        service = _Service()
        result = service.run([_Line(10), _Line(20, mask=None)])
        job = service.jobs.objects.rows[0]
        self.assertEqual(service.calls, [10])
        self.assertEqual(len(service.jobs.objects.rows), 1)
        self.assertEqual(result.job_id, job.pk)
        self.assertEqual(result.status, STATUS_PLANNED)
        self.assertEqual(result.code, "")
        self.assertEqual(result.message, PLAN_READY)
        self.assertEqual(result.requested_line_count, 1)
        self.assertEqual(result.skipped_line_count, 1)
        self.assertEqual(result.result_count, 0)
        self.assertEqual(result.skips, (("20", "no_mask"),))
        self.assertEqual(job.document, service.document)
        self.assertEqual(job.part, service.part)
        self.assertEqual(job.config, service.config)
        self.assertEqual(job.created_by, service.user)
        self.assertEqual(
            [(row.line_id, row.status, row.code) for row in service.lines.objects.rows],
            [("20", LINE_SKIPPED, "no_mask")],
        )
        text = _stored(job, service.lines.objects.rows, result)
        for secret in (IMAGE, ENDPOINT, TOKEN, TITLE, SECRET_PATH, USERNAME):
            self.assertNotIn(secret, text)
        self.assertFalse(hasattr(result, "request"))

    def test_disabled_config_fails_without_encoding(self):
        service = _Service(enabled=False)
        result = service.run([_Line(10), _Line(20)])
        job = service.jobs.objects.rows[0]
        self.assertEqual(service.calls, [])
        self.assertEqual(result.status, STATUS_FAILED)
        self.assertEqual(result.code, CODE_DISABLED)
        self.assertEqual(result.message, MESSAGE_DISABLED)
        self.assertEqual(result.requested_line_count, 0)
        self.assertEqual(result.skipped_line_count, 0)
        self.assertEqual(job.code, CODE_DISABLED)
        self.assertEqual(service.lines.objects.rows, [])
        text = _stored(job, [], result)
        for secret in (IMAGE, ENDPOINT, TOKEN, TITLE, SECRET_PATH):
            self.assertNotIn(secret, text)

    def test_all_skipped_lines_fail_with_skipped_rows(self):
        service = _Service()
        result = service.run([
            _Line(10, mask=None),
            _Line(20, baseline=None),
        ])
        job = service.jobs.objects.rows[0]
        self.assertEqual(service.calls, [])
        self.assertEqual(result.status, STATUS_FAILED)
        self.assertEqual(result.code, CODE_NOTHING)
        self.assertEqual(result.message, MESSAGE_NOTHING)
        self.assertEqual(result.requested_line_count, 0)
        self.assertEqual(result.skipped_line_count, 2)
        self.assertEqual(result.skips, (("10", "no_mask"), ("20", "no_baseline")))
        self.assertEqual(
            [(row.line_id, row.code) for row in service.lines.objects.rows],
            [("10", "no_mask"), ("20", "no_baseline")],
        )
        self.assertEqual(job.status, STATUS_FAILED)

    def test_live_mode_is_rejected_before_encoding(self):
        service = _Service()
        response = _response(("10",), (RAW,))
        response["endpoint_url"] = ENDPOINT
        with self.assertRaises(ExternalHtrServiceError) as caught:
            service.run(
                [_Line(10)],
                dry_run=False,
                response=response,
            )
        self.assertEqual(caught.exception.code, CODE_LIVE)
        self.assertEqual(str(caught.exception), MESSAGE_LIVE)
        self.assertEqual(service.calls, [])
        self.assertEqual(service.jobs.objects.rows, [])
        self.assertEqual(service.lines.objects.rows, [])
        self.assertEqual(service.atomic.entered, 0)
        for secret in (IMAGE, ENDPOINT, TOKEN, TITLE, SECRET_PATH, RAW):
            self.assertNotIn(secret, str(caught.exception))

    def test_explicit_response_finalizes_the_audit_job(self):
        service = _Service()
        response = _response(("20", "10"), ("", "kept"))
        response["results"][1]["warnings"] = ["low ink"]
        result = service.run([_Line(10), _Line(20)], response=response)
        job = service.jobs.objects.rows[0]
        included = [
            row for row in service.lines.objects.rows if row.status == LINE_INCLUDED
        ]
        self.assertEqual(len(service.jobs.objects.rows), 1)
        self.assertEqual(result.status, STATUS_COMPLETED)
        self.assertEqual(result.code, "")
        self.assertEqual(result.message, MESSAGE_READY)
        self.assertEqual(result.requested_line_count, 2)
        self.assertEqual(result.result_count, 2)
        self.assertEqual(result.warning_count, 1)
        self.assertEqual(result.empty_text_count, 1)
        self.assertEqual(result.skips, ())
        self.assertEqual(job.layer_source, "external-htr:pylaia:example-model")
        self.assertEqual(job.model_version, "1")
        self.assertEqual(job.api_version, "1")
        self.assertIn("updated_at", job.update_fields)
        self.assertEqual([row.line_id for row in included], ["10", "20"])
        self.assertEqual([row.text for row in included], ["kept", ""])
        text = _stored(job, service.lines.objects.rows, result)
        for secret in (IMAGE, ENDPOINT, TOKEN, TITLE, SECRET_PATH, RAW):
            self.assertNotIn(secret, text)


class IsolationTests(unittest.TestCase):
    def test_service_does_not_import_the_client_or_the_write_path(self):
        text = (AI_DIR / "external_htr_service.py").read_text()
        tree = ast.parse(text)
        top = []
        for node in tree.body:
            if isinstance(node, ast.ImportFrom) and node.module:
                top.append(node.module)
        self.assertEqual(top, [
            "__future__",
            "dataclasses",
            "ai.external_htr_apply_plan",
            "ai.external_htr_audit",
            "ai.external_htr_plan",
        ])
        for banned in (
            "htr_engine_client",
            "recognize_lines",
            "LineTranscription",
            "ai_transcribe",
            "endpoint_url",
            "urllib",
            "requests",
            "celery",
            "socket",
            "crop_line",
            "base64",
            "django",
        ):
            self.assertNotIn(banned, text, banned)

    def test_stage1_modules_do_not_import_the_service(self):
        for name in RUNTIME:
            text = (AI_DIR / name).read_text()
            self.assertNotIn("external_htr_service", text, name)
            self.assertNotIn("run_external_htr_dry_run", text, name)

    def test_admin_stays_list_only(self):
        text = (AI_DIR / "admin.py").read_text()
        self.assertNotIn("external_htr_service", text)
        self.assertNotIn("run_external_htr_dry_run", text)
        tree = ast.parse(text)
        found = set()
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            if node.name not in {"ExternalHTRJobAdmin", "ExternalHTRLineResultAdmin"}:
                continue
            found.add(node.name)
            dumped = ast.dump(node)
            for banned in ("probe_configs", "htr_engine_client", "recognize_lines", "actions"):
                self.assertNotIn(banned, dumped, node.name)
        self.assertEqual(found, {"ExternalHTRJobAdmin", "ExternalHTRLineResultAdmin"})


if __name__ == "__main__":
    unittest.main()
