"""Operator dry-run plan command. No Django, no network, no transcription write."""
import ast
import base64
import io
import unittest
from pathlib import Path

from PIL import Image

from ai.external_htr_plan import plan_external_htr
from ai.external_htr_plan_command import (
    CODE_CONFIG,
    CODE_IMAGE,
    CODE_INTERNAL,
    CODE_LIVE,
    CODE_PART,
    ConfigNotFound,
    PartNotFound,
    execute_external_htr_dry_run,
)
from ai.external_htr_service import run_external_htr_dry_run

AI_DIR = Path(__file__).resolve().parents[1]
COMMAND = AI_DIR / "management" / "commands" / "plan_external_htr.py"
MASK = [[0, 0], [10, 0], [10, 4], [0, 4]]
BASELINE = [[0, 2], [10, 2]]
IMAGE = "QUJDRA=="
ENDPOINT = "http://127.0.0.1:9/secret-endpoint"
TOKEN = "secret-token"
TITLE = "Secret Title"
SECRET_PATH = "/tmp/secret-document"
RAW = "raw-response-body"
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


class _Atomic:
    def __call__(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _Row:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)

    def save(self, update_fields=None):
        return None


class _Objects:
    def __init__(self):
        self.rows = []

    def create(self, **kwargs):
        row = _Row(**kwargs)
        row.pk = len(self.rows) + 1
        self.rows.append(row)
        return row

    def filter(self, **kwargs):
        return [
            row for row in self.rows
            if all(getattr(row, key) == value for key, value in kwargs.items())
        ]


class _Model:
    def __init__(self):
        self.objects = _Objects()


class _Line:
    def __init__(self, pk, mask=MASK, baseline=BASELINE):
        self.pk = pk
        self.mask = mask
        self.baseline = baseline


class _World:
    def __init__(self, enabled=True):
        self.jobs = _Model()
        self.lines = _Model()
        self.atomic = _Atomic()
        self.calls = []
        self.fetched_config = 0
        self.fetched_part = 0
        self.document = type("Document", (), {
            "pk": 7, "title": TITLE, "path": SECRET_PATH,
        })()
        self.part = type("Part", (), {
            "pk": 8, "document": self.document, "filename": SECRET_PATH,
        })()
        self.config = type("Config", (), {
            "pk": 3,
            "name": "PyLaia smoke",
            "enabled": enabled,
            "endpoint_url": ENDPOINT,
            "metadata": {"api_key": TOKEN},
        })()
        self.page = [_Line(10), _Line(20, mask=None)]

    def encode(self, line):
        self.calls.append(line.pk)
        return IMAGE

    def fetch_config(self, config_id):
        self.fetched_config += 1
        if config_id != 3:
            raise ConfigNotFound(ENDPOINT)
        return self.config

    def fetch_part(self, part_id):
        self.fetched_part += 1
        if part_id != 8:
            raise PartNotFound(SECRET_PATH)
        return self.part, self.page

    def run(self, **extra):
        kwargs = {
            "model_id": "example-model",
            "engine": "pylaia",
            "fetch_config": self.fetch_config,
            "fetch_part": self.fetch_part,
            "run": run_external_htr_dry_run,
            "encode_line": self.encode,
            "job_model": self.jobs,
            "line_model": self.lines,
            "atomic": self.atomic,
        }
        kwargs.update(extra)
        return execute_external_htr_dry_run(3, 8, **kwargs)


def _stored(world, line):
    parts = [line]
    for job in world.jobs.objects.rows:
        parts.append(str(job.message))
        parts.append(str(job.code))
    for row in world.lines.objects.rows:
        parts.append(str(row.text))
        parts.append(str(row.code))
    return "\n".join(parts)


def _dump(world):
    parts = []
    for job in world.jobs.objects.rows:
        parts.append(repr(vars(job)))
    for row in world.lines.objects.rows:
        parts.append(repr(vars(row)))
    return "\n".join(parts)


def _page(width=40, height=30):
    image = Image.new("RGB", (width, height), (255, 255, 255))
    image.filename = SECRET_PATH
    return image


def _rect(x0, y0, x1, y1):
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


def _png(payload):
    opened = Image.open(io.BytesIO(base64.standard_b64decode(payload)))
    opened.load()
    return opened


