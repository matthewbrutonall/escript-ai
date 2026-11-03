"""Calamari layout and a mocked predict process. No live prediction."""
import base64
import contextlib
import io
import json
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from ai.external_engines.calamari.backend import PredictCalamariBackend, UnavailableCalamariBackend
from ai.external_engines.calamari.check_model import main as check_main
from ai.external_engines.calamari.predictor import (
    LayoutError,
    PredictRunner,
    declared_line_height,
    inspect_model_dir,
    prepare_predict,
)
from ai.htr_engine_contract import parse_model_list, parse_recognize_response

SECRET = "secret transcription alpha"
PNG = b"\x89PNG\r\n\x1a\n" + b"synthetic"


def _pair(root: Path, name: str, payload: dict, weights: str = "saved") -> None:
    (root / f"{name}.json").write_text(json.dumps(payload), encoding="utf-8")
    if weights == "saved":
        folder = root / name
        folder.mkdir()
        (folder / "saved_model.pb").write_bytes(b"not-a-network")
    elif weights == "h5":
        (root / f"{name}.h5").write_bytes(b"not-a-network")
    elif weights == "file":
        (root / name).write_bytes(b"not-a-network")


def _checked(argv):
    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = check_main(argv)
    return code, stdout.getvalue(), stderr.getvalue()


def _request(model_id="0.ckpt", lines=None):
    if lines is None:
        lines = (SimpleNamespace(line_id="7", image=base64.b64encode(PNG).decode("ascii")),)
    return SimpleNamespace(
        job_id="job-1",
        document_id="9",
        part_id="8",
        engine="calamari",
        model_id=model_id,
        lines=lines,
    )


