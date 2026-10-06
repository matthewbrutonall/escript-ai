"""External HTR audit persistence. No Django, no network, no transcription write."""
import ast
import unittest
from pathlib import Path

from ai.external_htr_apply_plan import (
    MESSAGE_INVALID,
    MESSAGE_READY,
    ExternalHtrApplyPlan,
    PlannedLine,
    plan_external_htr_apply,
)
from ai.external_htr_audit import (
    CODE_ALREADY,
    CODE_INVALID,
    LINE_INCLUDED,
    LINE_SKIPPED,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_PLANNED,
    ExternalHtrAuditError,
    record_external_htr_apply,
    record_external_htr_plan,
)
from ai.external_htr_plan import (
    CODE_DISABLED,
    CODE_NOTHING,
    MESSAGE_DISABLED,
    MESSAGE_NOTHING,
    MESSAGE_READY as PLAN_READY,
    plan_external_htr,
)

AI_DIR = Path(__file__).resolve().parents[1]
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
JOB_TEXT = (
    "model_id", "status", "layer_source", "engine", "model_version", "api_version",
    "code", "message", "requested_line_count", "skipped_line_count", "result_count",
    "warning_count", "empty_text_count",
)
LINE_TEXT = (
    "line_id", "position", "text", "confidence", "timing_ms", "warnings", "status", "code",
)
FORBIDDEN = {
    "endpoint_url", "metadata", "image", "request", "response", "title", "path",
    "raw_body", "response_body",
}


class _Atomic:
    def __init__(self):
        self.entered = 0

    def __call__(self):
        return self

    def __enter__(self):
        self.entered += 1
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _Row:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)
        self.saved = 0
        self.update_fields = None

    def save(self, update_fields=None):
        self.saved += 1
        self.update_fields = list(update_fields)


class _Objects:
    def __init__(self):
        self.rows = []

    def create(self, **kwargs):
        row = _Row(**kwargs)
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


class _Store:
    def __init__(self):
        self.jobs = _Model()
        self.lines = _Model()
        self.atomic = _Atomic()
        self.document = type("Document", (), {
            "pk": 7, "title": TITLE, "path": SECRET_PATH,
        })()
        self.part = type("Part", (), {"pk": 8, "filename": SECRET_PATH})()
        self.config = type("Config", (), {
            "pk": 3,
            "name": "PyLaia smoke",
            "enabled": True,
            "endpoint_url": ENDPOINT,
            "metadata": {"api_key": TOKEN},
        })()

    def plan(self, lines, **overrides):
        config = self.config
        if "enabled" in overrides:
            config = type(self.config)()
            config.__dict__.update(self.config.__dict__)
            config.enabled = overrides.pop("enabled")
        kwargs = {
            "document_id": self.document.pk,
            "part_id": self.part.pk,
            "engine": "pylaia",
            "model_id": "example-model",
            "encode_line": lambda line: IMAGE,
        }
        kwargs.update(overrides)
        return plan_external_htr(config, lines, **kwargs)

    def record(self, plan, part=None):
        return record_external_htr_plan(
            plan,
            document=self.document,
            config=self.config,
            part=part,
            job_model=self.jobs,
            line_model=self.lines,
            atomic=self.atomic,
        )

    def apply(self, job, plan):
        return record_external_htr_apply(
            job, plan, line_model=self.lines, atomic=self.atomic,
        )


def _stored(job, lines):
    parts = [str(getattr(job, name)) for name in JOB_TEXT]
    for line in lines:
        parts.extend(str(getattr(line, name)) for name in LINE_TEXT)
    return "\n".join(parts)


def _response(line_ids, texts, **overrides):
    engine = overrides.get("engine", "pylaia")
    model_id = overrides.get("model_id", "example-model")
    model_version = overrides.get("model_version", "1")
    return {
        "api_version": "1",
        "job_id": overrides.get("job_id", "plan"),
        "engine": engine,
        "model_id": model_id,
        "model_version": model_version,
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
            "engine": engine,
            "model_id": model_id,
            "model_version": model_version,
            "api_version": "1",
        },
        "timing_ms": 0,
        "resources": {},
    }


