"""Loghi-HTR stays on the line contract. No network."""
import unittest
from pathlib import Path
from types import SimpleNamespace

from ai.external_htr_dispatch import is_htrflow_page_config
from ai.loghi_adapter import LOGHI_INTEGRATED

AI_DIR = Path(__file__).resolve().parents[1]
PACKAGE = AI_DIR / "external_engines" / "loghi"


class AdapterTests(unittest.TestCase):
    def test_integration_flag_is_true(self):
        self.assertIs(LOGHI_INTEGRATED, True)
        text = (AI_DIR / "loghi_adapter.py").read_text(encoding="utf-8")
        self.assertNotIn("import django", text)
        self.assertNotIn("urllib", text)
        self.assertNotIn("requests", text)
        self.assertNotIn("socket", text)
        self.assertNotIn("celery", text)
        self.assertNotIn("recognize_page", text)
        self.assertNotIn("acknowledge_sample", text)
        self.assertNotIn("mark_training_eligible", text)

    def test_a_loghi_row_is_not_the_page_contract(self):
        self.assertFalse(is_htrflow_page_config(SimpleNamespace(metadata={"engine": "loghi"})))
        self.assertFalse(is_htrflow_page_config(SimpleNamespace(metadata={})))

    def test_the_optional_compose_file_is_not_the_default_stack(self):
        escriptorium = Path(__file__).resolve().parents[4]
        default = (escriptorium / "docker-compose.yml").read_text(encoding="utf-8").lower()
        self.assertNotIn("loghi", default)
        optional = (escriptorium / "docker-compose.loghi.yml").read_text(encoding="utf-8")
        self.assertNotIn("\n    ports:", optional)
        self.assertIn("internal: true", optional)
        self.assertIn("loghi-work:", optional)
        self.assertIn("entrypoint.sh", optional)
        entry = (PACKAGE / "entrypoint.sh").read_text(encoding="utf-8")
        self.assertIn("--require-licence", entry)
        self.assertIn("http://127.0.0.1:5000", entry)
        self.assertIn("Error loading model", entry)
        self.assertIn("Failed to load model", entry)
        self.assertIn("Error loading tokenizer", entry)
        self.assertIn('LOGHI_GPUS: "-1"', optional)
        self.assertIn('CUDA_VISIBLE_DEVICES: ""', optional)
        self.assertIn("NVIDIA_VISIBLE_DEVICES: void", optional)
        image = (PACKAGE / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn(
            "python:3.11-slim-bookworm@sha256:94a8fee35252d06ab75856f15f12c7bae67b10e8a6da93c7fded82b2610926cd",
            image,
        )
        self.assertIn("requirements.lock", image)
        self.assertIn("c24c745e9aeb287bedeb6b4edee670ae5c81a3cd", image)
        lock = (PACKAGE / "requirements.lock").read_text(encoding="utf-8")
        self.assertIn("tensorflow==2.19.0", lock)
        self.assertNotIn("and-cuda", lock)
        app_requirements = (escriptorium / "app" / "requirements.txt").read_text(encoding="utf-8").lower()
        self.assertNotIn("loghi", app_requirements)
        self.assertNotIn("tensorflow", app_requirements)
        worker = (AI_DIR / "external_htr_run.py").read_text(encoding="utf-8").lower()
        self.assertNotIn("loghi", worker)


if __name__ == "__main__":
    unittest.main()
