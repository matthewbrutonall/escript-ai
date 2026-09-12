"""Phase 2: conventions, CER disagreement, layer gate. No Django, no network."""
import unittest
from types import SimpleNamespace

from ai.conventions import conventions_prompt, merge_conventions
from ai.gate import (
    ACK_PHRASE, LayerNotEligible, acknowledge_sample, assemble_sample_lines,
    assert_training_eligible, build_gate_sample, finalise_gate,
    mark_training_eligible,
)
from ai.compare import disagreement_rows_from_layers, find_cheap_recognizer
from ai.fewshot import fewshot_prompt_block, select_examples
from ai.ketos_hook import (
    eligible_collection_pairs, kept_training_parts, ketos_argv, parse_ketos_cer,
    reserve_held_out_parts, run_ketos_test,
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


class FewshotTests(unittest.TestCase):
    def test_pinned_before_recent(self):
        picked = select_examples([
            {"text": "old", "pinned": False, "created_at": 2},
            {"text": "gold", "pinned": True, "created_at": 1},
            {"text": "new", "pinned": False, "created_at": 3},
        ], cap=2)
        self.assertEqual([e["text"] for e in picked], ["gold", "new"])

    def test_prompt_block_empty_without_examples(self):
        self.assertEqual(fewshot_prompt_block([]), "")

    def test_build_prompt_includes_examples(self):
        config = SimpleNamespace(prompt_template=DEFAULT_PROMPT, conventions={})
        prompt = build_prompt(
            config, ["red"],
            examples=[{"text": "premisses", "pinned": True}])
        self.assertIn("premisses", prompt)
        self.assertIn("Corrected examples", prompt)


class ComparePassTests(unittest.TestCase):
    def test_prefers_public_recognizer(self):
        class QS:
            def __init__(self, items):
                self.items = items

            def filter(self, **kwargs):
                out = self.items
                if "job" in kwargs:
                    out = [i for i in out if i.job == kwargs["job"]]
                if "public" in kwargs:
                    out = [i for i in out if i.public is kwargs["public"]]
                return QS(out)

            def first(self):
                return self.items[0] if self.items else None

        private = SimpleNamespace(job=2, public=False, name="priv")
        public = SimpleNamespace(job=2, public=True, name="pub")
        self.assertEqual(
            find_cheap_recognizer(QS([private, public])).name, "pub")

    def test_disagreement_rows_skip_empty_ai(self):
        rows = disagreement_rows_from_layers(
            [1, 2], {1: "", 2: "hello"}, {2: "hallo"}, normalised_cer)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["line_pk"], 2)
        self.assertGreater(rows[0]["cer"], 0)


class KetosHookTests(unittest.TestCase):
    def test_too_few_parts_holds_none(self):
        self.assertEqual(reserve_held_out_parts([1, 2, 3, 4]), [])

    def test_holds_about_fifteen_percent(self):
        held = reserve_held_out_parts(list(range(20)), fraction=0.15)
        self.assertEqual(len(held), 3)
        self.assertTrue(set(held).issubset(set(range(20))))

    def test_parse_cer_percent_and_fraction(self):
        self.assertAlmostEqual(parse_ketos_cer("CER: 12.5"), 0.125)
        self.assertAlmostEqual(parse_ketos_cer("character error rate = 0.02"), 0.02)

    def test_missing_ketos_returns_none(self):
        self.assertIsNone(run_ketos_test("/tmp/m.mlmodel", ["/tmp/a.xml"], ketos_bin=""))

    def test_empty_eval_paths_does_not_build_argv(self):
        self.assertIsNone(ketos_argv("/tmp/m.mlmodel", None))
        self.assertIsNone(ketos_argv("/tmp/m.mlmodel", []))
        self.assertIsNone(run_ketos_test("/tmp/m.mlmodel", None, ketos_bin="/usr/bin/ketos"))

    def test_eval_paths_build_binary_argv(self):
        argv = ketos_argv("/tmp/m.mlmodel", ["/tmp/eval.arrow"], format_type="binary")
        self.assertEqual(argv[0], "ketos")
        self.assertIn("-m", argv)
        self.assertIn("/tmp/eval.arrow", argv)
        self.assertIn("binary", argv)

    def test_collection_pairs_empty_when_all_held_out(self):
        items = [
            {"transcription_layer_id": 1, "document_part_id": 10},
            {"transcription_layer_id": 1, "document_part_id": 11},
        ]
        self.assertEqual(eligible_collection_pairs(items, {10, 11}), [])

    def test_document_train_all_held_out_raises(self):
        with self.assertRaises(ValueError) as ctx:
            kept_training_parts([10, 11], {10, 11})
        self.assertIn("No pages remain", str(ctx.exception))

    def test_document_train_keeps_non_held_out(self):
        self.assertEqual(kept_training_parts([10, 11, 12], {11}), [10, 12])

    def test_collection_pairs_keeps_non_held_out(self):
        items = [
            {"transcription_layer_id": 1, "document_part_id": 10},
            {"transcription_layer_id": 1, "document_part_id": 11},
        ]
        self.assertEqual(
            eligible_collection_pairs(items, {10}),
            [(1, 11)],
        )
