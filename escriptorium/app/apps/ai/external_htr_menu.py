"""Engine menu rows for the external HTR action.

This module does not read the database, call an engine, or create a
config row. A missing PyLaia or Calamari row is represented by its own
disabled menu entry so the images page can show the name before a
service exists.
"""
from __future__ import annotations

PYLAIA_ENGINE = "pylaia"
PYLAIA_MENU_NAME = "PyLaia"
CALAMARI_ENGINE = "calamari"
CALAMARI_MENU_NAME = "Calamari"


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


def is_calamari_config(config) -> bool:
    """Return whether this saved row is the Calamari line engine."""
    name = getattr(config, "name", None)
    if isinstance(name, str) and CALAMARI_ENGINE in name.casefold():
        return True
    metadata = getattr(config, "metadata", None)
    return isinstance(metadata, dict) and metadata.get("engine") == CALAMARI_ENGINE


def calamari_placeholder() -> dict:
    """Menu entry used only when no Calamari config row exists.

    It has no id and no endpoint. The images page must not request models
    or queue recognition for it. Calamari stays on the line contract.
    """
    return {
        "id": None,
        "name": CALAMARI_MENU_NAME,
        "engine": CALAMARI_ENGINE,
        "tier": "research",
        "experimental": True,
        "enabled": False,
        "available": False,
        "placeholder": True,
    }


def engine_menu(configs, rows) -> list:
    """Return the public menu, appending each missing line engine once."""
    listed = list(rows)
    if not any(is_pylaia_config(config) for config in configs):
        listed.append(pylaia_placeholder())
    if not any(is_calamari_config(config) for config in configs):
        listed.append(calamari_placeholder())
    return listed
