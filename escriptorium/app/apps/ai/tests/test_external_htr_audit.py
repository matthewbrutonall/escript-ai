"""External HTR audit models. No Django, no network, no transcription write."""
import ast
import unittest
from pathlib import Path

AI_DIR = Path(__file__).resolve().parents[1]
MIGRATION = AI_DIR / "migrations" / "0008_externalhtrjob.py"
RUNTIME = (
    "tasks.py",
    "views.py",
    "serializers.py",
    "pipeline.py",
    "backends.py",
    "dispatch.py",
)


def _class_def(name):
    tree = ast.parse((AI_DIR / "models.py").read_text())
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    raise AssertionError(f"{name} is missing")


def _keywords(statement):
    if not isinstance(statement, ast.Assign) or len(statement.targets) != 1:
        return None, None
    target = statement.targets[0]
    if not isinstance(target, ast.Name) or not isinstance(statement.value, ast.Call):
        return None, None
    keywords = {}
    for keyword in statement.value.keywords:
        if keyword.arg:
            keywords[keyword.arg] = keyword.value
    return target.id, keywords


def _const(node, class_name=None):
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name) and class_name:
        for statement in _class_def(class_name).body:
            if not isinstance(statement, ast.Assign) or len(statement.targets) != 1:
                continue
            target = statement.targets[0]
            if (
                isinstance(target, ast.Name)
                and target.id == node.id
                and isinstance(statement.value, ast.Constant)
            ):
                return statement.value.value
    raise AssertionError(f"expected a constant, got {type(node).__name__}")


def _field_map(class_name):
    fields = {}
    for statement in _class_def(class_name).body:
        name, keywords = _keywords(statement)
        if name:
            fields[name] = keywords
    return fields


class AuditModelTests(unittest.TestCase):
    def test_job_defaults_are_planned_and_zero(self):
        fields = _field_map("ExternalHTRJob")
        self.assertEqual(_const(fields["status"]["default"], "ExternalHTRJob"), "planned")
        for name in (
            "requested_line_count",
            "skipped_line_count",
            "result_count",
            "warning_count",
            "empty_text_count",
        ):
            self.assertEqual(_const(fields[name]["default"]), 0, name)
        self.assertIn("null", fields["layer_source"])
        self.assertIs(fields["layer_source"]["null"].value, True)
        self.assertEqual(fields["layer_source"]["max_length"].value, 128)
        names = set(fields)
        for banned in ("endpoint_url", "image", "raw_body", "response_body", "metadata"):
            self.assertNotIn(banned, names)

    def test_line_warnings_use_a_fresh_list(self):
        fields = _field_map("ExternalHTRLineResult")
        self.assertIsInstance(fields["warnings"]["default"], ast.Name)
        self.assertEqual(fields["warnings"]["default"].id, "list")
        self.assertNotIsInstance(fields["warnings"]["default"], ast.List)
        self.assertEqual(
            _const(fields["status"]["default"], "ExternalHTRLineResult"), "included",
        )
        self.assertEqual(_const(fields["timing_ms"]["default"]), 0)
        self.assertEqual(_const(fields["text"]["default"]), "")
        self.assertIs(fields["confidence"]["null"].value, True)
        source = ast.get_source_segment(
            (AI_DIR / "models.py").read_text(), _class_def("ExternalHTRLineResult"),
        )
        self.assertNotIn("default=[]", source)
        self.assertNotIn("default={}", source)
        self.assertIn("warnings must be a list of strings.", source)
        self.assertIn("Do not store API keys or raw images.", source)
        self.assertIn("Do not store an image.", source)

    def test_help_text_rejects_secrets_and_images(self):
        source = ast.get_source_segment(
            (AI_DIR / "models.py").read_text(), _class_def("ExternalHTRJob"),
        )
        self.assertIn("Do not store API keys", source)
        self.assertIn("base64 images", source)
        self.assertIn("raw responses", source)
        self.assertIn("Transcription jobs do not read this table.", source)

    def test_models_do_not_import_the_client_or_the_contract(self):
        tree = ast.parse((AI_DIR / "models.py").read_text())
        modules = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.append(node.module)
        text = " ".join(modules)
        for banned in ("htr_engine_contract", "htr_engine_client", "requests", "urllib", "celery"):
            self.assertNotIn(banned, text)


class MigrationTests(unittest.TestCase):
    def test_migration_creates_only_the_new_tables(self):
        text = MIGRATION.read_text()
        self.assertEqual(text.count("migrations.CreateModel"), 2)
        self.assertIn("ExternalHTRJob", text)
        self.assertIn("ExternalHTRLineResult", text)
        self.assertIn("'0007_externalhtrengineconfig'", text)
        self.assertIn("default='planned'", text)
        self.assertIn("default=list", text)
        self.assertIn("default=0", text)
        for banned in (
            "AlterField",
            "RemoveField",
            "DeleteModel",
            "RunPython",
            "AddField",
            "AIJob",
            "AIBackendConfig",
            "LineTranscription",
            "endpoint_url",
            "base64",
            "'image'",
            "'raw_body'",
            "'response_body'",
            "default=[]",
            "default={}",
        ):
            self.assertNotIn(banned, text, banned)


class IsolationTests(unittest.TestCase):
    def test_runtime_modules_do_not_name_the_audit_models(self):
        for name in RUNTIME:
            text = (AI_DIR / name).read_text()
            self.assertNotIn("ExternalHTRJob", text, name)
            self.assertNotIn("ExternalHTRLineResult", text, name)
            self.assertNotIn("external_htr_jobs", text, name)

    def test_admin_does_not_call_an_endpoint_for_the_audit_models(self):
        tree = ast.parse((AI_DIR / "admin.py").read_text())
        found = set()
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            if node.name not in {"ExternalHTRJobAdmin", "ExternalHTRLineResultAdmin"}:
                continue
            found.add(node.name)
            dumped = ast.dump(node)
            for banned in (
                "probe_configs",
                "htr_engine_client",
                "recognize_lines",
                "urlopen",
                "requests",
                "actions",
            ):
                self.assertNotIn(banned, dumped, node.name)
        self.assertEqual(found, {"ExternalHTRJobAdmin", "ExternalHTRLineResultAdmin"})


if __name__ == "__main__":
    unittest.main()
