"""Read-only external HTR client. HTTP is mocked. No Django, no network."""
import ast
import io
import json
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from ai.htr_engine_client import (
    EngineClientError,
    _MAX_BODY,
    _NoRedirect,
    _request,
    fetch_capabilities,
    fetch_model,
    fetch_models,
    join_engine_url,
    recognize_lines,
)
from ai.htr_engine_contract import PATH_CAPABILITIES, PATH_MODEL, PATH_MODELS, ContractError

AI_DIR = Path(__file__).resolve().parents[1]
APPS_DIR = AI_DIR.parent

CAPABILITIES = {
    "api_version": "1",
    "engine": "example",
    "tier": "production",
    "tasks": ["recognize_lines"],
    "accepts": ["image/png"],
    "image_transport": ["base64"],
    "max_lines_per_request": 8,
    "preprocessing_supported": ["line_height"],
    "params_accepted": [],
    "reports": ["timing_ms"],
}

MODEL = {
    "api_version": "1",
    "engine": "example",
    "model_id": "example-model",
    "model_version": "1",
    "display_name": "Example",
    "licence": "unknown",
    "source": "local:example",
    "scripts": [],
    "languages": [],
    "input": {"line_height": 64},
    "alphabet_note": "",
    "experimental": False,
}


class Config:
    def __init__(self, **overrides):
        self.enabled = True
        self.endpoint_url = "http://engine.example/base/"
        self.timeout_seconds = 12
        self.metadata = {"token": "secret-token"}
        for key, value in overrides.items():
            setattr(self, key, value)


def _body(payload) -> bytes:
    return json.dumps(payload).encode("utf-8")


class JoinTests(unittest.TestCase):
    def test_trailing_slash_is_ignored(self):
        self.assertEqual(
            join_engine_url("http://engine.example/base/", PATH_CAPABILITIES),
            "http://engine.example/base/v1/capabilities",
        )
        self.assertEqual(
            join_engine_url("http://engine.example/base", PATH_MODELS),
            "http://engine.example/base/v1/models",
        )

    def test_userinfo_and_query_are_rejected_without_echoing_them(self):
        for url in (
            "http://user:secret-token@engine.example/base",
            "http://engine.example/base?token=secret-token",
            "http://engine.example/base#secret-token",
        ):
            with self.assertRaises(EngineClientError) as caught:
                join_engine_url(url, PATH_CAPABILITIES)
            self.assertEqual(caught.exception.code, "invalid_request")
            self.assertNotIn("secret-token", str(caught.exception))


class FetchTests(unittest.TestCase):
    def test_capabilities_success(self):
        with mock.patch("ai.htr_engine_client._request", return_value=(200, _body(CAPABILITIES))) as request:
            parsed = fetch_capabilities(Config())
        self.assertEqual(parsed.engine, "example")
        url, timeout = request.call_args.args
        self.assertEqual(url, "http://engine.example/base/v1/capabilities")
        self.assertEqual(timeout, 12)
        self.assertNotIn("secret-token", url)

    def test_models_success(self):
        payload = {"api_version": "1", "engine": "example", "models": [MODEL]}
        with mock.patch("ai.htr_engine_client._request", return_value=(200, _body(payload))) as request:
            parsed = fetch_models(Config(endpoint_url="http://engine.example/base"))
        self.assertEqual(parsed.models[0].model_id, "example-model")
        self.assertEqual(request.call_args.args[0], "http://engine.example/base/v1/models")

    def test_model_detail_success_quotes_the_id(self):
        payload = dict(MODEL)
        payload["model_id"] = "a/b"
        with mock.patch("ai.htr_engine_client._request", return_value=(200, _body(payload))) as request:
            parsed = fetch_model(Config(), "a/b")
        self.assertEqual(parsed.model_id, "a/b")
        self.assertEqual(
            request.call_args.args[0],
            "http://engine.example/base" + PATH_MODEL.format(model_id="a%2Fb"),
        )

    def test_disabled_engine_is_rejected_before_http(self):
        with mock.patch("ai.htr_engine_client._request") as request:
            with self.assertRaises(EngineClientError) as caught:
                fetch_capabilities(Config(enabled=False, metadata={"token": "secret-token"}))
        self.assertEqual(caught.exception.code, "disabled")
        self.assertNotIn("secret-token", str(caught.exception))
        request.assert_not_called()

    def test_bad_json_does_not_include_the_body(self):
        with mock.patch("ai.htr_engine_client._request", return_value=(200, b"{secret-token")):
            with self.assertRaises(EngineClientError) as caught:
                fetch_capabilities(Config())
        self.assertEqual(caught.exception.code, "invalid_response")
        self.assertNotIn("secret-token", str(caught.exception))

    def test_contract_error_body_is_mapped(self):
        payload = {
            "api_version": "1",
            "error": {
                "code": "model_not_found",
                "message": "missing",
                "retryable": False,
                "line_id": None,
            },
        }
        with mock.patch("ai.htr_engine_client._request", return_value=(404, _body(payload))):
            with self.assertRaises(ContractError) as caught:
                fetch_model(Config(), "example-model")
        self.assertEqual(caught.exception.code, "model_not_found")

    def test_http_error_body_is_not_returned(self):
        with mock.patch("ai.htr_engine_client._request", return_value=(500, b"secret-token")):
            with self.assertRaises(EngineClientError) as caught:
                fetch_models(Config())
        self.assertEqual(caught.exception.code, "http_error")
        self.assertEqual(caught.exception.status, 500)
        self.assertNotIn("secret-token", str(caught.exception))