class CommandResultTests(unittest.TestCase):
    def test_success_exits_zero(self):
        world = _World()
        code, line = world.run()
        self.assertEqual(code, 0)
        self.assertEqual(
            line,
            "OK: external HTR dry run planned job 1 (planned, included=1, skipped=1)",
        )
        self.assertEqual(world.calls, [10])
        self.assertEqual(len(world.jobs.objects.rows), 1)
        self.assertEqual(world.jobs.objects.rows[0].created_by, None)
        self.assertEqual(world.jobs.objects.rows[0].document, world.document)
        self._assert_clean(world, line)

    def test_disabled_config_fails_without_encoding(self):
        world = _World(enabled=False)
        code, line = world.run()
        self.assertEqual(code, 1)
        self.assertEqual(line, "FAILED: external HTR dry run disabled")
        self.assertEqual(world.calls, [])
        self.assertEqual(len(world.jobs.objects.rows), 1)
        self.assertEqual(world.lines.objects.rows, [])
        self._assert_clean(world, line)

    def test_all_skipped_lines_fail_with_skipped_rows(self):
        world = _World()
        world.page = [_Line(10, mask=None), _Line(20, baseline=None)]
        code, line = world.run()
        self.assertEqual(code, 1)
        self.assertEqual(line, "FAILED: external HTR dry run nothing_to_send")
        self.assertEqual(world.calls, [])
        self.assertEqual(
            [(row.line_id, row.code) for row in world.lines.objects.rows],
            [("10", "no_mask"), ("20", "no_baseline")],
        )
        self._assert_clean(world, line)

    def test_default_encoder_does_not_build_an_image(self):
        world = _World()
        world.page = [_Line(10)]
        code, line = world.run(encode_line=None)
        self.assertEqual(code, 1)
        self.assertEqual(line, "FAILED: external HTR dry run nothing_to_send")
        self.assertEqual(world.calls, [])
        self.assertEqual(
            [(row.line_id, row.code, row.text) for row in world.lines.objects.rows],
            [("10", "no_image", "")],
        )
        self._assert_clean(world, line)

    def test_missing_config_exits_nonzero(self):
        world = _World()

        def fetch_config(config_id):
            raise ConfigNotFound(ENDPOINT + RAW)

        def run(*args, **kwargs):
            raise AssertionError("dry run was called")

        code, line = world.run(fetch_config=fetch_config, run=run)
        self.assertEqual(code, 1)
        self.assertEqual(line, "FAILED: external HTR dry run " + CODE_CONFIG)
        self.assertEqual(world.fetched_part, 0)
        self.assertEqual(world.jobs.objects.rows, [])
        self._assert_clean(world, line)

    def test_missing_part_exits_nonzero(self):
        world = _World()

        def fetch_part(part_id):
            raise PartNotFound(SECRET_PATH + TITLE)

        def run(*args, **kwargs):
            raise AssertionError("dry run was called")

        code, line = world.run(fetch_part=fetch_part, run=run)
        self.assertEqual(code, 1)
        self.assertEqual(line, "FAILED: external HTR dry run " + CODE_PART)
        self.assertEqual(world.fetched_config, 1)
        self.assertEqual(world.jobs.objects.rows, [])
        self._assert_clean(world, line)

    def test_loader_exception_text_is_not_printed(self):
        world = _World()

        def fetch_config(config_id):
            raise RuntimeError(ENDPOINT + TOKEN + TITLE + SECRET_PATH + RAW)

        code, line = world.run(fetch_config=fetch_config)
        self.assertEqual(code, 1)
        self.assertEqual(line, "FAILED: external HTR dry run " + CODE_INTERNAL)
        self.assertEqual(world.fetched_part, 0)
        self._assert_clean(world, line)

    def test_live_mode_is_rejected_before_loading(self):
        world = _World()

        def run(*args, **kwargs):
            raise AssertionError("dry run was called")

        code, line = world.run(dry_run=False, run=run)
        self.assertEqual(code, 1)
        self.assertEqual(line, "FAILED: external HTR dry run " + CODE_LIVE)
        self.assertEqual(world.fetched_config, 0)
        self.assertEqual(world.fetched_part, 0)
        self.assertEqual(world.calls, [])
        self.assertEqual(world.jobs.objects.rows, [])
        self._assert_clean(world, line)

    def test_default_does_not_open_an_image(self):
        world = _World()
        world.page = [_Line(10)]
        opened = []

        def open_image(part):
            opened.append(part)
            return _page()

        code, line = world.run(encode_line=None, open_image=open_image)
        self.assertEqual(code, 1)
        self.assertEqual(line, "FAILED: external HTR dry run nothing_to_send")
        self.assertEqual(opened, [])
        self.assertEqual(
            [(row.line_id, row.code, row.text) for row in world.lines.objects.rows],
            [("10", "no_image", "")],
        )
        self._assert_clean(world, line)

    def test_encode_images_plans_a_cropped_line(self):
        world = _World()
        page = _page()
        page.putpixel((0, 0), (1, 2, 3))
        page.putpixel((39, 29), (9, 9, 9))
        world.page = [
            _Line(10),
            _Line(20, mask=_rect(-80, -40, -40, -20), baseline=[[-70, -30], [-50, -30]]),
        ]
        opened = []
        used = []
        holder = {}

        def open_image(part):
            opened.append(part)
            return page

        def decoy(line):
            used.append(line.pk)
            return IMAGE

        def spy(config, lines, **kwargs):
            holder["plan"] = plan_external_htr(
                config,
                lines,
                document_id=kwargs["document"].pk,
                part_id=kwargs["part"].pk,
                engine=kwargs["engine"],
                model_id=kwargs["model_id"],
                encode_line=kwargs["encode_line"],
            )
            return run_external_htr_dry_run(config, lines, **kwargs)

        code, line = world.run(
            encode_line=decoy,
            encode_images=True,
            open_image=open_image,
            run=spy,
        )
        self.assertEqual(code, 0)
        self.assertEqual(
            line,
            "OK: external HTR dry run planned job 1 (planned, included=1, skipped=1)",
        )
        self.assertEqual(opened, [world.part])
        self.assertEqual(used, [])
        payload = holder["plan"].request["lines"][0]["image"]
        self.assertFalse(payload.startswith("data:"))
        self.assertEqual([item["line_id"] for item in holder["plan"].request["lines"]], ["10"])
        opened_image = _png(payload)
        self.assertEqual(opened_image.size, (10, 4))
        self.assertEqual(opened_image.getpixel((0, 0)), (1, 2, 3))
        self.assertNotEqual(opened_image.size, page.size)
        self.assertEqual(
            [(row.line_id, row.code, row.text) for row in world.lines.objects.rows],
            [("20", "no_image", "")],
        )
        self.assertNotIn(payload, line)
        self.assertNotIn(payload, _dump(world))
        self.assertNotIn("request", vars(world.jobs.objects.rows[0]))
        self._assert_clean(world, line)
        self.assertNotIn(SECRET_PATH, payload)
        self.assertNotIn(TITLE, payload)

    def test_invalid_geometry_is_skipped_without_a_page_image(self):
        world = _World()
        opened = []

        def open_image(part):
            opened.append(part)
            return _page()

        world.page = [_Line(10, mask=[[5, 5], [5, 5], [5, 5]])]
        code, line = world.run(encode_images=True, open_image=open_image)
        self.assertEqual(code, 1)
        self.assertEqual(line, "FAILED: external HTR dry run nothing_to_send")
        self.assertEqual(
            [(row.line_id, row.code, row.text) for row in world.lines.objects.rows],
            [("10", "no_mask", "")],
        )
        self.assertEqual(len(opened), 1)
        self._assert_clean(world, line)

        world = _World()
        holder = {}

        def spy(config, lines, **kwargs):
            holder["plan"] = plan_external_htr(
                config,
                lines,
                document_id=kwargs["document"].pk,
                part_id=kwargs["part"].pk,
                engine=kwargs["engine"],
                model_id=kwargs["model_id"],
                encode_line=kwargs["encode_line"],
            )
            return run_external_htr_dry_run(config, lines, **kwargs)

        world.page = [_Line(
            10,
            mask=_rect(-80, -40, -40, -20),
            baseline=[[-70, -30], [-50, -30]],
        )]
        code, line = world.run(encode_images=True, open_image=open_image, run=spy)
        self.assertEqual(code, 1)
        self.assertEqual(line, "FAILED: external HTR dry run nothing_to_send")
        self.assertIsNone(holder["plan"].request)
        self.assertEqual(
            [(row.line_id, row.code, row.text) for row in world.lines.objects.rows],
            [("10", "no_image", "")],
        )
        self._assert_clean(world, line)

    def test_image_open_failure_is_a_fixed_line(self):
        world = _World()

        def explode(part):
            raise RuntimeError(SECRET_PATH + " decoder exploded")

        def run(*args, **kwargs):
            raise AssertionError("dry run was called")

        code, line = world.run(encode_images=True, open_image=explode, run=run)
        self.assertEqual(code, 1)
        self.assertEqual(line, "FAILED: external HTR dry run " + CODE_IMAGE)
        self.assertEqual(world.jobs.objects.rows, [])
        self.assertNotIn(SECRET_PATH, line)
        self.assertNotIn("decoder exploded", line)
        self._assert_clean(world, line)

        def give_path(part):
            return SECRET_PATH

        code, line = world.run(encode_images=True, open_image=give_path, run=run)
        self.assertEqual(line, "FAILED: external HTR dry run " + CODE_IMAGE)
        self.assertNotIn(SECRET_PATH, line)
        self.assertEqual(world.jobs.objects.rows, [])

    def test_disabled_encode_images_does_not_open_the_page(self):
        world = _World(enabled=False)
        opened = []

        def open_image(part):
            opened.append(part)
            return _page()

        code, line = world.run(encode_images=True, open_image=open_image)
        self.assertEqual(code, 1)
        self.assertEqual(line, "FAILED: external HTR dry run disabled")
        self.assertEqual(opened, [])
        self.assertEqual(world.calls, [])
        self._assert_clean(world, line)

    def test_live_refusal_does_not_open_an_image(self):
        world = _World()
        opened = []

        def open_image(part):
            opened.append(part)
            return _page()

        def run(*args, **kwargs):
            raise AssertionError("dry run was called")

        code, line = world.run(
            dry_run=False,
            encode_images=True,
            open_image=open_image,
            run=run,
        )
        self.assertEqual(code, 1)
        self.assertEqual(line, "FAILED: external HTR dry run " + CODE_LIVE)
        self.assertEqual(opened, [])
        self.assertEqual(world.fetched_config, 0)
        self.assertEqual(world.jobs.objects.rows, [])
        self._assert_clean(world, line)

    def _assert_clean(self, world, line):
        self.assertNotIn("\n", line)
        text = _stored(world, line)
        for secret in (IMAGE, ENDPOINT, TOKEN, TITLE, SECRET_PATH, RAW):
            self.assertNotIn(secret, text)


