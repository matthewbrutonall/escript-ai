"""Reference engine HTTP adapter. No socket, no Django."""
import ast
import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from ai.htr_engine_contract import parse_capabilities, parse_error
from ai.reference_engine.server import main, read_body, respond, serve

AI_DIR = Path(__file__).resolve().parents[1]
APPS_DIR = AI_DIR.parent
REPO = Path(__file__).resolve().parents[5]

SECRET_IMAGE = "@@@@"
SECRET_TEXT = "secret transcription alpha"


def _recognize(image: str, text: str) -> bytes:
    payload = {
        "api_version": "1",
        "job_id": "job-1",
        "document_id": "9",
        "part_id": "8",
        "engine": "reference",
        "model_id": "reference-line",
        "preprocessing": {"line_height": 64},
        "lines": [{"line_id": "7", "image": image, "text": text}],
    }
    return json.dumps(payload).encode("utf-8")


class AdapterTests(unittest.TestCase):
    def test_capabilities_are_json_with_status(self):
        status, raw = respond("GET", "/v1/capabilities", b"")
        self.assertEqual(status, 200)
        caps = parse_capabilities(json.loads(raw.decode("utf-8")))
        self.assertEqual(caps.engine, "reference")
        self.assertEqual(caps.tier, "research")

    def test_unsupported_method_and_path_use_the_contract_error(self):
        for method, path in (("PUT", "/v1/capabilities"), ("GET", "/v1/no-such")):
            status, raw = respond(method, path, b"")
            self.assertEqual(status, 400, path)
            parsed = parse_error(json.loads(raw.decode("utf-8")))
            self.assertEqual(parsed.code, "invalid_request")
            self.assertNotIn(path, raw.decode("utf-8"))

    def test_invalid_body_does_not_leak_image_or_text(self):
        status, raw = respond("POST", "/v1/recognize", _recognize(SECRET_IMAGE, SECRET_TEXT))
        text = raw.decode("utf-8")
        self.assertEqual(status, 400)
        self.assertEqual(parse_error(json.loads(text)).code, "invalid_request")
        self.assertNotIn(SECRET_IMAGE, text)
        self.assertNotIn(SECRET_TEXT, text)

    def test_bad_content_length_is_not_echoed(self):
        source = io.BytesIO(SECRET_TEXT.encode("utf-8"))
        body = read_body(source, "not-a-length")
        status, raw = respond("POST", "/v1/recognize", body)
        text = raw.decode("utf-8")
        self.assertEqual(source.tell(), 0)
        self.assertEqual(status, 400)
        self.assertNotIn(SECRET_TEXT, text)
        self.assertNotIn("not-a-length", text)

    def test_non_loopback_host_is_refused_before_bind(self):
        with mock.patch("ai.reference_engine.server.HTTPServer") as server:
            with self.assertRaises(ValueError) as caught:
                serve("0.0.0.0", 8765)
            server.assert_not_called()
        self.assertNotIn("0.0.0.0", str(caught.exception))

    def test_default_command_uses_loopback(self):
        with mock.patch("ai.reference_engine.server.HTTPServer") as server:
            code = main(["--port", "8765"])
        self.assertEqual(code, 0)
        self.assertEqual(server.call_args.args[0], ("127.0.0.1", 8765))

    def test_ipv6_loopback_is_refused_without_traceback(self):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            with mock.patch("ai.reference_engine.server.HTTPServer") as server:
                code = main(["--host", "::1", "--port", "8765"])
        text = stderr.getvalue()
        self.assertEqual(code, 2)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(text, "reference engine server binds to loopback only\n")
        self.assertNotIn("::1", text)
        self.assertNotIn("Traceback", text)
        server.assert_not_called()

    def test_bind_oserror_is_a_fixed_message(self):
        leak = "Address already in use /tmp/secret-model-path"
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            with mock.patch(
                "ai.reference_engine.server.HTTPServer",
                side_effect=OSError(98, leak),
            ) as server:
                code = main(["--port", "8765"])
        text = stderr.getvalue()
        self.assertEqual(code, 2)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(text, "reference engine server failed to bind\n")
        self.assertNotIn(leak, text)
        self.assertNotIn("Address already in use", text)
        self.assertNotIn("Traceback", text)
        self.assertNotIn("Errno", text)
        server.assert_called_once()
        with mock.patch(
            "ai.reference_engine.server.HTTPServer",
            side_effect=OSError(98, leak),
        ):
            with self.assertRaises(ValueError) as caught:
                serve("127.0.0.1", 8765)
        self.assertEqual(str(caught.exception), "reference engine server failed to bind")
        self.assertIsNone(caught.exception.__cause__)
        self.assertTrue(caught.exception.__suppress_context__)
        self.assertNotIn(leak, str(caught.exception))


class IsolationTests(unittest.TestCase):
    def test_server_module_does_not_log_or_write_files(self):
        text = (AI_DIR / "reference_engine" / "server.py").read_text()
        self.assertNotIn("logging", text)
        self.assertIn("Not a recognizer", text)
        self.assertIn("not for production", text)
        tree = ast.parse(text)
        modules = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.extend(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.append(node.module.split(".")[0])
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                self.assertNotIn(node.func.id, {"open", "socket"})
        self.assertEqual(set(modules), {"__future__", "argparse", "json", "sys", "http", "ai"})

    def test_stage1_and_startup_do_not_import_the_server(self):
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
            APPS_DIR / "api" / "urls.py",
            REPO / "escriptorium" / "docker-compose.yml",
            REPO / "escriptorium" / "docker-compose.preview.yml",
            REPO / "escriptorium" / "Dockerfile",
            REPO / "escriptorium" / "app" / "Dockerfile",
            REPO / "escriptorium" / "app" / "manage.py",
        ]
        for path in paths:
            text = path.read_text()
            self.assertNotIn("reference_engine.server", text, path)
            self.assertNotIn("reference_engine/server", text, path)


if __name__ == "__main__":
    unittest.main()
