"""Operator audit of one document part. No Django, no network, no transcription write."""
import ast
import unittest
from pathlib import Path

from PIL import Image

from ai.external_htr_apply_plan import (
    MESSAGE_READY as APPLY_READY,
    ExternalHtrApplyPlan,
    PlannedLine,
    plan_external_htr_apply,
)
from ai.external_htr_audit import (
    STATUS_COMPLETED,
    STATUS_FAILED,
    record_external_htr_apply,
    record_external_htr_plan,
)
from ai.external_htr_audit_command import (
    CODE_CONFIG,
    CODE_DISABLED,
    CODE_IMAGE,
    CODE_INTERNAL,
    CODE_NOTHING,
    CODE_PART,
    ConfigNotFound,
    PartNotFound,
    execute_external_htr_audit,
)
from ai.external_htr_live import MESSAGE_CLIENT, ExternalHtrLiveResult
from ai.external_htr_plan import MESSAGE_NOTHING, ExternalHtrPlan
from ai.external_htr_request import SkippedLine

AI_DIR = Path(__file__).resolve().parents[1]
COMMAND = AI_DIR / "management" / "commands" / "run_external_htr_audit.py"
PLAN_COMMAND = AI_DIR / "management" / "commands" / "plan_external_htr.py"
LIVE_COMMAND = AI_DIR / "management" / "commands" / "check_external_htr_live.py"
EXPORT_COMMAND = AI_DIR / "management" / "commands" / "export_external_htr_request.py"
WORKFLOW = AI_DIR / "docs" / "external-htr-operator-workflow.md"
MASK = [[0, 0], [10, 0], [10, 4], [0, 4]]
BASELINE = [[0, 2], [10, 2]]
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
UNWIRED = (
    "management/commands/plan_external_htr.py",
    "external_htr_plan_command.py",
    "external_htr_service.py",
    "external_htr_live.py",
    "external_htr_live_command.py",
    "management/commands/check_external_htr_live.py",
    "external_htr_export.py",
    "management/commands/export_external_htr_request.py",
    "tasks.py",
)
JOB_FIELDS = (
    "model_id", "status", "layer_source", "engine", "model_version", "api_version",
    "code", "message", "requested_line_count", "skipped_line_count", "result_count",
    "warning_count", "empty_text_count",
)
LINE_FIELDS = ("line_id", "text", "code", "status", "warnings")


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
        self.saved = 0

    def save(self, update_fields=None):
        self.saved += 1
        self.update_fields = list(update_fields)


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


def _page():
    image = Image.new("RGB", (40, 30), (0, 0, 0))
    image.putpixel((0, 0), (1, 2, 3))
    image.putpixel((39, 29), (9, 9, 9))
    image.filename = SECRET_PATH
    return image


def _rect(left, top, right, bottom):
    return [[left, top], [right, top], [right, bottom], [left, bottom]]


def _response(line_ids, texts):
    return {
        "api_version": "1",
        "job_id": "plan",
        "engine": "pylaia",
        "model_id": "example-model",
        "model_version": "1",
        "results": [
            {
                "line_id": line_id,
                "text": text,
                "confidence": 0.5,
                "timing_ms": 4,
                "warnings": [],
            }
            for line_id, text in zip(line_ids, texts)
        ],
        "provenance": {
            "engine": "pylaia",
            "model_id": "example-model",
            "model_version": "1",
            "api_version": "1",
        },
        "timing_ms": 0,
        "resources": {},
    }


def _scalars(job, lines):
    parts = [str(getattr(job, name, "")) for name in JOB_FIELDS]
    for line in lines:
        parts.extend(str(getattr(line, name, "")) for name in LINE_FIELDS)
    return "\n".join(parts)


