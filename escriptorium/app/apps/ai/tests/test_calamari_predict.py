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

from PIL import Image

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
                self.assertEqual(kwargs["env"]["CUDA_VISIBLE_DEVICES"], "")
                self.assertEqual(kwargs["env"]["HIP_VISIBLE_DEVICES"], "")
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
            self.assertNotIn("--data.line_height", calls[0][0])
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

    def test_gpu_mode_reports_the_device_it_was_given(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            work = root / "work"
            models = root / "models"
            work.mkdir()
            models.mkdir()
            _pair(models, "0.ckpt", {"version": 6, "scenario": {"data": {"line_height": 48}}})

            def fake_run(argv, **kwargs):
                self.assertEqual(kwargs["env"]["CUDA_VISIBLE_DEVICES"], "0")
                image = argv[argv.index("--data.images") + 1]
                (Path(image).parent / "line0001.pred.txt").write_text("kept\n", encoding="utf-8")
                return SimpleNamespace(returncode=0)

            backend = PredictCalamariBackend(model_dir=models, work_root=work, device="gpu")
            with patch.dict("os.environ", {"CUDA_VISIBLE_DEVICES": "0"}, clear=False):
                with patch("ai.external_engines.calamari.predictor.subprocess.run", side_effect=fake_run):
                    outcome = backend.recognize(_request())
            self.assertEqual(outcome["resources"]["device"], "gpu")

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

    def test_prepare_writes_black_on_white_for_both_polarities(self):
        grey = Image.new("RGB", (40, 16), (220, 218, 210))
        for x in range(8, 32):
            for y in range(4, 12):
                grey.putpixel((x, y), (30, 28, 26))
        grey_bytes = io.BytesIO()
        grey.save(grey_bytes, format="PNG")
        inverted = Image.new("L", (40, 16), 10)
        for x in range(8, 14):
            for y in range(4, 12):
                inverted.putpixel((x, y), 245)
        inverted_bytes = io.BytesIO()
        inverted.save(inverted_bytes, format="PNG")
        white = Image.new("L", (12, 8), 240)
        white_bytes = io.BytesIO()
        white.save(white_bytes, format="PNG")
        heavy = Image.new("L", (40, 16), 0)
        for x in range(40):
            heavy.putpixel((x, 0), 255)
            heavy.putpixel((x, 15), 255)
        for y in range(16):
            heavy.putpixel((0, y), 255)
            heavy.putpixel((39, y), 255)
        heavy_bytes = io.BytesIO()
        heavy.save(heavy_bytes, format="PNG")
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _pair(root, "0.ckpt", {"scenario": {"data": {}}})
            checkpoint = inspect_model_dir(root).checkpoints[0]
            prepared = prepare_predict(
                checkpoint=checkpoint,
                work_dir=root,
                lines=(
                    SimpleNamespace(line_id="grey", png=grey_bytes.getvalue()),
                    SimpleNamespace(line_id="inverted", png=inverted_bytes.getvalue()),
                    SimpleNamespace(line_id="white", png=white_bytes.getvalue()),
                    SimpleNamespace(line_id="heavy", png=heavy_bytes.getvalue()),
                    SimpleNamespace(line_id="raw", png=PNG),
                ),
            )
            written = [Path(item) for item in prepared.argv if str(item).endswith(".png")]
            self.assertEqual(len(written), 5)
            with Image.open(written[0]) as image:
                colours = set(image.getdata())
                self.assertEqual(image.mode, "L")
            self.assertEqual(colours, {0, 255})
            self.assertLess(list(Image.open(written[0]).getdata()).count(0), 40 * 16 / 2)
            with Image.open(written[1]) as image:
                pixels = list(image.getdata())
            self.assertEqual(set(pixels), {0, 255})
            self.assertLess(pixels.count(0), len(pixels) / 2)
            self.assertEqual(pixels[0], 255)
            with Image.open(written[2]) as image:
                self.assertEqual(set(image.getdata()), {255})
            with Image.open(written[3]) as image:
                heavy_pixels = list(image.getdata())
            self.assertEqual(heavy_pixels[0], 255)
            self.assertGreater(heavy_pixels.count(0), len(heavy_pixels) / 2)
            self.assertEqual(written[4].read_bytes(), PNG)

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
