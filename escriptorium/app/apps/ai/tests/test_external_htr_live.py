"""External HTR live recognition helper. No audit write, no transcription write."""
import ast
import unittest
from pathlib import Path

from ai.external_htr_live import (
    MESSAGE_APPLY,
    MESSAGE_CLIENT,
    MESSAGE_CONTRACT,
    MESSAGE_DISABLED,
    MESSAGE_READY,
    run_external_htr_live,
)
from ai.htr_engine_client import EngineClientError
from ai.htr_engine_contract import ContractError, parse_recognize_response

AI_DIR = Path(__file__).resolve().parents[1]
IMAGE = "QUJDRA=="
ENDPOINT = "http://127.0.0.1:9/secret-endpoint"
TOKEN = "secret-token"
TITLE = "Secret Title"
SECRET_PATH = "/tmp/secret-document"
RAW = "raw-response-body"
PARSER = "parser exploded at lines[0]"
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
UNWIRED = (
    "management/commands/plan_external_htr.py",
    "external_htr_plan_command.py",
    "external_htr_service.py",
    "tasks.py",
)


class _Config:
    def __init__(self, enabled=True):
        self.enabled = enabled
        self.name = "PyLaia smoke"
        self.endpoint_url = ENDPOINT
        self.metadata = {"api_key": TOKEN}
        self.title = TITLE
        self.path = SECRET_PATH


def _request():
    return {
        "api_version": "1",
        "job_id": "job-1",
        "document_id": 7,
        "part_id": 8,
        "engine": "pylaia",
        "model_id": "example-model",
        "preprocessing": {},
        "lines": [{
            "line_id": "10",
            "image": IMAGE,
            "baseline": [[0, 2], [10, 2]],
            "mask": [[0, 0], [10, 0], [10, 4], [0, 4]],
        }],
    }


def _response(text="hello", line_id="10"):
    return {
        "api_version": "1",
        "job_id": "job-1",
        "engine": "pylaia",
        "model_id": "example-model",
        "model_version": "1",
        "results": [{
            "line_id": line_id,
            "text": text,
            "confidence": None,
            "timing_ms": 0,
            "warnings": [],
        }],
        "provenance": {
            "engine": "pylaia",
            "model_id": "example-model",
            "model_version": "1",
            "api_version": "1",
        },
        "timing_ms": 0,
        "resources": {},
    }


def _secrets():
    return (IMAGE, ENDPOINT, TOKEN, TITLE, SECRET_PATH, RAW, PARSER)