class RecordPlanTests(unittest.TestCase):
    def test_request_plan_stores_one_job_and_skipped_rows(self):
        store = _Store()
        bad = type("Bad", (), {"pk": None, "mask": MASK, "baseline": BASELINE})()
        plan = store.plan([
            _Line(10),
            _Line(20, mask=None),
            bad,
        ])
        job = store.record(plan, part=store.part)
        lines = store.lines.objects.rows
        self.assertEqual(len(store.jobs.objects.rows), 1)
        self.assertEqual(store.atomic.entered, 1)
        self.assertIs(job.document, store.document)
        self.assertIs(job.part, store.part)
        self.assertIs(job.config, store.config)
        self.assertEqual(job.status, STATUS_PLANNED)
        self.assertIsNone(job.layer_source)
        self.assertEqual(job.engine, "pylaia")
        self.assertEqual(job.model_id, "example-model")
        self.assertEqual(job.model_version, "")
        self.assertEqual(job.api_version, "")
        self.assertEqual(job.requested_line_count, 1)
        self.assertEqual(job.skipped_line_count, 2)
        self.assertEqual(job.result_count, 0)
        self.assertEqual(job.warning_count, 0)
        self.assertEqual(job.empty_text_count, 0)
        self.assertEqual(job.code, "")
        self.assertEqual(job.message, PLAN_READY)
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0].line_id, "20")
        self.assertEqual(lines[0].position, 0)
        self.assertEqual(lines[0].status, LINE_SKIPPED)
        self.assertEqual(lines[0].code, "no_mask")
        self.assertEqual(lines[0].text, "")
        self.assertIsNone(lines[0].confidence)
        self.assertEqual(lines[0].warnings, [])
        self.assertTrue(FORBIDDEN.isdisjoint(vars(job)))
        self.assertTrue(FORBIDDEN.isdisjoint(vars(lines[0])))

    def test_duplicate_skip_keeps_one_row_and_the_full_count(self):
        store = _Store()
        plan = store.plan([_Line(10), _Line(10), _Line(10)])
        job = store.record(plan)
        self.assertEqual(job.requested_line_count, 1)
        self.assertEqual(job.skipped_line_count, 2)
        self.assertEqual(len(store.lines.objects.rows), 1)
        self.assertEqual(store.lines.objects.rows[0].line_id, "10")
        self.assertEqual(store.lines.objects.rows[0].code, "duplicate_line_id")
        self.assertEqual(store.lines.objects.rows[0].position, 0)

    def test_nothing_to_send_is_a_failed_job_with_skipped_rows(self):
        store = _Store()
        plan = store.plan([_Line(20, mask=None), _Line(21, baseline=None)])
        job = store.record(plan)
        self.assertFalse(plan.would_send)
        self.assertEqual(plan.code, CODE_NOTHING)
        self.assertEqual(job.status, STATUS_FAILED)
        self.assertEqual(job.code, CODE_NOTHING)
        self.assertEqual(job.message, MESSAGE_NOTHING)
        self.assertEqual(job.requested_line_count, 0)
        self.assertEqual(job.skipped_line_count, 2)
        self.assertEqual(
            [(row.line_id, row.position, row.code) for row in store.lines.objects.rows],
            [("20", 0, "no_mask"), ("21", 1, "no_baseline")],
        )

    def test_disabled_plan_stores_no_lines(self):
        store = _Store()
        plan = store.plan([_Line(10)], enabled=False)
        job = store.record(plan)
        self.assertEqual(plan.code, CODE_DISABLED)
        self.assertEqual(job.status, STATUS_FAILED)
        self.assertEqual(job.code, CODE_DISABLED)
        self.assertEqual(job.message, MESSAGE_DISABLED)
        self.assertEqual(job.skipped_line_count, 0)
        self.assertEqual(store.lines.objects.rows, [])

    def test_skipped_warning_lists_are_not_shared(self):
        store = _Store()
        job = store.record(store.plan([
            _Line(20, mask=None), _Line(21, baseline=None),
        ]))
        self.assertEqual(job.status, STATUS_FAILED)
        first, second = store.lines.objects.rows
        first.warnings.append("only-first")
        self.assertEqual(second.warnings, [])
        self.assertIsNot(first.warnings, second.warnings)

    def test_secrets_are_not_copied_from_the_request_plan(self):
        store = _Store()
        plan = store.plan([_Line(10), _Line(20, mask=None)])
        self.assertIn(IMAGE, str(plan.request))
        job = store.record(plan)
        text = _stored(job, store.lines.objects.rows)
        for secret in (IMAGE, ENDPOINT, TOKEN, TITLE, SECRET_PATH, "api_key"):
            self.assertNotIn(secret, text)
        self.assertNotIn("request", vars(job))
        self.assertNotIn("endpoint_url", vars(job))

    def test_invalid_plan_is_rejected_before_a_row_is_created(self):
        store = _Store()
        leaked = {"endpoint_url": ENDPOINT, "image": IMAGE, "raw": RAW}
        with self.assertRaises(ExternalHtrAuditError) as caught:
            store.record(leaked)
        self.assertEqual(caught.exception.code, CODE_INVALID)
        self.assertEqual(str(caught.exception), "audit plan could not be stored")
        for secret in (ENDPOINT, IMAGE, RAW):
            self.assertNotIn(secret, str(caught.exception))
        self.assertEqual(store.jobs.objects.rows, [])
        self.assertEqual(store.lines.objects.rows, [])
        self.assertEqual(store.atomic.entered, 0)


