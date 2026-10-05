"""Explicit PyLaia server backend selection. No live decode and no model files."""
import ast
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from ai.external_engines.pylaia.backend import DecodePyLaiaBackend
from ai.external_engines.pylaia.engine import handle
from ai.external_engines.pylaia.server import main, serve
from ai.htr_engine_contract import parse_error

AI_DIR = Path(__file__).resolve().parents[1]
APPS_DIR = AI_DIR.parent
REPO = Path(__file__).resolve().parents[5]
SECRET = "secret-model-path"
_REQUIRED = "decode backend requires model-dir, model-id, and work-root\n"
_FLAGS = "decode flags require --backend decode\n"
_REJECTED = "decode backend was rejected\n"
_LOOPBACK = "pylaia skeleton binds to loopback only\n"


def _bundle(root: Path) -> Path:
    model_dir = root / "bundle"
    model_dir.mkdir()
    (model_dir / "model").write_bytes(b"architecture")
    (model_dir / "syms.txt").write_text("<ctc> 0\n", encoding="utf-8")
    (model_dir / "weights.ckpt").write_bytes(b"checkpoint")
    return model_dir


def _invoke(argv: list[str]):
    stdout = io.StringIO()
    stderr = io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        with mock.patch("ai.external_engines.pylaia.server.HTTPServer") as server:
            code = main(argv)
    return code, stdout.getvalue(), stderr.getvalue(), server


def _recognize() -> bytes:
    return json.dumps({
        "api_version": "1",
        "job_id": "job-1",
        "document_id": "9",
        "part_id": "8",
        "engine": "pylaia",
        "model_id": "later",
        "preprocessing": {},
        "lines": [{"line_id": "7", "image": "AAAA"}],
    }).encode("utf-8")


def _decode_args(
    model_dir: Path,
    work_root: Path,
    timeout: str | None = None,
) -> list[str]:
    argv = [
        "--backend",
        "decode",
        "--model-dir",
        str(model_dir),
        "--model-id",
        "huginmunin",
        "--work-root",
        str(work_root),
    ]
    if timeout is not None:
        argv.extend(["--timeout", timeout])
    return argv


