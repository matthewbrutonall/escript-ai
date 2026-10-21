"""Source checks for the external HTR writeback helper.

No Django and no database. Behaviour is covered in
ai.django_integration_tests.test_external_htr_writeback_orm.
"""
import ast
import unittest
from pathlib import Path

AI_DIR = Path(__file__).resolve().parents[1]
WRITER = AI_DIR / "external_htr_writeback.py"
MIGRATION = AI_DIR / "migrations" / "0009_externalhtrjob_import_link.py"
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


class WritebackSourceTests(unittest.TestCase):
    def test_writer_stays_a_database_import(self):
        text = WRITER.read_text(encoding="utf-8")
        for banned in (
            "urlopen",
            "build_opener",
            "socket",
            "crop_line",
            "base64",
            "requests",
            "Image.open",
            "htr_engine_client",
            "external_htr_live",
            "get_or_create",
            "new_version",
            "acknowledge_sample",
            "mark_training_eligible",
            "find_comparison_transcription",
            "min_n",
            "pct=",
            ".image",
        ):
            self.assertNotIn(banned, text, banned)
        for required in (
            "select_for_update",
            "transaction.atomic",
            "IntegrityError",
            "CascadeUpdate.bypass",
            "build_gate_sample(",
            "comparison=None",
            "sample_line_pks=summary[\"sample_line_pks\"]",
            "mean_cer=summary[\"mean_cer\"]",
            "AILayerGate.objects.create",
            "state=AILayerGate.STATE_RAW",
            'update_fields=["imported", "imported_transcription", "updated_at"]',
        ):
            self.assertIn(required, text, required)
        self.assertLess(
            text.index("build_gate_sample("),
            text.index("AILayerGate.objects.create"),
        )
        self.assertLess(
            text.index("AILayerGate.objects.create"),
            text.index("job.imported = True"),
        )
        tree = ast.parse(text)
        imported = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)
            elif isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
        joined = " ".join(imported)
        for banned in ("htr_engine_client", "external_htr_live", "urllib", "requests", "socket"):
            self.assertNotIn(banned, joined, banned)
        self.assertIn("ai.external_htr_writeback_plan", joined)
        self.assertIn("ai.gate", joined)
        self.assertNotIn("import_external_htr_writeback", PLANNER.read_text(encoding="utf-8"))

    def test_stage1_and_commands_do_not_call_the_writer(self):
        for name in STAGE1:
            path = AI_DIR / name
            self.assertTrue(path.is_file(), name)
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("import_external_htr_writeback", text, name)
            self.assertNotIn("external_htr_writeback import", text, name)

    def test_migration_adds_only_the_import_link(self):
        text = MIGRATION.read_text(encoding="utf-8")
        self.assertEqual(text.count("migrations.AddField"), 2)
        self.assertIn("'0008_externalhtrjob'", text)
        self.assertIn("'0086_seed_default_ontology'", text)
        self.assertIn("name='imported'", text)
        self.assertIn("name='imported_transcription'", text)
        self.assertIn("deletion.SET_NULL", text)
        self.assertIn("default=False", text)
        for banned in (
            "RunPython",
            "RemoveField",
            "DeleteModel",
            "AlterField",
            "CreateModel",
            "LineTranscription",
            "endpoint_url",
            "base64",
            "on_delete=django.db.models.deletion.PROTECT",
        ):
            self.assertNotIn(banned, text, banned)


if __name__ == "__main__":
    unittest.main()
