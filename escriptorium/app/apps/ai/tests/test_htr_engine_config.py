"""External HTR engine config is stored only. No Django, no network, no client."""
import ast
import unittest
from pathlib import Path

from ai.htr_engine_contract import TIERS

AI_DIR = Path(__file__).resolve().parents[1]
APPS_DIR = AI_DIR.parent

JOB_PATHS = (
    AI_DIR / "tasks.py",
    AI_DIR / "views.py",
    AI_DIR / "pipeline.py",
    AI_DIR / "backends.py",
    AI_DIR / "dispatch.py",
    AI_DIR / "serializers.py",
    APPS_DIR / "api" / "urls.py",
)


def _class_def():
    tree = ast.parse((AI_DIR / "models.py").read_text())
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "ExternalHTREngineConfig":
            return node
    raise AssertionError("ExternalHTREngineConfig is missing")


def _call_keywords(node):
    if not isinstance(node, ast.Assign) or len(node.targets) != 1:
        return None, None
    target = node.targets[0]
    if not isinstance(target, ast.Name) or not isinstance(node.value, ast.Call):
        return None, None
    keywords = {}
    for keyword in node.value.keywords:
        if keyword.arg:
            keywords[keyword.arg] = keyword.value
    return target.id, keywords


def _const(node):
    if isinstance(node, ast.Constant):
        return node.value
    raise AssertionError(f"expected a constant, got {type(node).__name__}")


class ConfigModelTests(unittest.TestCase):
    def test_enabled_and_experimental_default_off(self):
        defaults = {}
        for statement in _class_def().body:
            name, keywords = _call_keywords(statement)
            if name in {"enabled", "experimental"}:
                defaults[name] = _const(keywords["default"])
        self.assertEqual(defaults, {"enabled": False, "experimental": False})

    def test_tier_choices_match_the_contract(self):
        constants = {}
        choice_pairs = None
        for statement in _class_def().body:
            if not isinstance(statement, ast.Assign):
                continue
            target = statement.targets[0]
            if not isinstance(target, ast.Name):
                continue
            if isinstance(statement.value, ast.Constant):
                constants[target.id] = statement.value.value
            elif target.id == "TIER_CHOICES":
                choice_pairs = statement.value.elts
        self.assertIsNotNone(choice_pairs)
        tiers = set()
        for pair in choice_pairs:
            key = pair.elts[0]
            if isinstance(key, ast.Name):
                tiers.add(constants[key.id])
            else:
                tiers.add(_const(key))
        self.assertEqual(tiers, set(TIERS))
        _name, keywords = next(
            pair for pair in (_call_keywords(statement) for statement in _class_def().body)
            if pair[0] == "tier"
        )
        self.assertIsInstance(keywords["choices"], ast.Name)
        self.assertEqual(keywords["choices"].id, "TIER_CHOICES")

    def test_timeout_rejects_zero(self):
        for statement in _class_def().body:
            name, keywords = _call_keywords(statement)
            if name != "timeout_seconds":
                continue
            self.assertEqual(_const(keywords["default"]), 30)
            validator = keywords["validators"].elts[0]
            self.assertEqual(validator.func.id, "MinValueValidator")
            self.assertEqual(_const(validator.args[0]), 1)
            return
        self.fail("timeout_seconds missing")

    def test_metadata_must_be_an_object(self):
        source = (AI_DIR / "models.py").read_text()
        self.assertIn("metadata must be a JSON object.", source)
        self.assertIn("isinstance(self.metadata, dict)", source)

    def test_model_does_not_import_the_wire_contract_or_a_client(self):
        tree = ast.parse((AI_DIR / "models.py").read_text())
        modules = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.append(node.module)
        text = " ".join(modules)
        for banned in ("htr_engine_contract", "requests", "httpx", "urllib", "celery"):
            self.assertNotIn(banned, text)


class IsolationTests(unittest.TestCase):
    def test_existing_jobs_do_not_name_the_config(self):
        for path in JOB_PATHS:
            text = path.read_text()
            self.assertNotIn("ExternalHTREngineConfig", text, path)
            self.assertNotIn("externalhtrengineconfig", text.lower(), path)

    def test_migration_creates_a_disabled_by_default_table(self):
        text = (AI_DIR / "migrations" / "0007_externalhtrengineconfig.py").read_text()
        self.assertIn("CreateModel", text)
        self.assertIn("ExternalHTREngineConfig", text)
        self.assertIn("default=False", text)
        self.assertIn("'0006_azure_mistral_providers'", text)
        for banned in ("pylaia", "trocr", "drethtr", "requests"):
            self.assertNotIn(banned, text.lower())


if __name__ == "__main__":
    unittest.main()
