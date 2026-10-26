"""Evaluate the application adapter against Kraken's actual report renderer.

No training, model inference, or provider calls run in these tests.
"""
from collections import Counter
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase
from kraken.serialization import render_report

from ai.ketos_hook import parse_ketos_cer
from core.tasks import _ketos_cer_from_qs


def kraken_report(characters, errors):
    return render_report(
        model="test-model", chars=characters, errors=errors,
        char_accuracy=1 - errors / characters,
        char_CI_accucary=1, word_accuracy=1,
        char_confusions=Counter(), scripts=Counter(),
        insertions=errors, deletions=Counter(), substitutions=Counter())


class KetosEvaluationTests(SimpleTestCase):
    def test_actual_report_preserves_cer_beyond_printed_precision(self):
        report = kraken_report(30000, 1)
        self.assertIn("100.00%\tCharacter Accuracy", report)
        self.assertAlmostEqual(parse_ketos_cer(report), 1 / 30000)

    def test_actual_report_allows_insertions_above_character_count(self):
        self.assertEqual(parse_ketos_cer(kraken_report(10, 15)), 1.5)

    @patch("ai.ketos_hook.subprocess.run")
    @patch("ai.ketos_hook.shutil.which", return_value="test-ketos")
    @patch("core.tasks.build_binary_dataset")
    @patch("core.tasks.make_recognition_segmentation", return_value=[])
    def test_adapter_uses_binary_report_and_ignores_failed_evaluation(
            self, segmentation, build, which, run):
        model = SimpleNamespace(file=SimpleNamespace(path="model.safetensors"))
        held_qs = Mock()
        held_qs.values.return_value = [{"content": "reviewed"}]
        report = kraken_report(100, 3)
        run.return_value = SimpleNamespace(returncode=0, stdout=report, stderr="")
        self.assertEqual(_ketos_cer_from_qs(model, held_qs), 0.03)
        argv = run.call_args.args[0]
        self.assertEqual(argv[:6], [
            "test-ketos", "test", "-m", "model.safetensors", "-f", "binary"])
        self.assertEqual(argv[6:], ["-n", build.call_args.kwargs["output_file"]])
        run.return_value.returncode = 1
        self.assertIsNone(_ketos_cer_from_qs(model, held_qs))

    @patch("ai.ketos_hook.subprocess.run")
    @patch("ai.ketos_hook.shutil.which", return_value="test-ketos")
    @patch("core.tasks.build_binary_dataset")
    @patch("core.tasks.make_recognition_segmentation", return_value=[])
    def test_adapter_passes_model_evaluation_options(
            self, segmentation, build, which, run):
        model = SimpleNamespace(
            file=SimpleNamespace(path="greek.safetensors"),
            evaluation_normalization="NFD",
            evaluation_normalize_whitespace=True)
        held_qs = Mock()
        held_qs.values.return_value = [{"content": "reviewed"}]
        run.return_value = SimpleNamespace(
            returncode=0, stdout=kraken_report(100, 3), stderr="")

        self.assertEqual(_ketos_cer_from_qs(model, held_qs), 0.03)
        self.assertEqual(run.call_args.args[0], [
            "test-ketos", "test", "-m", "greek.safetensors",
            "-f", "binary", "-u", "NFD", "-n",
            build.call_args.kwargs["output_file"]])
