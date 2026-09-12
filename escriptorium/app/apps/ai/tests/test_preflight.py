"""Pre-flight guards — no Django, no network."""
import unittest
from pathlib import Path

from ai.preflight import (
    FRAGMENT_WIDTH_RATIO,
    OVERLAP_THRESHOLD,
    evaluate_crop,
    is_degenerate,
    max_overlap,
)
from ai.tests.alto_masks import load_alto_masks

REPO = Path(__file__).resolve().parents[5]
HARD_SEG = REPO / "phase0-spike" / "_hard_seg.xml"


def box(x0, y0, x1, y1):
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


class OverlapTests(unittest.TestCase):
    def test_disjoint_ok(self):
        d = evaluate_crop([box(0, 0, 100, 20), box(0, 30, 100, 50)])
        self.assertTrue(d.send)
        self.assertEqual(d.keep, [0, 1])
        self.assertEqual(d.drop, [])

    def test_heavy_overlap_blocks_whole_crop(self):
        # Two boxes sharing most of their area — the HTRflow failure mode.
        d = evaluate_crop([box(0, 0, 100, 40), box(10, 5, 110, 45)])
        self.assertGreater(max_overlap([box(0, 0, 100, 40), box(10, 5, 110, 45)]),
                           OVERLAP_THRESHOLD)
        self.assertFalse(d.send)
        self.assertIn("overlap", d.reason)
        self.assertEqual(d.keep, [])
        self.assertEqual([why for _, why in d.drop], ["overlap", "overlap"])

    def test_slanted_parallel_masks_with_overlapping_bboxes_are_ok(self):
        # Dense cursive can stack axis-aligned bounding boxes while the actual
        # kraken polygons remain separate. This must not skip the crop.
        upper = [(0, 0), (120, 10), (120, 20), (0, 10)]
        lower = [(0, 15), (120, 25), (120, 35), (0, 25)]
        self.assertLessEqual(max_overlap([upper, lower]), OVERLAP_THRESHOLD)
        d = evaluate_crop([upper, lower])
        self.assertTrue(d.send, d.reason)
        self.assertEqual(d.keep, [0, 1])
        self.assertEqual(d.drop, [])

    def test_junk_fragments_do_not_skip_full_lines(self):
        # Modern Hindi part 8 crop 2: 9px / 69px junk on top of real lines
        # made bbox/polygon overlap fire and skipped the whole crop.
        junk = box(400, 12, 409, 33)
        leftover = box(0, 10, 69, 39)
        full = [
            box(0, 40, 419, 76),
            box(0, 80, 484, 119),
            box(0, 120, 483, 155),
            box(0, 160, 484, 197),
        ]
        d = evaluate_crop([leftover, junk] + full)
        self.assertTrue(d.send, d.reason)
        self.assertEqual(d.keep, [2, 3, 4, 5])
        self.assertEqual([why for _, why in d.drop], ["fragment", "fragment"])


class DegenerateTests(unittest.TestCase):
    def test_hairline_is_degenerate(self):
        self.assertTrue(is_degenerate(box(0, 0, 400, 4)))

    def test_ruling_aspect_is_degenerate(self):
        self.assertTrue(is_degenerate(box(0, 0, 2000, 20)))  # aspect 100

    def test_normal_line_is_not(self):
        self.assertFalse(is_degenerate(box(0, 0, 800, 40)))

    def test_hairline_dropped_sibling_still_sent(self):
        d = evaluate_crop([box(0, 0, 800, 40), box(0, 50, 800, 54)])
        self.assertTrue(d.send)
        self.assertEqual(d.keep, [0])
        self.assertEqual(d.drop, [(1, "degenerate")])


class FragmentTests(unittest.TestCase):
    def test_short_leftover_dropped_wide_sibling_kept(self):
        # 1866-shaped: 208px leftover next to 1711px line (ratio 0.12 < 0.15).
        leftover = box(0, 0, 208, 56)
        full = box(0, 60, 1711, 125)
        self.assertLess(208 / 1711, FRAGMENT_WIDTH_RATIO)
        d = evaluate_crop([leftover, full])
        self.assertTrue(d.send)
        self.assertEqual(d.keep, [1])
        self.assertEqual(d.drop, [(0, "fragment")])

    def test_single_short_line_crop_is_sent(self):
        d = evaluate_crop([box(0, 0, 80, 30)])
        self.assertTrue(d.send)
        self.assertEqual(d.keep, [0])


class HardSegFixtureTests(unittest.TestCase):
    """The page that produced the hallucination — guards must act on it."""

    @classmethod
    def setUpClass(cls):
        if not HARD_SEG.exists():
            raise unittest.SkipTest(f"missing fixture {HARD_SEG}")
        cls.masks = load_alto_masks(HARD_SEG)

    def test_fourteen_lines(self):
        self.assertEqual(len(self.masks), 14)

    def test_crop2_drops_the_leftover_keeps_the_body(self):
        crop = self.masks[12:14]   # leftover 208px + full-width last line
        d = evaluate_crop(crop)
        self.assertTrue(d.send, d.reason)
        self.assertEqual(d.keep, [1], "must still send the full-width last line")
        self.assertEqual(d.drop, [(0, "fragment")])

    def test_crop2_is_not_an_overlap_fail(self):
        self.assertLessEqual(max_overlap(self.masks[12:14]), OVERLAP_THRESHOLD)

    def test_body_crops_pass_clean(self):
        for start in (0, 6):
            d = evaluate_crop(self.masks[start:start + 6])
            # crop 0 contains the narrow "Day" header (141px vs ~2000) → fragment
            if start == 0:
                self.assertTrue(d.send)
                self.assertIn(0, [i for i, _ in d.drop])  # Day
                self.assertIn(2, d.keep)  # first body line
            else:
                self.assertTrue(d.send, d.reason)
                self.assertEqual(d.drop, [], d)


if __name__ == "__main__":
    unittest.main()
