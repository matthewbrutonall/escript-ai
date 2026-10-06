"""Operator export of an external HTR request file. No Django, no network, no transcription write."""
import ast
import base64
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from ai.external_htr_export import (
    CODE_CONFIG,
    CODE_DISABLED,
    CODE_EXISTS,
    CODE_IMAGE,
    CODE_INTERNAL,
    CODE_NOTHING,
    CODE_OUTPUT,
    CODE_PART,
    ConfigNotFound,
    OutputExists,
    OutputUnavailable,
    PartNotFound,
    execute_external_htr_export,
    write_request_json,
)
from ai.htr_engine_contract import parse_recognize_request

AI_DIR = Path(__file__).resolve().parents[1]
COMMAND = AI_DIR / "management" / "commands" / "export_external_htr_request.py"
PLAN_COMMAND = AI_DIR / "management" / "commands" / "plan_external_htr.py"
MASK = [[0, 0], [10, 0], [10, 4], [0, 4]]
BASELINE = [[0, 2], [10, 2]]
ENDPOINT = "http://127.0.0.1:9/secret-endpoint"
TOKEN = "secret-token"
TITLE = "Secret Title"
SECRET_PATH = "/tmp/secret-document"
OUTPUT_NAME = "secret-request-output.json"
RUNTIME = (
    "tasks.py",
    "views.py",
    "serializers.py",
    "pipeline.py",
    "backends.py",
    "dispatch.py",
    "models.py",
    "admin.py",
)
UNWIRED = (
    "management/commands/plan_external_htr.py",
    "external_htr_plan_command.py",
    "external_htr_service.py",
    "external_htr_live.py",
    "external_htr_live_command.py",
    "management/commands/check_external_htr_live.py",
    "tasks.py",
)


class _Line:
    def __init__(self, pk, mask=MASK, baseline=BASELINE):
        self.pk = pk
        self.mask = mask
        self.baseline = baseline
        self.title = TITLE


def _page():
    image = Image.new("RGB", (40, 30), (255, 255, 255))
    image.filename = SECRET_PATH
    image.putpixel((0, 0), (1, 2, 3))
    image.putpixel((39, 29), (9, 9, 9))
    return image


def _rect(x0, y0, x1, y1):
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


class _World:
    def __init__(self, enabled=True, lines=None):
        self.opened = []
        self.writes = []
        self.document = type("Document", (), {
            "pk": 7, "title": TITLE, "path": SECRET_PATH,
        })()
        self.part = type("Part", (), {
            "pk": 8,
            "document": self.document,
            "filename": SECRET_PATH,
            "image": type("ImageField", (), {"path": SECRET_PATH})(),
        })()
        self.config = type("Config", (), {
            "pk": 3,
            "name": TITLE,
            "enabled": enabled,
            "endpoint_url": ENDPOINT,
            "metadata": {"api_key": TOKEN},
        })()
        self.lines = [_Line(10)] if lines is None else lines
        self.page = _page()

    def fetch_config(self, config_id):
        if config_id != 3:
            raise ConfigNotFound(SECRET_PATH + ENDPOINT)
        return self.config

    def fetch_part(self, part_id):
        if part_id != 8:
            raise PartNotFound(SECRET_PATH + TITLE)
        return self.part, self.lines

    def open_image(self, part):
        self.opened.append(part)
        self.page = _page()
        return self.page

    def run(self, path, **extra):
        kwargs = {
            "model_id": "example-model",
            "engine": "pylaia",
            "output_path": path,
            "fetch_config": self.fetch_config,
            "fetch_part": self.fetch_part,
            "open_image": self.open_image,
        }
        kwargs.update(extra)
        return execute_external_htr_export(3, 8, **kwargs)


def _secrets():
    return (ENDPOINT, TOKEN, TITLE, SECRET_PATH, OUTPUT_NAME)


