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

from .billing import gemini_thinking_budget, sent_output_limit


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


#: Default Azure api-version. It must accept max_completion_tokens, which
#: replaces the deprecated max_tokens and also bounds reasoning tokens.
AZURE_API_VERSION = "2024-12-01-preview"


def anthropic_input_equivalent(usage):
    """Input tokens at the base input rate, never below the real charge.

    input_tokens excludes cache reads and writes. Writes cost 1.25x (5 min)
    or 2x (1 h) the input rate, so they count 2x; reads cost 0.1x and count
    1x. Both are conservative overestimates. None when nothing was reported.
    """
    fields = ("input_tokens", "cache_creation_input_tokens",
              "cache_read_input_tokens")
    if not any(usage.get(f) is not None for f in fields):
        return None
    return (int(usage.get("input_tokens") or 0)
            + 2 * int(usage.get("cache_creation_input_tokens") or 0)
            + int(usage.get("cache_read_input_tokens") or 0))


def azure_chat_url(endpoint, deployment, api_version=AZURE_API_VERSION) -> str:
    base = (endpoint or "").rstrip("/")
    return (f"{base}/openai/deployments/{deployment}/chat/completions"
            f"?api-version={api_version}")


class ProviderHTTPError(RuntimeError):
    """A non-2xx provider answer. `status_code` decides billing outcome."""

    def __init__(self, message, status_code=None):
        super().__init__(message)
        self.status_code = status_code


#: Endpoint a local backend uses when its config sets none.
LOCAL_DEFAULT_ENDPOINT = "http://localhost:11434/v1"

#: Read timeout per provider request. The job lease must outlast it.
PROVIDER_TIMEOUT_SECONDS = 600


def _chat_completions_vision(url, headers, model, prompt, b64,
                             timeout=PROVIDER_TIMEOUT_SECONDS, backend=None,
                             limit_param=None, limit=None):
    import requests
    payload = {
        "model": model,
        "temperature": 0,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url",
                 "image_url": {"url": f"data:image/png;base64,{b64}"}},
            ],
        }],
    }
    if limit_param:
        payload[limit_param] = limit
    if backend is not None:
        backend._mark_sent()
    r = requests.post(url, json=payload, headers=headers, timeout=timeout)
    if not r.ok:
        raise ProviderHTTPError(
            f"chat completions {r.status_code}: {r.text[:800]}", r.status_code)
    data = r.json()
    u = data.get("usage") or {}
    if backend is not None:
        backend._report_usage(u.get("prompt_tokens"), u.get("completion_tokens"))
    raw = data["choices"][0]["message"]["content"]
    return raw, u


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
    #: Set per call by billing_store; receives sent() and usage().
    meter = None
    #: billing.Price snapshot set by the meter. Prices are configured per
    #: model (ai.AIModelPrice); backends carry no rates of their own.
    price = None

    def __init__(self, config, api_key: str | None = None):
        self.config = config
        self.api_key = api_key

    def _mark_sent(self):
        """Call right before the request leaves. Earlier failures are unbilled."""
        if self.meter is not None:
            self.meter.sent()

    def _report_usage(self, tokens_in, tokens_out):
        """Call as soon as the body is decoded, before any parsing, so a
        parse failure cannot lose a charge. None for both = not reported."""
        if self.meter is not None and (tokens_in is not None
                                       or tokens_out is not None):
            self.meter.usage(int(tokens_in or 0), int(tokens_out or 0))

    def transcribe_region(self, image: Image.Image, keys, prompt) -> AIResult:
        raise NotImplementedError

    def transcribe_lines(self, crops, prompt) -> list:
        """Batch hook for §5.B per-line mode. v1 loops; providers that accept
        multiple images can override without changing the caller."""
        return [self.transcribe_region(image, keys, prompt) for image, keys in crops]

    def cost(self, tokens_in, tokens_out) -> float:
        """Display cost at the meter's price snapshot; 0 when unpriced (local).
        The ledger, not this value, is what budgets count."""
        if self.price is None:
            return 0.0
        from .billing import price_cost
        return float(price_cost(self.price, tokens_in, tokens_out))


