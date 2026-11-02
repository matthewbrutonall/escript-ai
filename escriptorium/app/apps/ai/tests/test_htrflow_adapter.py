"""HTRflow import planner. No Django, no network, no transcription write."""
import ast
import unittest
from pathlib import Path

from ai.htrflow_adapter import (
    CODE_INVALID,
    CODE_LINE,
    CODE_MISMATCH,
    CODE_READY,
    HTRFLOW_INTEGRATED,
    plan_htrflow_import,
)

MODULE = Path(__file__).resolve().parents[1] / "htrflow_adapter.py"


class PlannerTests(unittest.TestCase):
    def test_matching_page_keeps_empty_text_and_skips_unknown_lines(self):
        plan = plan_htrflow_import(
            {
                "document_id": 4,
                "part_id": 9,
                "regions": [
                    {
                        "lines": [
                            {"line_id": "20", "text": "", "reading_order": 2},
                            {"line_id": "10", "text": "kept", "reading_order": 0},
                            {"line_id": "missing", "text": "nope", "reading_order": 1},
                            {"line_id": "10", "text": "again", "reading_order": 3},
                        ],
                    },
                ],
            },
            4,
            9,
            ["10", "20"],
        )
        self.assertTrue(plan.allowed)
        self.assertEqual(plan.code, CODE_READY)
        self.assertEqual(
            [(line.line_id, line.text, line.reading_order) for line in plan.lines],
            [("10", "kept", 0), ("20", "", 2)],
        )
        self.assertEqual(
            [(line.line_id, line.reason) for line in plan.skipped],
            [("missing", CODE_LINE), ("10", CODE_LINE)],
        )

    def test_page_mismatch_contributes_no_lines(self):
        plan = plan_htrflow_import(
            {
                "document_id": 5,
                "part_id": 9,
                "regions": [{"lines": [{"line_id": "10", "text": "kept", "reading_order": 0}]}],
            },
            4,
            9,
            ["10"],
        )
        self.assertFalse(plan.allowed)
        self.assertEqual(plan.code, CODE_MISMATCH)
        self.assertEqual(plan.lines, ())
        self.assertEqual(plan.skipped, ())

    def test_bad_payload_is_refused(self):
        plan = plan_htrflow_import({"document_id": 4, "part_id": 9, "regions": {}}, 4, 9, ["10"])
        self.assertFalse(plan.allowed)
        self.assertEqual(plan.code, CODE_INVALID)
        self.assertEqual(plan.lines, ())

    def test_module_does_not_call_an_engine_or_claim_integration(self):
        self.assertIs(HTRFLOW_INTEGRATED, False)
        text = MODULE.read_text(encoding="utf-8")
        self.assertIn("HTRflow is not integrated", text)
        tree = ast.parse(text)
        modules = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                modules.append(node.module)
            elif isinstance(node, ast.Import):
                modules.extend(alias.name for alias in node.names)
        joined = " ".join(modules)
        for banned in ("django", "urllib", "requests", "socket", "celery", "htr_engine_client"):
            self.assertNotIn(banned, joined, banned)
        lowered = text.lower()
        for name in ("calamari", "loghi", "trocr", "drethtr", "laypa"):
            self.assertNotIn(name, lowered, name)


if __name__ == "__main__":
    unittest.main()