class ExportTests(unittest.TestCase):
    def test_success_writes_a_recognize_request(self):
        world = _World(lines=[
            _Line(10),
            _Line(99, mask=_rect(-80, -40, -40, -20), baseline=[[-70, -30], [-50, -30]]),
        ])
        page_size = world.page.size
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / OUTPUT_NAME)
            code, line = world.run(path)
            raw = Path(path).read_text(encoding="utf-8")
            names = sorted(item.name for item in Path(tmp).iterdir())
        self.assertEqual(names, [OUTPUT_NAME])
        self.assertEqual(code, 0)
        self.assertEqual(line, "OK: external HTR request exported lines=1 skipped=1")
        self.assertEqual(world.opened, [world.part])
        payload = json.loads(raw)
        self.assertEqual(
            raw,
            json.dumps(payload, separators=(",", ":"), ensure_ascii=False),
        )
        parsed = parse_recognize_request(payload)
        self.assertEqual(parsed.engine, "pylaia")
        self.assertEqual(parsed.model_id, "example-model")
        self.assertEqual(parsed.document_id, "7")
        self.assertEqual(parsed.part_id, "8")
        self.assertEqual([item.line_id for item in parsed.lines], ["10"])
        image = parsed.lines[0].image
        self.assertIn(image, raw)
        self.assertNotIn(image, line)
        opened = Image.open(io.BytesIO(base64.standard_b64decode(image)))
        opened.load()
        self.assertEqual(opened.size, (10, 4))
        self.assertEqual(opened.getpixel((0, 0)), (1, 2, 3))
        self.assertNotEqual(opened.size, page_size)
        self.assertEqual([item.line_id for item in parsed.lines], ["10"])
        self._assert_clean(line)
        self._assert_clean(raw)

    def test_disabled_config_does_not_open_or_write(self):
        for enabled in (False, 1, None):
            world = _World(enabled=enabled)
            written = []

            def write_output(path, payload, *, force, written=written):
                written.append(path)
                raise AssertionError("file was written")

            def open_image(part):
                raise AssertionError("image was opened")

            code, line = world.run(
                SECRET_PATH,
                open_image=open_image,
                write_output=write_output,
                force=True,
            )
            self.assertEqual((code, line), (1, "FAILED: external HTR request export " + CODE_DISABLED))
            self.assertEqual(written, [])
            self._assert_clean(line)

    def test_nothing_to_send_does_not_write(self):
        world = _World(lines=[_Line(10, mask=[[5, 5], [5, 5], [5, 5]])])
        written = []

        def write_output(path, payload, *, force):
            written.append(path)

        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / OUTPUT_NAME)
            code, line = world.run(path, write_output=write_output)
            self.assertFalse(Path(path).exists())
        self.assertEqual((code, line), (1, "FAILED: external HTR request export " + CODE_NOTHING))
        self.assertEqual(written, [])
        self.assertEqual(len(world.opened), 1)
        self._assert_clean(line)

    def test_existing_file_is_kept_unless_forced(self):
        world = _World()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / OUTPUT_NAME
            path.write_text("keep-me", encoding="utf-8")
            code, line = world.run(str(path))
            self.assertEqual((code, line), (1, "FAILED: external HTR request export " + CODE_EXISTS))
            self.assertEqual(path.read_text(encoding="utf-8"), "keep-me")
            self._assert_clean(line)

            code, line = world.run(str(path), force=1)
            self.assertEqual((code, line), (1, "FAILED: external HTR request export " + CODE_EXISTS))
            self.assertEqual(path.read_text(encoding="utf-8"), "keep-me")

            code, line = world.run(str(path), force=True)
            raw = path.read_text(encoding="utf-8")
        self.assertEqual(code, 0)
        self.assertEqual(line, "OK: external HTR request exported lines=1 skipped=0")
        self.assertNotEqual(raw, "keep-me")
        parsed = parse_recognize_request(json.loads(raw))
        self.assertEqual([item.line_id for item in parsed.lines], ["10"])
        self.assertNotIn(parsed.lines[0].image, line)
        self._assert_clean(line)

    def test_write_failure_is_a_fixed_line(self):
        world = _World()

        def write_output(path, payload, *, force):
            raise OutputUnavailable(SECRET_PATH + " permission denied")

        code, line = world.run(SECRET_PATH + "/" + OUTPUT_NAME, write_output=write_output)
        self.assertEqual((code, line), (1, "FAILED: external HTR request export " + CODE_OUTPUT))
        self._assert_clean(line)

        def explode(path, payload, *, force):
            raise OSError(SECRET_PATH + " disk full " + OUTPUT_NAME)

        code, line = world.run(SECRET_PATH, write_output=explode)
        self.assertEqual((code, line), (1, "FAILED: external HTR request export " + CODE_OUTPUT))
        self._assert_clean(line)

    def test_failed_write_leaves_the_destination_untouched(self):
        payload = {"marker": "complete"}

        def disk_full(*_args, **_kwargs):
            raise OSError(SECRET_PATH + " disk full " + OUTPUT_NAME)

        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            existing = directory / OUTPUT_NAME
            existing.write_text("keep-me", encoding="utf-8")
            fresh = directory / "fresh-request.json"

            with patch("ai.external_htr_export.os.fsync", side_effect=disk_full):
                with self.assertRaises(OutputUnavailable) as caught:
                    write_request_json(str(existing), payload, force=True)
            self.assertEqual(caught.exception.args, ())
            self.assertEqual(existing.read_text(encoding="utf-8"), "keep-me")
            self.assertEqual(set(directory.iterdir()), {existing})

            with patch("ai.external_htr_export.os.fsync", side_effect=disk_full):
                with self.assertRaises(OutputUnavailable) as caught:
                    write_request_json(str(fresh), payload, force=False)
            self.assertEqual(caught.exception.args, ())
            self.assertFalse(fresh.exists())
            self.assertEqual(set(directory.iterdir()), {existing})

            with patch(
                "ai.external_htr_export.tempfile.mkstemp",
                side_effect=AssertionError("temp file"),
            ):
                with self.assertRaises(OutputExists) as caught:
                    write_request_json(str(existing), payload, force=False)
            self.assertEqual(caught.exception.args, ())
            self.assertEqual(existing.read_text(encoding="utf-8"), "keep-me")
            self.assertEqual(set(directory.iterdir()), {existing})

            world = _World()
            with patch("ai.external_htr_export.os.fsync", side_effect=disk_full):
                code, line = world.run(str(existing), force=True)
            self.assertEqual(
                (code, line),
                (1, "FAILED: external HTR request export " + CODE_OUTPUT),
            )
            self.assertEqual(existing.read_text(encoding="utf-8"), "keep-me")
            self.assertEqual(set(directory.iterdir()), {existing})
            self._assert_clean(line)

            with patch("ai.external_htr_export.os.fsync", side_effect=disk_full):
                code, line = world.run(str(fresh))
            self.assertEqual(
                (code, line),
                (1, "FAILED: external HTR request export " + CODE_OUTPUT),
            )
            self.assertFalse(fresh.exists())
            self.assertEqual(set(directory.iterdir()), {existing})
            self._assert_clean(line)

    def test_missing_config_part_and_image_are_fixed(self):
        world = _World()

        def missing_config(config_id):
            raise ConfigNotFound(SECRET_PATH)

        code, line = world.run(
            SECRET_PATH,
            fetch_config=missing_config,
            open_image=self._forbid_open,
            write_output=self._forbid_write,
        )
        self.assertEqual((code, line), (1, "FAILED: external HTR request export " + CODE_CONFIG))
        self._assert_clean(line)

        def missing_part(part_id):
            raise PartNotFound(TITLE + SECRET_PATH)

        code, line = world.run(
            SECRET_PATH,
            fetch_part=missing_part,
            open_image=self._forbid_open,
            write_output=self._forbid_write,
        )
        self.assertEqual((code, line), (1, "FAILED: external HTR request export " + CODE_PART))
        self._assert_clean(line)

        def explode(part):
            raise RuntimeError(SECRET_PATH + " decoder exploded")

        code, line = world.run(
            SECRET_PATH,
            open_image=explode,
            write_output=self._forbid_write,
        )
        self.assertEqual((code, line), (1, "FAILED: external HTR request export " + CODE_IMAGE))
        self._assert_clean(line)

        code, line = world.run(
            SECRET_PATH,
            open_image=lambda part: SECRET_PATH,
            write_output=self._forbid_write,
        )
        self.assertEqual((code, line), (1, "FAILED: external HTR request export " + CODE_IMAGE))
        self.assertNotIn(SECRET_PATH, line)

    def test_loader_exception_text_is_not_printed(self):
        world = _World()

        def fetch_config(config_id):
            raise RuntimeError(ENDPOINT + TOKEN + SECRET_PATH)

        code, line = world.run(
            SECRET_PATH,
            fetch_config=fetch_config,
            open_image=self._forbid_open,
            write_output=self._forbid_write,
        )
        self.assertEqual((code, line), (1, "FAILED: external HTR request export " + CODE_INTERNAL))
        self._assert_clean(line)

    def _forbid_open(self, part):
        raise AssertionError("image was opened")

    def _forbid_write(self, path, payload, *, force):
        raise AssertionError("file was written")

    def _assert_clean(self, text):
        self.assertNotIn("\n", text)
        for secret in _secrets():
            self.assertNotIn(secret, text, secret)