class RequestTests(unittest.TestCase):
    def test_timeout_is_a_fixed_message(self):
        opener = mock.Mock()
        opener.open.side_effect = TimeoutError("timed out talking to secret-token")
        with mock.patch("ai.htr_engine_client.urllib.request.build_opener", return_value=opener):
            with self.assertRaises(EngineClientError) as caught:
                _request("http://engine.example/v1/capabilities", 9)
        self.assertEqual(caught.exception.code, "timeout")
        self.assertNotIn("secret-token", str(caught.exception))
        self.assertEqual(opener.open.call_args.kwargs["timeout"], 9)

    def test_network_failure_is_unavailable(self):
        opener = mock.Mock()
        opener.open.side_effect = urllib.error.URLError(OSError("down secret-token"))
        with mock.patch("ai.htr_engine_client.urllib.request.build_opener", return_value=opener):
            with self.assertRaises(EngineClientError) as caught:
                _request("http://engine.example/v1/models", 9)
        self.assertEqual(caught.exception.code, "unavailable")
        self.assertNotIn("secret-token", str(caught.exception))

    def test_redirect_is_not_followed(self):
        with self.assertRaises(EngineClientError) as caught:
            _NoRedirect().redirect_request(None, None, 302, "found", {}, "http://other.example/secret-token")
        self.assertEqual(caught.exception.code, "invalid_response")
        self.assertNotIn("secret-token", str(caught.exception))

    def test_http_error_returns_status_and_body_bytes(self):
        err = urllib.error.HTTPError(
            "http://engine.example/v1/models",
            503,
            "unavailable",
            hdrs={"Content-Type": "application/json"},
            fp=io.BytesIO(b"{}"),
        )
        opener = mock.Mock()
        opener.open.side_effect = err
        with mock.patch("ai.htr_engine_client.urllib.request.build_opener", return_value=opener):
            status, body = _request("http://engine.example/v1/models", 9)
        self.assertEqual(status, 503)
        self.assertEqual(body, b"{}")


RECOGNIZE_REQUEST = {
    "api_version": "1",
    "job_id": "job-1",
    "document_id": "123",
    "part_id": "456",
    "engine": "example",
    "model_id": "example-model",
    "preprocessing": {"line_height": 64},
    "lines": [{"line_id": "1", "image": "AAAA"}],
}

RECOGNIZE_RESPONSE = {
    "api_version": "1",
    "job_id": "job-1",
    "engine": "example",
    "model_id": "example-model",
    "model_version": "1",
    "results": [{
        "line_id": "1",
        "text": "hello",
        "confidence": None,
        "timing_ms": 0,
        "warnings": [],
    }],
    "provenance": {
        "engine": "example",
        "model_id": "example-model",
        "model_version": "1",
        "api_version": "1",
    },
    "timing_ms": 1,
    "resources": {},
}


