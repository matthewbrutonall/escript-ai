"""Calamari stays on the line contract. No network."""
import unittest
from pathlib import Path
from types import SimpleNamespace

from ai.calamari_adapter import CALAMARI_INTEGRATED
from ai.external_htr_dispatch import is_htrflow_page_config

AI_DIR = Path(__file__).resolve().parents[1]
PACKAGE = AI_DIR / "external_engines" / "calamari"


class AdapterTests(unittest.TestCase):
    def test_integration_flag_is_true(self):
        self.assertIs(CALAMARI_INTEGRATED, True)
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

    def test_the_optional_compose_file_is_not_the_default_stack(self):
        escriptorium = Path(__file__).resolve().parents[4]
        default = (escriptorium / "docker-compose.yml").read_text(encoding="utf-8").lower()
        self.assertNotIn("calamari", default)
        optional = (escriptorium / "docker-compose.calamari.yml").read_text(encoding="utf-8")
        self.assertNotIn("\n    ports:", optional)
        self.assertIn("internal: true", optional)
        self.assertIn("\nvolumes:\n", optional)
        self.assertIn("calamari-work:", optional)
        self.assertIn("--require-licence", optional)
        self.assertIn("--device", optional)
        self.assertIn("\n      - cpu", optional)
        gpu = (escriptorium / "docker-compose.calamari.gpu.yml").read_text(encoding="utf-8")
        self.assertNotIn("ports:", gpu)
        self.assertIn("--device", gpu)
        self.assertIn("\n      - gpu", gpu)
        self.assertIn('CUDA_VISIBLE_DEVICES: "0"', gpu)
        image = (PACKAGE / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn(
            "python:3.10-slim-bookworm@sha256:5be5aaecb962a41567bc8041f1e9e3432cb085b037fe057f9a562630b2e4b370",
            image,
        )
        self.assertIn("requirements.lock", image)
        lock = (PACKAGE / "requirements.lock").read_text(encoding="utf-8")
        self.assertIn("calamari_ocr==2.3.1\n", lock)
        self.assertIn("tensorflow==2.15.1\n", lock)
        app_requirements = (escriptorium / "app" / "requirements.txt").read_text(encoding="utf-8").lower()
        self.assertNotIn("calamari", app_requirements)
        self.assertNotIn("tensorflow", app_requirements)

    def test_the_worker_file_does_not_name_this_engine(self):
        text = (AI_DIR / "external_htr_run.py").read_text(encoding="utf-8").lower()
        self.assertNotIn("calamari", text)
        follow = (AI_DIR / "docs" / "htrflow-follow-up.md").read_text(encoding="utf-8")
        for name in ("Calamari", "Loghi", "TrOCR", "DRetHTR", "Laypa"):
            self.assertIn(name, follow, name)


if __name__ == "__main__":
    unittest.main()
