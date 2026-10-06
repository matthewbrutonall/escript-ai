"""External HTR apply plan. No Django, no network, no transcription write."""
import ast
import unittest
from pathlib import Path

from ai.external_htr_apply_plan import (
    MESSAGE_INVALID,
    MESSAGE_READY,
    VERSION_SOURCE_LIMIT,
    plan_external_htr_apply,
)
from ai.htr_engine_contract import parse_recognize_request

AI_DIR = Path(__file__).resolve().parents[1]

IMAGE = "AAAA"
SECRET = "/tmp/secret-document"
TOKEN = "secret-token"
ENDPOINT = "http://127.0.0.1:8766/secret-endpoint"


def _request(line_ids=("10", "20"), **overrides):
    payload = {
        "api_version": "1",
        "job_id": "job-1",
        "document_id": 7,
        "part_id": 8,
        "engine": "pylaia",
        "model_id": "example-model",
        "preprocessing": {},
        "lines": [{"line_id": line_id, "image": IMAGE} for line_id in line_ids],
    }
    payload.update(overrides)
    return payload


def _result(line_id, text="hello", warnings=None, confidence=None, timing_ms=3):
    return {
        "line_id": line_id,
        "text": text,
        "confidence": confidence,
        "timing_ms": timing_ms,
        "warnings": [] if warnings is None else warnings,
    }


def _response(results, **overrides):
    engine = overrides.get("engine", "pylaia")
    model_id = overrides.get("model_id", "example-model")
    model_version = overrides.get("model_version", "1")
    payload = {
        "api_version": "1",
        "job_id": overrides.get("job_id", "job-1"),
        "engine": engine,
        "model_id": model_id,
        "model_version": model_version,
        "results": results,
        "provenance": {
            "engine": engine,
            "model_id": model_id,
            "model_version": model_version,
            "api_version": "1",
        },
        "timing_ms": 0,
        "resources": {},
    }
    payload.update(overrides)
    return payload


