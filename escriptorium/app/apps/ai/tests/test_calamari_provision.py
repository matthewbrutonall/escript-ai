"""The model installer checks a local archive. It does not download one."""
import hashlib
import tarfile
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ai.external_engines.calamari.provision_model import (
    PINNED_BYTES,
    PINNED_COMMIT,
    PINNED_SHA256,
    PINNED_TAG,
    ProvisionError,
    install_archive,
)


class ProvisionTests(unittest.TestCase):
    def test_the_pin_is_the_recorded_release(self):
        self.assertEqual(PINNED_TAG, "2.2")
        self.assertEqual(PINNED_COMMIT, "308697f2fe2f0143d687107e6157688ea4116157")
        self.assertEqual(PINNED_BYTES, 30633782)
        self.assertEqual(
            PINNED_SHA256,
            "d3326df94559fda7b40dc6cbcbb5834ce2ec409346e405c103f6dadca3f090b7",
        )

    def test_a_checksum_mismatch_writes_nothing(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = _archive(root / "wrong.tar.gz")
            destination = root / "out" / "antiqua_historical"
            with self.assertRaises(ProvisionError) as caught:
                install_archive(archive, destination)
            self.assertEqual(caught.exception.code, "checksum")
            self.assertFalse(destination.exists())

    def test_a_matching_archive_writes_only_the_mit_label(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = _archive(root / "good.tar.gz")
            destination = root / "models" / "antiqua_historical"
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            installed = install_archive(
                archive,
                destination,
                expected_sha=digest,
                expected_bytes=archive.stat().st_size,
            )
            self.assertEqual((installed / "licence.txt").read_text(encoding="utf-8"), "MIT\n")
            self.assertTrue((installed / "0.ckpt.json").is_file())

    def test_a_path_outside_the_family_is_refused(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "escape.tar.gz"
            with tarfile.open(archive, "w:gz") as handle:
                payload = root / "payload.txt"
                payload.write_text("x", encoding="utf-8")
                handle.add(payload, arcname="../note.txt")
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            destination = root / "models" / "antiqua_historical"
            with self.assertRaises(ProvisionError) as caught:
                install_archive(
                    archive,
                    destination,
                    expected_sha=digest,
                    expected_bytes=archive.stat().st_size,
                )
            self.assertEqual(caught.exception.code, "unsafe_path")
            self.assertFalse((root / "note.txt").exists())
            self.assertFalse((destination / "licence.txt").exists())
            self.assertFalse(any(destination.iterdir()))

    def test_a_misnamed_destination_is_not_extracted(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = _archive(root / "good.tar.gz")
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            destination = root / "models" / "other"
            with self.assertRaises(ProvisionError) as caught:
                install_archive(
                    archive,
                    destination,
                    expected_sha=digest,
                    expected_bytes=archive.stat().st_size,
                )
            self.assertEqual(caught.exception.code, "archive")
            self.assertFalse(destination.exists())
            self.assertFalse((root / "models" / "antiqua_historical").exists())


def _archive(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(path, "w:gz") as handle:
        source = path.parent / "0.ckpt.json"
        source.write_text("{}", encoding="utf-8")
        handle.add(source, arcname="antiqua_historical/0.ckpt.json")
    return path


if __name__ == "__main__":
    unittest.main()