class IsolationTests(unittest.TestCase):
    def test_command_is_dry_run_only_and_does_not_call_an_engine(self):
        text = COMMAND.read_text()
        helper = (AI_DIR / "external_htr_plan_command.py").read_text()
        self.assertIn("run_external_htr_dry_run", text)
        self.assertIn("dry_run=True", text)
        self.assertIn("no_line_image", text)
        self.assertIn("encode_line=no_line_image", text)
        self.assertIn('encode_images=options["encode_images"]', text)
        self.assertIn("open_image=_open_part_image", text)
        self.assertIn("Image.open(part.image.path)", text)
        self.assertIn("--encode-images", text)
        self.assertIn('action="store_true"', text)
        self.assertNotIn("encode_images=True", text)
        self.assertNotIn("Image.open", helper)
        self.assertIn("stdout.write", text)
        self.assertIn("sys.exit", text)
        for banned in (
            "htr_engine_client",
            "recognize_lines",
            "LineTranscription",
            "ai_transcribe",
            "endpoint_url",
            "crop_line",
            "base64",
            "--live",
            "live=",
        ):
            self.assertNotIn(banned, text, banned)
            self.assertNotIn(banned, helper, banned)
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
            "config_id", "document_part_id", "--model-id", "--engine",
            "--encode-images",
        ])
        top = []
        helper_tree = ast.parse(helper)
        for node in helper_tree.body:
            if isinstance(node, ast.ImportFrom) and node.module:
                top.append(node.module)
        self.assertEqual(top, [
            "__future__",
            "ai.external_htr_image",
            "ai.external_htr_service",
        ])

    def test_stage1_modules_do_not_import_the_command(self):
        for name in RUNTIME:
            text = (AI_DIR / name).read_text()
            self.assertNotIn("external_htr_plan_command", text, name)
            self.assertNotIn("execute_external_htr_dry_run", text, name)
            self.assertNotIn("plan_external_htr", text, name)

    def test_admin_stays_list_only(self):
        text = (AI_DIR / "admin.py").read_text()
        self.assertNotIn("external_htr_plan_command", text)
        self.assertNotIn("plan_external_htr", text)
        tree = ast.parse(text)
        found = set()
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            if node.name not in {"ExternalHTRJobAdmin", "ExternalHTRLineResultAdmin"}:
                continue
            found.add(node.name)
            dumped = ast.dump(node)
            for banned in ("probe_configs", "htr_engine_client", "recognize_lines", "actions"):
                self.assertNotIn(banned, dumped, node.name)
        self.assertEqual(found, {"ExternalHTRJobAdmin", "ExternalHTRLineResultAdmin"})


if __name__ == "__main__":
    unittest.main()
