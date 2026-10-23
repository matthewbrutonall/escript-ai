"""Backend helpers — no Django, no network."""
import unittest
from pathlib import Path
from types import SimpleNamespace

from ai.backends import MockBackend, get_backend, parse_json_by_key


class ParseJsonTests(unittest.TestCase):
    def test_keeps_known_keys(self):
        raw = 'Here you go:\n{"red": "hello", "blue": "world"}\n'
        text, unknown = parse_json_by_key(raw, ["red", "blue"])
        self.assertEqual(text, {"red": "hello", "blue": "world"})
        self.assertEqual(unknown, set())

    def test_unknown_keys_are_reported_not_silently_dropped_from_the_set(self):
        raw = '{"red": "a", "red-ish": "b"}'
        text, unknown = parse_json_by_key(raw, ["red"])
        self.assertEqual(text, {"red": "a"})
        self.assertEqual(unknown, {"red-ish"})

    def test_garbage_returns_empty(self):
        text, unknown = parse_json_by_key("not json", ["red"])
        self.assertEqual(text, {})
        self.assertEqual(unknown, set())


class MockBackendTests(unittest.TestCase):
    def test_returns_gt_for_requested_keys_only(self):
        b = MockBackend(ground_truth_by_key={"red": "one", "blue": "two"})
        r = b.transcribe_region(None, ["red", "green"], "")
        self.assertEqual(r.text_by_key, {"red": "one"})
        self.assertEqual(r.unknown_keys, set())


