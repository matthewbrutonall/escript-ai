"""Scaffold for the PyLaia decode layout. No PyLaia process and no model load."""
import json
import tempfile
import unittest
from pathlib import Path

from ai.external_engines.pylaia.decoder import (
    DecodeLayoutError,
    DecodeRunner,
    LineImage,
    PreparedDecode,
    build_decode_command,
    prepare_decode,
)
from ai.external_engines.pylaia.engine import handle
from ai.htr_engine_contract import parse_error

AI_DIR = Path(__file__).resolve().parents[1]
DECODER = AI_DIR / "external_engines" / "pylaia" / "decoder.py"
PNG = b"\x89PNG\r\n\x1a\n" + b"not-a-real-image"
SECRET = "secret-model-path"
SECRET_LINE = "secret-line-id"
SECRET_MODEL = "secret-model-id"


def _line(line_id: str = "7", png: bytes = PNG) -> LineImage:
    return LineImage(line_id, png)


def _model_dir(root: Path, checkpoint: str = "weights.ckpt") -> Path:
    model_dir = root / "model"
    model_dir.mkdir()
    (model_dir / "model").write_bytes(b"architecture")
    (model_dir / "syms.txt").write_text("0 <ctc>\n", encoding="utf-8")
    (model_dir / checkpoint).write_bytes(b"checkpoint")
    return model_dir


