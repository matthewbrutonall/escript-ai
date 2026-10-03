"""PyLaia wrapper skeleton. No PyLaia, no GPU, no network, no model files."""
import ast
import json
import unittest
from pathlib import Path
from unittest import mock

from ai.external_engines.pylaia import ENGINE_NAME, handle
from ai.external_engines.pylaia.server import respond, serve
from ai.htr_engine_contract import parse_capabilities, parse_error, parse_model_list

AI_DIR = Path(__file__).resolve().parents[1]
APPS_DIR = AI_DIR.parent
REPO = Path(__file__).resolve().parents[5]

SECRET_IMAGE = "@@@@"
SECRET_TEXT = "secret transcription alpha"
SECRET_MODEL = "secret-model"


def _request(image: str, text: str | None = None, **overrides) -> bytes:
    line = {"line_id": "7", "image": image}
    if text is not None:
        line["text"] = text
    payload = {
        "api_version": "1",
        "job_id": "job-1",
        "document_id": "9",
        "part_id": "8",
        "engine": ENGINE_NAME,
        "model_id": "later",
        "preprocessing": {},
        "lines": [line],
    }
    payload.update(overrides)
    return json.dumps(payload).encode("utf-8")


class SkeletonTests(unittest.TestCase):
    def test_capabilities_are_research_and_not_a_production_claim(self):
        status, body = handle("GET", "/v1/capabilities")
        self.assertEqual(status, 200)
        caps = parse_capabilities(body)
        self.assertEqual(caps.engine, "pylaia")
        self.assertEqual(caps.tier, "research")
        self.assertEqual(caps.preprocessing_supported, ())

    def test_model_list_is_empty(self):
        status, body = handle("GET", "/v1/models")
        self.assertEqual(status, 200)
        parsed = parse_model_list(body)
        self.assertEqual(parsed.engine, "pylaia")
        self.assertEqual(parsed.models, ())

    def test_unknown_model_is_not_found_and_is_not_echoed(self):
        status, body = handle("GET", f"/v1/models/{SECRET_MODEL}")
        self.assertEqual(status, 404)
        self.assertEqual(parse_error(body).code, "model_not_found")
        self.assertNotIn(SECRET_MODEL, json.dumps(body))

    def test_recognize_is_unavailable_and_has_no_text(self):
        status, body = handle("POST", "/v1/recognize", _request("AAAA"))
        self.assertEqual(status, 503)
        parsed = parse_error(body)
        self.assertEqual(parsed.code, "unavailable")
        self.assertEqual(parsed.message, "recognition backend is not installed")
        self.assertTrue(parsed.retryable)
        self.assertNotIn("results", body)
        self.assertNotIn("AAAA", json.dumps(body))

    def test_invalid_recognize_does_not_echo_image_or_text(self):
        status, body = handle("POST", "/v1/recognize", _request(SECRET_IMAGE, SECRET_TEXT))
        encoded = json.dumps(body)
        self.assertEqual(status, 400)
        self.assertEqual(parse_error(body).code, "invalid_request")
        self.assertNotIn(SECRET_IMAGE, encoded)
        self.assertNotIn(SECRET_TEXT, encoded)

    def test_http_adapter_returns_the_same_unavailable_error(self):
        status, raw = respond("POST", "/v1/recognize", _request("AAAA"))
        self.assertEqual(status, 503)
        self.assertEqual(parse_error(json.loads(raw.decode("utf-8"))).code, "unavailable")
        self.assertNotIn("AAAA", raw.decode("utf-8"))

    def test_non_loopback_host_is_refused_before_bind(self):
        with mock.patch("ai.external_engines.pylaia.server.HTTPServer") as server:
            with self.assertRaises(ValueError) as caught:
                serve("0.0.0.0", 8766)
            server.assert_not_called()
        self.assertNotIn("0.0.0.0", str(caught.exception))


class IsolationTests(unittest.TestCase):
    def test_skeleton_does_not_import_pylaia_or_open_files(self):
        text = (AI_DIR / "external_engines" / "pylaia" / "engine.py").read_text()
        self.assertNotIn("import laia", text)
        self.assertNotIn("import torch", text)
        self.assertNotIn("LINE ", text)
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

    def test_stage1_and_startup_do_not_import_the_skeleton(self):
        paths = [
            AI_DIR / "tasks.py",
            AI_DIR / "views.py",
            AI_DIR / "pipeline.py",
            AI_DIR / "backends.py",
            AI_DIR / "dispatch.py",
            AI_DIR / "serializers.py",
            AI_DIR / "models.py",
            AI_DIR / "admin.py",
            AI_DIR / "apps.py",
            AI_DIR / "htr_engine_client.py",
            AI_DIR / "htr_engine_contract_check.py",
            AI_DIR / "management" / "commands" / "check_external_htr_engine.py",
            AI_DIR / "reference_engine" / "__init__.py",
            AI_DIR / "reference_engine" / "server.py",
            APPS_DIR / "api" / "urls.py",
            REPO / "escriptorium" / "docker-compose.yml",
            REPO / "escriptorium" / "docker-compose.preview.yml",
            REPO / "escriptorium" / "Dockerfile",
            REPO / "escriptorium" / "app" / "Dockerfile",
            REPO / "escriptorium" / "app" / "manage.py",
        ]
        for path in paths:
            text = path.read_text()
            self.assertNotIn("external_engines", text, path)
            self.assertNotIn("pylaia", text, path)


if __name__ == "__main__":
    unittest.main()
