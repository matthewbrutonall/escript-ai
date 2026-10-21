"""Plans for a new layer from a completed external HTR audit.

No Django, no network, and no transcription writes. The loader is executed
with a real database in ai.django_integration_tests.
"""
import ast
import socket
import unittest
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ai.external_htr_apply_plan import (
    LAYER_SOURCE_PREFIX as APPLY_PREFIX,
    VERSION_SOURCE_LIMIT as APPLY_LIMIT,
)
from ai.external_htr_writeback_plan import (
    CODE_CANCELLED,
    CODE_DUPLICATE,
    CODE_FAILED,
    CODE_INCOMPLETE,
    CODE_LINE,
    CODE_MALFORMED,
    CODE_PART,
    CODE_PLANNED,
    CODE_READY,
    CODE_SENT,
    CODE_TEXT,
    CONTENT_LIMIT,
    LAYER_NAME_LIMIT,
    LAYER_SOURCE_PREFIX,
    MESSAGE_CANCELLED,
    MESSAGE_DUPLICATE,
    MESSAGE_FAILED,
    MESSAGE_INCOMPLETE,
    MESSAGE_LINE,
    MESSAGE_MALFORMED,
    MESSAGE_PART,
    MESSAGE_PLANNED,
    MESSAGE_READY,
    MESSAGE_SENT,
    MESSAGE_TEXT,
    VERSION_SOURCE_LIMIT,
    plan_external_htr_writeback,
)

AI_DIR = Path(__file__).resolve().parents[1]
PLANNER = AI_DIR / "external_htr_writeback_plan.py"
STAGE1 = (
    "tasks.py",
    "views.py",
    "serializers.py",
    "pipeline.py",
    "backends.py",
    "dispatch.py",
    "models.py",
    "admin.py",
    "management/commands/plan_external_htr.py",
    "management/commands/check_external_htr_live.py",
    "management/commands/export_external_htr_request.py",
    "management/commands/run_external_htr_audit.py",
    "external_htr_live.py",
    "external_htr_service.py",
    "external_htr_audit.py",
    "external_htr_apply_plan.py",
    "external_htr_plan.py",
    "external_htr_plan_command.py",
    "external_htr_export.py",
)
SECRET_URL = "http://10.0.0.8:8766/secret"
IMAGE = "QUJDRA=="
TRACE = "Traceback (most recent call last)"


