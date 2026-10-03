"""CLI contract check. The checker is mocked. No Django, no network."""
import ast
import unittest
from pathlib import Path

from ai.htr_engine_contract_check import (
    ContractCheckResult,
    EngineConfigNotFound,
    execute_contract_check,
)

AI_DIR = Path(__file__).resolve().parents[1]
APPS_DIR = AI_DIR.parent
COMMAND = AI_DIR / "management" / "commands" / "check_external_htr_engine.py"

SECRET_URL = "http://secret.example/token"
SECRET_TOKEN = "secret-token"


class Config:
    def __init__(self):
        self.name = "North"
        self.endpoint_url = SECRET_URL
        self.metadata = {"token": SECRET_TOKEN}
        self.enabled = True


class CommandResultTests(unittest.TestCase):
    def test_success_exits_zero(self):
        seen = []

        def check(config):
            seen.append(config)
            return ContractCheckResult(True, "recognize", "ok", "contract check passed")

        code, line = execute_contract_check(7, self._fetch, check)
        self.assertEqual(seen, [self.config])
        self.assertEqual(code, 0)
        self.assertEqual(line, "OK: North (recognize)")
        self._assert_clean(line)

    def test_failure_exits_nonzero(self):
        def check(config):
            return ContractCheckResult(
                False, "capabilities", "timeout", "capabilities check failed",
            )

        code, line = execute_contract_check(7, self._fetch, check)
        self.assertEqual(code, 1)
        self.assertEqual(line, "FAILED: North (capabilities: timeout) capabilities check failed")
        self._assert_clean(line)

    def test_missing_config_exits_nonzero(self):
        def fetch(config_id):
            raise EngineConfigNotFound(SECRET_URL)

        def check(config):
            raise AssertionError("checker was called")

        code, line = execute_contract_check(7, fetch, check)
        self.assertEqual(code, 1)
        self.assertEqual(line, "FAILED: engine was not found")
        self._assert_clean(line)

    def test_disabled_result_exits_nonzero(self):
        def check(config):
            return ContractCheckResult(False, "disabled", "disabled", "engine is disabled")

        code, line = execute_contract_check(7, self._fetch, check)
        self.assertEqual(code, 1)
        self.assertEqual(line, "FAILED: North (disabled: disabled) engine is disabled")
        self._assert_clean(line)

    def setUp(self):
        self.config = Config()

    def _fetch(self, config_id):
        self.assertEqual(config_id, 7)
        return self.config

    def _assert_clean(self, line):
        self.assertNotIn("\n", line)
        self.assertNotIn(SECRET_URL, line)
        self.assertNotIn(SECRET_TOKEN, line)


class IsolationTests(unittest.TestCase):
    def test_command_calls_the_checker_and_hides_secrets(self):
        text = COMMAND.read_text()
        self.assertIn("run_contract_check", text)
        self.assertIn("execute_contract_check", text)
        self.assertIn("stdout.write", text)
        self.assertIn("sys.exit", text)
        self.assertIn("EngineConfigNotFound", text)
        self.assertNotIn("logging", text)
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                self.assertNotIn(node.attr, {"endpoint_url", "metadata"})
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                self.assertNotIn(node.func.id, {"open", "socket"})

    def test_stage1_modules_do_not_import_the_command(self):
        paths = [
            AI_DIR / "tasks.py",
            AI_DIR / "views.py",
            AI_DIR / "pipeline.py",
            AI_DIR / "backends.py",
            AI_DIR / "dispatch.py",
            AI_DIR / "serializers.py",
            AI_DIR / "models.py",
            AI_DIR / "admin.py",
            AI_DIR / "htr_engine_client.py",
            APPS_DIR / "api" / "urls.py",
        ]
        helper = (AI_DIR / "htr_engine_contract_check.py").read_text()
        self.assertNotIn("check_external_htr_engine", helper)
        for path in paths:
            text = path.read_text()
            self.assertNotIn("check_external_htr_engine", text, path)
            self.assertNotIn("execute_contract_check", text, path)


if __name__ == "__main__":
    unittest.main()
