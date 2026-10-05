"""PyLaia decode layout and mocked process runner. No real PyLaia decode."""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ai.external_engines.pylaia.decoder import (
    DecodeLayoutError,
    DecodeRunError,
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
        self.assertNotIn("shell=True", source)
        self.assertIn("shell=False", source)
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
        with mock.patch("ai.external_engines.pylaia.decoder.subprocess.run") as run:
            with self.assertRaises(DecodeRunError) as caught:
                DecodeRunner().run(
                    PreparedDecode("m", ("pylaia-htr-decode-ctc",), ("0001",), ("7",))
                )
            run.assert_not_called()
        self.assertEqual(caught.exception.code, "output")
        self.assertNotIn("pylaia-htr-decode-ctc", str(caught.exception))
        package = AI_DIR / "external_engines" / "pylaia"
        for name in ("__init__.py", "engine.py"):
            text = (package / name).read_text()
            self.assertNotIn("decoder", text, name)
            self.assertNotIn("prepare_decode", text, name)
            self.assertNotIn("DecodePyLaiaBackend", text, name)
        server = (package / "server.py").read_text()
        self.assertNotIn("prepare_decode", server)
        self.assertNotIn("DecodeRunner", server)
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


def _prepared(work_dir: Path) -> PreparedDecode:
    return PreparedDecode(
        model_id="model-1",
        command=(
            "pylaia-htr-decode-ctc",
            "--trainer.gpus",
            "0",
            "--decode.include_img_ids",
            "true",
            "--img_dirs",
            json.dumps([str(work_dir)]),
        ),
        image_ids=("0001", "0002"),
        line_ids=("line-a", "line-b"),
    )


class DecodeRunnerTests(unittest.TestCase):
    def test_subprocess_uses_an_argument_list_and_no_shell(self):
        secret = "secret-parent-env"
        previous = os.environ.get("PYLAIA_SECRET")
        os.environ["PYLAIA_SECRET"] = secret
        try:
            with tempfile.TemporaryDirectory() as tmp:
                work_dir = Path(tmp)
                prepared = _prepared(work_dir)
                completed = subprocess.CompletedProcess(
                    list(prepared.command),
                    0,
                    stdout="0001.png first line\n0002.png second line\n",
                    stderr="",
                )
                with mock.patch(
                    "ai.external_engines.pylaia.decoder.subprocess.run",
                    return_value=completed,
                ) as run:
                    result = DecodeRunner().run(prepared)
        finally:
            if previous is None:
                os.environ.pop("PYLAIA_SECRET", None)
            else:
                os.environ["PYLAIA_SECRET"] = previous
        args, kwargs = run.call_args
        self.assertIsInstance(args[0], list)
        self.assertEqual(args[0], list(prepared.command))
        self.assertIs(kwargs["shell"], False)
        self.assertEqual(kwargs["cwd"], str(work_dir))
        self.assertEqual(kwargs["timeout"], 30)
        self.assertIs(kwargs["stdin"], subprocess.DEVNULL)
        self.assertNotIn("PYLAIA_SECRET", kwargs["env"])
        self.assertNotIn(secret, kwargs["env"].values())
        self.assertEqual(
            [(line.line_id, line.text) for line in result.lines],
            [("line-a", "first line"), ("line-b", "second line")],
        )

    def test_stdout_ids_out_of_order_map_back_to_contract_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            prepared = _prepared(Path(tmp))
            completed = subprocess.CompletedProcess(
                [],
                0,
                stdout="0002.png second\n0001.png first\n",
                stderr="secret-stderr",
            )
            with mock.patch(
                "ai.external_engines.pylaia.decoder.subprocess.run",
                return_value=completed,
            ):
                result = DecodeRunner().run(prepared)
        self.assertEqual(result.lines[0].text, "first")
        self.assertEqual(result.lines[1].text, "second")
        self.assertEqual(result.lines[0].line_id, "line-a")

    def test_empty_transcription_is_valid(self):
        with tempfile.TemporaryDirectory() as tmp:
            prepared = _prepared(Path(tmp))
            completed = subprocess.CompletedProcess(
                [],
                0,
                stdout="0001.png \n0002.png kept\n",
                stderr="",
            )
            with mock.patch(
                "ai.external_engines.pylaia.decoder.subprocess.run",
                return_value=completed,
            ):
                result = DecodeRunner().run(prepared)
        self.assertEqual(
            [(line.line_id, line.text) for line in result.lines],
            [("line-a", ""), ("line-b", "kept")],
        )

    def test_missing_extra_and_malformed_stdout_fail_without_text(self):
        cases = (
            "0001.png only one\n",
            "0001.png one\n0002.png two\n0003.png extra\n",
            "0001.png one\n0002.png two\n0001.png again\n",
            "0001.png one\nnot a valid row\n",
            "0001.png\n0002.png kept\n",
            "0001.png secret-line-text\n",
        )
        for stdout in cases:
            with tempfile.TemporaryDirectory() as tmp:
                prepared = _prepared(Path(tmp))
                completed = subprocess.CompletedProcess([], 0, stdout=stdout, stderr="")
                with mock.patch(
                    "ai.external_engines.pylaia.decoder.subprocess.run",
                    return_value=completed,
                ):
                    with self.assertRaises(DecodeRunError) as caught:
                        DecodeRunner().run(prepared)
            self.assertEqual(caught.exception.code, "output")
            self.assertNotIn("secret-line-text", str(caught.exception))
            self.assertNotIn("extra", str(caught.exception))
            self.assertEqual(str(caught.exception), "decode run was rejected")

    def test_nonzero_exit_and_timeout_hide_process_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            prepared = _prepared(Path(tmp))
            completed = subprocess.CompletedProcess(
                [],
                1,
                stdout="0001.png secret-stdout\n",
                stderr="secret-stderr",
            )
            with mock.patch(
                "ai.external_engines.pylaia.decoder.subprocess.run",
                return_value=completed,
            ):
                with self.assertRaises(DecodeRunError) as caught:
                    DecodeRunner().run(prepared)
        self.assertEqual(caught.exception.code, "exit")
        self.assertNotIn("secret-stdout", str(caught.exception))
        self.assertNotIn("secret-stderr", str(caught.exception))

        with tempfile.TemporaryDirectory() as tmp:
            prepared = _prepared(Path(tmp))
            timeout = subprocess.TimeoutExpired(
                cmd=["pylaia-htr-decode-ctc"],
                timeout=30,
                output="secret-timeout-stdout",
                stderr="secret-timeout-stderr",
            )
            with mock.patch(
                "ai.external_engines.pylaia.decoder.subprocess.run",
                side_effect=timeout,
            ):
                with self.assertRaises(DecodeRunError) as caught:
                    DecodeRunner().run(prepared)
            self.assertNotIn(tmp, str(caught.exception))
        self.assertEqual(caught.exception.code, "timeout")
        self.assertNotIn("secret-timeout-stdout", str(caught.exception))
        self.assertNotIn("secret-timeout-stderr", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