def _job(**overrides):
    values = {
        "pk": 7,
        "document_id": 3,
        "part_id": 5,
        "status": "completed",
        "code": "",
        "message": "response would be written",
        "engine": "pylaia",
        "model_id": "example-model",
        "model_version": "1",
        "api_version": "1",
        "layer_source": "external-htr:pylaia:example-model",
        "result_count": 1,
        "warning_count": 0,
        "empty_text_count": 0,
        "requested_line_count": 9,
        "skipped_line_count": 4,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _row(line_id, text="kept", *, status="included", warnings=()):
    return SimpleNamespace(
        line_id=line_id, text=text, status=status, warnings=list(warnings),
    )


def _plan(job, rows, part_line_ids, *, part_document_id=3):
    return plan_external_htr_writeback(
        job, rows, part_line_ids, part_document_id=part_document_id,
    )


class WritebackPlanTests(unittest.TestCase):
    def test_completed_audit_keeps_the_given_order(self):
        job = _job(result_count=2, warning_count=1, empty_text_count=0)
        second = SimpleNamespace(pk=6, external_id="eSc_line_trap")
        first = SimpleNamespace(pk=4, external_id="999")
        plan = _plan(
            job,
            [
                _row("6", "  later  ", warnings=["soft"]),
                _row("8", "skip-me", status="skipped"),
                _row("4", "kept"),
            ],
            [second, first, SimpleNamespace(pk=10, external_id="4")],
        )
        self.assertTrue(plan.allowed)
        self.assertEqual(plan.code, CODE_READY)
        self.assertEqual(plan.message, MESSAGE_READY)
        self.assertEqual(plan.job_id, 7)
        self.assertEqual(plan.document_id, 3)
        self.assertEqual(plan.part_id, 5)
        self.assertEqual(
            [(item.line_id, item.text) for item in plan.lines],
            [("6", "  later  "), ("4", "kept")],
        )
        self.assertNotIn("eSc_line_trap", [item.line_id for item in plan.lines])
        self.assertNotIn("999", [item.line_id for item in plan.lines])
        self.assertNotIn("skip-me", plan.message)
        self.assertEqual(plan.included_count, 2)
        self.assertEqual(plan.skipped_count, 1)
        self.assertEqual(plan.empty_text_count, 0)
        self.assertEqual(plan.provenance_stamp, "external-htr:pylaia:example-model")
        self.assertEqual(plan.engine, "pylaia")
        self.assertEqual(plan.model_id, "example-model")
        self.assertEqual(plan.model_version, "1")
        self.assertEqual(plan.api_version, "1")
        self.assertEqual(plan.layer_name, "external-htr:pylaia:example-model:job-7")
        self.assertNotEqual(plan.layer_name, plan.provenance_stamp)
        self.assertFalse(hasattr(plan, "transcription_id"))

    def test_empty_text_is_kept_and_skipped_rows_are_excluded(self):
        job = _job(result_count=2, warning_count=0, empty_text_count=1)
        plan = _plan(
            job,
            [
                _row("4", "kept"),
                _row("8", "not-imported", status="skipped", warnings=["no-mask"]),
                _row("6", ""),
            ],
            [4, 6],
        )
        self.assertTrue(plan.allowed)
        self.assertEqual(
            [(item.line_id, item.text) for item in plan.lines],
            [("4", "kept"), ("6", "")],
        )
        self.assertEqual(plan.included_count, 2)
        self.assertEqual(plan.skipped_count, 1)
        self.assertEqual(plan.empty_text_count, 1)
        self.assertNotIn("not-imported", plan.message)

    def test_text_at_the_content_limit_is_kept_and_one_more_character_is_refused(self):
        self.assertEqual(CONTENT_LIMIT, 2048)
        for character in ("a", "é", "𠀋"):
            with self.subTest(character=character):
                kept = character * CONTENT_LIMIT
                self.assertEqual(len(kept), CONTENT_LIMIT)
                plan = _plan(_job(result_count=1), [_row("4", kept)], [4])
                self.assertTrue(plan.allowed)
                self.assertEqual(plan.lines[0].text, kept)
                self.assertEqual(len(plan.lines[0].text), CONTENT_LIMIT)
                too_long = kept + character
                refused = _plan(
                    _job(result_count=1, message=SECRET_URL),
                    [_row("4", too_long)],
                    [4],
                )
                self._assert_refused(refused, CODE_TEXT, MESSAGE_TEXT)
                self._assert_silent(refused)
                self.assertNotIn(too_long[:8], refused.message)
                self.assertNotIn(too_long[:8], refused.code)
        skipped = "é" * (CONTENT_LIMIT + 40)
        plan = _plan(
            _job(result_count=1),
            [_row("4", "kept"), _row("8", skipped, status="skipped")],
            [4],
        )
        self.assertTrue(plan.allowed)
        self.assertEqual([(item.line_id, item.text) for item in plan.lines], [("4", "kept")])

    def test_request_totals_need_not_match_the_remaining_rows(self):
        job = _job(
            result_count=1,
            warning_count=0,
            empty_text_count=1,
            requested_line_count=5,
            skipped_line_count=4,
        )
        plan = _plan(job, [_row("4", ""), _row("8", "", status="skipped")], [4])
        self.assertTrue(plan.allowed)
        self.assertEqual([(item.line_id, item.text) for item in plan.lines], [("4", "")])
        self.assertEqual(plan.skipped_count, 1)
        self.assertEqual(plan.empty_text_count, 1)

    def test_unfinished_statuses_fail_without_candidates(self):
        cases = (
            ("planned", CODE_PLANNED, MESSAGE_PLANNED),
            ("sent", CODE_SENT, MESSAGE_SENT),
            ("failed", CODE_FAILED, MESSAGE_FAILED),
            ("cancelled", CODE_CANCELLED, MESSAGE_CANCELLED),
        )
        for status, code, message in cases:
            with self.subTest(status=status):
                job = _job(status=status, message=SECRET_URL + IMAGE + TRACE)
                plan = _plan(job, [_row("4", IMAGE)], [4])
                self._assert_refused(plan, code, message)
                self.assertEqual(plan.job_id, 7)
                self._assert_silent(plan)

    def test_missing_provenance_and_bad_counts_fail_without_candidates(self):
        cases = (
            {"engine": ""},
            {"model_id": ""},
            {"model_version": ""},
            {"api_version": ""},
            {"layer_source": None},
            {"layer_source": "external-htr:other:model"},
            {"engine": SECRET_URL, "layer_source": "external-htr:pylaia:example-model"},
            {"code": "timeout"},
            {"pk": None},
            {"document_id": None},
            {"part_id": None},
            {"status": "holding"},
            {"result_count": 0},
            {"empty_text_count": 1},
            {"warning_count": 2},
            {"result_count": True},
        )
        for overrides in cases:
            with self.subTest(overrides=sorted(overrides)):
                plan = _plan(
                    _job(message=SECRET_URL + TRACE, **overrides),
                    [_row("4", IMAGE)],
                    [4],
                )
                self._assert_refused(plan, CODE_INCOMPLETE, MESSAGE_INCOMPLETE)
                self._assert_silent(plan)
        for text, warnings in ((None, []), ("kept", "low"), ("kept", ["ok", 1])):
            with self.subTest(text=text, warnings=warnings):
                row = SimpleNamespace(
                    line_id="4", text=text, status="included", warnings=warnings,
                )
                plan = _plan(_job(message=SECRET_URL), [row], [4])
                self._assert_refused(plan, CODE_INCOMPLETE, MESSAGE_INCOMPLETE)
                self._assert_silent(plan)

    def test_a_failed_line_row_is_not_a_completed_import(self):
        job = _job(result_count=1)
        plan = _plan(
            job,
            [_row("4", "kept"), _row("6", "", status="failed")],
            [4, 6],
        )
        self._assert_refused(plan, CODE_INCOMPLETE, MESSAGE_INCOMPLETE)

    def test_bad_included_ids_fail_without_candidates(self):
        cases = (
            (4, CODE_MALFORMED, MESSAGE_MALFORMED),
            ("", CODE_MALFORMED, MESSAGE_MALFORMED),
            ("eSc_line_4", CODE_MALFORMED, MESSAGE_MALFORMED),
            ("04", CODE_MALFORMED, MESSAGE_MALFORMED),
            ("1" * 257, CODE_MALFORMED, MESSAGE_MALFORMED),
            ("9", CODE_LINE, MESSAGE_LINE),
        )
        for line_id, code, message in cases:
            with self.subTest(line_id=line_id):
                plan = _plan(_job(), [_row(line_id, IMAGE)], [4])
                self._assert_refused(plan, code, message)
                self._assert_silent(plan)
                self.assertNotIn(IMAGE, plan.message)

    def test_duplicate_included_ids_fail_without_candidates(self):
        job = _job(result_count=2)
        plan = _plan(job, [_row("4", "kept"), _row("4", "again")], [4])
        self._assert_refused(plan, CODE_DUPLICATE, MESSAGE_DUPLICATE)
        self.assertNotIn("again", plan.message)

    def test_external_id_does_not_authorize_a_line(self):
        part_line = SimpleNamespace(pk=4, external_id="9")
        plan = _plan(_job(), [_row("9", "kept")], [part_line])
        self._assert_refused(plan, CODE_LINE, MESSAGE_LINE)

    def test_document_part_mismatch_fails_without_candidates(self):
        plan = _plan(_job(), [_row("4", IMAGE)], [4], part_document_id=8)
        self._assert_refused(plan, CODE_PART, MESSAGE_PART)
        self.assertEqual(plan.document_id, 3)
        self.assertEqual(plan.part_id, 5)
        self._assert_silent(plan)

    def test_long_provenance_stays_full_beside_the_truncated_stamp(self):
        engine = "e" * 300
        model_id = "m" * 300
        model_version = "v" * 240
        full = f"{LAYER_SOURCE_PREFIX}:{engine}:{model_id}"
        stamp = full[:VERSION_SOURCE_LIMIT]
        job = _job(
            pk=11,
            engine=engine,
            model_id=model_id,
            model_version=model_version,
            api_version="1",
            layer_source=stamp,
        )
        other = _job(
            pk=12,
            engine=engine,
            model_id=model_id,
            model_version=model_version,
            api_version="1",
            layer_source=stamp,
        )
        plan = _plan(job, [_row("4", "kept")], [4])
        second = _plan(other, [_row("4", "kept")], [4])
        self.assertTrue(plan.allowed)
        self.assertGreater(len(full), VERSION_SOURCE_LIMIT)
        self.assertEqual(plan.provenance_stamp, stamp)
        self.assertEqual(len(plan.provenance_stamp), VERSION_SOURCE_LIMIT)
        self.assertEqual(plan.engine, engine)
        self.assertEqual(plan.model_id, model_id)
        self.assertEqual(plan.model_version, model_version)
        self.assertNotIn(engine, plan.provenance_stamp)
        self.assertNotIn("job-11", plan.provenance_stamp)
        self.assertEqual(plan.provenance_stamp, second.provenance_stamp)
        self.assertNotEqual(plan.layer_name, second.layer_name)
        self.assertNotEqual(plan.layer_name, plan.provenance_stamp)
        self.assertTrue(plan.layer_name.endswith(":job-11"))
        self.assertTrue(second.layer_name.endswith(":job-12"))
        self.assertEqual(len(plan.layer_name), LAYER_NAME_LIMIT)
        self.assertLessEqual(len(plan.provenance_stamp), APPLY_LIMIT)

    def test_stamp_limit_matches_the_apply_plan(self):
        self.assertEqual(VERSION_SOURCE_LIMIT, 128)
        self.assertEqual(VERSION_SOURCE_LIMIT, APPLY_LIMIT)
        self.assertEqual(LAYER_SOURCE_PREFIX, APPLY_PREFIX)
        self.assertEqual(LAYER_NAME_LIMIT, 512)

    def test_planning_does_not_write_or_use_the_network(self):
        job = _job()
        job.writes = 0

        def save(*args, **kwargs):
            job.writes += 1
            raise AssertionError("save")

        job.save = save
        row = _row("4", "kept")
        row.save = save
        with patch.object(socket, "socket", side_effect=AssertionError("socket")), \
                patch.object(socket, "create_connection", side_effect=AssertionError("connect")), \
                patch.object(urllib.request, "urlopen", side_effect=AssertionError("urlopen")), \
                patch.object(urllib.request, "build_opener", side_effect=AssertionError("opener")):
            plan = _plan(job, [row], [4])
        self.assertTrue(plan.allowed)
        self.assertEqual(job.writes, 0)

    def test_stage1_modules_do_not_import_the_planner(self):
        text = PLANNER.read_text(encoding="utf-8")
        for banned in (
            "urlopen",
            "build_opener",
            "socket",
            "crop_line",
            "base64",
            "requests",
            "LineTranscription",
            "htr_engine_client",
            "external_htr_live",
            ".save",
            "objects.create",
            "objects.update",
        ):
            self.assertNotIn(banned, text, banned)
        tree = ast.parse(text)
        module_level = []
        nested = []
        for node in tree.body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                module_level.append(node)
            else:
                for child in ast.walk(node):
                    if isinstance(child, (ast.Import, ast.ImportFrom)):
                        nested.append(child)
        self.assertEqual(
            [_module_name(node) for node in module_level],
            ["__future__", "dataclasses"],
        )
        self.assertEqual(
            sorted(_module_name(node) for node in nested),
            ["ai.models", "core.models"],
        )
        imported_names = []
        for node in nested:
            imported_names.extend(alias.name for alias in node.names)
        self.assertEqual(
            sorted(imported_names),
            ["ExternalHTRJob", "ExternalHTRLineResult", "Line"],
        )
        for name in STAGE1:
            path = AI_DIR / name
            self.assertTrue(path.is_file(), name)
            self.assertNotIn("external_htr_writeback_plan", path.read_text(encoding="utf-8"))

    def _assert_refused(self, plan, code, message):
        self.assertFalse(plan.allowed)
        self.assertEqual(plan.code, code)
        self.assertEqual(plan.message, message)
        self.assertEqual(plan.lines, ())
        self.assertEqual(plan.included_count, 0)
        self.assertEqual(plan.skipped_count, 0)
        self.assertEqual(plan.empty_text_count, 0)
        self.assertEqual(plan.layer_name, "")
        self.assertEqual(plan.provenance_stamp, "")
        self.assertEqual(plan.engine, "")
        self.assertEqual(plan.model_id, "")
        self.assertEqual(plan.model_version, "")
        self.assertEqual(plan.api_version, "")

    def _assert_silent(self, plan):
        blob = " ".join((plan.code, plan.message, plan.layer_name, plan.engine, plan.provenance_stamp))
        for leaked in (SECRET_URL, IMAGE, TRACE, "10.0.0.8", "Traceback"):
            self.assertNotIn(leaked, blob)


def _module_name(node):
    if isinstance(node, ast.ImportFrom):
        return node.module or ""
    return node.names[0].name
