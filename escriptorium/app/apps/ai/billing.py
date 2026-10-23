"""
Pure budget arithmetic for hosted AI calls (docs/budget-reservations.md).

No Django here. billing_store.py holds the ORM side: locks, holds, and
call rows. Amounts are Decimal USD.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

ZERO = Decimal("0")
QUANT = Decimal("0.000001")

#: The request parameter that carries each hosted provider's output limit,
#: and the limit sent when the backend config sets none. The same number is
#: sent in the HTTP body and used for the reservation.
OUTPUT_LIMIT_PARAM = {
    "anthropic": "max_tokens",
    "openai": "max_output_tokens",
    "gemini": "max_output_tokens",
    "azure": "max_completion_tokens",
    "mistral": "max_tokens",
}
DEFAULT_OUTPUT_LIMIT = {"openai": 2048}
DEFAULT_OUTPUT_LIMIT_OTHER = 8192
#: Gemini thinking tokens are billed as output. Reports differ on whether
#: maxOutputTokens includes them, so the reservation adds a thinking
#: allowance: the sent thinkingBudget when the config sets one, otherwise
#: the largest documented 2.5-series budget (an assumption, not a sent limit).
GEMINI_THINKING_CEILING = 32768
PROMPT_OVERHEAD_TOKENS = 64

#: Expected output for the job estimate (not a bound).
EXPECTED_OUT_BASE = 256
EXPECTED_OUT_PER_LINE = 96
EXPECTED_OUT_SEG_REVIEW = 1024


class BillingStop(Exception):
    """Stop the job before another provider call. Never swallowed."""


class BudgetExceeded(BillingStop):
    """The next hold or call does not fit under a cap."""


class JobClaimLost(BillingStop):
    """This worker no longer owns the job (reconciled, finished, or taken)."""


class JobCancelled(BillingStop):
    """The job's task report was cancelled."""


class UnpricedModel(BillingStop):
    """No configured price for this hosted model. Nothing is sent."""


@dataclass(frozen=True)
class Price:
    """USD per million tokens. One snapshot per meter, stored on each call."""
    in_usd_per_1m: Decimal
    out_usd_per_1m: Decimal

    def __post_init__(self):
        for name in ("in_usd_per_1m", "out_usd_per_1m"):
            value = getattr(self, name)
            if not isinstance(value, Decimal):
                value = Decimal(str(value))
                object.__setattr__(self, name, value)
            if not value.is_finite() or value < 0:
                raise UnpricedModel(f"price {name} is not a finite amount >= 0")


ZERO_PRICE = Price(ZERO, ZERO)


def usd(value) -> Decimal:
    if value is None:
        return ZERO
    if isinstance(value, Decimal):
        return value.quantize(QUANT)
    try:
        return Decimal(str(value)).quantize(QUANT)
    except (InvalidOperation, ValueError):
        raise ValueError(f"not a USD amount: {value!r}")


def cap_or_none(cap):
    """None or negative = unlimited (None). Non-finite = 0 (blocked)."""
    if cap is None:
        return None
    try:
        if not math.isfinite(float(cap)):
            return ZERO
    except (TypeError, ValueError):
        return ZERO
    cap = usd(cap)
    return None if cap < 0 else cap


def fits(usage, delta, cap) -> bool:
    """Same rule as budget.budget_allows, in Decimal: cap 0 blocks always."""
    cap = cap_or_none(cap)
    if cap is None:
        return True
    if cap == 0:
        return False
    return usd(usage) + usd(delta) <= cap


def hold_remaining(amount, drawn) -> Decimal:
    return max(ZERO, usd(amount) - usd(drawn))


def extra_needed(need, amount, drawn) -> Decimal:
    """Part of a call's bound that the job hold cannot cover."""
    return max(ZERO, usd(need) - hold_remaining(amount, drawn))


def scope_usage(confirmed, held_calls, holds) -> Decimal:
    """confirmed + held calls + open holds' undrawn remainder.

    `holds` is an iterable of (amount, drawn) for open holds.
    """
    total = usd(confirmed) + usd(held_calls)
    for amount, drawn in holds:
        total += hold_remaining(amount, drawn)
    return total


def price_cost(price, tokens_in, tokens_out) -> Decimal:
    return usd(
        (Decimal(int(tokens_in or 0)) * price.in_usd_per_1m
         + Decimal(int(tokens_out or 0)) * price.out_usd_per_1m)
        / Decimal(1_000_000))


