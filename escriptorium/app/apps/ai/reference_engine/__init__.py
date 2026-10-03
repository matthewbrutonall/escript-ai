"""Fake external HTR engine for contract tests. Not a recognizer."""

from .engine import ENGINE_NAME, MODEL_ID, handle

__all__ = ["ENGINE_NAME", "MODEL_ID", "handle"]
