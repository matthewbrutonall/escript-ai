"""Calamari stays on the line contract and is not integrated. No network."""
import unittest
from pathlib import Path
from types import SimpleNamespace

from ai.calamari_adapter import CALAMARI_INTEGRATED
from ai.external_htr_dispatch import is_htrflow_page_config

AI_DIR = Path(__file__).resolve().parents[1]
PACKAGE = AI_DIR / "external_engines" / "calamari"


class AdapterTests(unittest.TestCase):
    def test_integration_flag_stays_false(self):
        self.assertIs(CALAMARI_INTEGRATED, False)
        text = (AI_DIR / "calamari_adapter.py").read_text(encoding="utf-8")
        self.assertNotIn("import django", text)
        self.assertNotIn("urllib", text)
        self.assertNotIn("requests", text)
        self.assertNotIn("socket", text)
        self.assertNotIn("celery", text)
        self.assertNotIn("recognize_page", text)
        self.assertNotIn("acknowledge_sample", text)
        self.assertNotIn("mark_training_eligible", text)

    def test_package_does_not_import_the_runtime_or_django(self):
        for path in PACKAGE.glob("*.py"):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("calamari_ocr", text, path.name)
            self.assertNotIn("tensorflow", text, path.name)
            self.assertNotIn("import django", text, path.name)
            self.assertNotIn("recognize_page", text, path.name)

    def test_a_calamari_row_is_not_the_page_contract(self):
        self.assertFalse(is_htrflow_page_config(SimpleNamespace(metadata={"engine": "calamari"})))
        self.assertFalse(is_htrflow_page_config(SimpleNamespace(metadata={})))
        self.assertTrue(is_htrflow_page_config(SimpleNamespace(
            metadata={"task": "recognize_page", "engine": "htrflow"},
        )))

    def test_the_worker_file_does_not_name_this_engine(self):
        text = (AI_DIR / "external_htr_run.py").read_text(encoding="utf-8").lower()
        self.assertNotIn("calamari", text)
        follow = (AI_DIR / "docs" / "htrflow-follow-up.md").read_text(encoding="utf-8")
        for name in ("Calamari", "Loghi", "TrOCR", "DRetHTR", "Laypa"):
            self.assertIn(name, follow, name)


if __name__ == "__main__":
    unittest.main()
