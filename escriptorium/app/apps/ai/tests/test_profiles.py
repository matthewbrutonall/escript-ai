"""Billing capability profiles. No Django, no network.

Expected numbers come from the providers' published rules and worked
examples (sources in ai/profiles.py and docs/budget-reservations.md), and
from boundary cases worked by hand from those rules. They are not computed
with the implementation's own helpers.
"""
import base64
import io
import math
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from PIL import Image

from ai.backends import (
    AnthropicBackend, AzureOpenAIBackend, OpenAIBackend, outgoing_image,
)
from ai.billing import reserved_input_tokens, sent_output_limit
from ai.profiles import (
    PROFILES, UnsupportedModel, anthropic_image_tokens, openai_tile_tokens,
    resolve_profile,
)


def cfg(provider, model_id="m", price_model_id="", **params):
    return SimpleNamespace(provider=provider, model_id=model_id,
                           price_model_id=price_model_id, max_edge_px=1024,
                           params=params, endpoint="https://example.test")


class OpenAITileTests(unittest.TestCase):
    """Tile table: gpt-4o and gpt-4.1 85 + 170/tile; gpt-4o-mini 2833 + 5667."""

    def test_published_worked_examples(self):
        # 1024 x 1024, high: shortest side 1024 > 768 -> 768 x 768 -> 4 tiles.
        self.assertEqual(openai_tile_tokens(1024, 1024, "high", 85, 170), 765)
        # 2048 x 4096, high: fit 2048 square -> 1024 x 2048 -> 768 x 1536
        # -> 2 x 3 = 6 tiles -> 85 + 6 * 170.
        self.assertEqual(openai_tile_tokens(2048, 4096, "high", 85, 170), 1105)
        # gpt-4o-mini, 1024 x 1024: 2833 + 4 * 5667.
        self.assertEqual(openai_tile_tokens(1024, 1024, "high", 2833, 5667), 25501)

    def test_low_detail_is_the_base_whatever_the_size(self):
        for size in ((4000, 3000), (16, 16), (1024, 64)):
            self.assertEqual(openai_tile_tokens(*size, "low", 85, 170), 85)
            self.assertEqual(openai_tile_tokens(*size, "low", 2833, 5667), 2833)

    def test_narrow_line_crops(self):
        # Shortest side below 768: no scaling; 1024 x 64 needs 2 x 1 tiles.
        self.assertEqual(openai_tile_tokens(1024, 64, "high", 2833, 5667), 14167)
        self.assertEqual(openai_tile_tokens(512, 32, "high", 2833, 5667), 8500)
        self.assertEqual(openai_tile_tokens(1024, 64, "high", 85, 170), 425)

    def test_tile_boundaries(self):
        self.assertEqual(openai_tile_tokens(512, 512, "high", 85, 170), 255)
        self.assertEqual(openai_tile_tokens(513, 512, "high", 85, 170), 425)
        self.assertEqual(openai_tile_tokens(1, 1, "high", 85, 170), 255)

    def test_auto_detail_uses_the_tile_rule(self):
        self.assertEqual(openai_tile_tokens(1024, 1024, "auto", 85, 170), 765)


class AnthropicTests(unittest.TestCase):
    """ceil(w/28) x ceil(h/28), capped at 4,784 (1,568 on the standard tier)."""

    def test_patch_boundaries(self):
        self.assertEqual(anthropic_image_tokens(28, 28), 1)
        self.assertEqual(anthropic_image_tokens(29, 28), 2)
        self.assertEqual(anthropic_image_tokens(56, 57), 6)

    def test_narrow_and_page_sizes(self):
        self.assertEqual(anthropic_image_tokens(1024, 64), 37 * 3)
        self.assertEqual(anthropic_image_tokens(1092, 1092), 39 * 39)

    def test_never_above_the_high_resolution_cap(self):
        self.assertEqual(anthropic_image_tokens(5000, 5000), 4784)


class RefusalTests(unittest.TestCase):
    def assertRefused(self, config, text):
        with self.assertRaises(UnsupportedModel) as ctx:
            resolve_profile(config)
        self.assertIn(text, str(ctx.exception))

    def test_whole_providers_without_a_ceiling(self):
        self.assertRefused(cfg("gemini", "gemini-2.5-flash"), "Gemini")
        self.assertRefused(cfg("gemini", "gemini-3.8-flash"), "Gemini")
        self.assertRefused(cfg("mistral", "pixtral-large-latest"), "Mistral")

    def test_unlisted_models(self):
        for config in (cfg("openai", "gpt-5.6-terra"),
                       cfg("openai", "gpt-4o-2024-08-06"),
                       cfg("anthropic", "claude-sonnet-5"),
                       cfg("anthropic", "claude-3-5-sonnet-latest")):
            self.assertRefused(config, "no documented billing profile")

    def test_azure_needs_a_supported_priced_model(self):
        self.assertRefused(cfg("azure", "my-deployment"), "price_model_id")
        self.assertRefused(cfg("azure", "my-deployment", "gpt-5.6-terra"),
                           "no documented billing profile")
        profile = resolve_profile(cfg("azure", "my-deployment", " gpt-4o "))
        self.assertIs(profile, PROFILES[("azure", "gpt-4o")])

    def test_azure_api_versions(self):
        ok = cfg("azure", "dep", "gpt-4o")
        resolve_profile(ok)  # default 2024-10-21
        resolve_profile(cfg("azure", "dep", "gpt-4o", api_version="2024-10-21"))
        for version in ("2024-08-01-preview", "2024-06-01", "2024-12-01-preview",
                        "2025-04-01-preview", "v1", ""):
            self.assertRefused(cfg("azure", "dep", "gpt-4o", api_version=version),
                               "api_version")

    def test_unknown_detail_is_refused(self):
        self.assertRefused(cfg("openai", "gpt-4o", detail="original"), "detail")

    def test_supported_set(self):
        self.assertEqual(sorted(PROFILES), sorted(
            [(p, m) for p in ("openai", "azure")
             for m in ("gpt-4o", "gpt-4.1", "gpt-4o-mini")]
            + [("anthropic", m) for m in
               ("claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5")]))


