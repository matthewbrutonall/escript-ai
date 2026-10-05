"""PyLaia model-directory check. No decode and no model files are committed."""
import io
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from ai.external_engines.pylaia.check_model import main
from ai.external_engines.pylaia.decoder import (
    DecodeLayoutError,
    LineImage,
    check_model_layout,
    prepare_decode,
)

AI_DIR = Path(__file__).resolve().parents[1]
SECRET = "secret-model-path"
_PNG = b"\x89PNG\r\n\x1a\n"
_USAGE = "usage: python -m ai.external_engines.pylaia.check_model MODEL_DIR\n"


def _model_dir(root: Path, checkpoint: str = "weights.ckpt") -> Path:
    model_dir = root / "model"
    model_dir.mkdir()
    (model_dir / "model").write_bytes(b"architecture")
    (model_dir / "syms.txt").write_text("0 <ctc>\n", encoding="utf-8")
    if checkpoint:
        (model_dir / checkpoint).write_bytes(b"checkpoint")
    return model_dir


def _run(argv: list[str]) -> tuple[int, str]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = main(argv)
    error = stderr.getvalue()
    if error or any(arg and arg in error for arg in argv):
        raise AssertionError("command wrote to stderr")
    return code, stdout.getvalue()


def _prepare(model_dir: Path):
    work = model_dir.parent / "work"
    work.mkdir()
    return prepare_decode(
        model_dir=model_dir,
        work_dir=work,
        lines=(LineImage("line-1", _PNG),),
        model_id="bundle",
    )


def _checkpoint_name(model_dir: Path) -> str:
    command = _prepare(model_dir).command
    return Path(command[command.index("--common.checkpoint") + 1]).name


