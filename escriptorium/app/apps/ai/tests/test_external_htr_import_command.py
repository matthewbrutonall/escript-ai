"""Checks for the external HTR import command.

The helper is exercised with fakes. Database behaviour is in
ai.django_integration_tests.test_external_htr_import_command_orm.
"""
import ast
import unittest
from pathlib import Path

from ai.external_htr_import_command import (
    CODE_INTERNAL,
    execute_external_htr_import,
)

AI_DIR = Path(__file__).resolve().parents[1]
HELPER = AI_DIR / "external_htr_import_command.py"
COMMAND = AI_DIR / "management" / "commands" / "import_external_htr_audit.py"
README = AI_DIR / "README.md"
WORKFLOW = AI_DIR / "docs" / "external-htr-operator-workflow.md"
INTEGRATION = AI_DIR / "docs" / "external-htr-integration.md"
UNWIRED = (
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
    "management/commands/check_external_htr_engine.py",
    "external_htr_live.py",
    "external_htr_service.py",
    "external_htr_audit.py",
    "external_htr_apply_plan.py",
    "external_htr_plan.py",
    "external_htr_plan_command.py",
    "external_htr_export.py",
    "external_htr_writeback_plan.py",
)


class _Plan:
    def __init__(self, allowed, code, job_id, lines):
        self.allowed = allowed
        self.code = code
        self.job_id = job_id
        self.lines = lines
        self.layer_name = "external-htr:secret-engine:secret-model:job-1"
        self.message = "recognized secret text"


class _Job:
    def __init__(self, imported=False, link=None):
        self.imported = imported
        self.imported_transcription_id = link


class _Result:
    def __init__(self, imported, code, job_id, transcription_id, message="secret message"):
        self.imported = imported
        self.code = code
        self.job_id = job_id
        self.transcription_id = transcription_id
        self.message = message


def _refuse(*args, **kwargs):
    raise AssertionError("not called")