class DecodeAdapterTests(unittest.TestCase):
    def test_command_is_an_argument_list_without_a_shell(self):
        source = DECODER.read_text()
        self.assertNotIn("subprocess", source)
        self.assertNotIn("shell=True", source)
        self.assertNotIn("import laia", source)
        self.assertNotIn("import torch", source)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            model_dir = _model_dir(root)
            work_dir = root / "work"
            work_dir.mkdir()
            (work_dir / "img_list.txt").write_text("0001.png\n", encoding="utf-8")
            command = build_decode_command(
                model_dir=model_dir,
                work_dir=work_dir,
                checkpoint=model_dir / "weights.ckpt",
                syms=model_dir / "syms.txt",
                img_list=work_dir / "img_list.txt",
            )
        self.assertIsInstance(command, tuple)
        self.assertTrue(all(isinstance(part, str) for part in command))
        self.assertEqual(command[0], "pylaia-htr-decode-ctc")
        self.assertEqual(command[command.index("--trainer.gpus") + 1], "0")
        self.assertEqual(
            json.loads(command[command.index("--img_dirs") + 1]),
            [str(work_dir.resolve())],
        )
        self.assertNotIn("sh", command)
        self.assertNotIn("bash", command)
        self.assertNotIn("-c", command)
        self.assertNotIn(PNG, " ".join(command).encode("utf-8"))

    def test_missing_model_files_use_a_fixed_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / SECRET
            root.mkdir()
            model_dir = root / "model"
            model_dir.mkdir()
            work_dir = root / "work"
            work_dir.mkdir()
            (model_dir / "syms.txt").write_text("0 <ctc>\n", encoding="utf-8")
            (model_dir / "weights.ckpt").write_bytes(b"checkpoint")
            with self.assertRaises(DecodeLayoutError) as caught:
                prepare_decode(
                    model_dir=model_dir,
                    work_dir=work_dir,
                    lines=[_line()],
                    model_id=SECRET_MODEL,
                )
        self.assertEqual(caught.exception.code, "model_file")
        self.assertNotIn(SECRET, str(caught.exception))
        self.assertNotIn(SECRET_MODEL, str(caught.exception))
        self.assertNotIn(SECRET_LINE, str(caught.exception))

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            model_dir = root / "model"
            model_dir.mkdir()
            work_dir = root / "work"
            work_dir.mkdir()
            (model_dir / "model").write_bytes(b"architecture")
            (model_dir / "weights.ckpt").write_bytes(b"checkpoint")
            with self.assertRaises(DecodeLayoutError) as caught:
                prepare_decode(
                    model_dir=model_dir,
                    work_dir=work_dir,
                    lines=[_line()],
                    model_id="m",
                )
        self.assertEqual(caught.exception.code, "syms")
        self.assertNotIn(str(model_dir), str(caught.exception))

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            model_dir = root / "model"
            model_dir.mkdir()
            work_dir = root / "work"
            work_dir.mkdir()
            (model_dir / "model").write_bytes(b"architecture")
            (model_dir / "syms.txt").write_text("0 <ctc>\n", encoding="utf-8")
            with self.assertRaises(DecodeLayoutError) as caught:
                prepare_decode(
                    model_dir=model_dir,
                    work_dir=work_dir,
                    lines=[_line()],
                    model_id="m",
                )
        self.assertEqual(caught.exception.code, "checkpoint")
        self.assertNotIn(str(model_dir), str(caught.exception))

    def test_checkpoint_prefers_weights_and_rejects_many(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            model_dir = _model_dir(root, checkpoint="epoch.ckpt")
            (model_dir / "weights.ckpt").write_bytes(b"preferred")
            work_dir = root / "work"
            work_dir.mkdir()
            prepared = prepare_decode(
                model_dir=model_dir,
                work_dir=work_dir,
                lines=[_line()],
                model_id="m",
            )
        checkpoint = prepared.command[prepared.command.index("--common.checkpoint") + 1]
        self.assertTrue(checkpoint.endswith("/weights.ckpt"))
        self.assertNotIn("epoch.ckpt", prepared.command)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            model_dir = _model_dir(root, checkpoint="epoch.ckpt")
            (model_dir / "other.ckpt").write_bytes(b"other")
            work_dir = root / "work"
            work_dir.mkdir()
            with self.assertRaises(DecodeLayoutError) as caught:
                prepare_decode(
                    model_dir=model_dir,
                    work_dir=work_dir,
                    lines=[_line()],
                    model_id="m",
                )
        self.assertEqual(caught.exception.code, "checkpoint")

    def test_img_list_uses_generated_names_inside_the_work_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            model_dir = _model_dir(root)
            work_dir = root / "work"
            work_dir.mkdir()
            prepared = prepare_decode(
                model_dir=model_dir,
                work_dir=work_dir,
                lines=[_line(SECRET_LINE), _line("8", PNG + b"b")],
                model_id=SECRET_MODEL,
            )
            listing = (work_dir / "img_list.txt").read_text(encoding="utf-8")
            self.assertEqual(listing, "0001.png\n0002.png\n")
            self.assertEqual((work_dir / "0001.png").read_bytes(), PNG)
            self.assertEqual((work_dir / "0002.png").read_bytes(), PNG + b"b")
            self.assertEqual(prepared.image_ids, ("0001", "0002"))
            self.assertEqual(prepared.line_ids, (SECRET_LINE, "8"))
            self.assertEqual(prepared.model_id, SECRET_MODEL)
            rendered = " ".join(prepared.command)
            self.assertNotIn(SECRET_LINE, rendered)
            self.assertNotIn(SECRET_MODEL, rendered)
            self.assertNotIn(SECRET_LINE, listing)
            self.assertNotIn(SECRET_MODEL, listing)

    def test_paths_cannot_escape_the_work_or_model_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            outside = root / SECRET
            outside.mkdir()
            leaked = outside / "leaked"
            leaked.write_bytes(b"untouched")
            model_dir = _model_dir(root)
            work_dir = root / "work"
            work_dir.mkdir()
            (work_dir / "0001.png").symlink_to(leaked)
            with self.assertRaises(DecodeLayoutError) as caught:
                prepare_decode(
                    model_dir=model_dir,
                    work_dir=work_dir,
                    lines=[_line()],
                    model_id="m",
                )
            self.assertEqual(caught.exception.code, "escape")
            self.assertEqual(leaked.read_bytes(), b"untouched")
            self.assertNotIn(SECRET, str(caught.exception))

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            model_dir = _model_dir(root)
            escaped = Path(str(model_dir) + "/../" + SECRET)
            work_dir = root / "work"
            work_dir.mkdir()
            with self.assertRaises(DecodeLayoutError) as caught:
                prepare_decode(
                    model_dir=escaped,
                    work_dir=work_dir,
                    lines=[_line()],
                    model_id="m",
                )
            self.assertEqual(caught.exception.code, "model_dir")
            self.assertNotIn(SECRET, str(caught.exception))

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            model_dir = _model_dir(root)
            outside = root / SECRET
            outside.mkdir()
            (model_dir / "model").unlink()
            (model_dir / "model").symlink_to(outside / "stolen")
            (outside / "stolen").write_bytes(b"outside")
            work_dir = root / "work"
            work_dir.mkdir()
            with self.assertRaises(DecodeLayoutError) as caught:
                prepare_decode(
                    model_dir=model_dir,
                    work_dir=work_dir,
                    lines=[_line()],
                    model_id="m",
                )
            self.assertEqual(caught.exception.code, "model_file")
            self.assertNotIn(SECRET, str(caught.exception))

    def test_runner_and_server_do_not_decode(self):
        with self.assertRaises(DecodeLayoutError) as caught:
            DecodeRunner().run(
                PreparedDecode("m", ("pylaia-htr-decode-ctc",), ("0001",), ("7",))
            )
        self.assertEqual(caught.exception.code, "not_implemented")
        for name in ("__init__.py", "engine.py", "server.py", "backend.py"):
            text = (AI_DIR / "external_engines" / "pylaia" / name).read_text()
            self.assertNotIn("decoder", text, name)
            self.assertNotIn("prepare_decode", text, name)
        status, body = handle(
            "POST",
            "/v1/recognize",
            json.dumps(
                {
                    "api_version": "1",
                    "job_id": "job-1",
                    "document_id": "9",
                    "part_id": "8",
                    "engine": "pylaia",
                    "model_id": "later",
                    "preprocessing": {},
                    "lines": [{"line_id": "7", "image": "AAAA"}],
                }
            ).encode("utf-8"),
        )
        self.assertEqual(status, 503)
        parsed = parse_error(body)
        self.assertEqual(parsed.code, "unavailable")
        self.assertEqual(parsed.message, "recognition backend is not installed")
        self.assertNotIn("results", body)
        self.assertNotIn("text", json.dumps(body))


if __name__ == "__main__":
    unittest.main()
