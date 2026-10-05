"""Check a PyLaia model directory. This does not import PyLaia or run decode."""
from __future__ import annotations

import sys
from pathlib import Path

from ai.external_engines.pylaia.decoder import check_model_layout

_FAILED = {
    "missing_model",
    "missing_syms",
    "missing_checkpoint",
    "multiple_checkpoints",
    "unsafe_path",
}
_USAGE = "usage: python -m ai.external_engines.pylaia.check_model MODEL_DIR"


def main(argv: list[str] | None = None) -> int:
    """Print ``OK`` or ``FAILED`` plus a fixed code. The path is not printed."""
    args = sys.argv[1:] if argv is None else list(argv)
    if args in (["-h"], ["--help"]):
        print(_USAGE)
        return 0
    if len(args) != 1:
        print(_USAGE)
        return 2
    try:
        result = check_model_layout(Path(args[0]))
    except Exception:
        print("FAILED: unsafe_path")
        return 1
    if result.ok:
        print("OK")
        return 0
    code = result.code if result.code in _FAILED else "unsafe_path"
    print(f"FAILED: {code}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
