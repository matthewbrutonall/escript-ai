"""Recognize-request builder. No Django, no network, no transcription write."""
import ast
import json
import unittest
from pathlib import Path

from ai.external_htr_request import (
    SKIP_BAD_IMAGE,
    SKIP_BAD_LINE_ID,
    SKIP_DUPLICATE,
    SKIP_LINE_CAP,
    SKIP_NO_BASELINE,
    SKIP_NO_IMAGE,
    SKIP_NO_MASK,
    SKIP_PAYLOAD_CAP,
    build_recognize_request,
)
from ai.htr_engine_contract import ContractError, parse_recognize_request

AI_DIR = Path(__file__).resolve().parents[1]

MASK = [[0, 0], [10, 0], [10, 4], [0, 4]]
BASELINE = [[0, 2], [10, 2]]
IMAGE = "AAAA"


class _Line:
    def __init__(self, pk, mask=MASK, baseline=BASELINE, **extra):
        self.pk = pk
        self.mask = mask
        self.baseline = baseline
        for key, value in extra.items():
            setattr(self, key, value)


def _encode(line):
    return IMAGE


def _build(lines, encode_line=_encode, **overrides):
    kwargs = {
        "job_id": "job-1",
        "document_id": 7,
        "part_id": 8,
        "engine": "pylaia",
        "model_id": "example-model",
        "encode_line": encode_line,
        "preprocessing": {"grayscale": True},
    }
    kwargs.update(overrides)
    return build_recognize_request(lines, **kwargs)