class GetBackendTests(unittest.TestCase):
    def test_unknown_provider_raises_instead_of_gemini_fallback(self):
        cfg = SimpleNamespace(provider="bogus", model_id="x")
        with self.assertRaises(ValueError) as ctx:
            get_backend(cfg)
        self.assertIn("bogus", str(ctx.exception))
        self.assertIn("Gemini", str(ctx.exception))

    def test_anthropic_maps_to_anthropic_backend(self):
        from ai.backends import AnthropicBackend
        cfg = SimpleNamespace(provider="anthropic", model_id="claude-sonnet-5")
        self.assertIsInstance(get_backend(cfg), AnthropicBackend)

    def test_anthropic_payload_omits_temperature(self):
        from ai.backends import AnthropicBackend
        from PIL import Image
        from unittest.mock import patch, MagicMock

        cfg = SimpleNamespace(
            provider="anthropic", model_id="claude-sonnet-5",
            max_edge_px=256, params={"temperature": 0, "max_tokens": 2048},
        )
        captured = {}

        def fake_post(url, headers=None, json=None, timeout=None, **kwargs):
            captured["json"] = json
            captured["allow_redirects"] = kwargs.get("allow_redirects")
            resp = MagicMock()
            resp.ok = True
            resp.raise_for_status = lambda: None
            resp.json.return_value = {
                "content": [{"type": "text", "text": '{"red": "x"}'}],
                "usage": {"input_tokens": 10, "output_tokens": 2},
            }
            return resp

        img = Image.new("RGB", (32, 32), "white")
        with patch("requests.post", fake_post):
            AnthropicBackend(cfg, api_key="sk-test").transcribe_region(
                img, ["red"], "transcribe")
        self.assertNotIn("temperature", captured["json"])
        self.assertNotIn("top_p", captured["json"])
        self.assertEqual(captured["json"]["max_tokens"], 2048)
        self.assertEqual(captured["json"]["model"], "claude-sonnet-5")
        self.assertIs(captured["allow_redirects"], False)

    def test_openai_maps_to_openai_backend(self):
        from ai.backends import OpenAIBackend
        cfg = SimpleNamespace(provider="openai", model_id="gpt-5.6-terra")
        self.assertIsInstance(get_backend(cfg), OpenAIBackend)

    def test_openai_payload_uses_responses_image_input(self):
        from ai.backends import OpenAIBackend
        from PIL import Image
        from unittest.mock import patch, MagicMock

        cfg = SimpleNamespace(
            provider="openai", model_id="gpt-5.6-terra",
            max_edge_px=256,
            params={
                "detail": "high",
                "max_output_tokens": 1024,
                "reasoning": {"effort": "low"},
            },
        )
        captured = {}

        def fake_post(url, headers=None, json=None, timeout=None, **kwargs):
            captured["url"] = url
            captured["headers"] = headers
            captured["json"] = json
            captured["allow_redirects"] = kwargs.get("allow_redirects")
            resp = MagicMock()
            resp.ok = True
            resp.raise_for_status = lambda: None
            resp.json.return_value = {
                "output_text": '{"red": "x"}',
                "usage": {"input_tokens": 20, "output_tokens": 4},
            }
            return resp

        img = Image.new("RGB", (32, 32), "white")
        with patch("requests.post", fake_post):
            result = OpenAIBackend(cfg, api_key="sk-test").transcribe_region(
                img, ["red"], "transcribe")
        self.assertEqual(captured["url"], "https://api.openai.com/v1/responses")
        self.assertEqual(captured["json"]["model"], "gpt-5.6-terra")
        self.assertEqual(captured["json"]["max_output_tokens"], 1024)
        self.assertEqual(captured["json"]["reasoning"], {"effort": "low"})
        content = captured["json"]["input"][0]["content"]
        self.assertEqual(content[0]["type"], "input_text")
        self.assertEqual(content[1]["type"], "input_image")
        self.assertTrue(content[1]["image_url"].startswith("data:image/png;base64,"))
        self.assertEqual(result.text_by_key, {"red": "x"})
        self.assertIs(captured["allow_redirects"], False)

    def test_local_maps_to_local_backend(self):
        from ai.backends import LocalOpenAIBackend
        cfg = SimpleNamespace(provider="local", model_id="llava")
        self.assertIsInstance(get_backend(cfg), LocalOpenAIBackend)

    def test_azure_and_mistral_map(self):
        from ai.backends import AzureOpenAIBackend, MistralBackend, azure_chat_url
        self.assertIsInstance(
            get_backend(SimpleNamespace(provider="azure", model_id="gpt-4o")),
            AzureOpenAIBackend)
        self.assertIsInstance(
            get_backend(SimpleNamespace(provider="mistral", model_id="pixtral-large-latest")),
            MistralBackend)
        url = azure_chat_url("https://ex.openai.azure.com", "my-dep", "2024-08-01-preview")
        self.assertIn("/openai/deployments/my-dep/chat/completions", url)
        self.assertIn("api-version=2024-08-01-preview", url)

    def test_azure_requires_endpoint(self):
        from ai.backends import AzureOpenAIBackend
        from PIL import Image
        cfg = SimpleNamespace(provider="azure", model_id="gpt-4o",
                              endpoint=None, max_edge_px=32, params={})
        img = Image.new("RGB", (8, 8), "white")
        with self.assertRaises(RuntimeError) as ctx:
            AzureOpenAIBackend(cfg, api_key="k").transcribe_region(img, ["red"], "p")
        self.assertIn("endpoint", str(ctx.exception))


