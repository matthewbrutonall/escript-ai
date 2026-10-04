"""Injected PyLaia decode backend. DecodeRunner.run is mocked. No live decode."""
import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ai.external_engines.pylaia.backend import (
    DecodePyLaiaBackend,
    UnavailablePyLaiaBackend,
)
from ai.external_engines.pylaia.decoder import (
    DecodeLayoutError,
    DecodeRunError,
    DecodedLine,
    DecodeRunResult,
)
from ai.external_engines.pylaia.engine import handle
from ai.htr_engine_contract import parse_error, parse_model, parse_model_list, parse_recognize_request

PNG = b"\x89PNG\r\n\x1a\n" + b"x"
IMAGE = base64.standard_b64encode(PNG).decode("ascii")
SECRET = "secret-model-path"
SECRET_TEXT = "secret-line-text"


def _model_dir(root: Path) -> Path:
    model_dir = root / "model"
    model_dir.mkdir()
    (model_dir / "model").write_bytes(b"architecture")
    (model_dir / "syms.txt").write_text("0 <ctc>\n", encoding="utf-8")
    (model_dir / "weights.ckpt").write_bytes(b"checkpoint")
    return model_dir


def _request_bytes(*line_ids: str, model_id: str = "configured") -> bytes:
    return json.dumps(
        {
            "api_version": "1",
            "job_id": "job-1",
            "document_id": "9",
            "part_id": "8",
            "engine": "pylaia",
            "model_id": model_id,
            "preprocessing": {},
            "lines": [{"line_id": line_id, "image": IMAGE} for line_id in line_ids],
        }
    ).encode("utf-8")


