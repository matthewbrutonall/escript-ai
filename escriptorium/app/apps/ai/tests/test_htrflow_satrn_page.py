"""SATRN stays a separate HTRflow page pipeline. No Django and no network."""
import json
import unittest
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ai.htrflow_adapter import HTRFLOW_INTEGRATED
from ai.htrflow_alto import parse_htrflow_alto
from ai.htrflow_client import (
    HtrflowClientError,
    fetch_page_models,
    recognize_page,
    retryable_failure,
)
from ai.htrflow_contract import beam_refusal, parse_page_model_list, public_num_beams

AI_DIR = Path(__file__).resolve().parents[1]
SATRN_ID = "satrn-riksarkivet"
TROCR_ID = "handwriting-english-large"
REVISION = "a40c7093232eaa47a83ce6469fc4abd033486bdc"

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
        <processingStepSettings>model: Riksarkivet/satrn_htr model_class: Satrn model_version: %s</processingStepSettings>
      </ocrProcessingStep>
    </OCRProcessing>
  </Description>
  <Layout>
    <Page WIDTH="960" HEIGHT="320">
      <TextBlock ID="region-a" HPOS="261" VPOS="67" WIDTH="615" HEIGHT="134">
        <Shape><Polygon POINTS="261,67 876,67 876,201 261,201"/></Shape>
        <TextLine ID="line-a" HPOS="261" VPOS="67" WIDTH="615" HEIGHT="134">
          <Shape><Polygon POINTS="261,67 261,201 876,201 876,67"/></Shape>
          <String CONTENT="kept" WC="0.50"/>
        </TextLine>
      </TextBlock>
    </Page>
  </Layout>