class LayoutTests(unittest.TestCase):
    def test_a_savedmodel_pair_reports_the_declared_height_and_no_path(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _pair(root, "0.ckpt", {
                "version": 6,
                "scenario": {
                    "data": {
                        "line_height": 48,
                        "input_channels": 1,
                        "pre_proc": {"processors": [{"line_height": 64}]},
                    },
                },
            })
            (root / "licence.txt").write_text("CC-BY-4.0\n", encoding="utf-8")
            report = inspect_model_dir(root)
            self.assertEqual(report.code, "ok")
            checkpoint = report.checkpoints[0]
            self.assertEqual(checkpoint.model_id, "0.ckpt")
            self.assertEqual(checkpoint.line_height, 48)
            self.assertEqual(checkpoint.model_version, "6")
            self.assertEqual(checkpoint.licence, "CC-BY-4.0")
            code, out, err = _checked([str(root)])
            self.assertEqual((code, out, err), (0, "OK\n", ""))
            self.assertNotIn(str(root), out + err)

    def test_missing_height_is_not_invented_and_a_paragraph_is_not_a_licence(self):
        payload = {"version": 6, "scenario": {"data": {"pre_proc": {"processors": [{"line_height": 48}]}}}}
        self.assertIsNone(declared_line_height(payload))
        self.assertIsNone(declared_line_height({"scenario": {"data": {"line_height": True}}}))
        self.assertIsNone(declared_line_height({"scenario": {"data": {"line_height": 0}}}))
        self.assertEqual(declared_line_height({"data": {"line_height": 48}}), 48)
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _pair(root, "0.ckpt", payload)
            (root / "licence.txt").write_text("This model is free for research.\n", encoding="utf-8")
            checkpoint = inspect_model_dir(root).checkpoints[0]
            self.assertIsNone(checkpoint.line_height)
            self.assertEqual(checkpoint.licence, "unspecified")

    def test_an_empty_directory_and_a_weights_gap_are_not_models(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(inspect_model_dir(root).code, "missing_checkpoint")
            code, out, err = _checked([str(root)])
            self.assertEqual(code, 1)
            self.assertEqual(out, "")
            self.assertEqual(err, "FAILED: missing_checkpoint\n")
            self.assertNotIn(str(root), err)
            (root / "0.ckpt.json").write_text("{}", encoding="utf-8")
            report = inspect_model_dir(root)
            self.assertEqual(report.checkpoints, ())
            self.assertEqual(report.code, "missing_weights")
            (root / "0.ckpt").mkdir()
            self.assertEqual(inspect_model_dir(root).code, "missing_weights")

    def test_h5_weights_are_a_pair_and_a_relative_directory_is_refused(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _pair(root, "fold.ckpt", {"version": 5, "scenario": {"data": {"line_height": 64}}}, weights="h5")
            checkpoint = inspect_model_dir(root).checkpoints[0]
            self.assertEqual(checkpoint.model_id, "fold.ckpt")
            self.assertEqual(checkpoint.line_height, 64)
            self.assertEqual(checkpoint.model_version, "5")
        with self.assertRaises(LayoutError):
            inspect_model_dir(Path("relative"))


class PredictTests(unittest.TestCase):
    def test_the_default_backend_has_no_models_and_no_text(self):
        backend = UnavailableCalamariBackend()
        self.assertEqual(backend.list_models()["models"], [])
        self.assertEqual(backend.recognize(object()).code, "unavailable")

    def test_mocked_prediction_keeps_order_and_hides_text_from_errors(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            work = root / "work"
            models = root / "models"
            work.mkdir()
            models.mkdir()
            _pair(models, "0.ckpt", {"version": 6, "scenario": {"data": {"line_height": 48}}})
            calls = []

            def fake_run(argv, **kwargs):
                calls.append((argv, kwargs))
                self.assertFalse(kwargs["shell"])
                self.assertEqual(kwargs["env"]["CUDA_VISIBLE_DEVICES"], "-1")
                self.assertEqual(kwargs["env"]["HIP_VISIBLE_DEVICES"], "-1")
                self.assertEqual(kwargs["env"]["NVIDIA_VISIBLE_DEVICES"], "void")
                for image in argv[argv.index("--data.images") + 1:]:
                    if image.startswith("--"):
                        break
                    Path(image).with_suffix("").with_suffix(".pred.txt")
                    stem = Path(image).name.split(".", 1)[0]
                    (Path(image).parent / f"{stem}.pred.txt").write_text(SECRET + "\n", encoding="utf-8")
                return SimpleNamespace(returncode=0)

            backend = PredictCalamariBackend(model_dir=models, work_root=work, runner=PredictRunner())
            listed = parse_model_list(backend.list_models())
            self.assertEqual(listed.models[0].model_id, "0.ckpt")
            self.assertEqual(listed.models[0].input.line_height, 48)
            self.assertNotIn(str(models), json.dumps(backend.list_models()))
            lines = tuple(
                SimpleNamespace(line_id=str(item), image=base64.b64encode(PNG).decode("ascii"))
                for item in (3, 4)
            )
            with patch("ai.external_engines.calamari.predictor.subprocess.run", side_effect=fake_run):
                outcome = backend.recognize(_request(lines=lines))
            parsed = parse_recognize_response(outcome, _request(lines=lines))
            self.assertEqual([item.line_id for item in parsed.results], ["3", "4"])
            self.assertEqual(parsed.results[0].text, SECRET)
            self.assertEqual(parsed.resources.device, "cpu")
            self.assertIn("--data.line_height", calls[0][0])
            self.assertEqual(calls[0][0][calls[0][0].index("--data.line_height") + 1], "48")
            self.assertFalse(work.iterdir() and any(work.iterdir()))
            with patch(
                "ai.external_engines.calamari.predictor.subprocess.run",
                return_value=SimpleNamespace(returncode=1, stdout=SECRET.encode(), stderr=SECRET.encode()),
            ):
                failed = backend.recognize(_request())
            self.assertEqual(failed.code, "internal")
            self.assertNotIn(SECRET, failed.code)

    def test_a_missing_program_is_unavailable_and_does_not_invent_text(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            work = root / "work"
            models = root / "models"
            work.mkdir()
            models.mkdir()
            _pair(models, "0.ckpt", {"version": 6, "scenario": {"data": {"line_height": 48}}})
            backend = PredictCalamariBackend(model_dir=models, work_root=work)
            with patch(
                "ai.external_engines.calamari.predictor.subprocess.run",
                side_effect=FileNotFoundError(SECRET),
            ):
                failed = backend.recognize(_request())
            self.assertEqual(failed.code, "unavailable")
            self.assertNotIn(SECRET, failed.code)

    def test_more_than_one_batch_stays_in_request_order(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            work = root / "work"
            models = root / "models"
            work.mkdir()
            models.mkdir()
            _pair(models, "0.ckpt", {"scenario": {"data": {"line_height": 48}}})
            seen = []

            def fake_run(argv, **kwargs):
                images = []
                take = False
                for item in argv:
                    if item == "--data.images":
                        take = True
                        continue
                    if take and item.startswith("--"):
                        break
                    if take:
                        images.append(item)
                seen.append(len(images))
                for image in images:
                    stem = Path(image).name.split(".", 1)[0]
                    (Path(image).parent / f"{stem}.pred.txt").write_text(stem, encoding="utf-8")
                return SimpleNamespace(returncode=0)

            lines = tuple(
                SimpleNamespace(line_id=f"L{index}", image=base64.b64encode(PNG).decode("ascii"))
                for index in range(33)
            )
            backend = PredictCalamariBackend(model_dir=models, work_root=work)
            with patch("ai.external_engines.calamari.predictor.subprocess.run", side_effect=fake_run):
                outcome = backend.recognize(_request(lines=lines))
            self.assertEqual(seen, [32, 1])
            self.assertEqual([row["line_id"] for row in outcome["results"]], [f"L{index}" for index in range(33)])
            self.assertFalse(work.exists() and any(work.iterdir()))

    def test_prepare_rejects_a_non_png_and_a_timeout_is_not_text(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _pair(root, "0.ckpt", {"scenario": {"data": {}}})
            checkpoint = inspect_model_dir(root).checkpoints[0]
            with self.assertRaises(LayoutError):
                prepare_predict(
                    checkpoint=checkpoint,
                    work_dir=root,
                    lines=(SimpleNamespace(line_id="1", png=b"not-png"),),
                )
            runner = PredictRunner()
            prepared = prepare_predict(
                checkpoint=checkpoint,
                work_dir=root,
                lines=(type("Line", (), {"line_id": "1", "png": PNG})(),),
            )
            with patch(
                "ai.external_engines.calamari.predictor.subprocess.run",
                side_effect=subprocess.TimeoutExpired(cmd=["calamari-predict"], timeout=1),
            ):
                with self.assertRaises(Exception) as caught:
                    runner.run(prepared)
            self.assertEqual(caught.exception.code, "timeout")
            self.assertNotIn(SECRET, caught.exception.code)


if __name__ == "__main__":
    unittest.main()