class GeminiBackend(BaseBackend):

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
            "generationConfig": {
                "temperature": self.config.params.get("temperature", 0),
                "maxOutputTokens": sent_output_limit(self.config)}}
        budget = gemini_thinking_budget(self.config)
        if budget is not None:
            payload["generationConfig"]["thinkingConfig"] = {"thinkingBudget": budget}
        self._mark_sent()
        r = requests.post(url, headers={"x-goog-api-key": self.api_key},
                          json=payload, timeout=PROVIDER_TIMEOUT_SECONDS)
        r.raise_for_status()
        data = r.json()
        u = data.get("usageMetadata") or {}
        # Thinking tokens are billed as output but reported separately.
        tokens_out = None
        if "candidatesTokenCount" in u or "thoughtsTokenCount" in u:
            tokens_out = (u.get("candidatesTokenCount", 0)
                          + u.get("thoughtsTokenCount", 0))
        self._report_usage(u.get("promptTokenCount"), tokens_out)
        raw = data["candidates"][0]["content"]["parts"][0]["text"]
        text_by_key, unknown = parse_json_by_key(raw, keys)
        return AIResult(text_by_key, unknown,
                        u.get("promptTokenCount", estimate_image_tokens(w, h)),
                        tokens_out or 0, raw)


class AnthropicBackend(BaseBackend):

    def transcribe_region(self, image, keys, prompt) -> AIResult:
        import requests
        if not self.api_key:
            raise RuntimeError("Anthropic backend requires an API key (key_ref unresolved).")
        b64, w, h = _downscale_png_b64(image, self.config.max_edge_px)
        # Sonnet 5 / Opus 4.7+: non-default temperature/top_p/top_k → HTTP 400.
        # Adaptive thinking is on by default and counts toward max_tokens.
        payload = {
            "model": self.config.model_id,
            "max_tokens": sent_output_limit(self.config),
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
        self._mark_sent()
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json=payload,
            timeout=PROVIDER_TIMEOUT_SECONDS,
        )
        if not r.ok:
            raise ProviderHTTPError(
                f"Anthropic {r.status_code}: {r.text[:800]}", r.status_code)
        r.raise_for_status()
        data = r.json()
        u = data.get("usage") or {}
        self._report_usage(anthropic_input_equivalent(u), u.get("output_tokens"))
        raw = "".join(
            block.get("text", "")
            for block in data.get("content", [])
            if block.get("type") == "text"
        )
        text_by_key, unknown = parse_json_by_key(raw, keys)
        return AIResult(text_by_key, unknown,
                        u.get("input_tokens", estimate_image_tokens(w, h)),
                        u.get("output_tokens", 0), raw)


class OpenAIBackend(BaseBackend):

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
            "max_output_tokens": sent_output_limit(self.config),
        }
        if "reasoning" in params:
            payload["reasoning"] = params["reasoning"]
        self._mark_sent()
        r = requests.post(
            "https://api.openai.com/v1/responses",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=PROVIDER_TIMEOUT_SECONDS,
        )
        if not r.ok:
            raise ProviderHTTPError(
                f"OpenAI {r.status_code}: {r.text[:800]}", r.status_code)
        r.raise_for_status()
        data = r.json()
        u = data.get("usage") or {}
        self._report_usage(u.get("input_tokens"), u.get("output_tokens"))
        raw = _extract_openai_text(data)
        text_by_key, unknown = parse_json_by_key(raw, keys)
        return AIResult(text_by_key, unknown,
                        u.get("input_tokens", estimate_image_tokens(w, h)),
                        u.get("output_tokens", 0), raw)


