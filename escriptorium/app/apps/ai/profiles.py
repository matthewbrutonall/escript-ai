"""
Billing capability profiles for hosted models (docs/budget-reservations.md).

A hosted model is callable only when it has a profile here AND a price row
(ai.AIModelPrice). The profile says how its image input is tokenized, what
request overhead to allow, which output limit is sent, and that the limit
also caps reasoning. Every bound below is a documented formula, not a guess;
anything without one is refused before a request is made.

Profiles are bound to exact model ids. Azure is profiled by the backend's
price_model_id (the model the deployment runs), which also selects the
price row. No pure function here touches Django.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable

from .billing import BillingStop

RETRIEVED = "2026-10-23"

SOURCE_OPENAI_VISION = "https://developers.openai.com/api/docs/guides/images-vision"
SOURCE_ANTHROPIC_VISION = "https://platform.claude.com/docs/en/build-with-claude/vision"
SOURCE_AZURE_LIFECYCLE = (
    "https://learn.microsoft.com/en-us/azure/ai-foundry/openai/api-version-lifecycle")

#: Allowance for message framing and the JSON request around the prompt.
REQUEST_OVERHEAD_TOKENS = 64

#: Azure api-versions that implement max_completion_tokens (added in
#: 2024-09-01-preview) and are documented as generally available. Dated
#: previews are refused: whether a resource still serves them cannot be
#: checked without a request.
AZURE_API_VERSIONS = ("2024-10-21",)


class UnsupportedModel(BillingStop):
    """No documented billing ceiling for this configuration. Nothing is sent."""


@dataclass(frozen=True)
class Profile:
    provider: str
    model: str
    #: Tokens for one image of the given outgoing size (after this app's own
    #: downscale) and config. Must be >= what the provider bills.
    image_tokens: Callable[[int, int, object], int]
    #: The sent output limit also bounds reasoning/thinking tokens.
    output_limit_covers_reasoning: bool
    sources: tuple
    request_overhead_tokens: int = REQUEST_OVERHEAD_TOKENS


# -- OpenAI tile models -------------------------------------------------------

def openai_detail(config):
    """The detail value actually sent. Only documented tile values."""
    detail = (getattr(config, "params", None) or {}).get("detail", "high")
    if detail not in ("low", "high", "auto"):
        raise UnsupportedModel(
            f"params.detail {detail!r} is not one of low, high, auto.")
    return detail


def openai_tile_tokens(w, h, detail, base, per_tile):
    """OpenAI's documented tile procedure for gpt-4o-family models.

    low: the base tokens, whatever the size. high and auto: fit within a
    2048 x 2048 square, then scale the shortest side down to 768 if it is
    longer, then count the 512 px tiles that cover the image. Scaled sides
    are kept fractional and tiles rounded up, so rounding cannot undercount.
    """
    if detail == "low":
        return base
    w, h = float(w), float(h)
    if max(w, h) > 2048:
        scale = 2048 / max(w, h)
        w, h = w * scale, h * scale
    if min(w, h) > 768:
        scale = 768 / min(w, h)
        w, h = w * scale, h * scale
    tiles = math.ceil(w / 512) * math.ceil(h / 512)
    return base + per_tile * tiles


def _openai_tiles(base, per_tile):
    def tokens(w, h, config):
        return openai_tile_tokens(w, h, openai_detail(config), base, per_tile)
    return tokens


# (base, per tile) from the tile table in SOURCE_OPENAI_VISION.
OPENAI_TILE_MODELS = {
    "gpt-4o": (85, 170),
    "gpt-4.1": (85, 170),
    "gpt-4o-mini": (2833, 5667),
}


# -- Anthropic ----------------------------------------------------------------

#: Visual-token caps: 1,568 on the standard tier, 4,784 on Claude 4.7 and
#: later. Larger images are scaled down by the API until they fit, so the
#: higher cap bounds both tiers.
ANTHROPIC_TOKEN_CAP = 4784


def anthropic_image_tokens(w, h, config=None):
    """ceil(w / 28) x ceil(h / 28) visual tokens, never above the cap."""
    return min(math.ceil(int(w) / 28) * math.ceil(int(h) / 28),
               ANTHROPIC_TOKEN_CAP)


ANTHROPIC_MODELS = ("claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5")


def _build():
    profiles = {}
    for model, (base, per_tile) in OPENAI_TILE_MODELS.items():
        for provider in ("openai", "azure"):
            profiles[(provider, model)] = Profile(
                provider=provider, model=model,
                image_tokens=_openai_tiles(base, per_tile),
                output_limit_covers_reasoning=True,
                sources=(SOURCE_OPENAI_VISION,)
                + ((SOURCE_AZURE_LIFECYCLE,) if provider == "azure" else ()))
    for model in ANTHROPIC_MODELS:
        profiles[("anthropic", model)] = Profile(
            provider="anthropic", model=model,
            image_tokens=anthropic_image_tokens,
            output_limit_covers_reasoning=True,
            sources=(SOURCE_ANTHROPIC_VISION,))
    return profiles


PROFILES = _build()

#: Why whole providers have no profile. Shown in the refusal.
REFUSED_PROVIDERS = {
    "gemini": (
        "Gemini is not supported for hosted billing: measured thinking plus "
        "output exceeds maxOutputTokens on some models, and no model's "
        "combined ceiling could be verified."),
    "mistral": (
        "Mistral vision is not supported for hosted billing: no image-token "
        "ceiling is published."),
}


def priced_model(config):
    """The model id that selects both the price row and the profile."""
    if config.provider == "azure":
        model = (getattr(config, "price_model_id", "") or "").strip()
        if not model:
            raise UnsupportedModel(
                f"Azure deployment {config.model_id!r} has no price_model_id; "
                "set the model it runs.")
        return model
    return config.model_id


def resolve_profile(config):
    """The profile for a hosted config, or UnsupportedModel before any send."""
    provider = config.provider
    if provider in REFUSED_PROVIDERS:
        raise UnsupportedModel(REFUSED_PROVIDERS[provider])
    model = priced_model(config)
    profile = PROFILES.get((provider, model))
    if profile is None:
        raise UnsupportedModel(
            f"{provider}:{model} has no documented billing profile and is not "
            "supported for hosted use. A price row alone does not enable it.")
    if provider == "azure":
        version = (getattr(config, "params", None) or {}).get(
            "api_version", AZURE_API_VERSIONS[0])
        if version not in AZURE_API_VERSIONS:
            raise UnsupportedModel(
                f"Azure api_version {version!r} is not supported; use one of "
                f"{', '.join(AZURE_API_VERSIONS)} (they implement "
                "max_completion_tokens).")
    if provider in ("openai", "azure"):
        openai_detail(config)
    return profile