class ImportCommandHelperTests(unittest.TestCase):
    def test_preview_reports_the_plan_and_does_not_write(self):
        calls = {"write": 0, "count": 0}

        def load_plan(job_id):
            self.assertEqual(job_id, 7)
            return _Plan(True, "writeback_planned", 7, ("kept", "second"))

        def write(job_id):
            calls["write"] += 1
            return _refuse(job_id)

        def count_lines(layer_id):
            calls["count"] += 1
            return _refuse(layer_id)

        code, line = execute_external_htr_import(
            "7",
            apply=False,
            load_plan=load_plan,
            import_writeback=write,
            fetch_job=lambda job_id: _Job(),
            count_lines=count_lines,
        )
        self.assertEqual(code, 0)
        self.assertEqual(line, "OK: external HTR import preview job=7 lines=2")
        self.assertEqual(calls, {"write": 0, "count": 0})
        self.assertNotIn("secret", line)
        self.assertNotIn("kept", line)

    def test_preview_of_an_imported_audit_ignores_an_accepting_planner(self):
        def load_plan(job_id):
            raise AssertionError("planner")

        code, line = execute_external_htr_import(
            4,
            apply=False,
            load_plan=load_plan,
            import_writeback=_refuse,
            fetch_job=lambda job_id: _Job(imported=True),
            count_lines=_refuse,
        )
        self.assertEqual((code, line), (1, "FAILED: external HTR import already_imported"))

        code, line = execute_external_htr_import(
            4,
            apply=False,
            load_plan=load_plan,
            import_writeback=_refuse,
            fetch_job=lambda job_id: _Job(link=9),
            count_lines=_refuse,
        )
        self.assertEqual(line, "FAILED: external HTR import already_imported")

    def test_invalid_job_ids_are_a_fixed_refusal(self):
        for value in (None, True, False, 0, -1, "0", "-1", "nope", "1.5", " 1", ""):
            code, line = execute_external_htr_import(
                value,
                apply=False,
                load_plan=_refuse,
                import_writeback=_refuse,
                fetch_job=_refuse,
                count_lines=_refuse,
            )
            self.assertEqual(code, 1, value)
            self.assertEqual(line, "FAILED: external HTR import audit_incomplete", value)

    def test_apply_uses_only_the_writer_result(self):
        def write(job_id):
            self.assertEqual(job_id, 7)
            return _Result(True, "writeback_imported", 7, 15)

        code, line = execute_external_htr_import(
            "7",
            apply=True,
            load_plan=_refuse,
            import_writeback=write,
            fetch_job=_refuse,
            count_lines=lambda layer_id: 3 if layer_id == 15 else _refuse(),
        )
        self.assertEqual(code, 0)
        self.assertEqual(line, "OK: external HTR import applied job=7 layer=15 lines=3")
        self.assertNotIn("secret", line)

    def test_apply_failure_and_hostile_text_stay_fixed(self):
        def write(job_id):
            return _Result(
                False,
                "http://10.0.0.8:9/secret recognized text",
                7,
                None,
                message="recognized secret text",
            )

        code, line = execute_external_htr_import(
            7,
            apply=True,
            load_plan=_refuse,
            import_writeback=write,
            fetch_job=_refuse,
            count_lines=_refuse,
        )
        self.assertEqual((code, line), (1, "FAILED: external HTR import " + CODE_INTERNAL))
        self.assertNotIn("secret", line)
        self.assertNotIn("http", line)
        self.assertNotIn("recognized", line)

    def test_no_included_lines_are_refused_and_blank_text_is_previewed(self):
        code, line = execute_external_htr_import(
            7,
            apply=False,
            load_plan=lambda job_id: _Plan(False, "no_included_lines", 7, ()),
            import_writeback=_refuse,
            fetch_job=lambda job_id: _Job(),
            count_lines=_refuse,
        )
        self.assertEqual((code, line), (1, "FAILED: external HTR import no_included_lines"))

        class Blank:
            text = ""

        code, line = execute_external_htr_import(
            7,
            apply=False,
            load_plan=lambda job_id: _Plan(True, "writeback_planned", 7, (Blank(),)),
            import_writeback=_refuse,
            fetch_job=lambda job_id: _Job(),
            count_lines=_refuse,
        )
        self.assertEqual(code, 0)
        self.assertEqual(line, "OK: external HTR import preview job=7 lines=1")
        self.assertNotIn("Blank", line)

    def test_writer_exception_does_not_leak(self):
        def write(job_id):
            raise RuntimeError("Traceback endpoint http://secret recognized text")

        code, line = execute_external_htr_import(
            7,
            apply=True,
            load_plan=_refuse,
            import_writeback=write,
            fetch_job=_refuse,
            count_lines=_refuse,
        )
        self.assertEqual((code, line), (1, "FAILED: external HTR import import_internal"))
        self.assertNotIn("Traceback", line)
        self.assertNotIn("secret", line)
        self.assertNotIn("recognized", line)

    def test_command_is_the_only_new_caller(self):
        helper = HELPER.read_text(encoding="utf-8")
        command = COMMAND.read_text(encoding="utf-8")
        for banned in (
            "urlopen",
            "build_opener",
            "socket",
            "crop_line",
            "base64",
            "requests",
            "Image.open",
            "htr_engine_client",
            "recognize_lines",
            "acknowledge_sample",
            "mark_training_eligible",
            "find_comparison_transcription",
            "traceback",
            "external_htr_live",
        ):
            self.assertNotIn(banned, helper, banned)
            self.assertNotIn(banned, command, banned)
        self.assertNotIn("import_external_htr_writeback", helper)
        self.assertIn("import_external_htr_writeback", command)
        self.assertIn("load_external_htr_writeback_plan", command)
        self.assertIn('nargs="?"', command)
        self.assertIn('"--apply"', command)
        for path in (HELPER, COMMAND):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ExceptHandler):
                    self.assertIsNone(node.name, path.name)
        for name in UNWIRED:
            text = (AI_DIR / name).read_text(encoding="utf-8")
            self.assertNotIn("execute_external_htr_import", text, name)
            self.assertNotIn("import_external_htr_audit", text, name)
            self.assertNotIn("import_external_htr_writeback", text, name)
        writer = (AI_DIR / "external_htr_writeback.py").read_text(encoding="utf-8")
        self.assertNotIn("execute_external_htr_import", writer)
        self.assertIn("import_external_htr_audit --apply", writer)

    def test_docs_describe_preview_and_the_separate_review(self):
        readme = README.read_text(encoding="utf-8")
        workflow = WORKFLOW.read_text(encoding="utf-8")
        integration = INTEGRATION.read_text(encoding="utf-8")
        for text in (readme, workflow, integration):
            self.assertIn("import_external_htr_audit", text)
            self.assertNotIn("Stage 2", text)
            self.assertNotIn("roadmap", text.lower())
        self.assertIn("--apply", workflow)
        self.assertIn("OK: external HTR import preview job=<id> lines=<n>", workflow)
        self.assertIn("OK: external HTR import applied job=<id> layer=<id> lines=<n>", workflow)
        self.assertIn("FAILED: external HTR import <code>", workflow)
        self.assertIn("does not write a transcription layer", workflow)
        self.assertIn("acknowledge", workflow)
        self.assertNotIn("That action is not implemented.", workflow)