class IsolationTests(unittest.TestCase):
    def test_helper_does_not_call_an_engine_or_write_a_transcription(self):
        text = (AI_DIR / "external_htr_export.py").read_text()
        tree = ast.parse(text)
        top = []
        for node in tree.body:
            if isinstance(node, ast.Import):
                top.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                top.append(node.module)
        self.assertEqual(top, [
            "__future__",
            "json",
            "os",
            "tempfile",
            "ai.external_htr_image",
            "ai.external_htr_plan",
        ])
        for banned in (
            "htr_engine_client",
            "recognize_lines",
            "external_htr_audit",
            "external_htr_service",
            "record_external_htr_apply",
            "record_external_htr_plan",
            "LineTranscription",
            "ai_transcribe",
            "endpoint_url",
            "DocumentPart",
            "Image.open",
            "urllib",
            "requests",
            "django",
            "crop_line",
        ):
            self.assertNotIn(banned, text, banned)
        self.assertIn('separators=(",", ":")', text)

    def test_command_opens_the_page_and_does_not_call_an_engine(self):
        text = COMMAND.read_text()
        self.assertIn("Image.open(part.image.path)", text)
        self.assertIn("force=options[\"force\"]", text)
        self.assertIn("stdout.write", text)
        self.assertIn("sys.exit", text)
        self.assertEqual(text.count("stdout.write"), 1)
        self.assertNotIn("stderr", text)
        for banned in (
            "htr_engine_client",
            "recognize_lines",
            "external_htr_audit",
            "external_htr_service",
            "record_external_htr",
            "LineTranscription",
            "ai_transcribe",
            "endpoint_url",
            "--live",
            "encode_images",
            "crop_line",
        ):
            self.assertNotIn(banned, text, banned)
        tree = ast.parse(text)
        arguments = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                self.assertNotIn(node.attr, {"endpoint_url", "metadata", "filename", "title"})
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr != "add_argument":
                continue
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    arguments.append(arg.value)
        self.assertEqual(arguments, [
            "config_id",
            "document_part_id",
            "--model-id",
            "--engine",
            "--output",
            "--force",
        ])
        self.assertIn('action="store_true"', text)

    def test_plan_command_stays_dry_run_and_stage1_does_not_import_this(self):
        plan = PLAN_COMMAND.read_text()
        self.assertIn("encode_line=no_line_image", plan)
        self.assertIn("dry_run=True", plan)
        self.assertNotIn("--live", plan)
        self.assertNotIn("export_external_htr_request", plan)
        self.assertNotIn("external_htr_export", plan)
        for name in UNWIRED:
            text = (AI_DIR / name).read_text()
            self.assertNotIn("export_external_htr_request", text, name)
            self.assertNotIn("external_htr_export", text, name)
            self.assertNotIn("execute_external_htr_export", text, name)
        for name in RUNTIME:
            text = (AI_DIR / name).read_text()
            self.assertNotIn("export_external_htr_request", text, name)
            self.assertNotIn("external_htr_export", text, name)
            self.assertNotIn("execute_external_htr_export", text, name)


if __name__ == "__main__":
    unittest.main()
