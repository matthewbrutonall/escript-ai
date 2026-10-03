"""PyLaia wrapper skeleton. No PyLaia, no GPU, no network, no model files."""
import ast
import json
import unittest
from pathlib import Path
from unittest import mock

from ai.external_engines.pylaia import ENGINE_NAME, UnavailablePyLaiaBackend, handle
from ai.external_engines.pylaia.backend import BackendFailure
from ai.external_engines.pylaia.server import respond, serve
from ai.htr_engine_contract import (
    parse_capabilities,
    parse_error,
    parse_model,
    parse_model_list,
    parse_recognize_response,
    parse_recognize_request,
)

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

    def test_injected_backend_returns_one_model_and_one_line(self):
        backend = _DemoBackend()
        status, listing = handle("GET", "/v1/models", backend=backend)
        self.assertEqual(status, 200)
        parsed = parse_model_list(listing)
        self.assertEqual(parsed.models[0].model_id, "demo")

        status, detail = handle("GET", "/v1/models/demo", backend=backend)
        self.assertEqual(status, 200)
        self.assertEqual(parse_model(detail).model_id, "demo")

        image = "QUJDRA=="
        raw = _request(image)
        status, body = respond("POST", "/v1/recognize", raw, backend)
        self.assertEqual(status, 200)
        response = parse_recognize_response(
            json.loads(body.decode("utf-8")),
            parse_recognize_request(json.loads(raw.decode("utf-8"))),
        )
        self.assertEqual(response.results[0].text, "demo")
        self.assertNotIn(image, body.decode("utf-8"))

    def test_unavailable_backend_stays_empty(self):
        backend = UnavailablePyLaiaBackend()
        status, listing = handle("GET", "/v1/models", backend=backend)
        self.assertEqual(status, 200)
        self.assertEqual(parse_model_list(listing).models, ())
        status, detail = handle("GET", f"/v1/models/{SECRET_MODEL}", backend=backend)
        self.assertEqual(status, 404)
        self.assertEqual(parse_error(detail).code, "model_not_found")
        self.assertNotIn(SECRET_MODEL, json.dumps(detail))
        status, body = handle("POST", "/v1/recognize", _request("AAAA"), backend=backend)
        self.assertEqual(status, 503)
        parsed = parse_error(body)
        self.assertEqual(parsed.code, "unavailable")
        self.assertEqual(parsed.message, "recognition backend is not installed")
        self.assertNotIn("AAAA", json.dumps(body))
        self.assertNotIn("results", body)

    def test_backend_exception_does_not_leak(self):
        class Boom:
            def capabilities(self):
                raise RuntimeError(SECRET_TEXT)

            def list_models(self):
                raise RuntimeError(SECRET_TEXT)

            def get_model(self, model_id):
                raise RuntimeError(model_id + SECRET_IMAGE)

            def recognize(self, request):
                raise RuntimeError(SECRET_TEXT + request.lines[0].image)

        status, body = handle("GET", "/v1/capabilities", backend=Boom())
        self.assertEqual(status, 500)
        self.assertEqual(parse_error(body).code, "internal")
        self.assertNotIn(SECRET_TEXT, json.dumps(body))

        status, body = handle("GET", f"/v1/models/{SECRET_MODEL}", backend=Boom())
        self.assertEqual(status, 500)
        encoded = json.dumps(body)
        self.assertEqual(parse_error(body).code, "internal")
        self.assertNotIn(SECRET_MODEL, encoded)
        self.assertNotIn(SECRET_IMAGE, encoded)

        image = "AAAA"
        status, body = handle("POST", "/v1/recognize", _request(image), backend=Boom())
        self.assertEqual(status, 500)
        encoded = json.dumps(body)
        self.assertEqual(parse_error(body).code, "internal")
        self.assertNotIn(image, encoded)
        self.assertNotIn(SECRET_TEXT, encoded)

    def test_non_loopback_host_is_refused_before_bind(self):
        with mock.patch("ai.external_engines.pylaia.server.HTTPServer") as server:
            with self.assertRaises(ValueError) as caught:
                serve("0.0.0.0", 8766)
            server.assert_not_called()
        self.assertNotIn("0.0.0.0", str(caught.exception))

    def test_serve_keeps_an_injected_backend_without_binding(self):
        backend = _DemoBackend()
        with mock.patch("ai.external_engines.pylaia.server.HTTPServer") as server:
            serve("127.0.0.1", 8766, backend)
        handler = server.call_args.args[1]
        self.assertIs(handler.bound_backend, backend)


