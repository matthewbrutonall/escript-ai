"""Calamari HTTP contract and server selection. No socket and no live prediction."""
import base64
import io
import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from ai.external_engines.calamari.backend import PredictCalamariBackend
from ai.external_engines.calamari.engine import _MAX_BODY, handle
from ai.external_engines.calamari.server import _read_body, _select_backend, respond, serve
from ai.htr_engine_contract import parse_error, parse_recognize_response

PNG = b"\x89PNG\r\n\x1a\n" + b"synthetic"
SECRET = "secret transcription alpha"


def _body(**overrides) -> bytes:
    payload = {
        "api_version": "1",
        "job_id": "job-1",
        "document_id": "9",
        "part_id": "8",
        "engine": "calamari",
        "model_id": "0.ckpt",
        "preprocessing": {},
        "lines": [{
            "line_id": "7",
            "image": base64.b64encode(PNG).decode("ascii"),
        }],
    }
    payload.update(overrides)
    return json.dumps(payload).encode("utf-8")


class UnavailableTests(unittest.TestCase):
    def test_capabilities_are_the_line_contract(self):
        status, body = handle("GET", "/v1/capabilities")
        self.assertEqual(status, 200)
        self.assertEqual(body["engine"], "calamari")
        self.assertEqual(body["tasks"], ["recognize_lines"])
        self.assertEqual(body["preprocessing_supported"], [])
        self.assertNotIn("recognize_page", json.dumps(body))

    def test_the_model_list_is_empty_and_recognize_has_no_text(self):
        status, body = handle("GET", "/v1/models")
        self.assertEqual(status, 200)
        self.assertEqual(body["models"], [])
        status, body = handle("POST", "/v1/recognize", _body())
        self.assertEqual(status, 503)
        self.assertEqual(parse_error(body).code, "unavailable")
        self.assertNotIn(SECRET, json.dumps(body))
        self.assertTrue(parse_error(body).retryable)

    def test_an_unknown_model_is_not_echoed(self):
        status, body = handle("GET", f"/v1/models/{SECRET}")
        self.assertEqual(status, 404)
        self.assertNotIn(SECRET, json.dumps(body))

    def test_preprocessing_is_rejected_before_a_backend_call(self):
        status, body = handle("POST", "/v1/recognize", _body(preprocessing={"line_height": 48}))
        self.assertEqual(status, 400)
        self.assertEqual(parse_error(body).code, "unsupported_preprocessing")


class PredictRouteTests(unittest.TestCase):
    def test_a_synthetic_pair_is_listed_and_a_mocked_line_comes_back(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            models = root / "models"
            work = root / "work"
            models.mkdir()
            work.mkdir()
            (models / "0.ckpt.json").write_text(json.dumps({
                "version": 6,
                "scenario": {"data": {"line_height": 48, "input_channels": 1}},
            }), encoding="utf-8")
            saved = models / "0.ckpt"
            saved.mkdir()
            (saved / "saved_model.pb").write_bytes(b"not-a-network")

            class Runner:
                def run(self, prepared):
                    from ai.external_engines.calamari.predictor import PredictRunResult, PredictedLine
                    return PredictRunResult(
                        prepared.model_id,
                        tuple(PredictedLine(line_id, "") for line_id, _path in prepared.outputs),
                    )

            backend = PredictCalamariBackend(model_dir=models, work_root=work, runner=Runner())
            status, body = handle("GET", "/v1/models", backend=backend)
            self.assertEqual(status, 200)
            self.assertEqual(body["models"][0]["model_id"], "0.ckpt")
            self.assertEqual(body["models"][0]["input"], {"line_height": 48})
            self.assertEqual(body["models"][0]["licence"], "unspecified")
            self.assertNotIn(str(models), json.dumps(body))
            status, body = handle("POST", "/v1/recognize", _body(), backend)
            self.assertEqual(status, 200)
            parsed = parse_recognize_response(body)
            self.assertEqual(parsed.results[0].text, "")
            self.assertEqual(parsed.engine, "calamari")

    def test_predict_mode_refuses_a_directory_without_a_checkpoint(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            models = root / "models"
            work = root / "work"
            models.mkdir()
            work.mkdir()
            args = type("Args", (), {
                "backend": "predict",
                "model_dir": str(models),
                "work_root": str(work),
                "timeout": None,
            })()
            with self.assertRaises(Exception) as caught:
                _select_backend(args)
            self.assertIn("FAILED: missing_checkpoint", str(caught.exception))
            self.assertNotIn(str(models), str(caught.exception))

    def test_unavailable_mode_rejects_model_flags(self):
        args = type("Args", (), {
            "backend": "unavailable",
            "model_dir": "/models",
            "work_root": None,
            "timeout": None,
        })()
        with self.assertRaises(Exception) as caught:
            _select_backend(args)
        self.assertIn("predict flags require --backend predict", str(caught.exception))

    def test_require_licence_refuses_an_unlabelled_directory(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            models = root / "models"
            work = root / "work"
            models.mkdir()
            work.mkdir()
            (models / "0.ckpt.json").write_text("{}", encoding="utf-8")
            folder = models / "0.ckpt"
            folder.mkdir()
            (folder / "saved_model.pb").write_bytes(b"not-a-network")
            args = type("Args", (), {
                "backend": "predict",
                "model_dir": str(models),
                "work_root": str(work),
                "timeout": None,
                "require_licence": True,
                "device": "cpu",
            })()
            with self.assertRaises(Exception) as caught:
                _select_backend(args)
            self.assertIn("FAILED: missing_licence", str(caught.exception))
            self.assertNotIn(str(models), str(caught.exception))

    def test_gpu_mode_accepts_one_numbered_device_only(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            models = root / "models"
            work = root / "work"
            models.mkdir()
            work.mkdir()
            (models / "licence.txt").write_text("MIT\n", encoding="utf-8")
            (models / "0.ckpt.json").write_text('{"version": 6}', encoding="utf-8")
            folder = models / "0.ckpt"
            folder.mkdir()
            (folder / "saved_model.pb").write_bytes(b"not-a-network")
            args = type("Args", (), {
                "backend": "predict",
                "model_dir": str(models),
                "work_root": str(work),
                "timeout": None,
                "require_licence": True,
                "device": "gpu",
            })()
            with patch.dict(os.environ, {"CUDA_VISIBLE_DEVICES": "all"}):
                with self.assertRaises(Exception) as caught:
                    _select_backend(args)
            self.assertNotIn("FAILED:", str(caught.exception))
            with patch.dict(os.environ, {"CUDA_VISIBLE_DEVICES": "0"}):
                backend = _select_backend(args)
            self.assertEqual(backend._runner._device, "gpu")

    def test_a_public_host_does_not_listen(self):
        with patch("ai.external_engines.calamari.server.HTTPServer") as server:
            with self.assertRaises(ValueError):
                serve("10.0.0.8", 8768)
            server.assert_not_called()

    def test_an_oversized_body_is_consumed_before_the_error(self):
        stream = io.BytesIO(b"x" * (_MAX_BODY + 8))
        body = _read_body(stream, str(_MAX_BODY + 8))
        self.assertEqual(stream.read(), b"")
        self.assertEqual(len(body), _MAX_BODY + 1)
        status, raw = respond("POST", "/v1/recognize", body)
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(raw)["error"]["code"], "invalid_request")


if __name__ == "__main__":
    unittest.main()