class _World:
    def __init__(self, lines=None, enabled=True):
        self.jobs = _Model()
        self.lines = _Model()
        self.atomic = _Atomic()
        self.opened = []
        self.calls = []
        self.request = None
        self.image = ""
        self.document = type("Document", (), {"pk": 7, "title": TITLE})()
        self.part = type("Part", (), {
            "pk": 8,
            "document": self.document,
            "filename": SECRET_PATH,
        })()
        self.config = type("Config", (), {
            "pk": 3,
            "name": "PyLaia smoke",
            "enabled": enabled,
            "endpoint_url": ENDPOINT,
            "metadata": {"api_key": TOKEN},
        })()
        self.part_lines = lines if lines is not None else [_Line(10)]

    def fetch_config(self, config_id):
        self.config_id = config_id
        return self.config

    def fetch_part(self, part_id):
        self.part_id = part_id
        return self.part, self.part_lines

    def open_image(self, part):
        self.opened.append(part)
        return _page()

    def record_plan(self, plan, *, document, config, part=None, created_by=None):
        self.created_by = created_by
        return record_external_htr_plan(
            plan,
            document=document,
            config=config,
            part=part,
            created_by=created_by,
            job_model=self.jobs,
            line_model=self.lines,
            atomic=self.atomic,
        )

    def record_apply(self, job, plan):
        return record_external_htr_apply(
            job, plan, line_model=self.lines, atomic=self.atomic,
        )

    def run_live(self, config, request):
        self.calls.append(config)
        self.request = request
        self.image = request["lines"][0]["image"]
        applied = plan_external_htr_apply(
            request, _response([line["line_id"] for line in request["lines"]], ["kept"]),
        )
        return ExternalHtrLiveResult(True, None, "recognition response is ready", applied)

    def execute(self, **overrides):
        kwargs = {
            "model_id": "example-model",
            "engine": "pylaia",
            "fetch_config": self.fetch_config,
            "fetch_part": self.fetch_part,
            "open_image": self.open_image,
            "record_plan": self.record_plan,
            "record_apply": self.record_apply,
            "run": self.run_live,
        }
        kwargs.update(overrides)
        return execute_external_htr_audit(3, 8, **kwargs)