class LocalRequestTests(unittest.TestCase):
    def test_provider_posts_disable_redirects(self):
        text = (Path(__file__).resolve().parents[1] / "backends.py").read_text()
        self.assertEqual(text.count("requests.post"), 1)
        self.assertIn('kwargs["allow_redirects"] = False', text)

    def test_local_cost_stays_zero(self):
        from ai.backends import LocalOpenAIBackend
        backend = LocalOpenAIBackend(
            SimpleNamespace(provider="local", model_id="llava"))
        self.assertEqual(backend.in_usd_per_1m, 0.0)
        self.assertEqual(backend.out_usd_per_1m, 0.0)
        self.assertEqual(backend.cost(1_000_000, 1_000_000), 0.0)

    def test_missing_endpoint_posts_to_the_loopback_default(self):
        from ai.backends import LOCAL_DEFAULT_ENDPOINT, LocalOpenAIBackend
        from PIL import Image
        from unittest.mock import patch, MagicMock

        captured = {}

        def fake_post(url, **kwargs):
            captured["url"] = url
            captured["allow_redirects"] = kwargs.get("allow_redirects")
            resp = MagicMock()
            resp.status_code = 200
            resp.ok = True
            resp.json.return_value = {
                "choices": [{"message": {"content": '{"red": "x"}'}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            }
            return resp

        cfg = SimpleNamespace(
            provider="local", model_id="llava", endpoint=None, max_edge_px=32)
        img = Image.new("RGB", (8, 8), "white")
        with patch("requests.post", fake_post), \
                patch("requests.get", side_effect=AssertionError("get")):
            result = LocalOpenAIBackend(cfg).transcribe_region(img, ["red"], "p")
        self.assertEqual(
            captured["url"], LOCAL_DEFAULT_ENDPOINT.rstrip("/") + "/chat/completions")
        self.assertEqual(captured["url"], "http://localhost:11434/v1/chat/completions")
        self.assertIs(captured["allow_redirects"], False)
        self.assertEqual(result.text_by_key, {"red": "x"})

    def test_redirect_is_a_fixed_failure_and_is_not_followed(self):
        from ai.backends import LocalOpenAIBackend
        from PIL import Image
        from unittest.mock import patch

        class RedirectResponse:
            status_code = 302
            ok = True
            location = "https://evil.example/collect"
            body = "collected https://evil.example/collect SECRET-BODY"

            @property
            def text(self):
                raise AssertionError("redirect body was read")

            @property
            def headers(self):
                raise AssertionError("redirect headers were read")

            def json(self):
                raise AssertionError("redirect body was parsed")

            def raise_for_status(self):
                raise AssertionError("redirect was treated as a normal error")

        calls = []

        def fake_post(url, **kwargs):
            calls.append((url, kwargs))
            return RedirectResponse()

        cfg = SimpleNamespace(
            provider="local", model_id="llava",
            endpoint="http://127.0.0.1:11434/v1", max_edge_px=32)
        img = Image.new("RGB", (8, 8), "white")
        with patch("requests.post", fake_post), \
                patch("requests.get", side_effect=AssertionError("get")):
            with self.assertRaises(RuntimeError) as ctx:
                LocalOpenAIBackend(cfg).transcribe_region(img, ["red"], "p")
        self.assertEqual(str(ctx.exception), "provider redirect refused")
        self.assertNotIn("evil.example", str(ctx.exception))
        self.assertNotIn("SECRET-BODY", str(ctx.exception))
        self.assertNotIn("Location", str(ctx.exception))
        self.assertEqual(len(calls), 1)
        url, kwargs = calls[0]
        self.assertEqual(url, "http://127.0.0.1:11434/v1/chat/completions")
        self.assertIs(kwargs["allow_redirects"], False)
        self.assertNotIn("https://evil.example/collect", str(kwargs))

    def test_each_redirect_status_is_refused_without_reading_the_response(self):
        from ai.backends import _provider_post
        from unittest.mock import patch

        class RedirectResponse:
            def __init__(self, status_code):
                self.status_code = status_code

            @property
            def text(self):
                raise AssertionError("body")

            @property
            def headers(self):
                raise AssertionError("headers")

        def make_post(status):
            def fake_post(url, **kwargs):
                self.assertIs(kwargs["allow_redirects"], False)
                return RedirectResponse(status)
            return fake_post

        for status in (301, 302, 303, 307, 308):
            with patch("requests.post", make_post(status)), \
                    patch("requests.get", side_effect=AssertionError("get")):
                with self.assertRaises(RuntimeError) as ctx:
                    _provider_post("http://127.0.0.1/v1/chat/completions", json={})
            self.assertEqual(str(ctx.exception), "provider redirect refused", status)


if __name__ == "__main__":
    unittest.main()