class RecordApplyTests(unittest.TestCase):
    def test_success_stores_provenance_and_result_order(self):
        store = _Store()
        job = store.record(store.plan([
            _Line(10), _Line(20), _Line(30, mask=None),
        ]))
        request = plan_external_htr(
            store.config,
            [_Line(10), _Line(20), _Line(30, mask=None)],
            document_id=7,
            part_id=8,
            engine="pylaia",
            model_id="example-model",
            encode_line=lambda line: IMAGE,
        ).request
        response = _response(("20", "10"), ("", "first"))
        response["results"][1]["warnings"] = ["low ink"]
        response["results"][1]["confidence"] = 0.25
        response["results"][1]["timing_ms"] = 9
        apply_plan = plan_external_htr_apply(request, response)
        self.assertTrue(apply_plan.would_write)
        store.apply(job, apply_plan)
        included = [
            row for row in store.lines.objects.rows if row.status == LINE_INCLUDED
        ]
        skipped = [
            row for row in store.lines.objects.rows if row.status == LINE_SKIPPED
        ]
        self.assertEqual(job.status, STATUS_COMPLETED)
        self.assertEqual(job.code, "")
        self.assertEqual(job.message, MESSAGE_READY)
        self.assertEqual(job.layer_source, "external-htr:pylaia:example-model")
        self.assertEqual(job.engine, "pylaia")
        self.assertEqual(job.model_id, "example-model")
        self.assertEqual(job.model_version, "1")
        self.assertEqual(job.api_version, "1")
        self.assertEqual(job.requested_line_count, 2)
        self.assertEqual(job.skipped_line_count, 1)
        self.assertEqual(job.result_count, 2)
        self.assertEqual(job.warning_count, 1)
        self.assertEqual(job.empty_text_count, 1)
        self.assertEqual(job.saved, 1)
        self.assertIn("updated_at", job.update_fields)
        self.assertEqual([row.line_id for row in included], ["10", "20"])
        self.assertEqual([row.position for row in included], [0, 1])
        self.assertEqual([row.text for row in included], ["first", ""])
        self.assertEqual(included[0].confidence, 0.25)
        self.assertEqual(included[0].timing_ms, 9)
        self.assertEqual(included[0].warnings, ["low ink"])
        self.assertEqual(included[0].code, "")
        self.assertEqual(skipped[0].line_id, "30")
        self.assertEqual(skipped[0].text, "")
        text = _stored(job, store.lines.objects.rows)
        for secret in (IMAGE, ENDPOINT, TOKEN, TITLE, SECRET_PATH):
            self.assertNotIn(secret, text)

    def test_warning_lists_are_copied(self):
        store = _Store()
        job = store.record(store.plan([_Line(10), _Line(20)]))
        shared = ("low ink",)
        plan = ExternalHtrApplyPlan(
            would_write=True,
            code=None,
            message=MESSAGE_READY,
            layer_source="external-htr:pylaia:example-model",
            engine="pylaia",
            model_id="example-model",
            model_version="1",
            api_version="1",
            results=(
                PlannedLine("10", "a", 0.5, 4, shared),
                PlannedLine("20", "b", None, 0, shared),
            ),
            result_count=2,
            warning_count=2,
            empty_text_count=0,
        )
        store.apply(job, plan)
        first, second = store.lines.objects.rows
        first.warnings.append("only-first")
        self.assertEqual(second.warnings, ["low ink"])
        self.assertEqual(shared, ("low ink",))
        self.assertIsNot(first.warnings, second.warnings)

    def test_failed_apply_stores_the_fixed_failure_and_no_result_text(self):
        store = _Store()
        job = store.record(store.plan([_Line(10), _Line(20), _Line(30, mask=None)]))
        request = store.plan([_Line(10), _Line(20), _Line(30, mask=None)]).request
        response = _response(("10",), (RAW,))
        apply_plan = plan_external_htr_apply(request, response)
        self.assertFalse(apply_plan.would_write)
        self.assertEqual(apply_plan.code, "line_failed")
        store.apply(job, apply_plan)
        self.assertEqual(job.status, STATUS_FAILED)
        self.assertEqual(job.code, "line_failed")
        self.assertEqual(job.message, MESSAGE_INVALID)
        self.assertEqual(job.engine, "pylaia")
        self.assertEqual(job.model_id, "example-model")
        self.assertIsNone(job.layer_source)
        self.assertEqual(job.model_version, "")
        self.assertEqual(job.result_count, 0)
        self.assertEqual(job.warning_count, 0)
        self.assertEqual(job.empty_text_count, 0)
        self.assertEqual(job.requested_line_count, 2)
        self.assertEqual(job.skipped_line_count, 1)
        self.assertEqual(job.saved, 1)
        self.assertIn("updated_at", job.update_fields)
        self.assertNotIn("layer_source", job.update_fields)
        self.assertNotIn("engine", job.update_fields)
        self.assertEqual(
            [row.status for row in store.lines.objects.rows], [LINE_SKIPPED],
        )
        self.assertEqual(store.lines.objects.rows[0].text, "")
        text = _stored(job, store.lines.objects.rows)
        for secret in (IMAGE, ENDPOINT, TOKEN, TITLE, SECRET_PATH, RAW):
            self.assertNotIn(secret, text)

    def test_duplicate_skip_is_updated_to_the_included_result(self):
        store = _Store()
        job = store.record(store.plan([_Line(10), _Line(10)]))
        apply_plan = plan_external_htr_apply(
            store.plan([_Line(10), _Line(10)]).request,
            _response(("10",), ("kept",)),
        )
        store.apply(job, apply_plan)
        self.assertEqual(len(store.lines.objects.rows), 1)
        row = store.lines.objects.rows[0]
        self.assertEqual(row.status, LINE_INCLUDED)
        self.assertEqual(row.text, "kept")
        self.assertEqual(row.code, "")
        self.assertEqual(row.position, 0)
        self.assertEqual(row.saved, 1)
        self.assertIn("updated_at", row.update_fields)
        self.assertEqual(job.skipped_line_count, 1)
        self.assertEqual(job.result_count, 1)

    def test_second_apply_is_rejected(self):
        store = _Store()
        job = store.record(store.plan([_Line(10)]))
        request = store.plan([_Line(10)]).request
        apply_plan = plan_external_htr_apply(request, _response(("10",), ("kept",)))
        store.apply(job, apply_plan)
        saved = job.saved
        line_count = len(store.lines.objects.rows)
        entered = store.atomic.entered
        with self.assertRaises(ExternalHtrAuditError) as caught:
            store.apply(job, apply_plan)
        self.assertEqual(caught.exception.code, CODE_ALREADY)
        self.assertEqual(str(caught.exception), "audit job is already finalized")
        self.assertEqual(job.status, STATUS_COMPLETED)
        self.assertEqual(job.saved, saved)
        self.assertEqual(len(store.lines.objects.rows), line_count)
        self.assertEqual(store.lines.objects.rows[0].text, "kept")
        self.assertEqual(store.atomic.entered, entered)

    def test_failed_apply_cannot_be_replaced(self):
        store = _Store()
        job = store.record(store.plan([_Line(10), _Line(20)]))
        request = store.plan([_Line(10), _Line(20)]).request
        failed = plan_external_htr_apply(request, _response(("10",), ("hidden",)))
        store.apply(job, failed)
        success = plan_external_htr_apply(
            request, _response(("10", "20"), ("kept", "also")),
        )
        with self.assertRaises(ExternalHtrAuditError) as caught:
            store.apply(job, success)
        self.assertEqual(caught.exception.code, CODE_ALREADY)
        self.assertEqual(job.status, STATUS_FAILED)
        self.assertEqual(job.code, "line_failed")
        self.assertEqual(store.lines.objects.rows, [])
        self.assertNotIn("hidden", _stored(job, []))
        self.assertNotIn("kept", _stored(job, []))


