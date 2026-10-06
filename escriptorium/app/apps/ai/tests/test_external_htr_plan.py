"""External HTR plan helper. No Django, no network, no transcription write."""
import ast
import unittest
from pathlib import Path

from ai.external_htr_plan import (
    CODE_DISABLED,
    CODE_INVALID,
    CODE_NOTHING,
    MESSAGE_DISABLED,
    MESSAGE_INVALID,
    MESSAGE_NOTHING,
    MESSAGE_READY,
    plan_external_htr,
)
from ai.htr_engine_contract import parse_recognize_request

AI_DIR = Path(__file__).resolve().parents[1]

MASK = [[0, 0], [10, 0], [10, 4], [0, 4]]
BASELINE = [[0, 2], [10, 2]]
IMAGE = "AAAA"
SECRET = "/tmp/secret-document"
TOKEN = "secret-token"
ENDPOINT = "http://127.0.0.1:8766/secret-endpoint"


class _Line:
    def __init__(self, pk, mask=MASK, baseline=BASELINE, **extra):
        self.pk = pk
        self.mask = mask
        self.baseline = baseline
        for key, value in extra.items():
            setattr(self, key, value)


class _Config:
    def __init__(self, enabled=True, name="PyLaia smoke", **extra):
        self.enabled = enabled
        self.name = name
        for key, value in extra.items():
            setattr(self, key, value)


def _encode(line):
    return IMAGE


def _plan(lines, encode_line=_encode, config=None, **overrides):
    kwargs = {
        "document_id": 7,
        "part_id": 8,
        "engine": "pylaia",
        "model_id": "example-model",
        "encode_line": encode_line,
        "preprocessing": {"grayscale": True},
    }
    kwargs.update(overrides)
    return plan_external_htr(config if config is not None else _Config(), lines, **kwargs)


class PlanTests(unittest.TestCase):
    def test_disabled_config_does_not_encode(self):
        calls = []

        def encode(line):
            calls.append(line.pk)
            return IMAGE

        plan = _plan([_Line(1), _Line(2)], encode_line=encode, config=_Config(enabled=False))
        self.assertEqual(calls, [])
        self.assertFalse(plan.would_send)
        self.assertIsNone(plan.request)
        self.assertEqual(plan.included_count, 0)
        self.assertEqual(plan.skipped, ())
        self.assertEqual(plan.code, CODE_DISABLED)
        self.assertEqual(plan.message, MESSAGE_DISABLED)
        self.assertEqual(plan.config_name, "PyLaia smoke")
        self.assertEqual(plan.engine, "pylaia")
        self.assertEqual(plan.model_id, "example-model")

    def test_truthy_non_bool_enabled_does_not_encode(self):
        calls = []

        def encode(line):
            calls.append(line.pk)
            return IMAGE

        plan = _plan([_Line(1)], encode_line=encode, config=_Config(enabled=1))
        self.assertEqual(calls, [])
        self.assertEqual(plan.code, CODE_DISABLED)

    def test_valid_lines_plan_a_request(self):
        calls = []

        def encode(line):
            calls.append(line.pk)
            return IMAGE

        plan = _plan([_Line(10), _Line(20, mask=None), _Line(30)], encode_line=encode)
        parsed = parse_recognize_request(plan.request)
        self.assertTrue(plan.would_send)
        self.assertIsNone(plan.code)
        self.assertEqual(plan.message, MESSAGE_READY)
        self.assertEqual(plan.included_count, 2)
        self.assertEqual([line.line_id for line in parsed.lines], ["10", "30"])
        self.assertEqual(calls, [10, 30])
        self.assertEqual([(item.line_id, item.reason) for item in plan.skipped], [("20", "no_mask")])
        self.assertEqual(plan.engine, "pylaia")
        self.assertEqual(plan.config_name, "PyLaia smoke")
        self.assertEqual(plan.model_id, "example-model")
        self.assertEqual(plan.request["job_id"], "plan")

    def test_all_skipped_lines_send_nothing(self):
        plan = _plan([_Line(1, mask=None), _Line(2, baseline=None)])
        self.assertFalse(plan.would_send)
        self.assertIsNone(plan.request)
        self.assertEqual(plan.included_count, 0)
        self.assertEqual(plan.code, CODE_NOTHING)
        self.assertEqual(plan.message, MESSAGE_NOTHING)
        self.assertEqual(
            [(item.line_id, item.reason) for item in plan.skipped],
            [("1", "no_mask"), ("2", "no_baseline")],
        )

    def test_bad_preprocessing_is_a_fixed_failure_and_does_not_encode(self):
        calls = []

        def encode(line):
            calls.append(line.pk)
            return IMAGE

        plan = _plan(
            [_Line(1)],
            encode_line=encode,
            preprocessing={SECRET: True, "colour": TOKEN},
        )
        self.assertEqual(calls, [])
        self.assertFalse(plan.would_send)
        self.assertIsNone(plan.request)
        self.assertEqual(plan.included_count, 0)
        self.assertEqual(plan.skipped, ())
        self.assertEqual(plan.code, CODE_INVALID)
        self.assertEqual(plan.message, MESSAGE_INVALID)
        self.assertNotIn(SECRET, plan.message)
        self.assertNotIn(TOKEN, plan.message)
        self.assertNotIn("unknown fields", plan.message)

    def test_messages_do_not_copy_document_or_endpoint_secrets(self):
        line = _Line(
            4,
            title="Secret Title",
            path=SECRET,
            metadata={"token": TOKEN},
            name="folio-name",
        )
        config = _Config(
            endpoint_url=ENDPOINT,
            metadata={"api_key": TOKEN},
            path=SECRET,
            title="Secret Title",
        )
        plan = _plan([line], config=config)
        for leaked in ("Secret Title", SECRET, TOKEN, ENDPOINT, "folio-name", IMAGE):
            self.assertNotIn(leaked, plan.message)
            self.assertNotIn(leaked, plan.code or "")
            self.assertNotIn(leaked, plan.engine)
            self.assertNotIn(leaked, plan.config_name)
            self.assertNotIn(leaked, plan.model_id)
            for item in plan.skipped:
                self.assertNotIn(leaked, item.reason)
                self.assertNotIn(leaked, item.line_id or "")
        self.assertEqual(plan.request["lines"][0]["image"], IMAGE)
        self.assertNotIn(ENDPOINT, str(plan.request))
        self.assertNotIn("Secret Title", str(plan.request))
        self.assertNotIn(TOKEN, str(plan.request))


class IsolationTests(unittest.TestCase):
    def test_plan_does_not_import_the_client_or_jobs(self):
        text = (AI_DIR / "external_htr_plan.py").read_text()
        tree = ast.parse(text)
        modules = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.extend(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.append(node.module.split(".")[0])
        self.assertEqual(modules, ["__future__", "dataclasses", "ai", "ai"])
        for banned in (
            "htr_engine_client",
            "recognize_lines",
            "LineTranscription",
            "ai_transcribe",
            "ExternalHTREngineConfig",
            "endpoint_url",
            "urllib",
            "requests",
            "celery",
            "socket",
        ):
            self.assertNotIn(banned, text)

    def test_stage1_modules_do_not_import_the_plan(self):
        for name in (
            "tasks.py",
            "views.py",
            "serializers.py",
            "pipeline.py",
            "backends.py",
            "dispatch.py",
            "models.py",
            "admin.py",
        ):
            text = (AI_DIR / name).read_text()
            self.assertNotIn("external_htr_plan", text, name)
            self.assertNotIn("plan_external_htr", text, name)


if __name__ == "__main__":
    unittest.main()