class DecodeBackendTests(unittest.TestCase):
    def test_default_backend_stays_unavailable_and_does_not_run(self):
        with mock.patch("ai.external_engines.pylaia.backend.DecodeRunner.run") as run:
            with mock.patch("ai.external_engines.pylaia.decoder.subprocess.run") as process:
                status, body = handle("POST", "/v1/recognize", _request_bytes("line-a"))
                failure = UnavailablePyLaiaBackend().recognize(object())
        run.assert_not_called()
        process.assert_not_called()
        self.assertEqual(status, 503)
        self.assertEqual(parse_error(body).code, "unavailable")
        self.assertNotIn("results", body)
        self.assertEqual(failure.code, "unavailable")

    def test_injected_backend_returns_text_in_contract_order(self):
        raw = _request_bytes("line-a", "line-b")
        request = parse_recognize_request(json.loads(raw.decode("utf-8")))
        result = DecodeRunResult(
            "configured",
            (
                DecodedLine("line-b", "0002", "second line"),
                DecodedLine("line-a", "0001", ""),
            ),
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            model_dir = _model_dir(root)
            work_root = root / "work"
            work_root.mkdir()
            backend = DecodePyLaiaBackend(
                model_id="configured",
                model_dir=model_dir,
                work_root=work_root,
            )
            resolved = str(model_dir.resolve())
            with mock.patch.object(backend._runner, "run", return_value=result) as run:
                with mock.patch("ai.external_engines.pylaia.decoder.subprocess.run") as process:
                    direct = backend.recognize(request)
                    status, body = handle("POST", "/v1/recognize", raw, backend=backend)
                    process.assert_not_called()
            self.assertEqual(list(work_root.iterdir()), [])
            prepared = run.call_args.args[0]
        self.assertEqual(prepared.line_ids, ("line-a", "line-b"))
        self.assertEqual(prepared.image_ids, ("0001", "0002"))
        self.assertIn(resolved, prepared.command)
        self.assertEqual(
            [(row["line_id"], row["text"]) for row in direct["results"]],
            [("line-a", ""), ("line-b", "second line")],
        )
        self.assertEqual(status, 200)
        self.assertEqual(
            [(row["line_id"], row["text"]) for row in body["results"]],
            [("line-a", ""), ("line-b", "second line")],
        )
        self.assertIsNone(body["results"][0]["confidence"])
        self.assertEqual(body["results"][0]["timing_ms"], 0)

    def test_layout_and_runner_failures_hide_paths_and_text(self):
        raw = _request_bytes("line-a", model_id="configured")
        request = parse_recognize_request(json.loads(raw.decode("utf-8")))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / SECRET
            root.mkdir()
            model_dir = _model_dir(root)
            (model_dir / "weights.ckpt").unlink()
            work_root = root / "work"
            work_root.mkdir()
            backend = DecodePyLaiaBackend(
                model_id="configured",
                model_dir=model_dir,
                work_root=work_root,
            )
            with mock.patch("ai.external_engines.pylaia.decoder.subprocess.run") as process:
                failure = backend.recognize(request)
                status, body = handle("POST", "/v1/recognize", raw, backend=backend)
            self.assertEqual(list(work_root.iterdir()), [])
            with mock.patch.object(
                backend._runner,
                "run",
                side_effect=DecodeRunError("output"),
            ):
                (model_dir / "weights.ckpt").write_bytes(b"checkpoint")
                runner_failure = backend.recognize(request)
                runner_status, runner_body = handle(
                    "POST", "/v1/recognize", raw, backend=backend
                )
            self.assertEqual(list(work_root.iterdir()), [])
        process.assert_not_called()
        self.assertEqual(failure.code, "model_not_found")
        self.assertEqual(status, 404)
        self.assertNotIn(SECRET, json.dumps(body))
        self.assertNotIn(SECRET_TEXT, json.dumps(body))
        self.assertEqual(runner_failure.code, "internal")
        self.assertEqual(runner_status, 500)
        self.assertEqual(parse_error(runner_body).message, "pylaia wrapper failed")
        self.assertNotIn(SECRET, json.dumps(runner_body))
        self.assertNotIn(SECRET_TEXT, str(runner_failure))

    def test_timeout_is_busy_and_a_bad_model_id_is_not_a_path(self):
        raw = _request_bytes("line-a")
        request = parse_recognize_request(json.loads(raw.decode("utf-8")))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            backend = DecodePyLaiaBackend(
                model_id="configured",
                model_dir=_model_dir(root),
                work_root=root / "work",
            )
            (root / "work").mkdir()
            with mock.patch.object(backend._runner, "run", side_effect=DecodeRunError("timeout")):
                failure = backend.recognize(request)
                status, body = handle("POST", "/v1/recognize", raw, backend=backend)
        self.assertEqual(failure.code, "busy")
        self.assertEqual(status, 429)
        self.assertTrue(parse_error(body).retryable)
        with self.assertRaises(DecodeLayoutError) as caught:
            DecodePyLaiaBackend(
                model_id="../" + SECRET,
                model_dir=Path("/tmp"),
                work_root=Path("/tmp"),
            )
        self.assertEqual(caught.exception.code, "model_id")
        self.assertNotIn(SECRET, str(caught.exception))

    def test_model_list_is_the_configured_id_without_a_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / SECRET
            root.mkdir()
            work_root = root / "work"
            work_root.mkdir()
            backend = DecodePyLaiaBackend(
                model_id="configured",
                model_dir=_model_dir(root),
                work_root=work_root,
            )
            status, body = handle("GET", "/v1/models", backend=backend)
            missing, missing_body = handle("GET", f"/v1/models/{SECRET}", backend=backend)
        parsed = parse_model_list(body)
        self.assertEqual(status, 200)
        self.assertEqual(len(parsed.models), 1)
        model = parse_model(body["models"][0])
        self.assertEqual(model.model_id, "configured")
        self.assertTrue(model.experimental)
        self.assertEqual(model.alphabet_note, "")
        self.assertNotIn(SECRET, json.dumps(body))
        self.assertEqual(missing, 404)
        self.assertEqual(parse_error(missing_body).code, "model_not_found")
        self.assertNotIn(SECRET, json.dumps(missing_body))

    def test_wrong_model_and_duplicate_runner_lines_do_not_return_text(self):
        raw = _request_bytes("line-a", model_id="other")
        request = parse_recognize_request(json.loads(raw.decode("utf-8")))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            backend = DecodePyLaiaBackend(
                model_id="configured",
                model_dir=_model_dir(root),
                work_root=root / "work",
            )
            (root / "work").mkdir()
            with mock.patch.object(backend._runner, "run") as run:
                failure = backend.recognize(request)
            run.assert_not_called()
            duplicated = DecodeRunResult(
                "configured",
                (
                    DecodedLine("line-a", "0001", SECRET_TEXT),
                    DecodedLine("line-a", "0001", "again"),
                ),
            )
            owned = _request_bytes("line-a")
            owned_request = parse_recognize_request(json.loads(owned.decode("utf-8")))
            with mock.patch.object(backend._runner, "run", return_value=duplicated):
                duplicate = backend.recognize(owned_request)
                status, body = handle("POST", "/v1/recognize", owned, backend=backend)
        self.assertEqual(failure.code, "model_not_found")
        self.assertEqual(duplicate.code, "internal")
        self.assertEqual(status, 500)
        self.assertNotIn(SECRET_TEXT, json.dumps(body))
        self.assertNotIn("results", body)