class ModelLayoutTests(unittest.TestCase):
    def test_valid_directory_prefers_weights_ckpt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            model_dir = _model_dir(root, "epoch.ckpt")
            (model_dir / "weights.ckpt").write_bytes(b"preferred")
            result = check_model_layout(model_dir)
            code, output = _run([str(model_dir)])
        self.assertTrue(result.ok)
        self.assertEqual(result.code, "ok")
        self.assertEqual(result.checkpoint_name, "weights.ckpt")
        self.assertEqual(code, 0)
        self.assertEqual(output, "OK\n")
        self.assertNotIn(str(model_dir), output)

    def test_missing_files_use_fixed_codes(self):
        cases = (
            ("model", "missing_model"),
            ("syms.txt", "missing_syms"),
            ("weights.ckpt", "missing_checkpoint"),
        )
        for name, expected in cases:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp) / SECRET
                root.mkdir()
                model_dir = _model_dir(root)
                (model_dir / name).unlink()
                result = check_model_layout(model_dir)
                code, output = _run([str(model_dir)])
            self.assertFalse(result.ok, name)
            self.assertEqual(result.code, expected, name)
            self.assertIsNone(result.checkpoint_name)
            self.assertEqual((code, output), (1, f"FAILED: {expected}\n"))
            self.assertNotIn(SECRET, output)
            self.assertNotIn(SECRET, str(result))

    def test_multiple_checkpoints_are_rejected_without_weights(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / SECRET
            root.mkdir()
            model_dir = _model_dir(root, "epoch.ckpt")
            (model_dir / "other.ckpt").write_bytes(b"other")
            result = check_model_layout(model_dir)
            code, output = _run([str(model_dir)])
        self.assertEqual(result.code, "multiple_checkpoints")
        self.assertEqual(output, "FAILED: multiple_checkpoints\n")
        self.assertEqual(code, 1)
        self.assertNotIn(SECRET, output)
        self.assertNotIn("epoch.ckpt", output)
        self.assertNotIn("other.ckpt", output)

    def test_symlink_escape_and_parent_paths_are_unsafe(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            outside = root / SECRET
            outside.mkdir()
            (outside / "stolen").write_bytes(b"outside")
            model_dir = _model_dir(root)
            (model_dir / "model").unlink()
            (model_dir / "model").symlink_to(outside / "stolen")
            result = check_model_layout(model_dir)
            code, output = _run([str(model_dir)])
        self.assertEqual(result.code, "unsafe_path")
        self.assertEqual((code, output), (1, "FAILED: unsafe_path\n"))
        self.assertNotIn(SECRET, output)
        self.assertNotIn(SECRET, str(result))

        escaped = Path(str(model_dir) + "/../" + SECRET)
        escaped_result = check_model_layout(escaped)
        self.assertEqual(escaped_result.code, "unsafe_path")
        self.assertNotIn(SECRET, str(escaped_result))
        relative = check_model_layout(Path(SECRET))
        self.assertEqual(relative.code, "unsafe_path")
        self.assertNotIn(SECRET, str(relative))

    def test_command_usage_does_not_require_a_path(self):
        code, output = _run([])
        self.assertEqual(code, 2)
        self.assertEqual(output, _USAGE)
        self.assertNotIn(SECRET, output)

    def test_help_and_extra_arguments_do_not_echo_a_path(self):
        for argv in (["-h"], ["--help"]):
            code, output = _run(argv)
            self.assertEqual((code, output), (0, _USAGE))
            self.assertNotIn(SECRET, output)
        supplied = f"/models/{SECRET}"
        for argv in (["--help", supplied], ["-h", supplied], [supplied, "extra"]):
            code, output = _run(argv)
            self.assertEqual((code, output), (2, _USAGE), argv)
            self.assertNotIn(SECRET, output)
            self.assertNotIn(supplied, output)

    def test_ignored_checkpoint_entries_leave_one_valid_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / SECRET
            root.mkdir()
            outside = root / "outside"
            outside.mkdir()
            (outside / "stolen").write_bytes(b"outside")
            model_dir = _model_dir(root, "epoch.ckpt")
            (model_dir / "escape.ckpt").symlink_to(outside / "stolen")
            result = check_model_layout(model_dir)
            code, output = _run([str(model_dir)])
            chosen = _checkpoint_name(model_dir)
        self.assertTrue(result.ok)
        self.assertEqual(result.code, "ok")
        self.assertEqual(result.checkpoint_name, "epoch.ckpt")
        self.assertEqual(chosen, "epoch.ckpt")
        self.assertEqual((code, output), (0, "OK\n"))
        self.assertNotIn(SECRET, output)
        self.assertNotIn("escape.ckpt", output)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / SECRET
            root.mkdir()
            model_dir = _model_dir(root, "epoch.ckpt")
            (model_dir / "dir.ckpt").mkdir()
            result = check_model_layout(model_dir)
            code, output = _run([str(model_dir)])
            chosen = _checkpoint_name(model_dir)
        self.assertEqual(result.checkpoint_name, "epoch.ckpt")
        self.assertEqual(chosen, "epoch.ckpt")
        self.assertEqual((code, output), (0, "OK\n"))
        self.assertNotIn(SECRET, output)
        self.assertNotIn("dir.ckpt", output)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / SECRET
            root.mkdir()
            outside = root / "outside"
            outside.mkdir()
            (outside / "stolen").write_bytes(b"outside")
            model_dir = _model_dir(root, "")
            (model_dir / "dir.ckpt").mkdir()
            (model_dir / "escape.ckpt").symlink_to(outside / "stolen")
            (model_dir / "broken.ckpt").symlink_to(model_dir / "missing-ckpt")
            result = check_model_layout(model_dir)
        self.assertEqual(result.code, "missing_checkpoint")
        self.assertIsNone(result.checkpoint_name)
        self.assertNotIn(SECRET, str(result))

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / SECRET
            root.mkdir()
            outside = root / "outside"
            outside.mkdir()
            (outside / "stolen").write_bytes(b"outside")
            model_dir = _model_dir(root, "epoch.ckpt")
            (model_dir / "other.ckpt").write_bytes(b"other")
            (model_dir / "escape.ckpt").symlink_to(outside / "stolen")
            result = check_model_layout(model_dir)
        self.assertEqual(result.code, "multiple_checkpoints")
        self.assertNotIn(SECRET, str(result))

    def test_unsafe_required_files_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / SECRET
            root.mkdir()
            outside = root / "outside"
            outside.mkdir()
            (outside / "stolen").write_bytes(b"outside")
            model_dir = _model_dir(root, "epoch.ckpt")
            (model_dir / "weights.ckpt").symlink_to(outside / "stolen")
            result = check_model_layout(model_dir)
            code, output = _run([str(model_dir)])
            with self.assertRaises(DecodeLayoutError) as caught:
                _prepare(model_dir)
        self.assertEqual(result.code, "unsafe_path")
        self.assertIsNone(result.checkpoint_name)
        self.assertEqual((code, output), (1, "FAILED: unsafe_path\n"))
        self.assertNotIn(SECRET, output)
        self.assertNotIn(SECRET, str(result))
        self.assertNotIn(SECRET, str(caught.exception))

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / SECRET
            root.mkdir()
            model_dir = _model_dir(root)
            (model_dir / "model").unlink()
            (model_dir / "model").mkdir()
            result = check_model_layout(model_dir)
            code, output = _run([str(model_dir)])
            with self.assertRaises(DecodeLayoutError) as caught:
                _prepare(model_dir)
        self.assertEqual(result.code, "unsafe_path")
        self.assertEqual((code, output), (1, "FAILED: unsafe_path\n"))
        self.assertNotIn(SECRET, output)
        self.assertNotIn(SECRET, str(caught.exception))

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / SECRET
            root.mkdir()
            model_dir = _model_dir(root)
            (model_dir / "model").unlink()
            (model_dir / "model").symlink_to(model_dir / "missing-model")
            result = check_model_layout(model_dir)
            code, output = _run([str(model_dir)])
            with self.assertRaises(DecodeLayoutError) as caught:
                _prepare(model_dir)
        self.assertEqual(result.code, "unsafe_path")
        self.assertEqual((code, output), (1, "FAILED: unsafe_path\n"))
        self.assertNotIn(SECRET, output)
        self.assertNotIn(SECRET, str(result))
        self.assertNotIn(SECRET, str(caught.exception))

    def test_missing_directory_and_non_path_are_unsafe(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / SECRET / "absent"
            result = check_model_layout(missing)
            code, output = _run([str(missing)])
        self.assertEqual(result.code, "unsafe_path")
        self.assertEqual((code, output), (1, "FAILED: unsafe_path\n"))
        self.assertNotIn(SECRET, output)
        self.assertNotIn(SECRET, str(result))
        for value in (SECRET, None, 3):
            bad = check_model_layout(value)
            self.assertEqual(bad.code, "unsafe_path")
            self.assertNotIn(SECRET, str(bad))

    def test_symlink_loop_returns_a_fixed_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            loop = root / SECRET
            loop.symlink_to(loop)
            result = check_model_layout(loop)
            code, output = _run([str(loop)])
        self.assertEqual(result.code, "unsafe_path")
        self.assertEqual((code, output), (1, "FAILED: unsafe_path\n"))
        self.assertNotIn(SECRET, output)
        self.assertNotIn(str(loop), output)
        self.assertNotIn(SECRET, str(result))

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / SECRET
            second = root / "other-link"
            first.symlink_to(second)
            second.symlink_to(first)
            result = check_model_layout(first)
            code, output = _run([str(first)])
        self.assertEqual(result.code, "unsafe_path")
        self.assertEqual((code, output), (1, "FAILED: unsafe_path\n"))
        self.assertNotIn(SECRET, output)
        self.assertNotIn(str(first), output)
        self.assertNotIn(SECRET, str(result))

    def test_unreadable_parent_does_not_print_the_path(self):
        if os.geteuid() == 0:
            self.skipTest("root ignores directory mode 0")
        with tempfile.TemporaryDirectory() as tmp:
            hidden = Path(tmp) / SECRET
            hidden.mkdir()
            model_dir = _model_dir(hidden)
            hidden.chmod(0)
            try:
                result = check_model_layout(model_dir)
                code, output = _run([str(model_dir)])
            finally:
                hidden.chmod(0o700)
        self.assertEqual(result.code, "unsafe_path")
        self.assertEqual((code, output), (1, "FAILED: unsafe_path\n"))
        self.assertNotIn(SECRET, output)
        self.assertNotIn(str(model_dir), output)
        self.assertNotIn(SECRET, str(result))

    def test_server_does_not_import_the_checker_and_it_does_not_import_pylaia(self):
        package = AI_DIR / "external_engines" / "pylaia"
        source = (package / "check_model.py").read_text()
        self.assertNotIn("import laia", source)
        self.assertNotIn("import torch", source)
        self.assertNotIn("subprocess", source)
        for name in ("__init__.py", "engine.py", "server.py"):
            text = (package / name).read_text()
            self.assertNotIn("check_model", text, name)
            self.assertNotIn("check_model_layout", text, name)
