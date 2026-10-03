"""Contract check for one external HTR engine.

Given a stored engine config, this calls capabilities, the model list,
one model, and one synthetic recognize request. It is for operator
validation. Transcription jobs do not import it.

The returned result uses fixed messages. It does not include the service
address, stored options, the synthetic image, a response body, or
exception text.
"""
from __future__ import annotations

from dataclasses import dataclass

from ai.htr_engine_client import (
    EngineClientError,
    fetch_capabilities,
    fetch_model,
    fetch_models,
    recognize_lines,
)
from ai.htr_engine_contract import ContractError

# Four characters, valid standard base64, and not a document image.
_SYNTHETIC_IMAGE = "AAAA"

_STEP_FAILED = {
    "capabilities": "capabilities check failed",
    "models": "model list check failed",
    "model": "model detail check failed",
    "recognize": "recognition check failed",
}


@dataclass(frozen=True)
class ContractCheckResult:
    """Outcome of one contract check.

    ``step`` is ``disabled``, ``capabilities``, ``models``, ``model``,
    or ``recognize``. ``code`` is a client or contract code, ``disabled``,
    ``empty_models``, or ``internal``. ``message`` is a fixed sentence.
    """

    ok: bool
    step: str
    code: str
    message: str


def run_contract_check(config) -> ContractCheckResult:
    """Run the four contract calls and stop at the first failure.

    A disabled config is not fetched. The recognize call sends one line
    and does not read a stored image.
    """
    if getattr(config, "enabled", False) is not True:
        return _fail("disabled", "disabled")
    try:
        capabilities = fetch_capabilities(config)
    except (EngineClientError, ContractError) as exc:
        return _fail("capabilities", _code(exc))
    except Exception:
        return _fail("capabilities", "internal")

    engine = _plain_name(getattr(capabilities, "engine", None))
    if engine is None:
        return _fail("capabilities", "invalid_response")

    try:
        listing = fetch_models(config)
    except (EngineClientError, ContractError) as exc:
        return _fail("models", _code(exc))
    except Exception:
        return _fail("models", "internal")

    model_id = _first_model_id(getattr(listing, "models", None))
    if model_id is None:
        empty = getattr(listing, "models", None) == ()
        return _fail("models", "empty_models" if empty else "invalid_response")

    try:
        fetch_model(config, model_id)
    except (EngineClientError, ContractError) as exc:
        return _fail("model", _code(exc))
    except Exception:
        return _fail("model", "internal")

    try:
        recognize_lines(config, _recognize_payload(engine, model_id))
    except (EngineClientError, ContractError) as exc:
        return _fail("recognize", _code(exc))
    except Exception:
        return _fail("recognize", "internal")
    return ContractCheckResult(True, "recognize", "ok", "contract check passed")


def _recognize_payload(engine: str, model_id: str) -> dict:
    return {
        "api_version": "1",
        "job_id": "contract-check",
        "document_id": "0",
        "part_id": "0",
        "engine": engine,
        "model_id": model_id,
        "preprocessing": {},
        "lines": [{"line_id": "1", "image": _SYNTHETIC_IMAGE}],
    }


def _first_model_id(models):
    if not isinstance(models, tuple) or not models:
        return None
    return _plain_name(getattr(models[0], "model_id", None))


def _plain_name(value):
    if isinstance(value, str) and value and value == value.strip() and len(value) <= 256:
        return value
    return None


def _code(exc) -> str:
    code = getattr(exc, "code", None)
    if (
        isinstance(code, str)
        and code.strip()
        and len(code) <= 64
        and code == code.strip()
        and " " not in code
        and "/" not in code
    ):
        return code
    return "internal"


def _fail(step: str, code: str) -> ContractCheckResult:
    if code == "disabled":
        message = "engine is disabled"
    elif code == "empty_models":
        message = "engine reported no models"
    else:
        message = _STEP_FAILED[step]
    return ContractCheckResult(False, step, code, message)
