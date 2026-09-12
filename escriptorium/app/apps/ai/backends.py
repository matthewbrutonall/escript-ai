"""
Provider abstraction (ARCHITECTURE.md §7). One interface, many providers.

  transcribe_region(image, keys, prompt) -> AIResult   (§5.A keyed region)

Backends: Gemini (REST), Anthropic Claude, OpenAI Responses API, Local
(OpenAI-compatible: Ollama/vLLM — the private, no-spend path), and Mock
(tests). Cost discipline (§11): each call reports token usage; a hosted call
must be gated by a budget check upstream (tasks.ai_transcribe), never spent
silently.
"""
from __future__ import annotations

import base64
import io
import json
import math
import re
from dataclasses import dataclass, field

from PIL import Image


@dataclass
class AIResult:
    text_by_key: dict            # {colour key: text}
    unknown_keys: set = field(default_factory=set)   # keys the model invented
    tokens_in: int = 0
    tokens_out: int = 0
    raw: str = ""


def _downscale_png_b64(image: Image.Image, max_edge: int) -> tuple[str, int, int]:
    im = image.convert("RGB")
    im.thumbnail((max_edge, max_edge))
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode(), im.width, im.height


def parse_json_by_key(raw: str, keys) -> tuple[dict, set]:
    known = set(keys)
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        return {}, set()
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return {}, set()
    return ({k: str(v) for k, v in data.items() if k in known},
            {k for k in data if k not in known})


def estimate_image_tokens(w: int, h: int) -> int:
    tiles = max(1, math.ceil(w / 768) * math.ceil(h / 768))
    return max(258, tiles * 258)


def _extract_openai_text(data: dict) -> str:
    if data.get("output_text"):
        return data["output_text"]
    chunks = []
    for item in data.get("output", []):
        for content in item.get("content", []):
            if content.get("type") in ("output_text", "text"):
                chunks.append(content.get("text", ""))
    return "".join(chunks)


class BaseBackend:
    #: USD per 1M tokens; override per provider/model. 0 for local.
    in_usd_per_1m = 0.0
    out_usd_per_1m = 0.0

    def __init__(self, config, api_key: str | None = None):
        self.config = config
        self.api_key = api_key

    def transcribe_region(self, image: Image.Image, keys, prompt) -> AIResult:
        raise NotImplementedError

    def transcribe_lines(self, crops, prompt) -> list:
        """Batch hook for §5.B per-line mode. v1 loops; providers that accept
        multiple images can override without changing the caller."""
        return [self.transcribe_region(image, keys, prompt) for image, keys in crops]

    def cost(self, tokens_in, tokens_out) -> float:
        return (tokens_in * self.in_usd_per_1m + tokens_out * self.out_usd_per_1m) / 1e6


class GeminiBackend(BaseBackend):
    in_usd_per_1m = 0.30
    out_usd_per_1m = 2.50

    def transcribe_region(self, image, keys, prompt) -> AIResult:
        import requests
        if not self.api_key:
            raise RuntimeError("Gemini backend requires an API key (key_ref unresolved).")
        b64, w, h = _downscale_png_b64(image, self.config.max_edge_px)
        url = ("https://generativelanguage.googleapis.com/v1beta/models/"
               f"{self.config.model_id}:generateContent")
        payload = {"contents": [{"parts": [
            {"text": prompt},
            {"inline_data": {"mime_type": "image/png", "data": b64}}]}],
            "generationConfig": {"temperature": self.config.params.get("temperature", 0)}}
        r = requests.post(url, headers={"x-goog-api-key": self.api_key},
                          json=payload, timeout=600)
        r.raise_for_status()
        data = r.json()
        raw = data["candidates"][0]["content"]["parts"][0]["text"]
        u = data.get("usageMetadata", {})
        text_by_key, unknown = parse_json_by_key(raw, keys)
        return AIResult(text_by_key, unknown,
                        u.get("promptTokenCount", estimate_image_tokens(w, h)),
                        u.get("candidatesTokenCount", 0), raw)


