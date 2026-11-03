"""Check a Calamari model directory. This does not import Calamari or predict."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ai.external_engines.calamari.predictor import LayoutError, inspect_model_dir

_CODES = frozenset({
    "missing_checkpoint",
    "missing_weights",
    "invalid_json",
    "model_id",
    "unsafe_path",
})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check a Calamari model directory. Does not predict.",
    )
    parser.add_argument("model_dir")
    args = parser.parse_args(argv)
    try:
        report = inspect_model_dir(Path(args.model_dir))
    except LayoutError as exc:
        print(f"FAILED: {_code(exc.code)}", file=sys.stderr)
        return 1
    except Exception:
        print("FAILED: unsafe_path", file=sys.stderr)
        return 1
    if not report.checkpoints:
        print(f"FAILED: {_code(report.code)}", file=sys.stderr)
        return 1
    print("OK")
    return 0


def _code(value: str) -> str:
    if value in _CODES:
        return value
    return "unsafe_path"


if __name__ == "__main__":
    sys.exit(main())
