"""Budget arithmetic and provider usage capture. No Django, no network.

Providers are mocked at requests.post. The ORM side (locks, holds,
concurrency) is in ai.django_integration_tests.test_ai_budget_orm.
"""
import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import requests
from PIL import Image

from ai.backends import (
    AnthropicBackend, AzureOpenAIBackend, GeminiBackend, LocalOpenAIBackend,
    MistralBackend, OpenAIBackend, ProviderHTTPError, backend_class,
    estimate_image_tokens,
)
from ai.billing import (
    call_bound_usd, describe_error, estimate_seg_review_usd,
    estimate_transcribe_usd, expected_call_usd, extra_needed, failure_state,
    fits, hold_remaining, image_token_bound, output_token_bound, price_cost,
    scope_usage, usd,
)


class FitsTests(unittest.TestCase):
    def test_unlimited(self):
        self.assertTrue(fits(10**6, 10**6, None))
        self.assertTrue(fits(10**6, 10**6, -1))

    def test_zero_cap_blocks_even_zero_delta(self):
        self.assertFalse(fits(0, 0, 0))
        self.assertFalse(fits(0, "0.000001", 0))

    def test_boundary_is_inclusive(self):
        self.assertTrue(fits("9.5", "0.5", 10))
        self.assertFalse(fits("9.5", "0.500001", 10))

    def test_decimal_has_no_float_drift(self):
        used = sum((usd("0.1") for _ in range(10)), Decimal(0))
        self.assertTrue(fits(used, 0, 1))
        self.assertFalse(fits(used, "0.000001", 1))


class HoldArithmeticTests(unittest.TestCase):
    def test_call_covered_by_hold_needs_no_extra(self):
        self.assertEqual(extra_needed("0.2", "1.0", "0.5"), Decimal("0"))

    def test_call_beyond_hold_needs_the_difference(self):
        self.assertEqual(extra_needed("0.4", "1.0", "0.8"), usd("0.2"))

    def test_exhausted_hold_needs_the_whole_bound(self):
        self.assertEqual(extra_needed("0.4", "1.0", "1.3"), usd("0.4"))
        self.assertEqual(hold_remaining("1.0", "1.3"), Decimal("0"))

    def test_each_call_counted_once(self):
        # Hold 1.00. Call A confirmed at 0.30, call B in flight held 0.50.
        # drawn = 0.80; remainder 0.20. Ledger 0.30 + held 0.50 + 0.20 = 1.00.
        total = scope_usage("0.30", "0.50", [("1.00", "0.80")])
        self.assertEqual(total, usd("1.00"))

    def test_settling_below_bound_returns_difference(self):
        # B confirms at 0.10: ledger 0.40, nothing held, drawn 0.40.
        self.assertEqual(scope_usage("0.40", "0", [("1.00", "0.40")]),
                         usd("1.00"))
        # Hold closed: only real spend remains.
        self.assertEqual(scope_usage("0.40", "0", []), usd("0.40"))


class FailureStateTests(unittest.TestCase):
    def test_not_sent_is_released(self):
        self.assertEqual(failure_state(RuntimeError("no key"), False), "released")

    def test_sent_then_error_is_unknown(self):
        self.assertEqual(failure_state(KeyError("candidates"), True), "unknown")
        self.assertEqual(failure_state(requests.ReadTimeout(), True), "unknown")
        self.assertEqual(
            failure_state(requests.ConnectionError("reset"), True), "unknown")

    def test_connect_timeout_is_released(self):
        self.assertEqual(
            failure_state(requests.ConnectTimeout(), True), "released")

    def test_auth_rejection_is_released(self):
        for status in (401, 403):
            self.assertEqual(
                failure_state(ProviderHTTPError("x", status), True), "released")

    def test_other_http_errors_are_unknown(self):
        for status in (400, 404, 408, 429, 500, 529):
            self.assertEqual(
                failure_state(ProviderHTTPError("x", status), True), "unknown")

    def test_requests_http_error_uses_response_status(self):
        resp = requests.Response()
        resp.status_code = 403
        self.assertEqual(
            failure_state(requests.HTTPError(response=resp), True), "released")
        resp.status_code = 503
        self.assertEqual(
            failure_state(requests.HTTPError(response=resp), True), "unknown")

    def test_error_text_has_no_body(self):
        text = describe_error(ProviderHTTPError("secret body sk-123", 500))
        self.assertEqual(text, "ProviderHTTPError HTTP 500")