class _DemoBackend:
    def capabilities(self):
        return {
            "api_version": "1",
            "engine": "pylaia",
            "tier": "research",
            "tasks": ["recognize_lines"],
            "accepts": ["image/png"],
            "image_transport": ["base64"],
            "max_lines_per_request": 32,
            "preprocessing_supported": [],
            "params_accepted": [],
            "reports": ["timing_ms", "confidence", "warnings"],
        }

    def list_models(self):
        return {"api_version": "1", "engine": "pylaia", "models": [self.get_model("demo")]}

    def get_model(self, model_id):
        if model_id != "demo":
            return BackendFailure("model_not_found")
        return {
            "api_version": "1",
            "engine": "pylaia",
            "model_id": "demo",
            "model_version": "0",
            "display_name": "Demo",
            "licence": "test-only",
            "source": "test:fake",
            "scripts": [],
            "languages": [],
            "input": {},
            "alphabet_note": "",
            "experimental": True,
        }

    def recognize(self, request):
        line_id = request.lines[0].line_id
        return {
            "api_version": "1",
            "job_id": request.job_id,
            "engine": request.engine,
            "model_id": request.model_id,
            "model_version": "0",
            "results": [{
                "line_id": line_id,
                "text": "demo",
                "confidence": None,
                "timing_ms": 0,
                "warnings": [],
            }],
            "provenance": {
                "engine": request.engine,
                "model_id": request.model_id,
                "model_version": "0",
                "api_version": "1",
            },
            "timing_ms": 0,
            "resources": {"device": "cpu"},
        }


class IsolationTests(unittest.TestCase):
    def test_skeleton_does_not_import_pylaia_or_open_files(self):
        package = AI_DIR / "external_engines" / "pylaia"
        for path in sorted(package.glob("*.py")):
            text = path.read_text()
            tree = ast.parse(text)
            modules = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    modules.extend(alias.name.split(".")[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    modules.append(node.module.split(".")[0])
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    self.assertNotIn(node.func.id, {"open", "socket"}, path.name)
            self.assertFalse({"laia", "torch"} & set(modules), path.name)
            self.assertNotIn("LINE ", text, path.name)
        tree = ast.parse((package / "engine.py").read_text())
        modules = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.extend(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.append(node.module.split(".")[0])
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

    def test_prototype_dockerfile_stays_loopback_and_unwired(self):
        dockerfile = (AI_DIR / "external_engines" / "pylaia" / "Dockerfile").read_text()
        readme = (AI_DIR / "external_engines" / "pylaia" / "README.md").read_text()
        self.assertIn("FROM python:3.10", dockerfile)
        self.assertIn("pylaia==1.1.2", dockerfile)
        self.assertIn("--host", dockerfile)
        self.assertIn("127.0.0.1", dockerfile)
        self.assertNotIn("0.0.0.0", dockerfile)
        self.assertNotIn("COPY .", dockerfile)
        for banned in ("weights.ckpt", "syms.txt", ".env", "API_KEY", "PASSWORD", "SECRET"):
            self.assertNotIn(banned, dockerfile, banned)
        self.assertIn("from escriptorium/app/apps/ai:", dockerfile)
        for snippet in (
            "COPY __init__.py /opt/engine/ai/__init__.py",
            "COPY htr_engine_contract.py /opt/engine/ai/htr_engine_contract.py",
            "COPY external_engines/__init__.py /opt/engine/ai/external_engines/__init__.py",
            "COPY external_engines/pylaia/__init__.py /opt/engine/ai/external_engines/pylaia/__init__.py",
            "COPY external_engines/pylaia/backend.py /opt/engine/ai/external_engines/pylaia/backend.py",
            "COPY external_engines/pylaia/engine.py /opt/engine/ai/external_engines/pylaia/engine.py",
            "COPY external_engines/pylaia/server.py /opt/engine/ai/external_engines/pylaia/server.py",
        ):
            self.assertIn(snippet, dockerfile, snippet)
        self.assertNotIn("COPY ai/", dockerfile)
        self.assertIn("is not built by default", readme)
        self.assertIn("Compose does not reference it", readme)
        self.assertIn("read-only model mount is future work", readme)
        self.assertIn("does not add a container bind mode", readme)
        self.assertIn("not reachable as a service yet", readme)


if __name__ == "__main__":
    unittest.main()