class AnthropicBackend(BaseBackend):
    # Claude Sonnet 5 standard pricing. Keep per-config overrides
    # possible later; the ledger records actual token counts either way.
    in_usd_per_1m = 3.00
    out_usd_per_1m = 15.00

    def transcribe_region(self, image, keys, prompt) -> AIResult:
        import requests
        if not self.api_key:
            raise RuntimeError("Anthropic backend requires an API key (key_ref unresolved).")
        b64, w, h = _downscale_png_b64(image, self.config.max_edge_px)
        # Sonnet 5 / Opus 4.7+: non-default temperature/top_p/top_k → HTTP 400.
        # Adaptive thinking is on by default and counts toward max_tokens.
        params = self.config.params or {}
        payload = {
            "model": self.config.model_id,
            "max_tokens": params.get("max_tokens", 8192),
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image",
                     "source": {
                         "type": "base64",
                         "media_type": "image/png",
                         "data": b64,
                     }},
                ],
            }],
        }
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json=payload,
            timeout=180,
        )
        if not r.ok:
            raise RuntimeError(
                f"Anthropic {r.status_code}: {r.text[:800]}")
        r.raise_for_status()
        data = r.json()
        raw = "".join(
            block.get("text", "")
            for block in data.get("content", [])
            if block.get("type") == "text"
        )
        u = data.get("usage", {})
        text_by_key, unknown = parse_json_by_key(raw, keys)
        return AIResult(text_by_key, unknown,
                        u.get("input_tokens", estimate_image_tokens(w, h)),
                        u.get("output_tokens", 0), raw)


class OpenAIBackend(BaseBackend):
    # GPT-5.6 Terra standard pricing. Keep per-config overrides possible later;
    # the ledger records actual token counts either way.
    in_usd_per_1m = 2.00
    out_usd_per_1m = 12.00

    def transcribe_region(self, image, keys, prompt) -> AIResult:
        import requests
        if not self.api_key:
            raise RuntimeError("OpenAI backend requires an API key (key_ref unresolved).")
        b64, w, h = _downscale_png_b64(image, self.config.max_edge_px)
        params = self.config.params or {}
        payload = {
            "model": self.config.model_id,
            "input": [{
                "role": "user",
                "content": [
                    {"type": "input_text", "text": prompt},
                    {"type": "input_image",
                     "image_url": f"data:image/png;base64,{b64}",
                     "detail": params.get("detail", "high")},
                ],
            }],
            "max_output_tokens": params.get("max_output_tokens", 2048),
        }
        if "reasoning" in params:
            payload["reasoning"] = params["reasoning"]
        r = requests.post(
            "https://api.openai.com/v1/responses",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=180,
        )
        if not r.ok:
            raise RuntimeError(f"OpenAI {r.status_code}: {r.text[:800]}")
        r.raise_for_status()
        data = r.json()
        raw = _extract_openai_text(data)
        u = data.get("usage", {})
        text_by_key, unknown = parse_json_by_key(raw, keys)
        return AIResult(text_by_key, unknown,
                        u.get("input_tokens", estimate_image_tokens(w, h)),
                        u.get("output_tokens", 0), raw)


class LocalOpenAIBackend(BaseBackend):
    """OpenAI-compatible chat endpoint (Ollama / vLLM). No API spend — the
    private path. Cost stays 0; only GPU-minutes (metered elsewhere)."""

    def transcribe_region(self, image, keys, prompt) -> AIResult:
        import requests
        b64, w, h = _downscale_png_b64(image, self.config.max_edge_px)
        url = (self.config.endpoint or "http://localhost:11434/v1").rstrip("/") + "/chat/completions"
        payload = {"model": self.config.model_id, "temperature": 0, "messages": [
            {"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url",
                 "image_url": {"url": f"data:image/png;base64,{b64}"}}]}]}
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        r = requests.post(url, json=payload, headers=headers, timeout=600)
        r.raise_for_status()
        data = r.json()
        raw = data["choices"][0]["message"]["content"]
        u = data.get("usage", {})
        text_by_key, unknown = parse_json_by_key(raw, keys)
        return AIResult(text_by_key, unknown,
                        u.get("prompt_tokens", 0), u.get("completion_tokens", 0), raw)


class MockBackend(BaseBackend):
    """Returns handed-in ground truth; for tests. Never opens the image."""
    def __init__(self, config=None, ground_truth_by_key=None, **kw):
        super().__init__(config)
        self._gt = ground_truth_by_key or {}

    def transcribe_region(self, image, keys, prompt) -> AIResult:
        return AIResult({k: self._gt.get(k, "") for k in keys if k in self._gt}, set())


def get_backend(config, api_key=None) -> BaseBackend:
    backends = {
        'gemini': GeminiBackend,
        'anthropic': AnthropicBackend,
        'openai': OpenAIBackend,
        'local': LocalOpenAIBackend,
    }
    cls = backends.get(config.provider)
    if cls is None:
        raise ValueError(
            f"No backend implemented for provider {config.provider!r} "
            f"(refusing to silently fall back to Gemini).")
    return cls(config, api_key=api_key)