class ApplyPlanTests(unittest.TestCase):
    def test_valid_response_follows_request_order(self):
        request = _request(("10", "20", "30"))
        response = _response([
            _result("30", text="third", confidence=0.2, timing_ms=9),
            _result("10", text="first", confidence=0.5, timing_ms=4),
            _result("20", text="second"),
        ])
        plan = plan_external_htr_apply(request, response)
        self.assertTrue(plan.would_write)
        self.assertIsNone(plan.code)
        self.assertEqual(plan.message, MESSAGE_READY)
        self.assertEqual([item.line_id for item in plan.results], ["10", "20", "30"])
        self.assertEqual([item.text for item in plan.results], ["first", "second", "third"])
        self.assertEqual(plan.results[0].confidence, 0.5)
        self.assertEqual(plan.results[0].timing_ms, 4)
        self.assertEqual(plan.result_count, 3)
        self.assertEqual(plan.empty_text_count, 0)
        self.assertEqual(plan.engine, "pylaia")
        self.assertEqual(plan.model_id, "example-model")
        self.assertEqual(plan.model_version, "1")
        self.assertEqual(plan.api_version, "1")
        self.assertEqual(plan.layer_source, "external-htr:pylaia:example-model")

    def test_parsed_request_is_accepted(self):
        parsed = parse_recognize_request(_request(("10",)))
        plan = plan_external_htr_apply(parsed, _response([_result("10")]))
        self.assertTrue(plan.would_write)
        self.assertEqual(plan.results[0].line_id, "10")

    def test_missing_line_does_not_produce_a_write_plan(self):
        plan = plan_external_htr_apply(
            _request(("10", "20")),
            _response([_result("10")]),
        )
        self._assert_rejected(plan, "line_failed")
        self.assertNotIn("20", plan.message)

    def test_extra_line_does_not_produce_a_write_plan(self):
        plan = plan_external_htr_apply(
            _request(("10",)),
            _response([_result("10"), _result("99", text=SECRET)]),
        )
        self._assert_rejected(plan, "invalid_request")
        self.assertNotIn("99", plan.message)
        self.assertNotIn(SECRET, plan.message)

    def test_wrong_identity_does_not_produce_a_write_plan(self):
        request = _request(("10",))
        good = [_result("10")]
        cases = (
            {"job_id": "other-job"},
            {"engine": "other-engine"},
            {"model_id": "other-model"},
        )
        for overrides in cases:
            with self.subTest(overrides=overrides):
                plan = plan_external_htr_apply(request, _response(good, **overrides))
                self._assert_rejected(plan, "invalid_request")
                for value in overrides.values():
                    self.assertNotIn(value, plan.message)
                    self.assertNotIn(value, plan.layer_source)
                    self.assertNotIn(value, plan.engine)

    def test_empty_text_is_counted(self):
        plan = plan_external_htr_apply(
            _request(("10", "20")),
            _response([_result("10", text=""), _result("20", text="kept")]),
        )
        self.assertTrue(plan.would_write)
        self.assertEqual(plan.empty_text_count, 1)
        self.assertEqual(plan.results[0].text, "")
        self.assertEqual(plan.result_count, 2)

    def test_warnings_stay_on_the_line_and_out_of_the_message(self):
        plan = plan_external_htr_apply(
            _request(("10", "20")),
            _response([
                _result("10", warnings=["low ink", "faint"]),
                _result("20", warnings=[]),
            ]),
        )
        self.assertEqual(plan.results[0].warnings, ("low ink", "faint"))
        self.assertEqual(plan.results[1].warnings, ())
        self.assertEqual(plan.warning_count, 2)
        self.assertNotIn("low ink", plan.message)
        self.assertNotIn("faint", plan.message)
        self.assertEqual(plan.message, MESSAGE_READY)

    def test_layer_source_is_truncated_to_the_version_source_limit(self):
        engine = "e" * 80
        model_id = "m" * 80
        full = f"external-htr:{engine}:{model_id}"
        plan = plan_external_htr_apply(
            _request(("10",), engine=engine, model_id=model_id),
            _response([_result("10")], engine=engine, model_id=model_id),
        )
        self.assertTrue(plan.would_write)
        self.assertGreater(len(full), VERSION_SOURCE_LIMIT)
        self.assertEqual(len(plan.layer_source), VERSION_SOURCE_LIMIT)
        self.assertEqual(plan.layer_source, full[:VERSION_SOURCE_LIMIT])
        self.assertEqual(plan.engine, engine)
        self.assertEqual(plan.model_id, model_id)

    def test_secrets_stay_out_of_code_message_and_layer_source(self):
        request = _request(("10",))
        request["lines"][0]["image"] = "QUJDRA=="
        response = _response([_result("10", text="kept")])
        response["endpoint_url"] = ENDPOINT
        response["metadata"] = {"api_key": TOKEN}
        response["title"] = "Secret Title"
        response["raw"] = SECRET
        plan = plan_external_htr_apply(request, response)
        self._assert_rejected(plan, "invalid_request")
        for leaked in ("QUJDRA==", ENDPOINT, TOKEN, "Secret Title", SECRET, "api_key"):
            self.assertNotIn(leaked, plan.message)
            self.assertNotIn(leaked, plan.code or "")
            self.assertNotIn(leaked, plan.layer_source)
            self.assertNotIn(leaked, plan.engine)
            self.assertNotIn(leaked, plan.model_id)

    def _assert_rejected(self, plan, code):
        self.assertFalse(plan.would_write)
        self.assertEqual(plan.code, code)
        self.assertEqual(plan.message, MESSAGE_INVALID)
        self.assertEqual(plan.results, ())
        self.assertEqual(plan.result_count, 0)
        self.assertEqual(plan.warning_count, 0)
        self.assertEqual(plan.empty_text_count, 0)
        self.assertEqual(plan.layer_source, "")
        self.assertEqual(plan.engine, "")
        self.assertEqual(plan.model_id, "")
        self.assertEqual(plan.model_version, "")
        self.assertEqual(plan.api_version, "")


class IsolationTests(unittest.TestCase):
    def test_apply_plan_does_not_import_jobs_or_the_client(self):
        text = (AI_DIR / "external_htr_apply_plan.py").read_text()
        tree = ast.parse(text)
        modules = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.extend(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.append(node.module.split(".")[0])
        self.assertEqual(modules, ["__future__", "dataclasses", "ai"])
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
            "versioning",
        ):
            self.assertNotIn(banned, text)

    def test_stage1_modules_do_not_import_the_apply_plan(self):
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
            self.assertNotIn("external_htr_apply_plan", text, name)
            self.assertNotIn("plan_external_htr_apply", text, name)


if __name__ == "__main__":
    unittest.main()