class LocalOpenAIBackend(BaseBackend):
    """OpenAI-compatible chat endpoint (Ollama / vLLM). No API spend — the
    private path. Cost stays 0; only GPU-minutes (metered elsewhere)."""

    def transcribe_region(self, image, keys, prompt) -> AIResult:
        b64, w, h = _downscale_png_b64(image, self.config.max_edge_px)
        url = (self.config.endpoint or LOCAL_DEFAULT_ENDPOINT).rstrip("/") + "/chat/completions"
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        raw, u = _chat_completions_vision(
            url, headers, self.config.model_id, prompt, b64, backend=self)
        text_by_key, unknown = parse_json_by_key(raw, keys)
        return AIResult(text_by_key, unknown,
                        u.get("prompt_tokens", 0), u.get("completion_tokens", 0), raw)


class AzureOpenAIBackend(BaseBackend):
    """Azure OpenAI chat completions (deployment name in model_id). Off-site
    unless the archive's own Azure tenant; still blocked by never_send_offsite.
    The deployment name does not identify the model, so the price comes from
    the config's price_model_id."""

    def transcribe_region(self, image, keys, prompt) -> AIResult:
        if not self.api_key:
            raise RuntimeError("Azure OpenAI requires an API key (key_ref unresolved).")
        if not self.config.endpoint:
            raise RuntimeError("Azure OpenAI requires endpoint (resource URL).")
        b64, w, h = _downscale_png_b64(image, self.config.max_edge_px)
        params = self.config.params or {}
        url = azure_chat_url(
            self.config.endpoint, self.config.model_id,
            params.get("api_version", AZURE_API_VERSION))
        raw, u = _chat_completions_vision(
            url, {"api-key": self.api_key}, self.config.model_id, prompt, b64,
            backend=self, limit_param="max_completion_tokens",
            limit=sent_output_limit(self.config))
        text_by_key, unknown = parse_json_by_key(raw, keys)
        return AIResult(text_by_key, unknown,
                        u.get("prompt_tokens", estimate_image_tokens(w, h)),
                        u.get("completion_tokens", 0), raw)


class MistralBackend(BaseBackend):
    """Mistral / Pixtral vision via OpenAI-compatible chat completions."""

    def transcribe_region(self, image, keys, prompt) -> AIResult:
        if not self.api_key:
            raise RuntimeError("Mistral backend requires an API key (key_ref unresolved).")
        b64, w, h = _downscale_png_b64(image, self.config.max_edge_px)
        url = (self.config.endpoint or "https://api.mistral.ai/v1").rstrip("/") + "/chat/completions"
        raw, u = _chat_completions_vision(
            url, {"Authorization": f"Bearer {self.api_key}"},
            self.config.model_id, prompt, b64, backend=self,
            limit_param="max_tokens", limit=sent_output_limit(self.config))
        text_by_key, unknown = parse_json_by_key(raw, keys)
        return AIResult(text_by_key, unknown,
                        u.get("prompt_tokens", estimate_image_tokens(w, h)),
                        u.get("completion_tokens", 0), raw)


class MockBackend(BaseBackend):
    """Returns handed-in ground truth; for tests. Never opens the image."""
    def __init__(self, config=None, ground_truth_by_key=None, **kw):
        super().__init__(config)
        self._gt = ground_truth_by_key or {}

    def transcribe_region(self, image, keys, prompt) -> AIResult:
        return AIResult({k: self._gt.get(k, "") for k in keys if k in self._gt}, set())


BACKENDS = {
    'gemini': GeminiBackend,
    'anthropic': AnthropicBackend,
    'openai': OpenAIBackend,
    'local': LocalOpenAIBackend,
    'azure': AzureOpenAIBackend,
    'mistral': MistralBackend,
}


def backend_class(provider):
    cls = BACKENDS.get(provider)
    if cls is None:
        raise ValueError(
            f"No backend implemented for provider {provider!r} "
            f"(refusing to silently fall back to Gemini).")
    return cls


def get_backend(config, api_key=None) -> BaseBackend:
    return backend_class(config.provider)(config, api_key=api_key)