class LiveCallTests(unittest.TestCase):
    def test_success_calls_recognize_once_and_returns_an_apply_plan(self):
        request = _request()
        seen = []

        def recognize(config, payload):
            seen.append((config, payload))
            return _response()

        config = _Config()
        result = run_external_htr_live(config, request, recognize_lines=recognize)
        self.assertEqual(len(seen), 1)
        self.assertIs(seen[0][0], config)
        self.assertIs(seen[0][1], request)
        self.assertTrue(result.ok)
        self.assertIsNone(result.code)
        self.assertEqual(result.message, MESSAGE_READY)
        self.assertTrue(result.apply_plan.would_write)
        self.assertEqual(result.apply_plan.results[0].text, "hello")
        self.assertEqual(result.apply_plan.results[0].line_id, "10")
        self._assert_clean(result)

    def test_parsed_client_response_returns_an_apply_plan(self):
        request = _request()
        parsed = parse_recognize_response(_response(text="kept"), None)

        def recognize(config, payload):
            return parsed

        result = run_external_htr_live(_Config(), request, recognize_lines=recognize)
        self.assertTrue(result.ok)
        self.assertEqual(result.apply_plan.results[0].text, "kept")
        self._assert_clean(result)

    def test_disabled_config_returns_before_recognize(self):
        def recognize(config, payload):
            raise AssertionError("recognize was called")

        for enabled in (False, 1, None):
            result = run_external_htr_live(
                _Config(enabled=enabled),
                _request(),
                recognize_lines=recognize,
            )
            self.assertFalse(result.ok)
            self.assertEqual(result.code, "disabled")
            self.assertEqual(result.message, MESSAGE_DISABLED)
            self.assertIsNone(result.apply_plan)
            self._assert_clean(result)

    def test_client_error_is_a_fixed_failure(self):
        def recognize(config, payload):
            raise EngineClientError(
                "http_error",
                ENDPOINT + TOKEN + RAW + SECRET_PATH + " exploded",
                status=500,
            )

        result = run_external_htr_live(_Config(), _request(), recognize_lines=recognize)
        self.assertFalse(result.ok)
        self.assertEqual(result.code, "http_error")
        self.assertEqual(result.message, MESSAGE_CLIENT)
        self.assertIsNone(result.apply_plan)
        self._assert_clean(result)

    def test_contract_error_is_a_fixed_failure(self):
        def recognize(config, payload):
            raise ContractError("invalid_request", PARSER + ENDPOINT + IMAGE + RAW)

        result = run_external_htr_live(_Config(), _request(), recognize_lines=recognize)
        self.assertFalse(result.ok)
        self.assertEqual(result.code, "invalid_request")
        self.assertEqual(result.message, MESSAGE_CONTRACT)
        self.assertIsNone(result.apply_plan)
        self._assert_clean(result)

    def test_rejected_apply_plan_hides_the_response(self):
        def recognize(config, payload):
            return _response(text=RAW + SECRET_PATH + TITLE, line_id="999")

        result = run_external_htr_live(_Config(), _request(), recognize_lines=recognize)
        self.assertFalse(result.ok)
        self.assertEqual(result.code, "invalid_request")
        self.assertEqual(result.message, MESSAGE_APPLY)
        self.assertIsNone(result.apply_plan)
        self._assert_clean(result)

    def _assert_clean(self, result):
        text = "\n".join((
            result.message,
            "" if result.code is None else result.code,
            "" if result.apply_plan is None else repr(result.apply_plan),
            repr(result),
        ))
        for secret in _secrets():
            self.assertNotIn(secret, text, secret)
        self.assertNotIn("response", result.__dict__ if hasattr(result, "__dict__") else ())


class IsolationTests(unittest.TestCase):
    def test_helper_does_not_import_audit_or_the_write_path(self):
        text = (AI_DIR / "external_htr_live.py").read_text()
        tree = ast.parse(text)
        top = []
        for node in tree.body:
            if isinstance(node, ast.ImportFrom) and node.module:
                top.append(node.module)
        self.assertEqual(top, [
            "__future__",
            "dataclasses",
            "ai.external_htr_apply_plan",
            "ai.htr_engine_client",
            "ai.htr_engine_contract",
        ])
        for banned in (
            "external_htr_audit",
            "record_external_htr_apply",
            "record_external_htr_plan",
            "LineTranscription",
            "ai_transcribe",
            "crop_line",
            "endpoint_url",
            "urllib",
            "requests",
            "celery",
            "socket",
            "django",
            "external_htr_image",
        ):
            self.assertNotIn(banned, text, banned)

    def test_command_and_stage1_do_not_import_the_helper(self):
        for name in UNWIRED:
            text = (AI_DIR / name).read_text()
            self.assertNotIn("external_htr_live", text, name)
            self.assertNotIn("run_external_htr_live", text, name)
        command = (AI_DIR / "management/commands/plan_external_htr.py").read_text()
        self.assertIn("encode_line=no_line_image", command)
        self.assertIn("dry_run=True", command)
        self.assertNotIn("--live", command)
        for name in RUNTIME:
            text = (AI_DIR / name).read_text()
            self.assertNotIn("external_htr_live", text, name)
            self.assertNotIn("run_external_htr_live", text, name)


if __name__ == "__main__":
    unittest.main()
