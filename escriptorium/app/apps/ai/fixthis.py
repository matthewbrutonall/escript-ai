"""Interactive 'fix this' re-read (ARCHITECTURE.md §10.2)."""
from __future__ import annotations

LINE_KEY = "text"

LINE_PROMPT = (
    "This is a crop of one manuscript line. "
    "Transcribe THIS line diplomatically: keep original spelling, "
    "do NOT expand abbreviations, do NOT modernise, do NOT add commentary. "
    'Return ONLY a JSON object: {"text": "..."}.'
)


def fix_prompt(partial="", conventions_block=""):
    parts = [LINE_PROMPT]
    guide = (partial or "").strip()
    if guide:
        parts.append(
            "The archivist started this correction; finish or correct it, "
            f"staying diplomatic: {guide!r}"
        )
    if conventions_block:
        parts.append(conventions_block)
    return "\n".join(parts)


def extract_line_text(result) -> str:
    text = (result.text_by_key.get(LINE_KEY) or "").strip()
    if text:
        return text
    raw = (getattr(result, "raw", None) or "").strip()
    if not raw or raw.startswith("{"):
        return ""
    return raw.splitlines()[0].strip()
