"""Engine menu rows for the external HTR action.

This module does not read the database, call an engine, or create a
config row. A missing PyLaia row is represented by one disabled menu
entry so the images page can show the name before a service exists.
"""
from __future__ import annotations

PYLAIA_ENGINE = "pylaia"
PYLAIA_MENU_NAME = "PyLaia"


def is_pylaia_config(config) -> bool:
    """Return whether this saved row is the PyLaia engine."""
    name = getattr(config, "name", None)
    if isinstance(name, str) and PYLAIA_ENGINE in name.casefold():
        return True
    metadata = getattr(config, "metadata", None)
    return isinstance(metadata, dict) and metadata.get("engine") == PYLAIA_ENGINE


def pylaia_placeholder() -> dict:
    """Menu entry used only when no PyLaia config row exists.

    It has no id and no endpoint. The images page must not request models
    or queue recognition for it.
    """
    return {
        "id": None,
        "name": PYLAIA_MENU_NAME,
        "engine": PYLAIA_ENGINE,
        "tier": "research",
        "experimental": True,
        "enabled": False,
        "available": False,
        "placeholder": True,
    }


def engine_menu(configs, rows) -> list:
    """Return the public menu, appending PyLaia only when it is not configured."""
    listed = list(rows)
    if any(is_pylaia_config(config) for config in configs):
        return listed
    listed.append(pylaia_placeholder())
    return listed
