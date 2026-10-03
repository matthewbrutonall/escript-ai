"""Backend seam for the PyLaia skeleton.

A future recognizer implements these four methods. This module does not
import PyLaia. The default backend has no models and does not recognize text.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass

ENGINE_NAME = "pylaia"

_CAPABILITIES = {
    "api_version": "1",
    "engine": ENGINE_NAME,
    "tier": "research",
    "tasks": ["recognize_lines"],
    "accepts": ["image/png"],
    "image_transport": ["base64"],
    "max_lines_per_request": 32,
    "preprocessing_supported": [],
    "params_accepted": [],
    "reports": ["timing_ms", "confidence", "warnings"],
}

_MODELS = {
    "api_version": "1",
    "engine": ENGINE_NAME,
    "models": [],
}


@dataclass(frozen=True)
class BackendFailure:
    """A contract error code. It carries no request data."""

    code: str


class UnavailablePyLaiaBackend:
    """Contract answers for a service with no recognition backend installed."""

    def capabilities(self) -> dict:
        return copy.deepcopy(_CAPABILITIES)

    def list_models(self) -> dict:
        return copy.deepcopy(_MODELS)

    def get_model(self, model_id: str) -> BackendFailure:
        return BackendFailure("model_not_found")

    def recognize(self, request) -> BackendFailure:
        return BackendFailure("unavailable")
