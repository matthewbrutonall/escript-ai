"""Install one checked Calamari model archive outside this repository.

The archive must already be on disk. This module does not download it and
does not import the recognition runtime. A hash mismatch writes nothing.
"""
from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
import tarfile
import tempfile
from pathlib import Path

PINNED_SHA256 = "d3326df94559fda7b40dc6cbcbb5834ce2ec409346e405c103f6dadca3f090b7"
PINNED_BYTES = 30633782
PINNED_TAG = "2.2"
PINNED_COMMIT = "308697f2fe2f0143d687107e6157688ea4116157"
LICENCE_LABEL = "MIT"
_FAMILY = "antiqua_historical"


class ProvisionError(Exception):
    """The archive was refused. The code is a fixed token."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def archive_matches(path: Path, expected_sha: str = PINNED_SHA256, expected_bytes: int = PINNED_BYTES) -> bool:
    """Return true when the file has the pinned size and sha256."""
    if not path.is_file() or path.is_symlink():
        return False
    if path.stat().st_size != expected_bytes:
        return False
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest() == expected_sha


def install_archive(
    archive: Path,
    destination: Path,
    *,
    expected_sha: str = PINNED_SHA256,
    expected_bytes: int = PINNED_BYTES,
) -> Path:
    """Extract a matching archive and write ``licence.txt``.

    ``destination`` is the family directory. It is created when missing and
    must not already contain a checkpoint. The return value is that directory.
    """
    if not archive_matches(archive, expected_sha, expected_bytes):
        raise ProvisionError("checksum")
    if not destination.is_absolute() or ".." in destination.parts:
        raise ProvisionError("unsafe_path")
    if destination.name != _FAMILY:
        raise ProvisionError("archive")
    destination.mkdir(parents=True, exist_ok=True)
    if any(destination.iterdir()):
        raise ProvisionError("destination_not_empty")
    try:
        with tempfile.TemporaryDirectory(dir=destination.parent) as staging:
            staging_path = Path(staging)
            with tarfile.open(archive, "r:gz") as handle:
                members = _members(handle)
                extract_kwargs = {"members": members}
                if hasattr(tarfile, "data_filter"):
                    extract_kwargs["filter"] = "data"
                handle.extractall(staging_path, **extract_kwargs)
            extracted = staging_path / _FAMILY
            if not extracted.is_dir():
                raise ProvisionError("archive")
            for child in extracted.iterdir():
                target = destination / child.name
                if target.exists():
                    raise ProvisionError("destination_not_empty")
                shutil.move(str(child), str(target))
    except ProvisionError:
        raise
    except (OSError, tarfile.TarError):
        raise ProvisionError("archive") from None
    label = destination / "licence.txt"
    label.write_text(LICENCE_LABEL + "\n", encoding="utf-8")
    return destination


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Install the pinned model archive.")
    parser.add_argument("--check", action="store_true", help="Verify the archive and write nothing.")
    parser.add_argument("archive")
    parser.add_argument("destination", nargs="?")
    args = parser.parse_args(argv)
    if args.check:
        if args.destination is not None or not archive_matches(Path(args.archive)):
            print("FAILED: checksum", file=sys.stderr)
            return 2
        print("OK")
        return 0
    if args.destination is None:
        print("FAILED: archive", file=sys.stderr)
        return 2
    try:
        install_archive(Path(args.archive), Path(args.destination))
    except ProvisionError as exc:
        print("FAILED: %s" % exc.code, file=sys.stderr)
        return 2
    print("OK")
    return 0


def _members(handle: tarfile.TarFile) -> list[tarfile.TarInfo]:
    members = []
    prefix = _FAMILY + "/"
    for member in handle.getmembers():
        name = member.name
        path = Path(name)
        if path.is_absolute() or ".." in path.parts:
            raise ProvisionError("unsafe_path")
        if name != _FAMILY and not name.startswith(prefix):
            raise ProvisionError("archive")
        if member.issym() or member.islnk():
            raise ProvisionError("unsafe_path")
        members.append(member)
    if not members:
        raise ProvisionError("archive")
    return members


if __name__ == "__main__":
    raise SystemExit(main())
