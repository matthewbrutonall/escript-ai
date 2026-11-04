"""Line-engine menu entries when no config row exists. No Django and no network."""
import unittest
from pathlib import Path
from types import SimpleNamespace

from ai.external_htr_menu import (
    calamari_placeholder,
    engine_menu,
    is_calamari_config,
    is_loghi_config,
    is_pylaia_config,
    loghi_placeholder,
    pylaia_placeholder,
)

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
        self.assertEqual(menu[2], calamari_placeholder())
        self.assertEqual(menu[3], loghi_placeholder())
        self.assertIsNone(menu[1]["id"])
        self.assertEqual(menu[1]["name"], "PyLaia")
        self.assertEqual(menu[1]["engine"], "pylaia")
        self.assertFalse(menu[1]["enabled"])
        self.assertFalse(menu[1]["available"])
        self.assertTrue(menu[1]["placeholder"])
        self.assertNotIn("endpoint_url", menu[1])
        self.assertNotIn("models", menu[1])
        self.assertIsNone(menu[2]["id"])
        self.assertEqual(menu[2]["name"], "Calamari")
        self.assertEqual(menu[2]["engine"], "calamari")
        self.assertFalse(menu[2]["enabled"])
        self.assertFalse(menu[2]["available"])
        self.assertTrue(menu[2]["placeholder"])
        self.assertNotIn("endpoint_url", menu[2])
        self.assertNotIn("models", menu[2])
        self.assertIsNone(menu[3]["id"])
        self.assertEqual(menu[3]["name"], "Loghi")
        self.assertEqual(menu[3]["engine"], "loghi")
        self.assertFalse(menu[3]["enabled"])
        self.assertFalse(menu[3]["available"])
        self.assertTrue(menu[3]["placeholder"])
        self.assertNotIn("endpoint_url", menu[3])
        self.assertNotIn("models", menu[3])

    def test_a_saved_pylaia_row_replaces_only_that_placeholder(self):
        row = {"id": 3, "name": "PyLaia", "available": True, "enabled": True}
        saved = _config("PyLaia", {})
        self.assertTrue(is_pylaia_config(saved))
        self.assertTrue(is_pylaia_config(_config("stage3-pylaia")))
        self.assertTrue(is_pylaia_config(_config("line engine", {"engine": "pylaia"})))
        self.assertFalse(is_pylaia_config(_config("HTRflow", {"engine": "htrflow"})))
        self.assertFalse(is_calamari_config(saved))
        menu = engine_menu([saved, _config("HTRflow")], [row, {"id": 7, "name": "HTRflow"}])
        self.assertEqual([item["name"] for item in menu], ["PyLaia", "HTRflow", "Calamari", "Loghi"])
        self.assertEqual(menu[2], calamari_placeholder())
        self.assertEqual(menu[3], loghi_placeholder())
        self.assertFalse(menu[0].get("placeholder"))

    def test_a_saved_calamari_row_replaces_only_that_placeholder(self):
        row = {"id": 4, "name": "Calamari", "available": False, "enabled": False}
        saved = _config("Calamari", {})
        self.assertTrue(is_calamari_config(saved))
        self.assertTrue(is_calamari_config(_config("antiqua", {"engine": "calamari"})))
        self.assertFalse(is_calamari_config(_config("PyLaia", {})))
        self.assertFalse(is_pylaia_config(saved))
        menu = engine_menu([saved], [row])
        self.assertEqual([item["name"] for item in menu], ["Calamari", "PyLaia", "Loghi"])
        self.assertEqual(menu[1], pylaia_placeholder())
        self.assertEqual(menu[2], loghi_placeholder())
        self.assertFalse(menu[0].get("placeholder"))

    def test_a_saved_loghi_row_replaces_only_that_placeholder(self):
        row = {"id": 5, "name": "Loghi", "available": True, "enabled": True}
        saved = _config("Loghi", {})
        self.assertTrue(is_loghi_config(saved))
        self.assertTrue(is_loghi_config(_config("line engine", {"engine": "loghi"})))
        self.assertFalse(is_loghi_config(_config("Calamari", {})))
        self.assertFalse(is_calamari_config(saved))
        self.assertFalse(is_pylaia_config(saved))
        menu = engine_menu([saved], [row])
        self.assertEqual([item["name"] for item in menu], ["Loghi", "PyLaia", "Calamari"])
        self.assertEqual(menu[1], pylaia_placeholder())
        self.assertEqual(menu[2], calamari_placeholder())
        self.assertFalse(menu[0].get("placeholder"))

    def test_panel_selects_the_placeholder_without_a_model_request(self):
        text = PANEL.read_text(encoding="utf-8")
        self.assertTrue(PANEL.is_file())
        self.assertIn('value === "pylaia" || value === "calamari" || value === "loghi"', text)
        self.assertIn(
            'this.engineName === "pylaia" || this.engineName === "calamari" || this.engineName === "loghi"',
            text,
        )
        self.assertIn("engine.placeholder ? engine.engine : String(engine.id)", text)
        self.assertIn("this.configId = \"\";", text)
        self.assertIn("this.models = [];", text)
        self.assertIn("this.configId && this.modelId", text)
        self.assertNotIn("huginmunin", text)
        page = IMAGES.read_text(encoding="utf-8")
        self.assertIn("placeholder ? engine.engine : String(engine.id)", page)
        self.assertIn(
            'engineSelect.value === "pylaia" || engineSelect.value === "calamari" || engineSelect.value === "loghi"',
            page,
        )
        self.assertIn("startButton.disabled = true", page)
        self.assertLess(
            page.index('engineSelect.value === "pylaia"'),
            page.index('"/external-htr/engines/" + engineSelect.value + "/models/"'),
        )


if __name__ == "__main__":
    unittest.main()
