"""Numbered/colour-key assignment health (ARCHITECTURE.md Phase 3)."""


def filled_keys(keys, text_by_key):
    return {
        k: (text_by_key.get(k) or "").strip()
        for k in keys
        if (text_by_key.get(k) or "").strip()
    }


def assignment_scrambled(keys, unknown_keys) -> bool:
    """Model invented as many (or more) keys as we asked for — mapping is junk."""
    expected = len(keys)
    if not expected:
        return True
    return bool(unknown_keys) and len(unknown_keys) >= max(1, expected // 2)


def crop_assignment_failed(keys, text_by_key, unknown_keys) -> bool:
    if assignment_scrambled(keys, unknown_keys):
        return True
    if not keys:
        return True
    filled = filled_keys(keys, text_by_key)
    return (len(filled) / len(keys)) < 0.5


def keys_to_stamp(keys, text_by_key, unknown_keys):
    """Stamp only trustworthy filled keys. Scrambled crops stamp nothing."""
    if assignment_scrambled(keys, unknown_keys):
        return {}
    return filled_keys(keys, text_by_key)
