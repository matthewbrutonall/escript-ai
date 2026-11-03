"""HTRflow page contract and ALTO parser. No Django and no network."""
import ast
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ai.htr_engine_contract import TASKS
from ai.htrflow_adapter import HTRFLOW_INTEGRATED, plan_htrflow_import
from ai.htrflow_alto import parse_htrflow_alto
from ai.htrflow_client import (
    HtrflowClientError,
    _NoRedirect,
    fetch_page_models,
    recognize_page,
    retryable_failure,
)
from ai.htrflow_contract import (
    beam_refusal,
    parse_page_model_list,
    public_num_beams,
)
from ai.external_htr_dispatch import is_htrflow_page_config

AI_DIR = Path(__file__).resolve().parents[1]

ALTO = """<?xml version="1.0" encoding="UTF-8"?>
<alto xmlns="http://www.loc.gov/standards/alto/ns-v4#">
  <Description>
    <sourceImageInformation><fileName>not-stored.jpg</fileName></sourceImageInformation>
    <OCRProcessing>
      <ocrProcessingStep>
        <processingStepSettings>model: example/regions model_class: YOLO model_version: version-regions</processingStepSettings>
        <processingSoftware><softwareName>htrflow</softwareName><softwareVersion>0.2.6</softwareVersion></processingSoftware>
      </ocrProcessingStep>
      <ocrProcessingStep>
        <processingStepSettings>model: example/lines model_class: YOLO model_version: version-lines</processingStepSettings>
      </ocrProcessingStep>
      <ocrProcessingStep>
        <processingStepSettings>model: example/text model_class: TrOCR model_version: version-text</processingStepSettings>
      </ocrProcessingStep>
    </OCRProcessing>
  </Description>
  <Layout>
    <Page WIDTH="200" HEIGHT="80">
      <PrintSpace>
        <TextBlock ID="block-a" HPOS="10" VPOS="10" WIDTH="80" HEIGHT="30">
          <Shape><Polygon POINTS="10.2,10.8 90.4,10.2 90.2,40.6 10.4,40.2"/></Shape>
          <TextLine ID="line-a" HPOS="10" VPOS="10" WIDTH="80" HEIGHT="20">
            <Shape><Polygon POINTS="10,10 90,10 90,30 10,30"/></Shape>
            <String CONTENT="kept" WC="0.50"/>
          </TextLine>
          <TextLine ID="line-b" HPOS="10" VPOS="30" WIDTH="80" HEIGHT="10">
            <Shape><Polygon POINTS="10,30 90,30 90,40 10,40"/></Shape>
            <String CONTENT=""/>
          </TextLine>
          <TextLine ID="line-c"><String CONTENT="skip-me"/></TextLine>
        </TextBlock>
      </PrintSpace>
    </Page>
  </Layout>
</alto>
"""

BARE = """<alto><Layout><Page WIDTH="20" HEIGHT="10">
<TextBlock ID="only"><TextLine ID="only-line" HPOS="1" VPOS="1" WIDTH="8" HEIGHT="4">
<String CONTENT=""/>
</TextLine></TextBlock></Page></Layout></alto>"""


class _Response:
    def __init__(self, body, url):
        self._body = body
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


