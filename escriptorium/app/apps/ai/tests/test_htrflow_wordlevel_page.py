"""WordLevelTrOCR stays on the HTRflow page contract. No Django and no network."""
import unittest
from pathlib import Path

from ai.htrflow_adapter import HTRFLOW_INTEGRATED
from ai.htrflow_alto import parse_htrflow_alto

AI_DIR = Path(__file__).resolve().parents[1]
MODEL_ID = "wordlevel-trocr-iam"
REVISION = "eaacaf452b06415df8f10bb6fad3a4c11e609406"

ALTO = """<?xml version="1.0" encoding="UTF-8"?>
<alto xmlns="http://www.loc.gov/standards/alto/ns-v4#">
  <Description>
    <OCRProcessing>
      <ocrProcessingStep>
        <processingStepSettings>model: Riksarkivet/yolov9-regions-1 model_class: YOLO model_version: 9e1a97431764db9b20e5c2afdfed2f6d36e3b456</processingStepSettings>
        <processingSoftware><softwareVersion>0.2.6</softwareVersion></processingSoftware>
      </ocrProcessingStep>
      <ocrProcessingStep>
        <processingStepSettings>model: Riksarkivet/yolov9-lines-within-regions-1 model_class: YOLO model_version: 1dda6137ce6cc4f9768c79d5625d007c24a0f8df</processingStepSettings>
      </ocrProcessingStep>
      <ocrProcessingStep>
        <processingStepSettings>model: microsoft/trocr-base-handwritten model_class: WordLevelTrOCR model_version: %s</processingStepSettings>
      </ocrProcessingStep>
    </OCRProcessing>
  </Description>
  <Layout>
    <Page WIDTH="120" HEIGHT="40">
      <TextBlock ID="region-a" HPOS="10" VPOS="8" WIDTH="100" HEIGHT="24">
        <TextLine ID="line-words" HPOS="10" VPOS="8" WIDTH="80" HEIGHT="12">
          <String ID="late" HPOS="50" VPOS="8" WIDTH="20" HEIGHT="12" CONTENT="world"/>
          <String ID="early" HPOS="10" VPOS="8" WIDTH="24" HEIGHT="12" CONTENT="Hello"/>
        </TextLine>
        <TextLine ID="line-gap" HPOS="10" VPOS="20" WIDTH="80" HEIGHT="12">
          <String CONTENT="Hello"/>
          <String CONTENT=" "/>
          <String CONTENT="world"/>
        </TextLine>
        <TextLine ID="line-one" HPOS="10" VPOS="8" WIDTH="40" HEIGHT="12">
          <String CONTENT="Hello world" WC="0.40"/>
        </TextLine>
        <TextLine ID="line-words" HPOS="10" VPOS="8" WIDTH="40" HEIGHT="12">
          <String CONTENT="duplicate"/>
        </TextLine>
        <TextLine ID="line-empty" HPOS="10" VPOS="8" WIDTH="40" HEIGHT="12">
          <String CONTENT=""/>
        </TextLine>
        <TextLine ID="line-bare"><String CONTENT="no-geometry"/></TextLine>
      </TextBlock>
    </Page>
  </Layout>
</alto>
""" % REVISION

EMPTY = "<alto><Layout><Page WIDTH=\"20\" HEIGHT=\"10\"></Page></Layout></alto>"


class WordLevelPageTests(unittest.TestCase):
    def test_word_strings_keep_spaces_and_document_order(self):
        parsed = parse_htrflow_alto(ALTO, 4, 8)
        self.assertTrue(parsed.ok)
        by_id = {item.line_id: item for item in parsed.lines}
        words = by_id["line-words"]
        self.assertEqual(words.text, "world Hello")
        self.assertNotEqual(words.text, "worldHello")
        self.assertNotEqual(words.text, "Hello world")
        self.assertEqual(words.status, "included")
        self.assertIsNone(words.confidence)
        self.assertEqual(words.polygon[0], (10, 8))
        self.assertEqual(words.polygon[1], (90, 8))
        self.assertNotEqual(words.polygon[0], (50, 8))
        self.assertEqual(by_id["line-gap"].text, "Hello  world")
        self.assertEqual(by_id["line-one"].text, "Hello world")
        self.assertEqual(by_id["line-one"].confidence, 0.4)
        self.assertEqual(by_id["line-empty"].text, "")
        self.assertEqual(by_id["line-empty"].status, "included")
        duplicate = [item for item in parsed.lines if item.code == "line_id_missing"]
        self.assertEqual(len(duplicate), 1)
        self.assertEqual(duplicate[0].status, "skipped")
        self.assertEqual(duplicate[0].text, "")
        self.assertNotIn("duplicate", [item.text for item in parsed.lines])
        self.assertEqual(by_id["line-bare"].status, "skipped")
        self.assertEqual(by_id["line-bare"].code, "no_geometry")
        self.assertEqual(by_id["line-bare"].text, "")
        layout = parsed.layout()
        self.assertNotIn("world Hello", str(layout))
        self.assertNotIn("Hello  world", str(layout))
        self.assertEqual(parsed.models[2].model_class, "WordLevelTrOCR")
        self.assertEqual(parsed.models[2].model_version, REVISION)
        payload = parsed.planner_payload()
        stored = {line["line_id"]: line["text"] for region in payload["regions"] for line in region["lines"]}
        self.assertEqual(stored["line-words"], "world Hello")
        self.assertNotIn("line-bare", stored)

    def test_empty_page_has_no_lines(self):
        parsed = parse_htrflow_alto(EMPTY, 1, 1)
        self.assertTrue(parsed.ok)
        self.assertEqual(parsed.lines, ())
        self.assertEqual(parsed.planner_payload()["regions"], [])

    def test_follow_up_names_wordlevel_without_a_runner_branch(self):
        self.assertIs(HTRFLOW_INTEGRATED, True)
        follow = (AI_DIR / "docs" / "htrflow-follow-up.md").read_text(encoding="utf-8")
        self.assertIn("`wordlevel-trocr-iam` is another HTRflow 0.2.6 page pipeline", follow)
        self.assertIn("WordLevelTrOCR (English handwriting, IAM)", follow)
        self.assertIn(REVISION, follow)
        self.assertIn("license: mit", follow)
        self.assertIn("does not pass `num_beams`", follow)
        self.assertIn("`attn_implementation: eager`", follow)
        self.assertIn("`max_new_tokens: 128`", follow)
        self.assertIn("does not switch to another recognizer", follow)
        self.assertIn("does not fall back to line-level TrOCR or to SATRN", follow)
        self.assertIn("not one editor line per word", follow)
        self.assertIn("raw review gate", follow)
        self.assertIn("not marked training-eligible", follow)
        self.assertIn("does not state a character-error rate", follow)
        self.assertIn("Calamari fresh-clone runtime and model packaging task is separate", follow)
        self.assertIn("handwriting-english-large", follow)
        self.assertIn("satrn-riksarkivet", follow)
        self.assertNotIn("Stage 2 roadmap", follow)
        for name in ("htrflow_run.py", "htrflow_adapter.py", "htrflow_client.py"):
            text = (AI_DIR / name).read_text(encoding="utf-8")
            self.assertNotIn("wordlevel", text.casefold(), name)
            self.assertNotIn("wordleveltrocr", text.casefold(), name)


if __name__ == "__main__":
    unittest.main()