class RecognizeTests(unittest.TestCase):
    def test_success_posts_the_contract_path(self):
        with mock.patch(
            "ai.htr_engine_client._request",
            return_value=(200, _body(RECOGNIZE_RESPONSE)),
        ) as request, mock.patch(
            "ai.htr_engine_client.urllib.request.build_opener",
            side_effect=AssertionError("socket opened"),
        ):
            parsed = recognize_lines(Config(), RECOGNIZE_REQUEST)
        self.assertEqual(parsed.results[0].text, "hello")
        url, timeout = request.call_args.args
        self.assertEqual(request.call_args.kwargs["method"], "POST")
        self.assertTrue(url.endswith("/v1/recognize"))
        self.assertEqual(timeout, 12)
        self.assertIn(b"AAAA", request.call_args.kwargs["data"])

    def test_disabled_config_is_rejected_before_the_request(self):
        with mock.patch("ai.htr_engine_client._request") as request:
            with self.assertRaises(EngineClientError) as caught:
                recognize_lines(Config(enabled=False), RECOGNIZE_REQUEST)
        self.assertEqual(caught.exception.code, "disabled")
        self.assertNotIn("AAAA", str(caught.exception))
        self.assertNotIn("secret.example", str(caught.exception))
        request.assert_not_called()

    def test_bad_json_does_not_include_the_body(self):
        with mock.patch("ai.htr_engine_client._request", return_value=(200, b"{AAAA")):
            with self.assertRaises(EngineClientError) as caught:
                recognize_lines(Config(), RECOGNIZE_REQUEST)
        self.assertEqual(caught.exception.code, "invalid_response")
        self.assertNotIn("AAAA", str(caught.exception))

    def test_contract_error_hides_the_engine_message(self):
        payload = {
            "api_version": "1",
            "error": {
                "code": "line_failed",
                "message": "failed AAAA http://secret.example/token",
                "retryable": False,
                "line_id": "1",
            },
        }
        with mock.patch("ai.htr_engine_client._request", return_value=(422, _body(payload))):
            with self.assertRaises(ContractError) as caught:
                recognize_lines(Config(), RECOGNIZE_REQUEST)
        self.assertEqual(caught.exception.code, "line_failed")
        self.assertEqual(str(caught.exception), "engine rejected the recognition request")
        self.assertNotIn("AAAA", str(caught.exception))
        self.assertNotIn("secret.example", str(caught.exception))

    def test_http_error_hides_the_body(self):
        with mock.patch("ai.htr_engine_client._request", return_value=(500, b"AAAA secret.example")):
            with self.assertRaises(EngineClientError) as caught:
                recognize_lines(Config(), RECOGNIZE_REQUEST)
        self.assertEqual(caught.exception.code, "http_error")
        self.assertEqual(caught.exception.status, 500)
        self.assertNotIn("AAAA", str(caught.exception))
        self.assertNotIn("secret.example", str(caught.exception))

    def test_oversized_response_is_rejected(self):
        with mock.patch(
            "ai.htr_engine_client._request",
            return_value=(200, b"x" * (_MAX_BODY + 1)),
        ):
            with self.assertRaises(EngineClientError) as caught:
                recognize_lines(Config(), RECOGNIZE_REQUEST)
        self.assertEqual(caught.exception.code, "invalid_response")
        self.assertEqual(str(caught.exception), "engine response is too large")

    def test_oversized_request_is_rejected_before_http(self):
        payload = dict(RECOGNIZE_REQUEST)
        payload["lines"] = [{"line_id": "1", "image": "A" * 80}]
        with mock.patch("ai.htr_engine_client._MAX_REQUEST", 64), mock.patch(
            "ai.htr_engine_client._request",
        ) as request:
            with self.assertRaises(EngineClientError) as caught:
                recognize_lines(Config(), payload)
        self.assertEqual(caught.exception.code, "invalid_request")
        self.assertEqual(str(caught.exception), "recognition request is too large")
        self.assertNotIn("AAAA", str(caught.exception))
        request.assert_not_called()


class IsolationTests(unittest.TestCase):
    def test_client_does_not_read_metadata(self):
        text = (AI_DIR / "htr_engine_client.py").read_text()
        self.assertNotIn("logging", text)
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                self.assertNotEqual(node.attr, "metadata")
        modules = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.extend(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.append(node.module.split(".")[0])
        self.assertEqual(set(modules), {"__future__", "json", "urllib", "ai"})
        for banned in ("pylaia", "trocr", "drethtr", "requests", "httpx", "django"):
            self.assertNotIn(banned, text.lower())

    def test_stage1_modules_do_not_reference_the_client(self):
        paths = [
            AI_DIR / "tasks.py",
            AI_DIR / "views.py",
            AI_DIR / "pipeline.py",
            AI_DIR / "backends.py",
            AI_DIR / "dispatch.py",
            AI_DIR / "serializers.py",
            AI_DIR / "models.py",
            APPS_DIR / "api" / "urls.py",
        ]
        for path in paths:
            text = path.read_text()
            self.assertNotIn("htr_engine_client", text, path)
            self.assertNotIn("recognize_lines", text, path)


if __name__ == "__main__":
    unittest.main()
