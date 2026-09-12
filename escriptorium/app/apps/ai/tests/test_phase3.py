"""Phase 3: assignment failure, Passim-style align, fix-this prompt. No Django."""
import unittest
from types import SimpleNamespace

from ai.assignment import (
    assignment_scrambled, crop_assignment_failed, filled_keys, keys_to_stamp,
)
from ai.fixthis import LINE_KEY, extract_line_text, fix_prompt
from ai.passim_fallback import align_witness_to_lines


class AssignmentTests(unittest.TestCase):
    def test_half_empty_is_failure(self):
        keys = ["red", "blue", "green", "orange"]
        text = {"red": "one", "blue": ""}
        self.assertTrue(crop_assignment_failed(keys, text, set()))

    def test_mostly_filled_is_ok(self):
        keys = ["red", "blue", "green"]
        text = {"red": "a", "blue": "b", "green": "c"}
        self.assertFalse(crop_assignment_failed(keys, text, set()))

    def test_invented_keys_scramble(self):
        keys = ["red", "blue"]
        unknown = {"cyan", "lime"}
        self.assertTrue(assignment_scrambled(keys, unknown))
        self.assertEqual(keys_to_stamp(keys, {"red": "x"}, unknown), {})

    def test_stamp_skips_blanks_when_not_scrambled(self):
        keys = ["red", "blue"]
        stamped = keys_to_stamp(keys, {"red": "hello", "blue": "  "}, set())
        self.assertEqual(stamped, {"red": "hello"})

    def test_filled_keys_strips(self):
        self.assertEqual(
            filled_keys(["red"], {"red": "  hi  "}),
            {"red": "hi"},
        )


class PassimFallbackTests(unittest.TestCase):
    def test_aligns_witness_onto_ocr_lines(self):
        witness = "The morning the letter was written to the bishop"
        ocr = [
            (1, "The morning the letter"),
            (2, "was written to the bishop"),
        ]
        out = align_witness_to_lines(witness, ocr)
        self.assertIn(1, out)
        self.assertIn(2, out)
        self.assertTrue(out[1].lower().startswith("the morning"))

    def test_unmatched_ocr_is_omitted_not_filled_from_kraken(self):
        witness = "alpha beta"
        ocr = [
            (1, "alpha beta"),
            (2, "zzzzzzzz not in witness at all"),
        ]
        out = align_witness_to_lines(witness, ocr, threshold=0.6)
        self.assertEqual(out.get(1), "alpha beta")
        self.assertNotIn(2, out)

    def test_empty_witness_aligns_nothing(self):
        self.assertEqual(align_witness_to_lines("", [(1, "hello")]), {})

    def test_never_returns_ocr_when_witness_missing(self):
        out = align_witness_to_lines("completely different words here",
                                     [(9, "q̃ p̃ ⁊")], threshold=0.8)
        self.assertNotIn(9, out)


class FixThisTests(unittest.TestCase):
    def test_prompt_includes_partial(self):
        p = fix_prompt("The mor")
        self.assertIn("The mor", p)
        self.assertIn("JSON", p)

    def test_prompt_without_partial_still_asks_for_one_line(self):
        p = fix_prompt("")
        self.assertIn("one manuscript line", p)
        self.assertNotIn("archivist started", p)

    def test_extract_prefers_json_text_key(self):
        result = SimpleNamespace(text_by_key={LINE_KEY: "  hello  "}, raw="")
        self.assertEqual(extract_line_text(result), "hello")

    def test_extract_plain_raw_when_json_empty(self):
        result = SimpleNamespace(text_by_key={}, raw="just a line\nignored")
        self.assertEqual(extract_line_text(result), "just a line")

    def test_extract_ignores_raw_json_blob(self):
        result = SimpleNamespace(text_by_key={}, raw='{"nope": 1}')
        self.assertEqual(extract_line_text(result), "")
