"""Contract check. The engine client is mocked. No Django, no network."""
import ast
import unittest
from pathlib import Path
from unittest import mock

from ai.htr_engine_client import EngineClientError
from ai.htr_engine_contract import ContractError
from ai.htr_engine_contract_check import run_contract_check

AI_DIR = Path(__file__).resolve().parents[1]
APPS_DIR = AI_DIR.parent

SECRET_URL = "http://secret.example/token"
SECRET_TOKEN = "secret-token"
RAW_BODY = "raw-body-AAAA"


class Config:
    def __init__(self, **overrides):
        self.enabled = True
        self.endpoint_url = SECRET_URL
        self.timeout_seconds = 12
        self.metadata = {"token": SECRET_TOKEN}
        for key, value in overrides.items():
            setattr(self, key, value)


class Caps:
    engine = "example"


class Model:
    def __init__(self, model_id="example-model"):
        self.model_id = model_id


class Listing:
    def __init__(self, models=(Model(),)):
        self.models = models


def _leak_text():
    return f"{SECRET_URL} {SECRET_TOKEN} {RAW_BODY}"


class ContractCheckTests(unittest.TestCase):
    def test_success_runs_capabilities_models_detail_and_recognize(self):
        calls = []

        def capabilities(config):
            calls.append("capabilities")
            return Caps()

        def models(config):
            calls.append("models")
            return Listing()

        def model(config, model_id):
            calls.append(("model", model_id))
            return Model(model_id)

        def recognize(config, payload):
            calls.append("recognize")
            self.assertEqual(payload["engine"], "example")
            self.assertEqual(payload["model_id"], "example-model")
            self.assertEqual(payload["lines"], [{"line_id": "1", "image": "AAAA"}])
            self.assertNotIn(SECRET_URL, str(payload))
            self.assertNotIn(SECRET_TOKEN, str(payload))
            return object()

        with self._patches(capabilities, models, model, recognize):
            result = run_contract_check(Config())
        self.assertEqual(calls, ["capabilities", "models", ("model", "example-model"), "recognize"])
        self.assertTrue(result.ok)
        self.assertEqual(result.step, "recognize")
        self.assertEqual(result.code, "ok")
        self.assertEqual(result.message, "contract check passed")
        self._assert_clean(result)

    def test_capabilities_failure_stops(self):
        def capabilities(config):
            raise EngineClientError("timeout", _leak_text())

        with self._patches(capabilities, self._fail_if_called, self._fail_if_called, self._fail_if_called):
            result = run_contract_check(Config())
        self.assertFalse(result.ok)
        self.assertEqual(result.step, "capabilities")
        self.assertEqual(result.code, "timeout")
        self.assertEqual(result.message, "capabilities check failed")
        self._assert_clean(result)

    def test_empty_model_list_stops_before_detail(self):
        def models(config):
            return Listing(models=())

        with self._patches(lambda config: Caps(), models, self._fail_if_called, self._fail_if_called):
            result = run_contract_check(Config())
        self.assertFalse(result.ok)
        self.assertEqual(result.step, "models")
        self.assertEqual(result.code, "empty_models")
        self.assertEqual(result.message, "engine reported no models")
        self._assert_clean(result)

    def test_model_detail_failure_stops_before_recognize(self):
        def model(config, model_id):
            raise ContractError("model_not_found", _leak_text())

        with self._patches(lambda config: Caps(), lambda config: Listing(), model, self._fail_if_called):
            result = run_contract_check(Config())
        self.assertFalse(result.ok)
        self.assertEqual(result.step, "model")
        self.assertEqual(result.code, "model_not_found")
        self.assertEqual(result.message, "model detail check failed")
        self._assert_clean(result)

    def test_recognize_failure_hides_the_exception_text(self):
        def recognize(config, payload):
            raise EngineClientError("unavailable", _leak_text())

        with self._patches(lambda config: Caps(), lambda config: Listing(), lambda config, model_id: Model(), recognize):
            result = run_contract_check(Config())
        self.assertFalse(result.ok)
        self.assertEqual(result.step, "recognize")
        self.assertEqual(result.code, "unavailable")
        self.assertEqual(result.message, "recognition check failed")
        self._assert_clean(result)

    def test_disabled_config_is_not_called(self):
        with self._patches(self._fail_if_called, self._fail_if_called, self._fail_if_called, self._fail_if_called):
            result = run_contract_check(Config(enabled=False))
        self.assertFalse(result.ok)
        self.assertEqual(result.step, "disabled")
        self.assertEqual(result.code, "disabled")
        self.assertEqual(result.message, "engine is disabled")
        self._assert_clean(result)

    def _patches(self, capabilities, models, model, recognize):
        return mock.patch.multiple(
            "ai.htr_engine_contract_check",
            fetch_capabilities=capabilities,
            fetch_models=models,
            fetch_model=model,
            recognize_lines=recognize,
        )

    def _fail_if_called(self, *args, **kwargs):
        raise AssertionError("client was called")

    def _assert_clean(self, result):
        text = f"{result.ok} {result.step} {result.code} {result.message} {result!r}"
        for secret in (SECRET_URL, SECRET_TOKEN, RAW_BODY, "AAAA"):
            self.assertNotIn(secret, text)


class IsolationTests(unittest.TestCase):
    def test_checker_does_not_read_config_secrets(self):
        text = (AI_DIR / "htr_engine_contract_check.py").read_text()
        self.assertNotIn("logging", text)
        tree = ast.parse(text)
        modules = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                self.assertNotIn(node.attr, {"endpoint_url", "metadata"})
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                self.assertNotIn(node.func.id, {"open", "socket"})
            if isinstance(node, ast.Import):
                modules.extend(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.append(node.module.split(".")[0])
        self.assertEqual(set(modules), {"__future__", "dataclasses", "ai"})

    def test_stage1_modules_do_not_import_the_checker(self):
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
        for path in paths:
            text = path.read_text()
            self.assertNotIn("htr_engine_contract_check", text, path)
            self.assertNotIn("run_contract_check", text, path)


if __name__ == "__main__":
    unittest.main()