class AuditCommandTests(unittest.TestCase):
    def test_success_records_a_completed_audit(self):
        world = _World(lines=[
            _Line(10),
            _Line(99, mask=_rect(-80, -40, -40, -20)),
        ])
        code, line = world.execute()
        job = world.jobs.objects.rows[0]
        stored = world.lines.objects.rows
        self.assertEqual(code, 0)
        self.assertEqual(
            line,
            "OK: external HTR audit job 1 completed results=1 skipped=1",
        )
        self.assertEqual(len(world.calls), 1)
        self.assertIs(world.calls[0], world.config)
        self.assertEqual([item["line_id"] for item in world.request["lines"]], ["10"])
        self.assertTrue(world.image)
        self.assertEqual(job.status, STATUS_COMPLETED)
        self.assertEqual(job.layer_source, "external-htr:pylaia:example-model")
        self.assertEqual(job.engine, "pylaia")
        self.assertEqual(job.model_id, "example-model")
        self.assertEqual(job.model_version, "1")
        self.assertEqual(job.api_version, "1")
        self.assertEqual(job.message, APPLY_READY)
        self.assertEqual(job.result_count, 1)
        self.assertEqual(job.skipped_line_count, 1)
        self.assertIsNone(job.created_by)
        self.assertIs(job.document, world.document)
        self.assertIs(job.part, world.part)
        included = [row for row in stored if row.status == "included"]
        skipped = [row for row in stored if row.status == "skipped"]
        self.assertEqual([row.text for row in included], ["kept"])
        self.assertEqual([row.text for row in skipped], [""])
        self._assert_clean(line, job, stored, world.image)

    def test_disabled_config_stops_before_image_live_and_audit(self):
        for enabled in (False, 1, None):
            world = _World(enabled=enabled)
            world.open_image = self._forbidden
            world.run_live = self._forbidden
            world.record_plan = self._forbidden
            code, line = world.execute()
            self.assertEqual(
                (code, line),
                (1, "FAILED: external HTR audit " + CODE_DISABLED),
            )
            self.assertEqual(world.jobs.objects.rows, [])
            self.assertEqual(world.opened, [])
            self._assert_clean(line, None, [], "")

    def test_image_open_failure_writes_no_audit(self):
        world = _World()

        def explode(part):
            raise RuntimeError(SECRET_PATH + " " + ENDPOINT + " " + RAW)

        world.open_image = explode
        code, line = world.execute()
        self.assertEqual((code, line), (1, "FAILED: external HTR audit " + CODE_IMAGE))
        self.assertEqual(world.jobs.objects.rows, [])
        self.assertEqual(world.calls, [])
        self._assert_clean(line, None, [], "")

        world = _World()
        world.open_image = lambda part: SECRET_PATH
        code, line = world.execute()
        self.assertEqual((code, line), (1, "FAILED: external HTR audit " + CODE_IMAGE))
        self.assertEqual(world.jobs.objects.rows, [])
        self._assert_clean(line, None, [], "")

    def test_missing_config_and_part_are_fixed(self):
        world = _World()

        def missing_config(config_id):
            raise ConfigNotFound

        def missing_part(part_id):
            raise PartNotFound

        code, line = world.execute(fetch_config=missing_config, fetch_part=self._forbidden)
        self.assertEqual((code, line), (1, "FAILED: external HTR audit " + CODE_CONFIG))
        code, line = world.execute(fetch_part=missing_part, open_image=self._forbidden)
        self.assertEqual((code, line), (1, "FAILED: external HTR audit " + CODE_PART))
        self.assertEqual(world.jobs.objects.rows, [])

    def test_nothing_to_send_records_a_failed_job_without_a_live_call(self):
        world = _World(lines=[_Line(10, mask=[[5, 5], [5, 5], [5, 5]])])
        code, line = world.execute()
        job = world.jobs.objects.rows[0]
        self.assertEqual((code, line), (1, "FAILED: external HTR audit " + CODE_NOTHING))
        self.assertEqual(world.calls, [])
        self.assertEqual(len(world.opened), 1)
        self.assertEqual(job.status, STATUS_FAILED)
        self.assertEqual(job.code, CODE_NOTHING)
        self.assertEqual(job.message, "nothing to send")
        self.assertEqual(job.result_count, 0)
        self.assertEqual([row.text for row in world.lines.objects.rows], [""])
        self.assertTrue(world.lines.objects.rows)
        self._assert_clean(line, job, world.lines.objects.rows, "")

    def test_live_failure_stores_a_fixed_code_and_no_payload(self):
        world = _World()
        image_holder = {}

        def fail(config, request):
            image_holder["image"] = request["lines"][0]["image"]
            hostile = ExternalHtrApplyPlan(
                would_write=False,
                code="timeout",
                message=ENDPOINT + TOKEN + RAW,
                layer_source="",
                engine=ENDPOINT,
                model_id=TOKEN,
                model_version="",
                api_version="",
                results=(PlannedLine(
                    line_id="10",
                    text=image_holder["image"],
                    confidence=None,
                    timing_ms=0,
                    warnings=(RAW,),
                ),),
                result_count=1,
                warning_count=1,
                empty_text_count=0,
            )
            return ExternalHtrLiveResult(False, "timeout", ENDPOINT + RAW, hostile)

        code, line = world.execute(run=fail)
        job = world.jobs.objects.rows[0]
        self.assertEqual((code, line), (1, "FAILED: external HTR audit timeout"))
        self.assertEqual(job.status, STATUS_FAILED)
        self.assertEqual(job.code, "timeout")
        self.assertEqual(job.message, MESSAGE_CLIENT)
        self.assertEqual(job.result_count, 0)
        self.assertEqual(job.engine, "pylaia")
        self.assertEqual(world.lines.objects.rows, [])
        self._assert_clean(line, job, [], image_holder["image"])

    def test_live_exception_text_is_not_stored(self):
        world = _World()
        image_holder = {}

        def explode(config, request):
            image_holder["image"] = request["lines"][0]["image"]
            raise RuntimeError(image_holder["image"] + ENDPOINT + TOKEN + SECRET_PATH + RAW)

        code, line = world.execute(run=explode)
        job = world.jobs.objects.rows[0]
        self.assertEqual((code, line), (1, "FAILED: external HTR audit " + CODE_INTERNAL))
        self.assertEqual(job.status, STATUS_FAILED)
        self.assertEqual(job.code, CODE_INTERNAL)
        self.assertEqual(job.message, MESSAGE_CLIENT)
        self.assertEqual(job.result_count, 0)
        self.assertEqual(world.lines.objects.rows, [])
        self._assert_clean(line, job, [], image_holder["image"])

    def test_hostile_failure_code_is_replaced(self):
        blob = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAA"
        for hostile in (
            ENDPOINT, TOKEN, RAW, SECRET_PATH, blob, "secrettoken",
            "RuntimeError: " + SECRET_PATH,
        ):
            world = _World()

            def fail(config, request, hostile=hostile):
                return ExternalHtrLiveResult(False, hostile, hostile, None)

            code, line = world.execute(run=fail)
            job = world.jobs.objects.rows[0]
            self.assertEqual(
                (code, line),
                (1, "FAILED: external HTR audit " + CODE_INTERNAL),
            )
            self.assertEqual(job.code, CODE_INTERNAL)
            self.assertEqual(job.message, MESSAGE_CLIENT)
            self.assertEqual(job.result_count, 0)
            self.assertEqual(world.lines.objects.rows, [])
            self._assert_clean(line, job, [], blob)

    def test_skip_reason_cannot_carry_a_payload(self):
        world = _World()
        payload = ENDPOINT + TOKEN + "iVBORw0KGgo"

        def plan(*args, **kwargs):
            return ExternalHtrPlan(
                False, None, 0, (SkippedLine("10", payload),),
                "pylaia", "PyLaia smoke", "example-model",
                CODE_NOTHING, MESSAGE_NOTHING,
            )

        code, line = world.execute(plan=plan)
        job = world.jobs.objects.rows[0]
        stored = world.lines.objects.rows
        self.assertEqual((code, line), (1, "FAILED: external HTR audit " + CODE_NOTHING))
        self.assertEqual(world.calls, [])
        self.assertEqual(job.status, STATUS_FAILED)
        self.assertEqual(job.code, CODE_NOTHING)
        self.assertEqual(job.message, MESSAGE_NOTHING)
        self.assertEqual([row.code for row in stored], ["no_image"])
        self.assertEqual([row.text for row in stored], [""])
        self._assert_clean(line, job, stored, payload)

    def test_record_exception_is_a_fixed_line(self):
        world = _World()

        def explode(*args, **kwargs):
            raise RuntimeError(ENDPOINT + TOKEN + RAW)

        code, line = world.execute(record_plan=explode)
        self.assertEqual((code, line), (1, "FAILED: external HTR audit " + CODE_INTERNAL))
        self.assertEqual(world.jobs.objects.rows, [])
        self.assertEqual(world.calls, [])
        self._assert_clean(line, None, [], "")

    def _forbidden(self, *args, **kwargs):
        raise AssertionError("forbidden")

    def _assert_clean(self, line, job, lines, image):
        self.assertNotIn("\n", line)
        blob = line if job is None else line + "\n" + _scalars(job, lines)
        for secret in (ENDPOINT, TOKEN, TITLE, SECRET_PATH, RAW, image):
            if secret:
                self.assertNotIn(secret, blob)


