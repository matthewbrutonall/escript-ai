"""Reference engine. Calls the handler directly. No socket, no Django."""
import ast
import json
import unittest
from pathlib import Path

from ai.htr_engine_contract import (
    parse_capabilities,
    parse_error,
    parse_model,
    parse_model_list,
    parse_recognize_request,
    parse_recognize_response,
)
from ai.reference_engine import MODEL_ID, handle

AI_DIR = Path(__file__).resolve().parents[1]
APPS_DIR = AI_DIR.parent

REQUEST = {
    "api_version": "1",
    "job_id": "job-1",
    "document_id": "9",
    "part_id": "8",
    "engine": "reference",
    "model_id": MODEL_ID,
    "preprocessing": {"line_height": 64},
    "lines": [
        {"line_id": "7", "image": "AAAA"},
        {"line_id": "8", "image": "BBBB"},
    ],
}


def _post(payload) -> tuple[int, dict]:
    return handle("POST", "/v1/recognize", json.dumps(payload).encode("utf-8"))


class ReferenceEngineTests(unittest.TestCase):
    def test_capabilities_and_models(self):
        status, body = handle("GET", "/v1/capabilities")
        self.assertEqual(status, 200)
        caps = parse_capabilities(body)
        self.assertEqual(caps.engine, "reference")
        self.assertEqual(caps.tier, "research")

        status, listing = handle("GET", "/v1/models")
        self.assertEqual(status, 200)
        parsed = parse_model_list(listing)
        self.assertEqual(parsed.models[0].model_id, MODEL_ID)
        self.assertTrue(parsed.models[0].experimental)

        status, model = handle("GET", f"/v1/models/{MODEL_ID}")
        self.assertEqual(status, 200)
        self.assertEqual(parse_model(model).model_id, MODEL_ID)

    def test_recognize_returns_placeholder_text(self):
        status, body = _post(REQUEST)
        self.assertEqual(status, 200)
        parsed = parse_recognize_response(body, parse_recognize_request(REQUEST))
        self.assertEqual(
            tuple(line.text for line in parsed.results),
            ("LINE 7", "LINE 8"),
        )
        other = dict(REQUEST)
        other["lines"] = [
            {"line_id": "7", "image": "CCCC"},
            {"line_id": "8", "image": "DDDD"},
        ]
        again = _post(other)[1]
        self.assertEqual(
            tuple(line["text"] for line in again["results"]),
            ("LINE 7", "LINE 8"),
        )

    def test_invalid_recognize_request_is_a_contract_error(self):
        payload = dict(REQUEST)
        payload["lines"] = [{"line_id": "7", "image": "@@@@"}]
        status, body = _post(payload)
        self.assertEqual(status, 400)
        parsed = parse_error(body)
        self.assertEqual(parsed.code, "invalid_request")
        encoded = json.dumps(body)
        self.assertNotIn("@@@@", encoded)
        self.assertNotIn("AAAA", encoded)

    def test_unknown_model_is_not_found(self):
        status, body = handle("GET", "/v1/models/other")
        self.assertEqual(status, 404)
        self.assertEqual(parse_error(body).code, "model_not_found")
        self.assertNotIn("other", json.dumps(body))


class IsolationTests(unittest.TestCase):
    def test_engine_does_not_open_a_socket_or_write_files(self):
        text = (AI_DIR / "reference_engine" / "engine.py").read_text()
        tree = ast.parse(text)
        modules = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.extend(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.append(node.module.split(".")[0])
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                self.assertNotIn(node.func.id, {"open", "socket"})
        self.assertEqual(set(modules), {"__future__", "json", "ai"})

    def test_stage1_modules_do_not_import_the_reference_engine(self):
        paths = [
            AI_DIR / "tasks.py",
            AI_DIR / "views.py",
            AI_DIR / "pipeline.py",
            AI_DIR / "backends.py",
            AI_DIR / "dispatch.py",
            AI_DIR / "serializers.py",
            AI_DIR / "models.py",
            AI_DIR / "admin.py",
            AI_DIR / "htr_engine_client.py",
            APPS_DIR / "api" / "urls.py",
        ]
        for path in paths:
            self.assertNotIn("reference_engine", path.read_text(), path)


if __name__ == "__main__":
    unittest.main()