class ContractTests(unittest.TestCase):
    def test_line_contract_stays_line_only_and_beams_must_be_one(self):
        self.assertEqual(TASKS, frozenset({"recognize_lines"}))
        self.assertIsNone(beam_refusal(None))
        self.assertIsNone(beam_refusal(1))
        for value in (True, False, "1", 1.0, 0, 4):
            self.assertEqual(beam_refusal(value), "beam_width_refused", value)
        listed = parse_page_model_list({
            "engine": "htrflow",
            "models": [
                {"model_id": "handwriting-english-large", "num_beams": 1, "licence": "example"},
                {"model_id": "wide", "num_beams": "1"},
                {"model_id": "plain"},
            ],
        })
        self.assertEqual(public_num_beams(listed.models[0]), 1)
        self.assertEqual(public_num_beams(listed.models[1]), 0)
        self.assertIsNone(public_num_beams(listed.models[2]))
        self.assertTrue(is_htrflow_page_config(SimpleNamespace(
            metadata={"engine": "htrflow", "task": "recognize_page"},
        )))
        self.assertFalse(is_htrflow_page_config(SimpleNamespace(metadata={"token": "stored"})))
        self.assertFalse(is_htrflow_page_config(SimpleNamespace(
            metadata={"task": "recognize_page", "engine": "htrflow", "extra": True},
        )))

    def test_alto_keeps_layout_text_and_model_steps_without_the_filename(self):
        parsed = parse_htrflow_alto(ALTO, 4, 9)
        self.assertTrue(parsed.ok)
        self.assertEqual([(item.role, item.model, item.model_version) for item in parsed.models], [
            ("regions", "example/regions", "version-regions"),
            ("lines", "example/lines", "version-lines"),
            ("recognition", "example/text", "version-text"),
        ])
        self.assertEqual(parsed.software_version, "0.2.6")
        by_id = {line.line_id: line for line in parsed.lines}
        self.assertEqual(by_id["line-a"].text, "kept")
        self.assertEqual(by_id["line-a"].status, "included")
        self.assertEqual(parsed.regions[0].polygon[0], (10, 11))
        self.assertEqual(by_id["line-a"].polygon[0], (10, 10))
        self.assertEqual(by_id["line-a"].baseline, ((10, 30), (90, 30)))
        self.assertEqual(by_id["line-b"].text, "")
        self.assertEqual(by_id["line-b"].status, "included")
        self.assertEqual(by_id["line-c"].status, "skipped")
        self.assertEqual(by_id["line-c"].code, "no_geometry")
        self.assertEqual(by_id["line-c"].text, "")
        layout = json.dumps(parsed.layout())
        self.assertNotIn("not-stored.jpg", layout)
        self.assertNotIn("kept", layout)
        self.assertNotIn("skip-me", layout)
        self.assertIn("line-a", layout)
        plan = plan_htrflow_import(parsed.planner_payload(), 4, 9, ["line-a"])
        self.assertEqual([(line.line_id, line.text) for line in plan.lines], [("line-a", "kept")])
        self.assertTrue(any(line.line_id == "line-b" for line in plan.skipped))
        empty = plan_htrflow_import(parsed.planner_payload(), 4, 9, [])
        self.assertEqual(empty.lines, ())
        bare = parse_htrflow_alto(BARE, 4, 9)
        self.assertEqual(bare.lines[0].text, "")
        self.assertEqual(bare.lines[0].status, "included")
        self.assertGreaterEqual(len(bare.lines[0].polygon), 3)

    def test_alto_rejects_bad_page_dimensions_and_out_of_bounds_geometry(self):
        for width in ("", "0", "-4", "nope", "inf"):
            parsed = parse_htrflow_alto(
                '<alto><Layout><Page WIDTH="%s" HEIGHT="10">'
                '<TextLine ID="a" HPOS="1" VPOS="1" WIDTH="4" HEIGHT="4">'
                '<String CONTENT="kept"/></TextLine></Page></Layout></alto>' % width,
                4, 9,
            )
            self.assertFalse(parsed.ok, width)
            self.assertEqual(parsed.code, "page_dimensions")
            self.assertEqual(parsed.lines, ())
            self.assertNotIn("kept", json.dumps(parsed.layout()))
        missing = parse_htrflow_alto(
            '<alto><Layout><Page HEIGHT="10"></Page></Layout></alto>', 4, 9,
        )
        self.assertFalse(missing.ok)
        self.assertEqual(missing.code, "page_dimensions")
        bounded = parse_htrflow_alto(
            '<alto><Layout><Page WIDTH="30" HEIGHT="20">'
            '<TextBlock ID="inside" HPOS="1" VPOS="1" WIDTH="8" HEIGHT="6">'
            '<TextLine ID="good" HPOS="1" VPOS="1" WIDTH="8" HEIGHT="6">'
            '<String CONTENT="kept"/></TextLine></TextBlock>'
            '<TextBlock ID="outside" HPOS="1" VPOS="1" WIDTH="8" HEIGHT="6">'
            '<Shape><Polygon POINTS="1,1 90,1 90,8 1,8"/></Shape>'
            '<TextLine ID="bad" HPOS="1" VPOS="1" WIDTH="8" HEIGHT="6">'
            '<Shape><Polygon POINTS="1,1 40,1 40,8 1,8"/></Shape>'
            '<String CONTENT="outside-text"/></TextLine></TextBlock>'
            '<TextLine ID="edge" HPOS="0" VPOS="0" WIDTH="30" HEIGHT="20">'
            '<String CONTENT=""/></TextLine>'
            '</Page></Layout></alto>',
            4, 9,
        )
        self.assertTrue(bounded.ok)
        by_id = {line.line_id: line for line in bounded.lines}
        self.assertEqual(by_id["good"].status, "included")
        self.assertEqual(by_id["good"].text, "kept")
        self.assertEqual(by_id["edge"].status, "included")
        self.assertEqual(by_id["bad"].status, "skipped")
        self.assertEqual(by_id["bad"].code, "geometry_out_of_bounds")
        self.assertEqual(by_id["bad"].text, "")
        self.assertEqual(by_id["bad"].polygon, ())
        layout = bounded.layout()
        self.assertEqual([item["region_id"] for item in layout["regions"]], ["inside"])
        self.assertEqual([item["line_id"] for item in layout["lines"]], ["good", "edge"])
        stored = json.dumps(layout)
        self.assertNotIn("outside-text", stored)
        self.assertNotIn("outside", stored)

    def test_non_finite_box_and_polygon_coordinates_skip_the_line(self):
        boxes = {
            "inf": 'HPOS="inf" VPOS="1" WIDTH="8" HEIGHT="6"',
            "-inf": 'HPOS="1" VPOS="-inf" WIDTH="8" HEIGHT="6"',
            "1e309": 'HPOS="1" VPOS="1" WIDTH="1e309" HEIGHT="6"',
        }
        polygons = {
            "inf": "1,1 inf,1 2,2",
            "-inf": "1,1 -inf,1 2,2",
            "1e309": "1,1 1e309,1 2,2",
        }
        for token, attrs in boxes.items():
            parsed = parse_htrflow_alto(
                '<alto><Layout><Page WIDTH="30" HEIGHT="20">'
                '<TextLine ID="good" HPOS="1" VPOS="1" WIDTH="8" HEIGHT="6">'
                '<String CONTENT="kept"/></TextLine>'
                '<TextLine ID="bad" %s><String CONTENT="bad-text"/></TextLine>'
                '</Page></Layout></alto>' % attrs,
                4, 9,
            )
            self.assertTrue(parsed.ok, token)
            by_id = {line.line_id: line for line in parsed.lines}
            self.assertEqual(by_id["good"].status, "included", token)
            self.assertEqual(by_id["good"].text, "kept", token)
            self.assertEqual(by_id["bad"].status, "skipped", token)
            self.assertEqual(by_id["bad"].code, "no_geometry", token)
            self.assertEqual(by_id["bad"].text, "", token)
            self.assertEqual(by_id["bad"].polygon, (), token)
            self.assertNotIn("bad-text", json.dumps(parsed.layout()), token)
        for token, points in polygons.items():
            parsed = parse_htrflow_alto(
                '<alto><Layout><Page WIDTH="30" HEIGHT="20">'
                '<TextLine ID="good" HPOS="1" VPOS="1" WIDTH="8" HEIGHT="6">'
                '<String CONTENT="kept"/></TextLine>'
                '<TextLine ID="bad"><Shape><Polygon POINTS="%s"/></Shape>'
                '<String CONTENT="bad-text"/></TextLine>'
                '</Page></Layout></alto>' % points,
                4, 9,
            )
            self.assertTrue(parsed.ok, token)
            by_id = {line.line_id: line for line in parsed.lines}
            self.assertEqual(by_id["good"].status, "included", token)
            self.assertEqual(by_id["bad"].status, "skipped", token)
            self.assertEqual(by_id["bad"].code, "no_geometry", token)
            self.assertEqual(by_id["bad"].text, "", token)
            self.assertEqual(by_id["bad"].polygon, (), token)
            self.assertNotIn("bad-text", json.dumps(parsed.layout()), token)

    def test_retryable_failures_follow_the_line_worker_and_http_status(self):
        self.assertTrue(retryable_failure(HtrflowClientError("unavailable", "down")))
        self.assertTrue(retryable_failure(HtrflowClientError("busy", "later")))
        self.assertFalse(retryable_failure(HtrflowClientError("timeout", "slow")))
        self.assertFalse(retryable_failure(HtrflowClientError("invalid_response", "body")))
        self.assertFalse(retryable_failure(HtrflowClientError("http_error", "status", status=400)))
        self.assertFalse(retryable_failure(HtrflowClientError("http_error", "status", status=404)))
        self.assertFalse(retryable_failure(HtrflowClientError("http_error", "status", status=422)))
        self.assertFalse(retryable_failure(HtrflowClientError("http_error", "status", status=True)))
        self.assertFalse(retryable_failure(HtrflowClientError("http_error", "status")))
        self.assertTrue(retryable_failure(HtrflowClientError("http_error", "status", status=408)))
        self.assertTrue(retryable_failure(HtrflowClientError("http_error", "status", status=429)))
        self.assertTrue(retryable_failure(HtrflowClientError("http_error", "status", status=503)))
        self.assertTrue(retryable_failure(HtrflowClientError("http_error", "status", status=500)))

    def test_client_sends_one_beam_and_does_not_follow_a_redirect(self):
        config = SimpleNamespace(
            enabled=True, endpoint_url="http://127.0.0.1:9/base", timeout_seconds=3,
        )
        seen = {}

        def fake_open(request, timeout):
            seen["url"] = request.full_url
            seen["timeout"] = timeout
            seen["body"] = request.data
            if request.full_url.endswith("/v1/page-models"):
                body = json.dumps({
                    "engine": "htrflow",
                    "models": [{"model_id": "handwriting-english-large", "num_beams": 1}],
                }).encode()
            else:
                body = b'{"alto": "<alto></alto>"}'
            return _Response(body, request.full_url)

        with patch("ai.htrflow_client.urllib.request.urlopen", fake_open):
            listed = fetch_page_models(config)
            alto = recognize_page(config, model_id="handwriting-english-large", image=b"png-bytes")
        self.assertEqual(listed.models[0].model_id, "handwriting-english-large")
        self.assertEqual(alto, "<alto></alto>")
        self.assertEqual(seen["timeout"], 3)
        self.assertTrue(seen["url"].endswith("/v1/recognize-page"))
        payload = json.loads(seen["body"])
        self.assertEqual(payload["num_beams"], 1)
        self.assertEqual(payload["model_id"], "handwriting-english-large")
        disabled = SimpleNamespace(enabled=False, endpoint_url="http://127.0.0.1:9/", timeout_seconds=3)
        with patch("ai.htrflow_client.urllib.request.urlopen", side_effect=AssertionError("urlopen")):
            with self.assertRaises(HtrflowClientError) as caught:
                fetch_page_models(disabled)
        self.assertEqual(caught.exception.code, "disabled")
        with patch("ai.htrflow_client.urllib.request.urlopen", side_effect=AssertionError("urlopen")):
            with self.assertRaises(HtrflowClientError) as refused:
                recognize_page(config, model_id="handwriting-english-large", image=b"png", num_beams=4)
        self.assertEqual(refused.exception.code, "beam_width_refused")
        with self.assertRaises(HtrflowClientError) as redirected:
            _NoRedirect().redirect_request(None, None, 302, "see other", {}, "http://secret.example/next")
        self.assertNotIn("secret.example", str(redirected.exception))
        self.assertEqual(redirected.exception.code, "invalid_response")

    def test_modules_do_not_call_line_recognition_or_claim_integration(self):
        self.assertIs(HTRFLOW_INTEGRATED, False)
        adapter = (AI_DIR / "htrflow_adapter.py").read_text(encoding="utf-8")
        self.assertIn("HTRflow is not integrated", adapter)
        follow = (AI_DIR / "docs" / "htrflow-follow-up.md").read_text(encoding="utf-8")
        self.assertIn("HTRflow is not integrated into Escript AI.", follow)
        self.assertIn("Line.external_id", follow)
        self.assertIn("Geometry validation is not complete.", follow)
        self.assertNotIn("Stage 2 roadmap", follow)
        for name in ("htrflow_contract.py", "htrflow_alto.py"):
            self._assert_isolated(AI_DIR / name, {"django", "urllib", "requests", "socket", "celery"})
        self._assert_isolated(AI_DIR / "htrflow_client.py", {"django", "requests", "socket", "celery"})
        run = (AI_DIR / "htrflow_run.py").read_text(encoding="utf-8")
        importer = (AI_DIR / "htrflow_run_import.py").read_text(encoding="utf-8")
        worker = (AI_DIR / "external_htr_run.py").read_text(encoding="utf-8")
        for text in (run, importer):
            self.assertNotIn("recognize_lines", text)
            self.assertNotIn("crop_line", text)
            self.assertNotIn("Image.open", text)
        self.assertIn("htrflow run %s %s", run)
        self.assertNotIn("htrflow", worker.lower())
        self.assertIn("build_gate_sample", importer)
        self.assertIn("STATE_RAW", importer)
        self.assertNotIn("acknowledge_sample", importer)
        self.assertNotIn("mark_training_eligible", importer)
        task = (AI_DIR / "external_htr_tasks.py").read_text(encoding="utf-8")
        self.assertIn("dispatch_external_htr_run", task)
        self.assertIn('name="ai.external_htr_recognize"', task)
        for banned in ("recognize_lines", "htr_engine_client", "import_external_htr_writeback"):
            self.assertNotIn(banned, task)

    def _assert_isolated(self, path, banned):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        modules = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                modules.append(node.module)
            elif isinstance(node, ast.Import):
                modules.extend(alias.name for alias in node.names)
        joined = " ".join(modules)
        for name in banned:
            self.assertNotIn(name, joined, path.name)


if __name__ == "__main__":
    unittest.main()