class BuildTests(unittest.TestCase):
    def test_two_lines_make_a_valid_request(self):
        calls = []

        def encode(line):
            calls.append(line.pk)
            return IMAGE

        built = _build([_Line(10), _Line(20)], encode_line=encode)
        parsed = parse_recognize_request(built.request)
        self.assertEqual([line.line_id for line in parsed.lines], ["10", "20"])
        self.assertEqual(calls, [10, 20])
        self.assertEqual(built.request["job_id"], "job-1")
        self.assertEqual(built.request["document_id"], 7)
        self.assertEqual(built.request["part_id"], 8)
        self.assertEqual(built.request["engine"], "pylaia")
        self.assertEqual(built.request["model_id"], "example-model")
        self.assertEqual(built.request["preprocessing"], {"grayscale": True})
        self.assertEqual(built.skipped, ())
        self.assertEqual(
            [line["line_id"] for line in built.request["lines"]],
            ["10", "20"],
        )

    def test_order_survives_a_skipped_line_between_them(self):
        built = _build([_Line(10), _Line(11, mask=None), _Line(12)])
        self.assertEqual(
            [line["line_id"] for line in built.request["lines"]],
            ["10", "12"],
        )
        self.assertEqual(
            [(item.line_id, item.reason) for item in built.skipped],
            [("11", SKIP_NO_MASK)],
        )

    def test_unusable_lines_are_skipped_and_not_encoded(self):
        calls = []
        lines = [
            _Line(1),
            _Line(2, mask=None),
            _Line(3, mask=[]),
            _Line(4, mask=[[0, 0], [1, 0], [2, 0]]),
            _Line(5, baseline=None),
            _Line(6, baseline=[[0, 0]]),
            _Line(True),
            _Line(-1),
            _Line(1),
            _Line(7),
            _Line(8),
        ]

        def encode_some(line):
            calls.append(line.pk)
            if line.pk == 7:
                return None
            if line.pk == 8:
                return "data:image/png;base64,AAAA"
            return IMAGE

        built = _build(lines, encode_line=encode_some)
        self.assertEqual([line["line_id"] for line in built.request["lines"]], ["1"])
        self.assertEqual(calls, [1, 7, 8])
        reasons = [(item.line_id, item.reason) for item in built.skipped]
        self.assertEqual(reasons, [
            ("2", SKIP_NO_MASK),
            ("3", SKIP_NO_MASK),
            ("4", SKIP_NO_MASK),
            ("5", SKIP_NO_BASELINE),
            ("6", SKIP_NO_BASELINE),
            (None, SKIP_BAD_LINE_ID),
            (None, SKIP_BAD_LINE_ID),
            ("1", SKIP_DUPLICATE),
            ("7", SKIP_NO_IMAGE),
            ("8", SKIP_BAD_IMAGE),
        ])
        text = json.dumps(built.request)
        self.assertNotIn("data:", text)

    def test_preprocessing_is_parsed_into_the_request(self):
        built = _build([_Line(1)], preprocessing={
            "grayscale": False,
            "line_height": 64,
            "params": {"deskew": ["a", 1]},
        })
        parsed = parse_recognize_request(built.request)
        self.assertEqual(parsed.preprocessing.grayscale, False)
        self.assertEqual(parsed.preprocessing.line_height, 64)
        self.assertEqual(parsed.preprocessing.params, (("deskew", ("a", 1)),))
        self.assertEqual(built.request["preprocessing"]["params"]["deskew"], ["a", 1])

    def test_unknown_preprocessing_does_not_encode(self):
        calls = []

        def encode(line):
            calls.append(line.pk)
            return IMAGE

        with self.assertRaises(ContractError) as caught:
            _build([_Line(1)], encode_line=encode, preprocessing={"colour": True})
        self.assertEqual(caught.exception.code, "invalid_request")
        self.assertEqual(calls, [])

    def test_line_cap_skips_later_usable_lines_without_encoding(self):
        calls = []

        def encode(line):
            calls.append(line.pk)
            return IMAGE

        built = _build(
            [_Line(1), _Line(2, mask=None), _Line(3)],
            encode_line=encode,
            max_lines=1,
        )
        self.assertEqual([line["line_id"] for line in built.request["lines"]], ["1"])
        self.assertEqual(calls, [1])
        self.assertEqual(
            [(item.line_id, item.reason) for item in built.skipped],
            [("2", SKIP_NO_MASK), ("3", SKIP_LINE_CAP)],
        )

    def test_payload_cap_keeps_the_fitting_line_only(self):
        calls = []
        wide = "AAAA" * 20

        def encode(line):
            calls.append(line.pk)
            return IMAGE if line.pk == 1 else wide

        first = _build([_Line(1)], encode_line=encode)
        cap = len(json.dumps(first.request, separators=(",", ":")).encode("utf-8"))
        built = _build([_Line(1), _Line(2), _Line(3)], encode_line=encode, max_payload_bytes=cap)
        self.assertEqual([line["line_id"] for line in built.request["lines"]], ["1"])
        self.assertEqual(calls, [1, 1, 2])
        self.assertEqual(
            [(item.line_id, item.reason) for item in built.skipped],
            [("2", SKIP_PAYLOAD_CAP), ("3", SKIP_PAYLOAD_CAP)],
        )
        self.assertNotIn(wide, json.dumps(built.request))
        self.assertNotIn(wide, json.dumps([(item.line_id, item.reason) for item in built.skipped]))

    def test_all_skipped_lines_do_not_produce_a_request(self):
        built = _build([_Line(1, mask=None)])
        self.assertIsNone(built.request)
        self.assertEqual(built.skipped[0].reason, SKIP_NO_MASK)

    def test_document_metadata_is_not_copied(self):
        line = _Line(
            4,
            title="Secret Title",
            path="/tmp/secret-document",
            metadata={"token": "secret-token"},
            name="folio-name",
        )
        built = _build([line], document_id=7, part_id=8)
        text = json.dumps(built.request)
        for leaked in ("Secret Title", "/tmp/secret-document", "secret-token", "folio-name"):
            self.assertNotIn(leaked, text)
        self.assertEqual(set(built.request), {
            "api_version", "job_id", "document_id", "part_id", "engine",
            "model_id", "preprocessing", "lines",
        })
        self.assertEqual(set(built.request["lines"][0]), {
            "line_id", "image", "baseline", "mask",
        })


class IsolationTests(unittest.TestCase):
    def test_builder_does_not_import_jobs_or_the_client(self):
        text = (AI_DIR / "external_htr_request.py").read_text()
        tree = ast.parse(text)
        modules = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.extend(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.append(node.module.split(".")[0])
        self.assertEqual(modules, [
            "__future__", "json", "math", "re", "collections", "dataclasses", "ai",
        ])
        for banned in (
            "htr_engine_client",
            "recognize_lines",
            "LineTranscription",
            "ai_transcribe",
            "ExternalHTREngineConfig",
            "urllib",
            "requests",
            "celery",
            "socket",
            "overlay",
        ):
            self.assertNotIn(banned, text)

    def test_stage1_modules_do_not_import_the_builder(self):
        for name in (
            "tasks.py",
            "views.py",
            "serializers.py",
            "pipeline.py",
            "backends.py",
            "dispatch.py",
            "models.py",
            "admin.py",
        ):
            text = (AI_DIR / name).read_text()
            self.assertNotIn("external_htr_request", text, name)
            self.assertNotIn("build_recognize_request", text, name)


if __name__ == "__main__":
    unittest.main()
