"""Check a Loghi-HTR model directory. This does not import TensorFlow or predict."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ai.external_engines.loghi.layout import LayoutError, inspect_model_dir

_CODES = frozenset({
    "missing_file",
    "invalid_config",
    "channel_mismatch",
    "missing_licence",
    "checksum_mismatch",
    "savedmodel_refused",
    "unsafe_path",
})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check a Loghi-HTR model directory. Does not predict.",
    )
    parser.add_argument("model_dir")
    args = parser.parse_args(argv)
    path = Path(args.model_dir)
    try:
        inspect_model_dir(path)
    except LayoutError as exc:
        code = exc.code if exc.code in _CODES else "unsafe_path"
        print(f"FAILED: {code}", file=sys.stderr)
        return 1
    except Exception:
        print("FAILED: unsafe_path", file=sys.stderr)
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