class IsolationTests(unittest.TestCase):
    def test_helper_reaches_the_engine_only_through_the_live_helper(self):
        text = (AI_DIR / "external_htr_audit_command.py").read_text()
        tree = ast.parse(text)
        modules = []
        for node in tree.body:
            if isinstance(node, ast.ImportFrom) and node.module and node.module != "__future__":
                modules.append(node.module)
        self.assertEqual(modules, [
            "ai.external_htr_apply_plan",
            "ai.external_htr_audit",
            "ai.external_htr_image",
            "ai.external_htr_live",
            "ai.external_htr_plan",
        ])
        for banned in (
            "htr_engine_client",
            "recognize_lines",
            "LineTranscription",
            "ai_transcribe",
            "endpoint_url",
            "urllib",
            "requests",
            "crop_line",
            "metadata",
        ):
            self.assertNotIn(banned, text, banned)
        self.assertIn("run_external_htr_live", text)
        self.assertIn("record_external_htr_plan", text)
        self.assertIn("record_external_htr_apply", text)
        self.assertIn("results=()", text)

    def test_command_is_an_audit_and_other_entry_points_stay_separate(self):
        text = COMMAND.read_text()
        self.assertIn("Image.open(part.image.path)", text)
        self.assertIn("audit", text)
        self.assertIn("does not write transcription output", text)
        self.assertNotIn("recognize_lines", text)
        self.assertNotIn("htr_engine_client", text)
        self.assertNotIn("--live", text)
        self.assertNotIn("LineTranscription", text)
        tree = ast.parse(text)
        arguments = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Attribute) or func.attr != "add_argument":
                continue
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    arguments.append(arg.value)
        self.assertEqual(arguments, [
            "config_id",
            "document_part_id",
            "--engine",
            "--model-id",
        ])
        plan = PLAN_COMMAND.read_text()
        self.assertIn("dry_run=True", plan)
        self.assertNotIn("--live", plan)
        self.assertNotIn("run_external_htr_audit", plan)
        self.assertNotIn("execute_external_htr_audit", plan)
        for name in (LIVE_COMMAND, EXPORT_COMMAND):
            other = name.read_text()
            self.assertNotIn("run_external_htr_audit", other, name.name)
            self.assertNotIn("execute_external_htr_audit", other, name.name)
        for name in UNWIRED:
            other = (AI_DIR / name).read_text()
            self.assertNotIn("external_htr_audit_command", other, name)
            self.assertNotIn("execute_external_htr_audit", other, name)
            self.assertNotIn("run_external_htr_audit", other, name)
        for name in RUNTIME:
            other = (AI_DIR / name).read_text()
            self.assertNotIn("external_htr_audit_command", other, name)
            self.assertNotIn("execute_external_htr_audit", other, name)
            self.assertNotIn("run_external_htr_audit", other, name)
        workflow = WORKFLOW.read_text()
        self.assertIn("Phase A", workflow)
        self.assertIn("run_external_htr_audit", workflow)
        self.assertIn("does not write a transcription layer", workflow)
        self.assertNotIn("Stage 2", workflow)
        self.assertNotIn("roadmap", workflow.lower())


if __name__ == "__main__":
    unittest.main()