def downscaled_size(w, h, max_edge):
    """PIL.thumbnail keeps aspect and never upsizes."""
    w, h = int(w), int(h)
    if max(w, h) <= max_edge:
        return w, h
    scale = max_edge / max(w, h)
    return max(1, round(w * scale)), max(1, round(h * scale))


def image_token_bound(w, h) -> int:
    """Generous bound across tokenizer schemes. An assumption, not verified."""
    w, h = max(1, int(w)), max(1, int(h))
    tiles = max(258, math.ceil(w / 768) * math.ceil(h / 768) * 258)
    return max(
        tiles,
        math.ceil(w / 32) * math.ceil(h / 32),
        math.ceil(w / 16) * math.ceil(h / 16),
        math.ceil(w * h / 750),
    )


def sent_output_limit(config) -> int:
    """The output limit placed in the HTTP body for a hosted provider."""
    provider = getattr(config, "provider", "")
    param = OUTPUT_LIMIT_PARAM.get(provider)
    if param is None:
        raise ValueError(f"no output limit parameter for provider {provider!r}")
    params = getattr(config, "params", None) or {}
    value = params.get(param, DEFAULT_OUTPUT_LIMIT.get(
        provider, DEFAULT_OUTPUT_LIMIT_OTHER))
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"params.{param} must be a positive integer")
    return value


def gemini_thinking_budget(config):
    """The thinkingBudget to send, or None to send none."""
    params = getattr(config, "params", None) or {}
    value = params.get("thinking_budget")
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("params.thinking_budget must be an integer >= 0")
    return value


def output_reserve_tokens(config) -> int:
    """Output tokens a call can be billed for, from what the request sends."""
    limit = sent_output_limit(config)
    if getattr(config, "provider", "") == "gemini":
        budget = gemini_thinking_budget(config)
        limit += GEMINI_THINKING_CEILING if budget is None else budget
    return limit


def call_bound_usd(price, config, image_size, prompt) -> Decimal:
    max_edge = getattr(config, "max_edge_px", 1024) or 1024
    w, h = downscaled_size(*image_size, max_edge)
    tokens_in = (image_token_bound(w, h)
                 + len((prompt or "").encode("utf-8"))
                 + PROMPT_OVERHEAD_TOKENS)
    return price_cost(price, tokens_in, output_reserve_tokens(config))


def expected_call_usd(price, config, n_lines=0, *, seg_review=False,
                      prompt_chars=1200) -> Decimal:
    """Typical cost of one call, for the job estimate only."""
    edge = getattr(config, "max_edge_px", 1024) or 1024
    from .backends import estimate_image_tokens
    tokens_in = estimate_image_tokens(edge, edge) + prompt_chars // 4
    if seg_review:
        tokens_out = EXPECTED_OUT_SEG_REVIEW
    else:
        tokens_out = EXPECTED_OUT_BASE + EXPECTED_OUT_PER_LINE * int(n_lines)
    tokens_out = min(tokens_out, output_reserve_tokens(config))
    return price_cost(price, tokens_in, tokens_out)


def estimate_transcribe_usd(price, config, lines_per_part, per_crop) -> Decimal:
    total = ZERO
    per_crop = max(1, int(per_crop))
    for n in lines_per_part:
        n = int(n)
        full, rest = divmod(n, per_crop)
        total += full * expected_call_usd(price, config, per_crop)
        if rest:
            total += expected_call_usd(price, config, rest)
    return usd(total)


def estimate_seg_review_usd(price, config, n_parts) -> Decimal:
    return usd(int(n_parts) * expected_call_usd(
        price, config, seg_review=True))


def http_status(exc):
    status = getattr(exc, "status_code", None)
    if status is None:
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
    return status if isinstance(status, int) else None


def failure_state(exc, sent) -> str:
    """'released' only with evidence of no charge; else 'unknown'.

    Released: never sent; ConnectTimeout (no connection was made, so the
    provider never received the request); HTTP 401 (authentication failed,
    no account to bill). Everything else, 403 included, stays unknown:
    a permission or policy refusal does not prove no tokens were processed.
    """
    if not sent:
        return "released"
    try:
        import requests
        if isinstance(exc, requests.exceptions.ConnectTimeout):
            return "released"
    except ImportError:
        pass
    if http_status(exc) == 401:
        return "released"
    return "unknown"


def describe_error(exc) -> str:
    """Class name and status only. Never the response body or the key."""
    status = http_status(exc)
    name = type(exc).__name__
    return f"{name} HTTP {status}" if status else name
