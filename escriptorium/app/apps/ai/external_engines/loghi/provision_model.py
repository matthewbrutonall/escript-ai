"""Download the pinned GLOBALISE Loghi-HTR SavedModel outside this repository.

The files stay out of git. ``licence.txt`` is written only after every
Dataverse SHA-1 matches. This does not load TensorFlow and does not predict.
"""
from __future__ import annotations

import argparse
import hashlib
import sys
import urllib.request
from pathlib import Path

DOI = "10.34894/HHM4TE"
LICENCE_TEXT = "CC-BY-4.0\n"
_FILES = (
    (579011, "charlist.txt", "04efd11861091b1ecd80022451adc6b3b8e70fb9"),
    (579012, "config.json", "c829221195c244d112ac3a047dd4a0e24df09524"),
    (579013, "fingerprint.pb", "ff8692704eec63ac83dd58d6e45a024cd59edea7"),
    (579014, "keras_metadata.pb", "abbf96b898eb34f0ae94e67e41338b31e17a34a6"),
    (579015, "metadata.json", "5d2aeabc418c60a4d841720ef8917f8ba399db21"),
    (579016, "saved_model.pb", "1d2d7b3e05bdd46a0a8a6b1f181ce226c930d196"),
    (579017, "variables/variables.data-00000-of-00001", "af20dda7edab5bc9abfe2e4bfae96d6e30a7325f"),
    (579018, "variables/variables.index", "e22fa024594f2a62fad5b6113d2636a34950e90a"),
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Install the pinned GLOBALISE model outside git.")
    parser.add_argument("destination", help="Empty or existing model directory. Not a git path.")
    args = parser.parse_args(argv)
    root = Path(args.destination)
    if not root.is_absolute():
        print("FAILED: destination", file=sys.stderr)
        return 1
    try:
        root.mkdir(parents=True, exist_ok=True)
        (root / "variables").mkdir(exist_ok=True)
        for file_id, name, expected in _FILES:
            target = root / name
            if target.is_file() and _sha1(target) == expected:
                continue
            _download(file_id, target)
            if _sha1(target) != expected:
                target.unlink(missing_ok=True)
                _drop_licence(root)
                print("FAILED: checksum", file=sys.stderr)
                return 1
        (root / "licence.txt").write_text(LICENCE_TEXT, encoding="utf-8")
    except Exception:
        _drop_licence(root)
        print("FAILED: download", file=sys.stderr)
        return 1
    print("OK")
    return 0


def _drop_licence(root: Path) -> None:
    licence = root / "licence.txt"
    if licence.is_file():
        licence.unlink(missing_ok=True)


def _download(file_id: int, target: Path) -> None:
    url = f"https://dataverse.nl/api/access/datafile/{file_id}"
    partial = target.with_name(target.name + ".partial")
    request = urllib.request.Request(url)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=120) as response, partial.open("wb") as handle:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            handle.write(chunk)
    partial.replace(target)


def _sha1(path: Path) -> str:
    digest = hashlib.sha1()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    sys.exit(main())