class ServerBackendTests(unittest.TestCase):
    def test_default_server_uses_unavailable_backend(self):
        code, stdout, stderr, server = _invoke([])
        self.assertEqual((code, stdout, stderr), (0, "", ""))
        handler = server.call_args.args[1]
        self.assertIsNone(handler.bound_backend)
        self.assertEqual(server.call_args.args[0], ("127.0.0.1", 8766))
        status, body = handle("GET", "/v1/models", backend=handler.bound_backend)
        self.assertEqual(status, 200)
        self.assertEqual(body["models"], [])
        status, body = handle(
            "POST",
            "/v1/recognize",
            _recognize(),
            backend=handler.bound_backend,
        )
        self.assertEqual(status, 503)
        self.assertEqual(parse_error(body).code, "unavailable")
        with mock.patch("ai.external_engines.pylaia.server.check_model_layout") as check:
            again, _, _, _ = _invoke([])
        self.assertEqual(again, 0)
        check.assert_not_called()

    def test_explicit_unavailable_matches_the_default(self):
        code, stdout, stderr, server = _invoke(["--backend", "unavailable"])
        self.assertEqual((code, stdout, stderr), (0, "", ""))
        self.assertIsNone(server.call_args.args[1].bound_backend)
        self.assertEqual(server.call_args.args[0], ("127.0.0.1", 8766))

    def test_unavailable_rejects_decode_flags_without_echo(self):
        cases = (
            ["--model-dir", f"/tmp/{SECRET}"],
            ["--backend", "unavailable", "--model-id", SECRET],
            ["--backend", "unavailable", "--work-root", f"/tmp/{SECRET}"],
            ["--timeout", "180"],
            [
                "--backend",
                "unavailable",
                "--model-dir",
                f"/tmp/{SECRET}",
                "--model-id",
                SECRET,
                "--work-root",
                f"/tmp/{SECRET}",
                "--timeout",
                "180",
            ],
        )
        for argv in cases:
            code, stdout, stderr, server = _invoke(argv)
            self.assertEqual((code, stdout, stderr), (2, "", _FLAGS), argv)
            self.assertNotIn(SECRET, stderr)
            self.assertNotIn("Traceback", stderr)
            server.assert_not_called()

    def test_decode_without_required_flags_fails_without_echo(self):
        cases = (
            ["--backend", "decode"],
            ["--backend", "decode", "--model-id", "huginmunin", "--work-root", "/tmp/work"],
            ["--backend", "decode", "--model-dir", f"/tmp/{SECRET}", "--work-root", "/tmp/work"],
            ["--backend", "decode", "--model-dir", f"/tmp/{SECRET}", "--model-id", "huginmunin"],
            ["--backend", "decode", "--model-dir", "  ", "--model-id", "huginmunin", "--work-root", "/tmp/work"],
        )
        for argv in cases:
            code, stdout, stderr, server = _invoke(argv)
            self.assertEqual((code, stdout, stderr), (2, "", _REQUIRED), argv)
            self.assertNotIn(SECRET, stderr)
            self.assertNotIn("Traceback", stderr)
            server.assert_not_called()

    def test_invalid_model_dir_fails_without_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            work = root / "work"
            work.mkdir()
            missing = root / SECRET
            code, stdout, stderr, server = _invoke(_decode_args(missing, work))
            self.assertEqual((code, stdout, stderr), (2, "", "FAILED: unsafe_path\n"))
            self.assertNotIn(SECRET, stderr)
            self.assertNotIn(str(missing), stderr)
            server.assert_not_called()

            empty = root / "empty"
            empty.mkdir()
            code, stdout, stderr, server = _invoke(_decode_args(empty, work))
            self.assertEqual((code, stdout, stderr), (2, "", "FAILED: missing_model\n"))
            self.assertNotIn(str(empty), stderr)
            self.assertNotIn(SECRET, stderr)
            server.assert_not_called()

        code, stdout, stderr, server = _invoke([
            "--backend",
            "decode",
            "--model-dir",
            SECRET,
            "--model-id",
            "huginmunin",
            "--work-root",
            "/tmp",
        ])
        self.assertEqual((code, stdout, stderr), (2, "", "FAILED: unsafe_path\n"))
        self.assertNotIn(SECRET, stderr)
        server.assert_not_called()

    def test_bad_model_id_and_work_root_fail_without_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            model_dir = _bundle(root)
            work = root / "work"
            work.mkdir()
            code, stdout, stderr, server = _invoke([
                "--backend",
                "decode",
                "--model-dir",
                str(model_dir),
                "--model-id",
                f"../{SECRET}",
                "--work-root",
                str(work),
            ])
            self.assertEqual((code, stdout, stderr), (2, "", _REJECTED))
            self.assertNotIn(SECRET, stderr)
            self.assertNotIn(str(model_dir), stderr)
            server.assert_not_called()

            code, stdout, stderr, server = _invoke([
                "--backend",
                "decode",
                "--model-dir",
                str(model_dir),
                "--model-id",
                "huginmunin",
                "--work-root",
                SECRET,
            ])
            self.assertEqual((code, stdout, stderr), (2, "", _REJECTED))
            self.assertNotIn(SECRET, stderr)
            self.assertNotIn(str(model_dir), stderr)
            server.assert_not_called()

            nested = model_dir / "nested"
            nested.mkdir()
            code, stdout, stderr, server = _invoke(_decode_args(model_dir, nested))
            self.assertEqual((code, stdout, stderr), (2, "", _REJECTED))
            self.assertNotIn(str(model_dir), stderr)
            self.assertNotIn(str(nested), stderr)
            server.assert_not_called()

            for timeout in ("0", "3601"):
                code, stdout, stderr, server = _invoke(
                    _decode_args(model_dir, work, timeout)
                )
                self.assertEqual((code, stdout, stderr), (2, "", _REJECTED), timeout)
                self.assertNotIn(str(model_dir), stderr)
                server.assert_not_called()

    def test_valid_model_dir_constructs_decode_backend(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            model_dir = _bundle(root)
            work = root / "work"
            work.mkdir()
            with mock.patch("ai.external_engines.pylaia.decoder.subprocess.run") as process:
                code, stdout, stderr, server = _invoke(
                    _decode_args(model_dir, work, "180")
                )
            process.assert_not_called()
        self.assertEqual((code, stdout, stderr), (0, "", ""))
        backend = server.call_args.args[1].bound_backend
        self.assertIsInstance(backend, DecodePyLaiaBackend)
        self.assertEqual(backend._model_id, "huginmunin")
        self.assertEqual(backend._model_dir, model_dir.resolve())
        self.assertEqual(backend._work_root, work.resolve())
        self.assertEqual(backend._runner.timeout_seconds, 180)
        self.assertNotIn("huginmunin", str(backend._model_dir))

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            model_dir = _bundle(root)
            work = root / "work"
            work.mkdir()
            code, stdout, stderr, server = _invoke(_decode_args(model_dir, work))
        self.assertEqual((code, stdout, stderr), (0, "", ""))
        backend = server.call_args.args[1].bound_backend
        self.assertEqual(backend._runner.timeout_seconds, 30)

    def test_container_bind_rules_still_apply_in_decode_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            model_dir = _bundle(root)
            work = root / "work"
            work.mkdir()
            refused = _decode_args(model_dir, work)
            refused.extend(["--host", "0.0.0.0"])
            code, stdout, stderr, server = _invoke(refused)
            self.assertEqual((code, stdout, stderr), (2, "", _LOOPBACK))
            self.assertNotIn("0.0.0.0", stderr)
            self.assertNotIn(str(model_dir), stderr)
            server.assert_not_called()

            allowed = _decode_args(model_dir, work)
            allowed.extend(["--host", "0.0.0.0", "--allow-container-bind"])
            code, stdout, stderr, server = _invoke(allowed)
            self.assertEqual((code, stdout, stderr), (0, "", ""))
            self.assertEqual(server.call_args.args[0], ("0.0.0.0", 8766))
            self.assertIsInstance(
                server.call_args.args[1].bound_backend,
                DecodePyLaiaBackend,
            )

        with mock.patch("ai.external_engines.pylaia.server.HTTPServer") as server:
            with self.assertRaises(ValueError) as caught:
                serve("0.0.0.0", 8766, allow_container_bind=1)
            server.assert_not_called()
        self.assertEqual(str(caught.exception), "pylaia skeleton binds to loopback only")
        self.assertNotIn("0.0.0.0", str(caught.exception))

    def test_environment_does_not_select_decode(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            model_dir = _bundle(root)
            work = root / "work"
            work.mkdir()
            with mock.patch.dict(
                os.environ,
                {
                    "PYLAIA_BACKEND": "decode",
                    "PYLAIA_MODEL_DIR": str(model_dir),
                    "PYLAIA_MODEL_ID": "huginmunin",
                    "PYLAIA_WORK_ROOT": str(work),
                    "PYLAIA_TIMEOUT": "180",
                },
            ):
                code, stdout, stderr, server = _invoke([])
                partial, partial_out, partial_err, partial_server = _invoke([
                    "--backend",
                    "decode",
                    "--model-id",
                    "huginmunin",
                    "--work-root",
                    str(work),
                ])
        self.assertEqual((code, stdout, stderr), (0, "", ""))
        self.assertIsNone(server.call_args.args[1].bound_backend)
        self.assertEqual((partial, partial_out, partial_err), (2, "", _REQUIRED))
        self.assertNotIn(str(model_dir), partial_err)
        self.assertNotIn(SECRET, partial_err)
        partial_server.assert_not_called()
        source = (AI_DIR / "external_engines" / "pylaia" / "server.py").read_text()
        self.assertNotIn("os.environ", source)
        self.assertNotIn("getenv", source)

    def test_stage1_files_do_not_select_the_decode_backend(self):
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
            self.assertNotIn("DecodePyLaiaBackend", text, path)
        server = (AI_DIR / "external_engines" / "pylaia" / "server.py").read_text()
        tree = ast.parse(server)
        modules = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.extend(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.append(node.module.split(".")[0])
        self.assertFalse({"laia", "torch", "os"} & set(modules))


if __name__ == "__main__":
    unittest.main()
