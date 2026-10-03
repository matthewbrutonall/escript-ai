"""Admin connection check. The client is mocked. No Django, no network."""
import ast
import unittest
from pathlib import Path

from ai.htr_engine_client import EngineClientError, probe_configs
from ai.htr_engine_contract import ContractError

AI_DIR = Path(__file__).resolve().parents[1]

SECRET_URL = "http://secret.example/token"
RAW_BODY = "raw-body-should-not-appear"


class Config:
    def __init__(self, **overrides):
        self.name = "Example engine"
        self.enabled = True
        self.endpoint_url = SECRET_URL
        self.metadata = {"token": "secret-token"}
        for key, value in overrides.items():
            setattr(self, key, value)


def _caps():
    return type("Caps", (), {"tier": "research"})()


def _models(count):
    return type("Listing", (), {"models": tuple(range(count))})()


class ProbeTests(unittest.TestCase):
    def test_success_reports_tier_and_model_count(self):
        def fetch_capabilities(config):
            return _caps()

        def fetch_models(config):
            return _models(2)

        level, text = probe_configs(
            [Config()],
            fetch_capabilities=fetch_capabilities,
            fetch_models=fetch_models,
        )[0]
        self.assertEqual(level, "success")
        self.assertEqual(text, "Example engine responded (research, 2 models)")
        self.assertNotIn(SECRET_URL, text)
        self.assertNotIn("secret-token", text)
        self.assertNotIn(RAW_BODY, text)

    def test_client_failure_uses_the_code_only(self):
        def fetch_capabilities(config):
            raise EngineClientError(
                "timeout",
                f"engine request timed out {SECRET_URL} {RAW_BODY}",
            )

        def fetch_models(config):
            raise AssertionError("models should not be fetched after capabilities fails")

        level, text = probe_configs(
            [Config()],
            fetch_capabilities=fetch_capabilities,
            fetch_models=fetch_models,
        )[0]
        self.assertEqual(level, "error")
        self.assertEqual(text, "Example engine was not checked (timeout)")
        self.assertNotIn(SECRET_URL, text)
        self.assertNotIn(RAW_BODY, text)

    def test_contract_failure_uses_the_code_only(self):
        def fetch_capabilities(config):
            return _caps()

        def fetch_models(config):
            raise ContractError("model_not_found", f"missing {SECRET_URL} {RAW_BODY}")

        level, text = probe_configs(
            [Config()],
            fetch_capabilities=fetch_capabilities,
            fetch_models=fetch_models,
        )[0]
        self.assertEqual(level, "error")
        self.assertEqual(text, "Example engine was not checked (model_not_found)")
        self.assertNotIn(SECRET_URL, text)
        self.assertNotIn(RAW_BODY, text)

    def test_mixed_selection_continues_after_the_middle_failure(self):
        calls = []
        north = Config(name="North")
        middle = Config(name="Middle")
        south = Config(name="South", enabled=False)

        def fetch_capabilities(config):
            if config.enabled is not True:
                raise AssertionError("disabled engine was called")
            calls.append(("capabilities", config.name))
            if config.name == "Middle":
                raise EngineClientError("timeout", f"timed out {SECRET_URL} {RAW_BODY}")
            return _caps()

        def fetch_models(config):
            if config.enabled is not True:
                raise AssertionError("disabled engine was called")
            calls.append(("models", config.name))
            return _models(1)

        results = probe_configs(
            [north, middle, south],
            fetch_capabilities=fetch_capabilities,
            fetch_models=fetch_models,
        )
        self.assertEqual(results, [
            ("success", "North responded (research, 1 models)"),
            ("error", "Middle was not checked (timeout)"),
            ("warning", "South is disabled and was not called"),
        ])
        self.assertEqual(calls, [
            ("capabilities", "North"),
            ("models", "North"),
            ("capabilities", "Middle"),
        ])
        for _level, text in results:
            self.assertNotIn(SECRET_URL, text)
            self.assertNotIn(RAW_BODY, text)
            self.assertNotIn("secret-token", text)

    def test_disabled_engine_is_not_called(self):
        def fetch_capabilities(config):
            raise AssertionError("disabled engine was called")

        def fetch_models(config):
            raise AssertionError("disabled engine was called")

        level, text = probe_configs(
            [Config(enabled=False)],
            fetch_capabilities=fetch_capabilities,
            fetch_models=fetch_models,
        )[0]
        self.assertEqual(level, "warning")
        self.assertEqual(text, "Example engine is disabled and was not called")
        self.assertNotIn(SECRET_URL, text)

    def test_unexpected_error_hides_the_exception_text(self):
        def fetch_capabilities(config):
            raise RuntimeError(f"socket to {SECRET_URL} said {RAW_BODY}")

        level, text = probe_configs(
            [Config()],
            fetch_capabilities=fetch_capabilities,
            fetch_models=lambda config: _models(0),
        )[0]
        self.assertEqual(level, "error")
        self.assertEqual(text, "Example engine was not checked")
        self.assertNotIn(SECRET_URL, text)
        self.assertNotIn(RAW_BODY, text)


class AdminWiringTests(unittest.TestCase):
    def test_action_only_forwards_probe_messages(self):
        tree = ast.parse((AI_DIR / "admin.py").read_text())
        action = None
        for node in tree.body:
            if isinstance(node, ast.ClassDef) and node.name == "ExternalHTREngineConfigAdmin":
                for child in node.body:
                    if isinstance(child, ast.FunctionDef) and child.name == "test_connection":
                        action = child
        self.assertIsNotNone(action)
        dumped = ast.dump(action)
        self.assertIn("probe_configs", dumped)
        self.assertIn("message_user", dumped)
        for banned in ("endpoint_url", "metadata", "recognize"):
            self.assertNotIn(banned, dumped)

    def test_jobs_do_not_call_the_probe(self):
        for name in ("tasks.py", "views.py", "serializers.py", "pipeline.py", "dispatch.py"):
            text = (AI_DIR / name).read_text()
            self.assertNotIn("probe_configs", text, name)
            self.assertNotIn("test_connection", text, name)


if __name__ == "__main__":
    unittest.main()