class BoundTests(unittest.TestCase):
    def cfg(self, provider, **params):
        return SimpleNamespace(provider=provider, model_id="m",
                               max_edge_px=1024, params=params)

    def test_image_bound_covers_known_schemes(self):
        for w, h in ((1024, 1024), (1024, 200), (50, 900), (1, 1)):
            bound = image_token_bound(w, h)
            self.assertGreaterEqual(bound, estimate_image_tokens(w, h))
            self.assertGreaterEqual(bound, -(-w * h // 750))

    def test_output_bound_follows_sent_limits(self):
        self.assertEqual(output_token_bound(self.cfg("anthropic")), 8192)
        self.assertEqual(
            output_token_bound(self.cfg("anthropic", max_tokens=1000)), 1000)
        self.assertEqual(output_token_bound(self.cfg("openai")), 2048)
        self.assertEqual(output_token_bound(self.cfg("gemini")), 65536)
        self.assertEqual(output_token_bound(self.cfg("mistral")), 16384)
        self.assertEqual(output_token_bound(
            self.cfg("azure", max_output_tokens_bound=4000)), 4000)

    def test_bound_covers_a_maximal_response(self):
        cfg = self.cfg("anthropic")
        prompt = "p" * 2000
        bound = call_bound_usd(AnthropicBackend, cfg, (3000, 2000), prompt)
        worst = price_cost(AnthropicBackend, 1024 * 683 // 750 + 2000, 8192)
        self.assertGreaterEqual(bound, worst)

    def test_non_ascii_prompt_counts_bytes(self):
        cfg = self.cfg("openai")
        ascii_ = call_bound_usd(OpenAIBackend, cfg, (10, 10), "a" * 300)
        devanagari = call_bound_usd(OpenAIBackend, cfg, (10, 10), "क" * 300)
        self.assertGreater(devanagari, ascii_)

    def test_local_costs_nothing(self):
        cfg = self.cfg("local")
        self.assertEqual(call_bound_usd(
            LocalOpenAIBackend, cfg, (1024, 1024), "x"), Decimal("0"))

    def test_job_estimate_counts_crops(self):
        cfg = self.cfg("gemini")
        six = expected_call_usd(GeminiBackend, cfg, 6)
        two = expected_call_usd(GeminiBackend, cfg, 2)
        # 14 lines at 6 per crop: two full crops and one of 2 lines.
        self.assertEqual(
            estimate_transcribe_usd(GeminiBackend, cfg, [14], 6),
            usd(2 * six + two))
        self.assertEqual(
            estimate_transcribe_usd(GeminiBackend, cfg, [0, 0], 6), Decimal(0))
        self.assertGreater(
            estimate_seg_review_usd(GeminiBackend, cfg, 3), Decimal(0))

    def test_backend_class_refuses_unknown(self):
        with self.assertRaises(ValueError):
            backend_class("bogus")


class RecordingMeter:
    def __init__(self):
        self.events = []

    def sent(self):
        self.events.append("sent")

    def usage(self, tokens_in, tokens_out):
        self.events.append(("usage", tokens_in, tokens_out))


def _resp(body, ok=True, status=200):
    resp = MagicMock()
    resp.ok = ok
    resp.status_code = status
    resp.text = "body"
    resp.raise_for_status = lambda: None
    resp.json.return_value = body
    return resp


class UsageCaptureTests(unittest.TestCase):
    """Usage is reported before parsing, so a parse failure keeps the charge."""

    img = Image.new("RGB", (16, 16), "white")

    def run_backend(self, cls, provider, body, **cfg_extra):
        cfg = SimpleNamespace(provider=provider, model_id="m", max_edge_px=64,
                              params={}, endpoint="https://example.test",
                              **cfg_extra)
        backend = cls(cfg, api_key="k")
        meter = RecordingMeter()
        backend.meter = meter
        order = []

        def fake_post(*a, **kw):
            order.append(list(meter.events))
            return _resp(body)

        with patch("requests.post", fake_post):
            try:
                result = backend.transcribe_region(self.img, ["red"], "p")
            except Exception as exc:
                result = exc
        return meter.events, order, result

    def test_anthropic_usage_before_parse_failure(self):
        events, order, result = self.run_backend(
            AnthropicBackend, "anthropic",
            {"content": "not-a-list", "usage": {"input_tokens": 50,
                                                "output_tokens": 7}})
        self.assertEqual(order, [["sent"]])
        self.assertEqual(events, ["sent", ("usage", 50, 7)])
        self.assertIsInstance(result, Exception)

    def test_openai_usage_before_parse_failure(self):
        events, _, result = self.run_backend(
            OpenAIBackend, "openai",
            {"output": [{"content": "bad"}],
             "usage": {"input_tokens": 9, "output_tokens": 3}})
        self.assertEqual(events, ["sent", ("usage", 9, 3)])
        self.assertIsInstance(result, Exception)

    def test_gemini_counts_thinking_and_survives_missing_candidates(self):
        events, _, result = self.run_backend(
            GeminiBackend, "gemini",
            {"usageMetadata": {"promptTokenCount": 300,
                               "candidatesTokenCount": 20,
                               "thoughtsTokenCount": 400}})
        self.assertEqual(events, ["sent", ("usage", 300, 420)])
        self.assertIsInstance(result, KeyError)

    def test_chat_completion_backends_usage_before_parse_failure(self):
        body = {"choices": [], "usage": {"prompt_tokens": 11,
                                         "completion_tokens": 2}}
        for cls, provider in ((AzureOpenAIBackend, "azure"),
                              (MistralBackend, "mistral"),
                              (LocalOpenAIBackend, "local")):
            events, _, result = self.run_backend(cls, provider, body)
            self.assertEqual(events, ["sent", ("usage", 11, 2)], provider)
            self.assertIsInstance(result, IndexError)

    def test_missing_usage_is_not_reported_as_usage(self):
        events, _, result = self.run_backend(
            GeminiBackend, "gemini",
            {"candidates": [{"content": {"parts": [{"text": '{"red": "a"}'}]}}]})
        self.assertEqual(events, ["sent"])
        self.assertEqual(result.text_by_key, {"red": "a"})

    def test_missing_key_fails_before_send(self):
        cfg = SimpleNamespace(provider="anthropic", model_id="m",
                              max_edge_px=64, params={})
        backend = AnthropicBackend(cfg, api_key=None)
        backend.meter = meter = RecordingMeter()
        with patch("requests.post") as post:
            with self.assertRaises(RuntimeError):
                backend.transcribe_region(self.img, ["red"], "p")
        post.assert_not_called()
        self.assertEqual(meter.events, [])

    def test_http_error_carries_status(self):
        cfg = SimpleNamespace(provider="anthropic", model_id="m",
                              max_edge_px=64, params={})
        backend = AnthropicBackend(cfg, api_key="k")
        with patch("requests.post",
                   lambda *a, **kw: _resp({}, ok=False, status=401)):
            with self.assertRaises(ProviderHTTPError) as ctx:
                backend.transcribe_region(self.img, ["red"], "p")
        self.assertEqual(ctx.exception.status_code, 401)
        self.assertIsInstance(ctx.exception, RuntimeError)


if __name__ == "__main__":
    unittest.main()
