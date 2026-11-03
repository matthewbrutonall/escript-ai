"""Source checks for the external HTR action. No Django and no network."""
import unittest
from pathlib import Path

AI_DIR = Path(__file__).resolve().parents[1]
APPS_DIR = AI_DIR.parent
REPO = AI_DIR.parents[2]
TASKS = AI_DIR / "tasks.py"
TRANSCRIBE = REPO / "front" / "vue" / "components" / "TranscribeModal" / "TranscribeModal.vue"
BANNED_IN_TASKS = (
    "htr_engine_contract",
    "htr_engine_client",
    "recognize_lines",
    "import_external_htr_writeback",
    "ExternalHTRJob",
    "ExternalHTRLineResult",
    "external_htr_jobs",
    "ExternalHTREngineConfig",
    "external_htr_policy",
)


class SurfaceTests(unittest.TestCase):
    def test_task_registration_stays_out_of_the_transcribe_path(self):
        text = TASKS.read_text(encoding="utf-8")
        self.assertIn("def ai_transcribe(", text)
        self.assertIn(
            "from ai.external_htr_tasks import external_htr_recognize  # noqa: F401",
            text,
        )
        for banned in BANNED_IN_TASKS:
            self.assertNotIn(banned, text, banned)
        task = (AI_DIR / "external_htr_tasks.py").read_text(encoding="utf-8")
        self.assertIn('name="ai.external_htr_recognize"', task)
        self.assertIn("except Exception:", task)
        self.assertNotIn("recognize_lines", task)
        self.assertNotIn("htr_engine_client", task)
        self.assertNotIn("import_external_htr_writeback", task)
        modal = TRANSCRIBE.read_text(encoding="utf-8")
        self.assertNotIn("external-htr", modal)
        self.assertNotIn("external_htr", modal)
        urls = (APPS_DIR / "api" / "urls.py").read_text(encoding="utf-8")
        self.assertNotIn("external-htr", urls)
        self.assertNotIn("external_htr", urls)

    def test_import_helper_does_not_call_the_single_audit_writer(self):
        text = (AI_DIR / "external_htr_run_import.py").read_text(encoding="utf-8")
        self.assertNotIn("import_external_htr_writeback", text)
        self.assertNotIn("acknowledge_sample", text)
        self.assertNotIn("mark_training_eligible", text)
        self.assertIn("build_gate_sample", text)
        self.assertIn("STATE_RAW", text)

    def test_worker_names_one_engine_and_the_hosted_refusal(self):
        text = (AI_DIR / "external_htr_run.py").read_text(encoding="utf-8")
        self.assertIn("hosted engines are not used for this action", text)
        self.assertIn("external htr run %s %s", text)
        for name in ("calamari", "loghi", "trocr", "drethtr", "laypa", "htrflow"):
            self.assertNotIn(name, text.lower(), name)

    def test_migration_adds_the_run_without_rewriting_the_import_link(self):
        created = (AI_DIR / "migrations" / "0011_externalhtrrun.py").read_text(encoding="utf-8")
        self.assertIn("CreateModel", created)
        self.assertIn("ExternalHTRRun", created)
        self.assertIn("AlterField", created)
        self.assertIn("related_name='external_htr_imports'", created)
        self.assertIn("default=list", created)
        self.assertNotIn("RunPython", created)
        previous = (AI_DIR / "migrations" / "0009_externalhtrjob_import_link.py").read_text(
            encoding="utf-8",
        )
        self.assertIn("OneToOneField", previous)
        self.assertNotIn("AlterField", previous)

    def test_public_notes_keep_the_line_crop_rule_and_record_htrflow(self):
        integration = (AI_DIR / "docs" / "external-htr-integration.md").read_text(encoding="utf-8")
        self.assertIn("Do not use `crop_line`, whose empty-box", integration)
        self.assertNotIn("Stage 2 roadmap", integration)
        follow = (AI_DIR / "docs" / "htrflow-follow-up.md").read_text(encoding="utf-8")
        self.assertIn("HTRflow page recognition is integrated into Escript AI.", follow)
        self.assertIn("Line.external_id", follow)
        for name in ("Calamari", "Loghi", "TrOCR", "DRetHTR", "Laypa"):
            self.assertIn(name, follow, name)


if __name__ == "__main__":
    unittest.main()
