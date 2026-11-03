"""PyLaia menu entry when no config row exists. No Django and no network."""
import unittest
from pathlib import Path
from types import SimpleNamespace

from ai.external_htr_menu import engine_menu, is_pylaia_config, pylaia_placeholder

AI_DIR = Path(__file__).resolve().parents[1]
REPO = AI_DIR.parents[2]
PANEL = REPO / "front" / "vue" / "components" / "ExternalHtrPanel" / "ExternalHtrPanel.vue"
IMAGES = AI_DIR.parents[1] / "escriptorium" / "templates" / "core" / "document_images.html"


def _config(name, metadata=None):
    return SimpleNamespace(name=name, metadata={} if metadata is None else metadata)


class MenuTests(unittest.TestCase):
    def test_missing_pylaia_is_a_disabled_entry_with_no_id(self):
        htrflow = {
            "id": 7,
            "name": "HTRflow",
            "tier": "research",
            "experimental": True,
            "enabled": True,
            "available": True,
        }
        menu = engine_menu([_config("HTRflow", {"task": "recognize_page", "engine": "htrflow"})], [htrflow])
        self.assertEqual(menu[0], htrflow)
        self.assertEqual(menu[1], pylaia_placeholder())
        self.assertIsNone(menu[1]["id"])
        self.assertEqual(menu[1]["name"], "PyLaia")
        self.assertEqual(menu[1]["engine"], "pylaia")
        self.assertFalse(menu[1]["enabled"])
        self.assertFalse(menu[1]["available"])
        self.assertTrue(menu[1]["placeholder"])
        self.assertNotIn("endpoint_url", menu[1])
        self.assertNotIn("models", menu[1])

    def test_a_saved_pylaia_row_replaces_the_placeholder(self):
        row = {"id": 3, "name": "PyLaia", "available": True, "enabled": True}
        saved = _config("PyLaia", {})
        self.assertTrue(is_pylaia_config(saved))
        self.assertTrue(is_pylaia_config(_config("stage3-pylaia")))
        self.assertTrue(is_pylaia_config(_config("line engine", {"engine": "pylaia"})))
        self.assertFalse(is_pylaia_config(_config("HTRflow", {"engine": "htrflow"})))
        menu = engine_menu([saved, _config("HTRflow")], [row, {"id": 7, "name": "HTRflow"}])
        self.assertEqual([item["name"] for item in menu], ["PyLaia", "HTRflow"])
        self.assertFalse(any(item.get("placeholder") for item in menu))

    def test_panel_selects_the_placeholder_without_a_model_request(self):
        text = PANEL.read_text(encoding="utf-8")
        self.assertTrue(PANEL.is_file())
        self.assertIn('value === "pylaia"', text)
        self.assertIn("engineName === 'pylaia' ? 'pylaia' : ''", text)
        self.assertIn("this.configId = \"\";", text)
        self.assertIn("this.models = [];", text)
        self.assertIn("this.configId && this.modelId", text)
        self.assertNotIn("huginmunin", text)
        page = IMAGES.read_text(encoding="utf-8")
        self.assertIn('placeholder ? "pylaia" : String(engine.id)', page)
        self.assertIn('engineSelect.value === "pylaia"', page)
        self.assertIn("startButton.disabled = true", page)
        self.assertLess(
            page.index('engineSelect.value === "pylaia"'),
            page.index('"/external-htr/engines/" + engineSelect.value + "/models/"'),
        )


if __name__ == "__main__":
    unittest.main()
