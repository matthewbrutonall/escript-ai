"""Phase 2: conventions, CER disagreement, layer gate. No Django, no network."""
import unittest
from types import SimpleNamespace

from ai.conventions import conventions_prompt, merge_conventions
from ai.gate import (
    ACK_PHRASE, LayerNotEligible, acknowledge_sample, assemble_sample_lines,
    assert_training_eligible, build_gate_sample, finalise_gate,
    mark_training_eligible,
)
from ai.pipeline import DEFAULT_PROMPT, build_prompt
from ai.triage import (
    levenshtein, normalised_cer, pick_random_sample, pick_sample, sample_size,
)


class ConventionsTests(unittest.TestCase):
    def test_defaults_forbid_modernising(self):
        text = conventions_prompt({})
        self.assertIn("do NOT modernise", text)
        self.assertIn("do NOT expand", text)
        self.assertIn("long s", text)

    def test_expand_abbreviations_toggle(self):
        text = conventions_prompt({"expand_abbreviations": True})
        self.assertIn("Expand abbreviations", text)
        self.assertNotIn("do NOT expand", text)

    def test_unknown_keys_ignored(self):
        merged = merge_conventions({"expand_abbreviations": True, "nope": 1})
        self.assertTrue(merged["expand_abbreviations"])
        self.assertNotIn("nope", merged)

    def test_build_prompt_appends_conventions_and_keys(self):
        config = SimpleNamespace(prompt_template=DEFAULT_PROMPT, conventions={})
        prompt = build_prompt(config, ["red", "blue"])
        self.assertIn("red", prompt)
        self.assertIn("Conventions:", prompt)
        self.assertIn("do NOT modernise", prompt)


class CerTests(unittest.TestCase):
    def test_identical_is_zero(self):
        self.assertEqual(normalised_cer("premisses", "premisses"), 0.0)

    def test_modernisation_is_nonzero(self):
        cer = normalised_cer("premises", "premisses")
        self.assertGreater(cer, 0)
        self.assertLess(cer, 0.5)

    def test_empty_reference_uses_denom_one(self):
        self.assertEqual(normalised_cer("hello", ""), 5.0)

    def test_levenshtein_insert_delete(self):
        self.assertEqual(levenshtein("kitten", "sitting"), 3)


class SampleTests(unittest.TestCase):
    def test_small_document_reviews_all(self):
        self.assertEqual(sample_size(10), 10)
        self.assertEqual(sample_size(50), 50)

    def test_large_document_uses_two_percent_floor_fifty(self):
        self.assertEqual(sample_size(10000), 200)

    def test_pick_sample_prefers_high_cer(self):
        rows = [
            {"line_pk": i, "cer": cer}
            for i, cer in enumerate([0.0, 0.1, 0.9, 0.05, 0.8, 0.2])
        ]
        pks = pick_sample(rows, min_n=4, pct=0.5)
        self.assertEqual(len(pks), 4)
        self.assertIn(2, pks)
        self.assertIn(4, pks)

    def test_random_sample_small_doc_takes_all(self):
        pks = pick_random_sample(list(range(10)), min_n=50, pct=0.02)
        self.assertEqual(sorted(pks), list(range(10)))


class GateTests(unittest.TestCase):
    def test_finalise_sets_mean_and_sample(self):
        rows = [{"line_pk": i, "cer": 0.1 * i} for i in range(5)]
        out = finalise_gate(None, rows, min_n=3, pct=0.5)
        self.assertEqual(len(out["sample_line_pks"]), 3)
        self.assertAlmostEqual(out["mean_cer"], 0.2)

    def test_raw_layer_not_trainable(self):
        gate = SimpleNamespace(state="raw")
        with self.assertRaises(LayerNotEligible):
            assert_training_eligible(SimpleNamespace(), gate=gate)

    def test_manual_layer_without_gate_is_trainable(self):
        assert_training_eligible(SimpleNamespace(ai_gate=None), gate=None)

    def test_acknowledge_requires_phrase(self):
        gate = SimpleNamespace(sample_line_pks=[1], state="raw")
        with self.assertRaises(ValueError):
            acknowledge_sample(gate, phrase="ok")
        acknowledge_sample(gate, phrase=ACK_PHRASE)
        self.assertEqual(gate.state, "sampled")

    def test_training_eligible_only_after_sample(self):
        gate = SimpleNamespace(state="raw", sample_line_pks=[1])
        with self.assertRaises(LayerNotEligible):
            mark_training_eligible(gate)
        acknowledge_sample(gate, phrase=ACK_PHRASE)
        mark_training_eligible(gate)
        self.assertEqual(gate.state, "training-eligible")
        assert_training_eligible(SimpleNamespace(), gate=gate)


class NoComparisonGateTests(unittest.TestCase):
    def test_no_comparison_sample_from_written_lines(self):
        written = list(range(1, 11))
        out = build_gate_sample(
            rows=[], written_pks=written, comparison=None, min_n=50, pct=0.02)
        self.assertEqual(sorted(out["sample_line_pks"]), written)
        self.assertIsNone(out["mean_cer"])
        self.assertFalse(out["write_disagreements"])

    def test_no_comparison_can_acknowledge_and_become_eligible(self):
        out = build_gate_sample(
            rows=[], written_pks=[1, 2, 3], comparison=None, min_n=50)
        gate = SimpleNamespace(
            state="raw", sample_line_pks=out["sample_line_pks"])
        with self.assertRaises(LayerNotEligible):
            assert_training_eligible(SimpleNamespace(), gate=gate)
        acknowledge_sample(gate, phrase=ACK_PHRASE)
        self.assertEqual(gate.state, "sampled")
        mark_training_eligible(gate)
        self.assertEqual(gate.state, "training-eligible")
        assert_training_eligible(SimpleNamespace(), gate=gate)

    def test_assemble_sample_prefers_disagreement_rows(self):
        lines = assemble_sample_lines(
            [1, 2],
            disagreements_by_line={1: {"ai_text": "a", "comparison_text": "b", "cer": 0.5}},
            ai_text_by_line={2: "only-ai"},
        )
        self.assertEqual(lines[0]["cer"], 0.5)
        self.assertEqual(lines[1]["ai_text"], "only-ai")
        self.assertIsNone(lines[1]["cer"])

    def test_comparison_path_still_writes_disagreements(self):
        rows = [{"line_pk": i, "cer": 0.1 * i} for i in range(5)]
        out = build_gate_sample(
            rows, written_pks=[0, 1, 2, 3, 4], comparison=object(), min_n=3)
        self.assertTrue(out["write_disagreements"])
        self.assertIsNotNone(out["mean_cer"])