</alto>
""" % REVISION


class _Response:
    def __init__(self, body, url):
        self._body = body if isinstance(body, bytes) else body.encode("utf-8")
        self.status = 200
        self._url = url

    def read(self, _limit=-1):
        return self._body

    def geturl(self):
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def _config():
    return SimpleNamespace(
        enabled=True,
        endpoint_url="http://127.0.0.1:9/",
        timeout_seconds=3,
        metadata={"task": "recognize_page", "engine": "htrflow"},
    )


class SatrnPageTests(unittest.TestCase):
    def test_listing_keeps_satrn_separate_from_trocr(self):
        listed = parse_page_model_list({
            "engine": "htrflow",
            "models": [
                {
                    "model_id": TROCR_ID,
                    "display_name": "TrOCR English",
                    "licence": "mit",
                    "num_beams": 1,
                },
                {
                    "model_id": SATRN_ID,
                    "display_name": "SATRN Riksarkivet",
                    "licence": "mit",
                    "experimental": True,
                    "num_beams": 1,
                },
            ],
        })
        self.assertEqual([model.model_id for model in listed.models], [TROCR_ID, SATRN_ID])
        satrn = listed.models[1]
        self.assertEqual(satrn.licence, "mit")
        self.assertTrue(satrn.experimental)
        self.assertEqual(public_num_beams(satrn), 1)
        self.assertIsNone(beam_refusal(satrn.num_beams))
        omitted = parse_page_model_list({
            "engine": "htrflow",
            "models": [{"model_id": TROCR_ID, "num_beams": 1}],
        })
        self.assertEqual([model.model_id for model in omitted.models], [TROCR_ID])
        for value in (4, True, "1", 1.0):
            self.assertEqual(beam_refusal(value), "beam_width_refused", value)
            reported = parse_page_model_list({
                "engine": "htrflow",
                "models": [{"model_id": SATRN_ID, "num_beams": value}],
            })
            shown = public_num_beams(reported.models[0])
            self.assertNotEqual(shown, 1, value)
            if isinstance(value, bool) or not isinstance(value, int):
                self.assertEqual(shown, 0, value)
            else:
                self.assertEqual(shown, value)

    def test_alto_geometry_stays_on_the_source_page_and_names_satrn(self):
        parsed = parse_htrflow_alto(ALTO, 4, 9)
        self.assertTrue(parsed.ok)
        self.assertEqual(parsed.width, 960)
        self.assertEqual(parsed.height, 320)
        self.assertEqual(parsed.software_version, "0.2.6")
        self.assertEqual(
            [(item.role, item.model_class, item.model_version) for item in parsed.models],
            [
                ("regions", "YOLO", "9e1a97431764db9b20e5c2afdfed2f6d36e3b456"),
                ("lines", "YOLO", "1dda6137ce6cc4f9768c79d5625d007c24a0f8df"),
                ("recognition", "Satrn", REVISION),
            ],
        )
        line = parsed.lines[0]
        self.assertEqual(line.line_id, "line-a")
        self.assertEqual(line.status, "included")
        self.assertEqual(line.text, "kept")
        self.assertEqual(line.polygon, ((261, 67), (261, 201), (876, 201), (876, 67)))
        for x_coord, y_coord in line.polygon:
            self.assertGreaterEqual(x_coord, 0)
            self.assertGreaterEqual(y_coord, 0)
            self.assertLessEqual(x_coord, parsed.width)
            self.assertLessEqual(y_coord, parsed.height)
        layout = parsed.layout()
        self.assertEqual(layout["page"], {"width": 960, "height": 320})
        self.assertEqual(layout["models"][2]["model"], "Riksarkivet/satrn_htr")
        self.assertEqual(layout["lines"][0]["polygon"], [[261, 67], [261, 201], [876, 201], [876, 67]])
        self.assertNotIn("kept", json.dumps(layout))

    def test_client_selects_satrn_and_an_unavailable_runtime_is_not_retried_as_trocr(self):
        seen = {}

        def fake_open(request, timeout):
            seen["url"] = request.full_url
            seen["timeout"] = timeout
            if request.data is None:
                body = json.dumps({
                    "engine": "htrflow",
                    "models": [{
                        "model_id": SATRN_ID,
                        "display_name": "SATRN Riksarkivet",
                        "licence": "mit",
                        "experimental": True,
                        "num_beams": 1,
                    }],
                })
                return _Response(body, request.full_url)
            seen["body"] = request.data
            return _Response(ALTO, request.full_url)

        config = _config()
        with patch("ai.htrflow_client.urllib.request.urlopen", fake_open):
            listed = fetch_page_models(config)
            alto = recognize_page(config, model_id=SATRN_ID, image=b"png-bytes")
        self.assertEqual([model.model_id for model in listed.models], [SATRN_ID])
        self.assertNotIn(TROCR_ID, [model.model_id for model in listed.models])
        payload = json.loads(seen["body"])
        self.assertEqual(payload["model_id"], SATRN_ID)
        self.assertEqual(payload["num_beams"], 1)
        self.assertNotEqual(payload["model_id"], TROCR_ID)
        self.assertTrue(alto.lstrip().startswith("<"))
        self.assertIn("Satrn", alto)

        def down(_request, timeout):
            self.assertEqual(timeout, 3)
            raise urllib.error.URLError("down")

        with patch("ai.htrflow_client.urllib.request.urlopen", down):
            with self.assertRaises(HtrflowClientError) as caught:
                recognize_page(config, model_id=SATRN_ID, image=b"png-bytes")
        self.assertEqual(caught.exception.code, "unavailable")
        self.assertTrue(retryable_failure(caught.exception))
        permanent = HtrflowClientError("http_error", "engine returned HTTP 422", status=422)
        self.assertFalse(retryable_failure(permanent))
        with patch("ai.htrflow_client.urllib.request.urlopen", side_effect=AssertionError("urlopen")):
            with self.assertRaises(HtrflowClientError) as refused:
                recognize_page(config, model_id=SATRN_ID, image=b"png", num_beams=4)
        self.assertEqual(refused.exception.code, "beam_width_refused")

    def test_follow_up_names_satrn_without_changing_the_trocr_or_calamari_notes(self):
        self.assertIs(HTRFLOW_INTEGRATED, True)
        follow = (AI_DIR / "docs" / "htrflow-follow-up.md").read_text(encoding="utf-8")
        self.assertIn("`satrn-riksarkivet` is a separate HTRflow page pipeline.", follow)
        self.assertIn("It is not a line adapter", follow)
        self.assertIn("handwriting-english-large", follow)
        self.assertIn("It does not fall back to TrOCR.", follow)
        self.assertIn("external-htr:htrflow:satrn-riksarkivet", follow)
        self.assertIn("leaves the new layer raw", follow)
        self.assertIn("does not mark it training-eligible", follow)
        self.assertIn("This note does not state a character-error rate.", follow)
        self.assertIn("The operator page service is not part of this repository.", follow)
        self.assertIn(
            "Calamari is listed as a line-recognition choice and is integrated.",
            follow,
        )
        self.assertNotIn("Stage 2 roadmap", follow)
        for name in ("htrflow_run.py", "htrflow_adapter.py", "htrflow_client.py"):
            text = (AI_DIR / name).read_text(encoding="utf-8")
            self.assertNotIn("satrn", text.casefold(), name)
            self.assertNotIn("mmocr", text.casefold(), name)


if __name__ == "__main__":
    unittest.main()
