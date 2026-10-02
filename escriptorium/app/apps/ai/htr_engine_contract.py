"""External HTR engine wire contract, version 1.

Stage 2.1 scaffolding. Nothing in the Stage 1 transcription path imports
this module. Escript AI does not serve these routes and does not start
an engine container.

The external process owns the routes:

  GET  /v1/capabilities
  GET  /v1/models
  GET  /v1/models/{model_id}
  POST /v1/recognize

Line images are standard base64 with no ``data:`` prefix. Confidence is
meaningful inside one engine only; do not rank engines by it.
A ``timing_ms`` of 0 means the engine did not measure that interval.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

API_VERSION = "1"

PATH_CAPABILITIES = "/v1/capabilities"
PATH_MODELS = "/v1/models"
PATH_MODEL = "/v1/models/{model_id}"
PATH_RECOGNIZE = "/v1/recognize"

TIERS = frozenset({"production", "api", "research"})
TASKS = frozenset({"recognize_lines"})
IMAGE_TRANSPORTS = frozenset({"base64"})
ACCEPT_REQUIRED = "image/png"
PREPROCESSING_FIELDS = (
    "line_height",
    "deslant",
    "grayscale",
    "preserve_aspect",
)
REPORT_FIELDS = frozenset({"timing_ms", "confidence", "warnings"})
REPORTS_REQUIRED = frozenset({"timing_ms"})
ERROR_CODES = frozenset({
    "invalid_request",
    "unsupported_preprocessing",
    "model_not_found",
    "line_failed",
    "busy",
    "unavailable",
    "internal",
})
ERROR_HTTP_STATUS = {
    "invalid_request": 400,
    "unsupported_preprocessing": 400,
    "model_not_found": 404,
    "line_failed": 422,
    "busy": 429,
    "unavailable": 503,
    "internal": 500,
}

_B64 = re.compile(r"^[A-Za-z0-9+/]+={0,2}$")


class ContractError(ValueError):
    """A payload broke the v1 contract. ``code`` is an ``ERROR_CODES`` value."""

    def __init__(self, code: str, message: str, *, line_id: str | None = None):
        if code not in ERROR_CODES:
            raise ValueError(f"unknown contract error code: {code}")
        self.code = code
        self.line_id = line_id
        super().__init__(message)


@dataclass(frozen=True)
class Preprocessing:
    line_height: int
    deslant: bool
    grayscale: bool
    preserve_aspect: bool


@dataclass(frozen=True)
class LineInput:
    line_id: str
    image: str
    baseline: tuple[tuple[float, float], ...] | None
    mask: tuple[tuple[float, float], ...] | None


@dataclass(frozen=True)
class RecognizeRequest:
    api_version: str
    job_id: str
    document_id: str
    part_id: str
    engine: str
    model_id: str
    preprocessing: Preprocessing
    lines: tuple[LineInput, ...]


@dataclass(frozen=True)
class LineResult:
    line_id: str
    text: str
    confidence: float | None
    timing_ms: int
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class Provenance:
    engine: str
    model_id: str
    model_version: str
    api_version: str


@dataclass(frozen=True)
class Resources:
    device: str | None


@dataclass(frozen=True)
class RecognizeResponse:
    api_version: str
    job_id: str
    engine: str
    model_id: str
    model_version: str
    results: tuple[LineResult, ...]
    provenance: Provenance
    timing_ms: int
    resources: Resources


@dataclass(frozen=True)
class Capabilities:
    api_version: str
    engine: str
    tier: str
    tasks: tuple[str, ...]
    accepts: tuple[str, ...]
    image_transport: tuple[str, ...]
    max_lines_per_request: int
    preprocessing_supported: tuple[str, ...]
    reports: tuple[str, ...]


@dataclass(frozen=True)
class ModelMetadata:
    api_version: str
    engine: str
    model_id: str
    model_version: str
    display_name: str
    licence: str
    source: str
    scripts: tuple[str, ...]
    languages: tuple[str, ...]
    input: Preprocessing
    alphabet_note: str
    experimental: bool


@dataclass(frozen=True)
class ModelList:
    api_version: str
    engine: str
    models: tuple[ModelMetadata, ...]


@dataclass(frozen=True)
class EngineErrorBody:
    api_version: str
    code: str
    message: str
    retryable: bool
    line_id: str | None


def parse_capabilities(payload) -> Capabilities:
    obj = _object(payload, "capabilities")
    _exact(obj, (
        "api_version", "engine", "tier", "tasks", "accepts",
        "image_transport", "max_lines_per_request",
        "preprocessing_supported", "reports",
    ), "capabilities")
    _version(obj["api_version"], "capabilities.api_version")
    engine = _name(obj["engine"], "capabilities.engine")
    tier = obj["tier"]
    if tier not in TIERS:
        raise ContractError("invalid_request", f"unknown tier: {tier!r}")
    tasks = _name_tuple(obj["tasks"], "capabilities.tasks", allowed=TASKS)
    if "recognize_lines" not in tasks:
        raise ContractError("invalid_request", "capabilities.tasks must include recognize_lines")
    accepts = _name_tuple(obj["accepts"], "capabilities.accepts")
    if ACCEPT_REQUIRED not in accepts:
        raise ContractError("invalid_request", f"capabilities.accepts must include {ACCEPT_REQUIRED}")
    transport = _name_tuple(
        obj["image_transport"], "capabilities.image_transport", allowed=IMAGE_TRANSPORTS,
    )
    supported = _name_tuple(
        obj["preprocessing_supported"],
        "capabilities.preprocessing_supported",
        allowed=frozenset(PREPROCESSING_FIELDS),
        allow_empty=True,
    )
    reports = _name_tuple(obj["reports"], "capabilities.reports", allowed=REPORT_FIELDS)
    missing_reports = REPORTS_REQUIRED - set(reports)
    if missing_reports:
        raise ContractError(
            "invalid_request",
            f"capabilities.reports missing {sorted(missing_reports)}",
        )
    return Capabilities(
        api_version=API_VERSION,
        engine=engine,
        tier=tier,
        tasks=tasks,
        accepts=accepts,
        image_transport=transport,
        max_lines_per_request=_positive_int(
            obj["max_lines_per_request"], "capabilities.max_lines_per_request",
        ),
        preprocessing_supported=supported,
        reports=reports,
    )


def parse_model(payload, *, list_engine: str | None = None) -> ModelMetadata:
    obj = _object(payload, "model")
    _exact(obj, (
        "api_version", "engine", "model_id", "model_version", "display_name",
        "licence", "source", "scripts", "languages", "input", "alphabet_note",
        "experimental",
    ), "model")
    _version(obj["api_version"], "model.api_version")
    engine = _name(obj["engine"], "model.engine")
    if list_engine is not None and engine != list_engine:
        raise ContractError(
            "invalid_request",
            f"model.engine {engine!r} does not match list engine {list_engine!r}",
        )
    return ModelMetadata(
        api_version=API_VERSION,
        engine=engine,
        model_id=_name(obj["model_id"], "model.model_id"),
        model_version=_name(obj["model_version"], "model.model_version"),
        display_name=_name(obj["display_name"], "model.display_name"),
        licence=_name(obj["licence"], "model.licence"),
        source=_name(obj["source"], "model.source"),
        scripts=_name_tuple(obj["scripts"], "model.scripts", allow_empty=True),
        languages=_name_tuple(obj["languages"], "model.languages", allow_empty=True),
        input=parse_preprocessing(obj["input"], "model.input"),
        alphabet_note=_text(obj["alphabet_note"], "model.alphabet_note", allow_empty=True),
        experimental=_bool(obj["experimental"], "model.experimental"),
    )


def parse_model_list(payload) -> ModelList:
    obj = _object(payload, "models")
    _exact(obj, ("api_version", "engine", "models"), "models")
    _version(obj["api_version"], "models.api_version")
    engine = _name(obj["engine"], "models.engine")
    raw_models = obj["models"]
    if not isinstance(raw_models, list):
        raise ContractError("invalid_request", "models.models must be a list")
    models = tuple(parse_model(item, list_engine=engine) for item in raw_models)
    ids = [item.model_id for item in models]
    if len(ids) != len(set(ids)):
        raise ContractError("invalid_request", "models.models has a duplicate model_id")
    return ModelList(api_version=API_VERSION, engine=engine, models=models)


def parse_recognize_request(payload) -> RecognizeRequest:
    obj = _object(payload, "recognize request")
    _exact(obj, (
        "api_version", "job_id", "document_id", "part_id", "engine",
        "model_id", "preprocessing", "lines",
    ), "recognize request")
    _version(obj["api_version"], "recognize request.api_version")
    lines_raw = obj["lines"]
    if not isinstance(lines_raw, list) or not lines_raw:
        raise ContractError("invalid_request", "recognize request.lines must be a non-empty list")
    lines = tuple(_parse_line(item, i) for i, item in enumerate(lines_raw))
    ids = [line.line_id for line in lines]
    if len(ids) != len(set(ids)):
        raise ContractError("invalid_request", "recognize request.lines has a duplicate line_id")
    return RecognizeRequest(
        api_version=API_VERSION,
        job_id=_name(obj["job_id"], "recognize request.job_id"),
        document_id=_identity(obj["document_id"], "recognize request.document_id"),
        part_id=_identity(obj["part_id"], "recognize request.part_id"),
        engine=_name(obj["engine"], "recognize request.engine"),
        model_id=_name(obj["model_id"], "recognize request.model_id"),
        preprocessing=parse_preprocessing(obj["preprocessing"], "recognize request.preprocessing"),
        lines=lines,
    )


def parse_recognize_response(payload, request: RecognizeRequest | None = None) -> RecognizeResponse:
    obj = _object(payload, "recognize response")
    _exact(obj, (
        "api_version", "job_id", "engine", "model_id", "model_version",
        "results", "provenance", "timing_ms", "resources",
    ), "recognize response")
    _version(obj["api_version"], "recognize response.api_version")
    job_id = _name(obj["job_id"], "recognize response.job_id")
    engine = _name(obj["engine"], "recognize response.engine")
    model_id = _name(obj["model_id"], "recognize response.model_id")
    model_version = _name(obj["model_version"], "recognize response.model_version")
    provenance = _parse_provenance(obj["provenance"])
    if (provenance.engine, provenance.model_id, provenance.model_version) != (
        engine, model_id, model_version,
    ):
        raise ContractError("invalid_request", "provenance does not match the response")
    results_raw = obj["results"]
    if not isinstance(results_raw, list):
        raise ContractError("invalid_request", "recognize response.results must be a list")
    results = tuple(_parse_result(item, i) for i, item in enumerate(results_raw))
    result_ids = [item.line_id for item in results]
    if len(result_ids) != len(set(result_ids)):
        raise ContractError("invalid_request", "recognize response.results has a duplicate line_id")
    if request is not None:
        _match_request(request, job_id, engine, model_id, result_ids)
    resources = _parse_resources(obj["resources"])
    return RecognizeResponse(
        api_version=API_VERSION,
        job_id=job_id,
        engine=engine,
        model_id=model_id,
        model_version=model_version,
        results=results,
        provenance=provenance,
        timing_ms=_non_negative_int(obj["timing_ms"], "recognize response.timing_ms"),
        resources=resources,
    )


def parse_error(payload) -> EngineErrorBody:
    obj = _object(payload, "error")
    _exact(obj, ("api_version", "error"), "error")
    _version(obj["api_version"], "error.api_version")
    body = _object(obj["error"], "error.error")
    _exact(body, ("code", "message", "retryable", "line_id"), "error.error")
    code = body["code"]
    if code not in ERROR_CODES:
        raise ContractError("invalid_request", f"unknown error code: {code!r}")
    line_id = body["line_id"]
    if line_id is not None:
        line_id = _identity(line_id, "error.error.line_id")
    return EngineErrorBody(
        api_version=API_VERSION,
        code=code,
        message=_name(body["message"], "error.error.message"),
        retryable=_bool(body["retryable"], "error.error.retryable"),
        line_id=line_id,
    )


def parse_preprocessing(value, where: str) -> Preprocessing:
    obj = _object(value, where)
    extra = [key for key in obj if key not in PREPROCESSING_FIELDS]
    if extra:
        raise ContractError("unsupported_preprocessing", f"{where} has unsupported fields: {extra}")
    missing = [key for key in PREPROCESSING_FIELDS if key not in obj]
    if missing:
        raise ContractError("invalid_request", f"{where} missing {missing}")
    return Preprocessing(
        line_height=_positive_int(obj["line_height"], f"{where}.line_height"),
        deslant=_bool(obj["deslant"], f"{where}.deslant"),
        grayscale=_bool(obj["grayscale"], f"{where}.grayscale"),
        preserve_aspect=_bool(obj["preserve_aspect"], f"{where}.preserve_aspect"),
    )


def _match_request(request, job_id, engine, model_id, result_ids):
    if (job_id, engine, model_id) != (request.job_id, request.engine, request.model_id):
        raise ContractError("invalid_request", "response does not match the request identity")
    wanted = {line.line_id for line in request.lines}
    got = set(result_ids)
    extra = sorted(got - wanted)
    if extra:
        raise ContractError("invalid_request", f"response has unknown line_id values: {extra}")
    missing = sorted(wanted - got)
    if missing:
        raise ContractError(
            "line_failed",
            f"response omitted line_id {missing[0]}",
            line_id=missing[0],
        )


def _parse_line(item, index: int) -> LineInput:
    where = f"lines[{index}]"
    obj = _object(item, where)
    _exact(obj, ("line_id", "image"), where, optional=("baseline", "mask"))
    return LineInput(
        line_id=_identity(obj["line_id"], f"{where}.line_id"),
        image=_image(obj["image"], f"{where}.image"),
        baseline=_points(obj.get("baseline"), f"{where}.baseline", minimum=2),
        mask=_points(obj.get("mask"), f"{where}.mask", minimum=3),
    )


def _parse_result(item, index: int) -> LineResult:
    where = f"results[{index}]"
    obj = _object(item, where)
    _exact(obj, ("line_id", "text", "confidence", "timing_ms", "warnings"), where)
    return LineResult(
        line_id=_identity(obj["line_id"], f"{where}.line_id"),
        text=_text(obj["text"], f"{where}.text", allow_empty=True),
        confidence=_confidence(obj["confidence"], f"{where}.confidence"),
        timing_ms=_non_negative_int(obj["timing_ms"], f"{where}.timing_ms"),
        warnings=_warnings(obj["warnings"], f"{where}.warnings"),
    )


def _parse_provenance(value) -> Provenance:
    obj = _object(value, "provenance")
    _exact(obj, ("engine", "model_id", "model_version", "api_version"), "provenance")
    _version(obj["api_version"], "provenance.api_version")
    return Provenance(
        engine=_name(obj["engine"], "provenance.engine"),
        model_id=_name(obj["model_id"], "provenance.model_id"),
        model_version=_name(obj["model_version"], "provenance.model_version"),
        api_version=API_VERSION,
    )


def _parse_resources(value) -> Resources:
    obj = _object(value, "resources")
    _exact(obj, (), "resources", optional=("device",))
    device = obj.get("device")
    if device is not None:
        device = _name(device, "resources.device")
    return Resources(device=device)


def _object(value, where: str) -> dict:
    if not isinstance(value, dict):
        raise ContractError("invalid_request", f"{where} must be an object")
    return value


def _exact(value: dict, required, where: str, optional=()):
    allowed = set(required) | set(optional)
    missing = [key for key in required if key not in value]
    extra = sorted(key for key in value if key not in allowed)
    if missing or extra:
        raise ContractError(
            "invalid_request",
            f"{where} fields mismatch: missing={missing} extra={extra}",
        )


def _version(value, where: str):
    if value != API_VERSION:
        raise ContractError("invalid_request", f"{where} must be {API_VERSION!r}")


def _text(value, where: str, *, allow_empty: bool) -> str:
    if not isinstance(value, str):
        raise ContractError("invalid_request", f"{where} must be a string")
    if not allow_empty and not value.strip():
        raise ContractError("invalid_request", f"{where} must be non-empty")
    return value


def _name(value, where: str) -> str:
    text = _text(value, where, allow_empty=False)
    if text != text.strip():
        raise ContractError("invalid_request", f"{where} must not have surrounding space")
    return text


def _identity(value, where: str) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ContractError("invalid_request", f"{where} must be a string or integer")
    if isinstance(value, int):
        if value < 0:
            raise ContractError("invalid_request", f"{where} must be >= 0")
        return str(value)
    return _name(value, where)


def _bool(value, where: str) -> bool:
    if not isinstance(value, bool):
        raise ContractError("invalid_request", f"{where} must be a boolean")
    return value


def _positive_int(value, where: str) -> int:
    number = _non_negative_int(value, where)
    if number < 1:
        raise ContractError("invalid_request", f"{where} must be >= 1")
    return number


def _non_negative_int(value, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ContractError("invalid_request", f"{where} must be an integer")
    if value < 0:
        raise ContractError("invalid_request", f"{where} must be >= 0")
    return value


def _confidence(value, where: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContractError("invalid_request", f"{where} must be a number or null")
    number = float(value)
    if number < 0 or number > 1:
        raise ContractError("invalid_request", f"{where} must be between 0 and 1")
    return number


def _warnings(value, where: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ContractError("invalid_request", f"{where} must be a list")
    notes = []
    for index, item in enumerate(value):
        notes.append(_name(item, f"{where}[{index}]"))
    return tuple(notes)


def _name_tuple(value, where: str, allowed: frozenset | None = None, allow_empty: bool = False):
    if not isinstance(value, list):
        raise ContractError("invalid_request", f"{where} must be a list")
    if not value and not allow_empty:
        raise ContractError("invalid_request", f"{where} must be non-empty")
    names = []
    for index, item in enumerate(value):
        name = _name(item, f"{where}[{index}]")
        if allowed is not None and name not in allowed:
            code = "unsupported_preprocessing" if "preprocessing" in where else "invalid_request"
            raise ContractError(code, f"{where}[{index}] is not supported: {name}")
        names.append(name)
    if len(names) != len(set(names)):
        raise ContractError("invalid_request", f"{where} has a duplicate")
    return tuple(names)


def _image(value, where: str) -> str:
    text = _name(value, where)
    if text.startswith("data:"):
        raise ContractError("invalid_request", f"{where} must be raw base64, not a data: URL")
    if len(text) % 4 != 0 or not _B64.fullmatch(text):
        raise ContractError("invalid_request", f"{where} must be standard base64")
    return text


def _points(value, where: str, *, minimum: int):
    if value is None:
        return None
    if not isinstance(value, list):
        raise ContractError("invalid_request", f"{where} must be a list")
    if len(value) < minimum:
        raise ContractError("invalid_request", f"{where} needs at least {minimum} points")
    points = []
    for index, point in enumerate(value):
        if not isinstance(point, list) or len(point) != 2:
            raise ContractError("invalid_request", f"{where}[{index}] must be [x, y]")
        points.append((
            _coord(point[0], f"{where}[{index}][0]"),
            _coord(point[1], f"{where}[{index}][1]"),
        ))
    return tuple(points)


def _coord(value, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContractError("invalid_request", f"{where} must be a number")
    return float(value)
