"""Segmentation review parse + mapping. No Django, no mask writes."""
import unittest
from types import SimpleNamespace

from ai.dispatch import CrossDocumentError, assert_parts_belong
from ai.seg_review import (
    merge_geom_spurious, overlap_spurious_items, parse_review_json,
    suggestions_from_review,
)
from ai.seg_apply import planned_action
from ai.workflow import client_process


def _box(x0, y0, x1, y1):
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


class ParseReviewTests(unittest.TestCase):
    def test_parses_all_kinds(self):
        raw = """
        {"spurious": [{"n": 2, "reason": "dup of 1"}],
         "missed": [{"after_n": 3, "where": "margin", "reason": "unboxed"}],
         "order": [{"n": 5, "should_follow_n": 3, "reason": "column"}],
         "typology": [{"n": 1, "type": "heading", "reason": "title"}]}
        """
        p = parse_review_json(raw, n_lines=6)
        self.assertEqual(p["spurious"][0]["n"], 2)
        self.assertEqual(p["missed"][0]["where"], "margin")
        self.assertEqual(p["order"][0]["should_follow_n"], 3)
        self.assertEqual(p["typology"][0]["type"], "heading")

    def test_strips_fences_and_drops_out_of_range(self):
        raw = '```json\n{"spurious": [{"n": 99, "reason": "x"}]}\n```'
        p = parse_review_json(raw, n_lines=3)
        self.assertEqual(p["spurious"], [])

    def test_junk_returns_empty_lists(self):
        p = parse_review_json("not json", 4)
        self.assertEqual(p["spurious"], [])
        self.assertEqual(p["missed"], [])


class OverlapSpuriousTests(unittest.TestCase):
    def test_flags_smaller_of_overlapping_pair(self):
        masks = [
            _box(0, 0, 100, 40),
            _box(10, 5, 90, 35),
        ]
        flagged = overlap_spurious_items(masks, threshold=0.2)
        self.assertEqual([item["n"] for item in flagged], [2])
        self.assertIn("overlaps box 1", flagged[0]["reason"])
        self.assertEqual(flagged[0]["source"], "geometry")

    def test_leaves_side_by_side_lines(self):
        masks = [
            _box(0, 0, 100, 20),
            _box(0, 30, 100, 50),
        ]
        self.assertEqual(overlap_spurious_items(masks, threshold=0.2), [])

    def test_merge_keeps_geometry_and_extra_vlm(self):
        geom = [{"n": 2, "reason": "overlaps box 1", "source": "geometry"}]
        parsed = parse_review_json(
            '{"spurious":[{"n":2,"reason":"vlm dup"},{"n":5,"reason":"empty"}],'
            '"typology":[{"n":2,"type":"heading","reason":"x"},'
            '{"n":1,"type":"body","reason":"y"}]}',
            6)
        merged = merge_geom_spurious(geom, parsed)
        self.assertEqual(merged["spurious"][0]["source"], "geometry")
        self.assertEqual([s["n"] for s in merged["spurious"]], [2, 5])
        self.assertEqual([t["n"] for t in merged["typology"]], [1])


class SuggestionMapTests(unittest.TestCase):
    def test_maps_numbers_to_lines_without_touching_masks(self):
        lines = [SimpleNamespace(pk=10 + i, mask=[[0, i]]) for i in range(4)]
        parsed = parse_review_json(
            '{"spurious":[{"n":2,"reason":"junk"}],'
            '"missed":[{"after_n":1,"where":"left","reason":"gap"}]}',
            4)
        out = suggestions_from_review(lines, parsed)
        kinds = [x["kind"] for x in out]
        self.assertEqual(kinds, ["spurious", "missed"])
        self.assertEqual(out[0]["line"].pk, 11)
        self.assertEqual(out[0]["line"].mask, [[0, 1]])  # unchanged


class ApplyPlanTests(unittest.TestCase):
    def test_spurious_deletes_when_line_exists(self):
        self.assertEqual(planned_action("spurious", {}, True), "delete_line")
        self.assertEqual(planned_action("spurious", {}, False), "acknowledge")

    def test_typology_sets_type(self):
        self.assertEqual(
            planned_action("typology", {"type": "heading"}, True),
            ("set_type", "heading"),
        )
        self.assertEqual(
            planned_action("typology", {"type": "nope"}, True),
            "acknowledge",
        )

    def test_missed_and_order_are_ack_only(self):
        self.assertEqual(planned_action("missed", {}, True), "acknowledge")
        self.assertEqual(planned_action("order", {}, True), "acknowledge")


class WorkflowMapTests(unittest.TestCase):
    def test_seg_review_flashes_segment_icon(self):
        self.assertEqual(client_process("ai.tasks.ai_seg_review"), "segment")


class SegReviewDocumentGuardTests(unittest.TestCase):
    def test_mixed_parts_are_rejected(self):
        doc = SimpleNamespace(pk=10)
        parts = [
            SimpleNamespace(pk=1, document_id=10),
            SimpleNamespace(pk=2, document_id=99),
        ]
        with self.assertRaises(CrossDocumentError):
            assert_parts_belong(doc, parts)