class IsolationTests(unittest.TestCase):
    def test_helper_does_not_import_the_client_or_the_write_path(self):
        text = (AI_DIR / "external_htr_audit.py").read_text()
        tree = ast.parse(text)
        top = []
        for node in tree.body:
            if isinstance(node, ast.ImportFrom) and node.module:
                top.append(node.module)
        self.assertEqual(top, [
            "__future__",
            "ai.external_htr_apply_plan",
            "ai.external_htr_plan",
        ])
        modules = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                modules.append(node.module)
        self.assertIn("ai.models", modules)
        self.assertIn("django.db", modules)
        for banned in (
            "htr_engine_client",
            "recognize_lines",
            "LineTranscription",
            "ai_transcribe",
            "endpoint_url",
            "urllib",
            "requests",
            "celery",
            "socket",
            "crop_line",
            "build_recognize_request",
            "plan_external_htr",
            "base64",
        ):
            self.assertNotIn(banned, text, banned)

    def test_stage1_modules_do_not_import_the_helper(self):
        for name in RUNTIME:
            text = (AI_DIR / name).read_text()
            self.assertNotIn("external_htr_audit", text, name)
            self.assertNotIn("record_external_htr_plan", text, name)
            self.assertNotIn("record_external_htr_apply", text, name)

    def test_admin_audit_views_still_have_no_actions(self):
        text = (AI_DIR / "admin.py").read_text()
        self.assertNotIn("external_htr_audit", text)
        self.assertNotIn("record_external_htr", text)
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