def _resp():
    resp = MagicMock()
    resp.ok = True
    resp.status_code = 200
    resp.raise_for_status = lambda: None
    resp.json.return_value = {}
    return resp


def _independent_openai_tokens(w, h, detail, base, per_tile):
    """The published procedure, restated here rather than imported."""
    if detail == "low":
        return base
    if w > 2048 or h > 2048:
        f = 2048 / max(w, h)
        w, h = w * f, h * f
    if min(w, h) > 768:
        f = 768 / min(w, h)
        w, h = w * f, h * f
    return base + per_tile * math.ceil(w / 512) * math.ceil(h / 512)


class OutgoingPayloadTests(unittest.TestCase):
    """The reservation covers the image the provider actually receives."""

    def send(self, cls, config, source):
        sent = {}

        def fake_post(url, headers=None, json=None, timeout=None):
            sent["url"], sent["json"] = url, json
            return _resp()

        with patch("requests.post", fake_post):
            try:
                cls(config, api_key="k").transcribe_region(source, ["red"], "prompt")
            except Exception:
                pass  # empty response body; only the request is examined
        return sent

    @staticmethod
    def decoded(data_or_url):
        b64 = data_or_url.split("base64,", 1)[-1]
        return Image.open(io.BytesIO(base64.b64decode(b64))).size

    def test_openai_mini_narrow_crop(self):
        config = cfg("openai", "gpt-4o-mini")
        profile = resolve_profile(config)
        source = Image.new("RGB", (3000, 180), "white")  # downscaled to 1024 x 61
        body = self.send(OpenAIBackend, config, source)["json"]
        content = body["input"][0]["content"][1]
        size = self.decoded(content["image_url"])
        self.assertEqual(size, outgoing_image(source, 1024).size)
        self.assertEqual(content["detail"], "high")
        billed = _independent_openai_tokens(*size, "high", 2833, 5667)
        self.assertEqual(billed, 14167)
        reserved = reserved_input_tokens(profile, config, size, "prompt")
        self.assertGreaterEqual(reserved, billed + len("prompt"))
        self.assertEqual(body["max_output_tokens"], sent_output_limit(config))

    def test_openai_low_detail_is_what_is_sent_and_reserved(self):
        config = cfg("openai", "gpt-4o", detail="low")
        profile = resolve_profile(config)
        source = Image.new("RGB", (900, 900), "white")
        body = self.send(OpenAIBackend, config, source)["json"]
        self.assertEqual(body["input"][0]["content"][1]["detail"], "low")
        size = outgoing_image(source, 1024).size
        self.assertGreaterEqual(reserved_input_tokens(profile, config, size, ""), 85)

    def test_azure_sends_detail_limit_and_supported_version(self):
        config = cfg("azure", "my-deployment", "gpt-4o")
        profile = resolve_profile(config)
        source = Image.new("RGB", (2000, 1500), "white")
        sent = self.send(AzureOpenAIBackend, config, source)
        self.assertIn("api-version=2024-10-21", sent["url"])
        image = sent["json"]["messages"][0]["content"][1]["image_url"]
        self.assertEqual(image["detail"], "high")
        size = self.decoded(image["url"])
        self.assertEqual(size, (1024, 768))
        billed = _independent_openai_tokens(*size, "high", 85, 170)
        self.assertEqual(billed, 765)
        self.assertGreaterEqual(
            reserved_input_tokens(profile, config, size, "prompt"), billed)
        self.assertEqual(sent["json"]["max_completion_tokens"],
                         sent_output_limit(config))
        self.assertNotIn("max_tokens", sent["json"])

    def test_anthropic_line_crop(self):
        config = cfg("anthropic", "claude-sonnet-5-5")
        profile = resolve_profile(config)
        source = Image.new("RGB", (1500, 90), "white")  # -> 1024 x 61
        body = self.send(AnthropicBackend, config, source)["json"]
        data = body["messages"][0]["content"][1]["source"]["data"]
        size = self.decoded(data)
        self.assertEqual(size, outgoing_image(source, 1024).size)
        billed = math.ceil(size[0] / 28) * math.ceil(size[1] / 28)
        self.assertEqual(billed, 37 * 3)
        self.assertGreaterEqual(
            reserved_input_tokens(profile, config, size, "prompt"), billed)
        self.assertEqual(body["max_tokens"], sent_output_limit(config))


if __name__ == "__main__":
    unittest.main()
