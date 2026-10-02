"""External HTR engine contract. No Django, no network, no engine SDK."""
import ast
import unittest
from pathlib import Path

from ai.htr_engine_contract import (
    API_VERSION,
    ERROR_CODES,
    ERROR_HTTP_STATUS,
    PATH_CAPABILITIES,
    PATH_MODEL,
    PATH_MODELS,
    PATH_RECOGNIZE,
    ContractError,
    check_preprocessing,
    parse_capabilities,
    parse_error,
    parse_model,
    parse_model_list,
    parse_preprocessing,
    parse_recognize_request,
    parse_recognize_response,
)

AI_DIR = Path(__file__).resolve().parents[1]
APPS_DIR = AI_DIR.parent

PREPROCESS = {
    "line_height": 64,
    "deslant": False,
    "grayscale": True,
    "preserve_aspect": True,
}

IMAGE = "AAAA"


def _request(**overrides):
    payload = {
        "api_version": API_VERSION,
        "job_id": "job-1",
        "document_id": 123,
        "part_id": 456,
        "engine": "example",
        "model_id": "example-model",
        "preprocessing": dict(PREPROCESS),
        "lines": [{
            "line_id": 1,
            "image": IMAGE,
            "baseline": [[10, 20], [120, 22]],
            "mask": [[8, 5], [130, 7], [132, 40], [6, 39]],
        }],
    }
    payload.update(overrides)
    return payload


def _response(**overrides):
    payload = {
        "api_version": API_VERSION,
        "job_id": "job-1",
        "engine": "example",
        "model_id": "example-model",
        "model_version": "1",
        "results": [{
            "line_id": "1",
            "text": "transcribed line",
            "confidence": 0.91,
            "timing_ms": 34,
            "warnings": [],
        }],
        "provenance": {
            "engine": "example",
            "model_id": "example-model",
            "model_version": "1",
            "api_version": API_VERSION,
        },
        "timing_ms": 120,
        "resources": {"device": "cpu"},
    }
    payload.update(overrides)
    return payload


def _capabilities(**overrides):
    payload = {
        "api_version": API_VERSION,
        "engine": "example",
        "tier": "research",
        "tasks": ["recognize_lines"],
        "accepts": ["image/png"],
        "image_transport": ["base64"],
        "max_lines_per_request": 32,
        "preprocessing_supported": ["line_height", "grayscale"],
        "params_accepted": [],
        "reports": ["timing_ms", "confidence", "warnings"],
    }
    payload.update(overrides)
    return payload


def _model(**overrides):
    payload = {
        "api_version": API_VERSION,
        "engine": "example",
        "model_id": "example-model",
        "model_version": "1",
        "display_name": "Example",
        "licence": "unknown",
        "source": "local:example",
        "scripts": ["Latn"],
        "languages": ["en"],
        "input": dict(PREPROCESS),
        "alphabet_note": "",
        "experimental": True,
    }
    payload.update(overrides)
    return payload


class IsolationTests(unittest.TestCase):
    def test_contract_imports_stdlib_only(self):
        tree = ast.parse((AI_DIR / "htr_engine_contract.py").read_text())
        modules = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.extend(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.append(node.module.split(".")[0])
        self.assertEqual(modules, ["__future__", "re", "dataclasses"])

    def test_stage1_modules_do_not_reference_the_contract(self):
        paths = [
            AI_DIR / "tasks.py",
            AI_DIR / "views.py",
            AI_DIR / "pipeline.py",
            AI_DIR / "backends.py",
            AI_DIR / "dispatch.py",
            AI_DIR / "models.py",
            AI_DIR / "serializers.py",
            APPS_DIR / "api" / "urls.py",
        ]
        for path in paths:
            text = path.read_text()
            self.assertNotIn("htr_engine_contract", text, path)


class PathTests(unittest.TestCase):
    def test_paths_are_engine_routes(self):
        self.assertEqual(PATH_CAPABILITIES, "/v1/capabilities")
        self.assertEqual(PATH_MODELS, "/v1/models")
        self.assertEqual(PATH_MODEL, "/v1/models/{model_id}")
        self.assertEqual(PATH_RECOGNIZE, "/v1/recognize")

    def test_every_error_code_has_one_http_status(self):
        self.assertEqual(set(ERROR_HTTP_STATUS), set(ERROR_CODES))


class CapabilitiesTests(unittest.TestCase):
    def test_accepts_a_closed_capabilities_object(self):
        parsed = parse_capabilities(_capabilities())
        self.assertEqual(parsed.engine, "example")
        self.assertEqual(parsed.tier, "research")
        self.assertEqual(parsed.max_lines_per_request, 32)

    def test_rejects_an_unknown_tier(self):
        with self.assertRaises(ContractError) as caught:
            parse_capabilities(_capabilities(tier="experimental"))
        self.assertEqual(caught.exception.code, "invalid_request")

    def test_rejects_an_unsupported_preprocessing_name(self):
        with self.assertRaises(ContractError) as caught:
            parse_capabilities(_capabilities(preprocessing_supported=["binarize"]))
        self.assertEqual(caught.exception.code, "unsupported_preprocessing")


class ModelTests(unittest.TestCase):
    def test_model_list_requires_a_unique_id_per_engine(self):
        parsed = parse_model_list({
            "api_version": API_VERSION,
            "engine": "example",
            "models": [_model(), _model(model_id="other")],
        })
        self.assertEqual(
            tuple(item.model_id for item in parsed.models),
            ("example-model", "other"),
        )

    def test_duplicate_model_id_is_invalid(self):
        with self.assertRaises(ContractError) as caught:
            parse_model_list({
                "api_version": API_VERSION,
                "engine": "example",
                "models": [_model(), _model()],
            })
        self.assertEqual(caught.exception.code, "invalid_request")

    def test_listed_model_engine_must_match_the_list(self):
        with self.assertRaises(ContractError):
            parse_model(_model(engine="other"), list_engine="example")


class RecognizeTests(unittest.TestCase):
    def test_request_normalises_integer_ids(self):
        parsed = parse_recognize_request(_request())
        self.assertEqual(parsed.document_id, "123")
        self.assertEqual(parsed.part_id, "456")
        self.assertEqual(parsed.lines[0].line_id, "1")
        self.assertEqual(parsed.lines[0].baseline[0], (10.0, 20.0))

    def test_rejects_a_boolean_id(self):
        payload = _request()
        payload["lines"] = [{"line_id": True, "image": IMAGE}]
        with self.assertRaises(ContractError) as caught:
            parse_recognize_request(payload)
        self.assertEqual(caught.exception.code, "invalid_request")

    def test_rejects_a_data_url_image(self):
        payload = _request()
        payload["lines"] = [{"line_id": 1, "image": "data:image/png;base64,AAAA"}]
        with self.assertRaises(ContractError):
            parse_recognize_request(payload)

    def test_unknown_top_level_field_must_go_in_params(self):
        preprocessing = dict(PREPROCESS)
        preprocessing["beam_width"] = 4
        with self.assertRaises(ContractError) as caught:
            parse_recognize_request(_request(preprocessing=preprocessing))
        self.assertEqual(caught.exception.code, "invalid_request")

    def test_params_hold_scalars_without_a_new_field(self):
        preprocessing = dict(PREPROCESS)
        preprocessing["params"] = {
            "beam_width": 4,
            "enabled": True,
            "flags": ["tight", "raw"],
        }
        parsed = parse_recognize_request(_request(preprocessing=preprocessing))
        self.assertEqual(
            parsed.preprocessing.params,
            (("beam_width", 4), ("enabled", True), ("flags", ("tight", "raw"))),
        )

    def test_null_param_is_rejected(self):
        with self.assertRaises(ContractError) as caught:
            parse_preprocessing({"params": {"note": None}}, "preprocessing")
        self.assertEqual(caught.exception.code, "invalid_request")
        with self.assertRaises(ContractError) as caught:
            parse_preprocessing({"params": {"flags": ["tight", None]}}, "preprocessing")
        self.assertEqual(caught.exception.code, "invalid_request")

    def test_omitted_shared_fields_stay_unset(self):
        parsed = parse_preprocessing({"params": {"crop_side": "left"}}, "preprocessing")
        self.assertIsNone(parsed.line_height)
        self.assertIsNone(parsed.deslant)
        self.assertEqual(parsed.params, (("crop_side", "left"),))

    def test_nested_param_object_is_rejected(self):
        with self.assertRaises(ContractError) as caught:
            parse_preprocessing({"params": {"crop": {"side": "left"}}}, "preprocessing")
        self.assertEqual(caught.exception.code, "invalid_request")

    def test_declared_params_pass_and_unknown_params_do_not(self):
        capabilities = parse_capabilities(_capabilities(
            preprocessing_supported=["line_height"],
            params_accepted=["beam_width"],
        ))
        accepted = parse_preprocessing(
            {"line_height": 64, "params": {"beam_width": 4}},
            "preprocessing",
        )
        check_preprocessing(accepted, capabilities)
        extra = parse_preprocessing({"params": {"slant": 0.2}}, "preprocessing")
        with self.assertRaises(ContractError) as caught:
            check_preprocessing(extra, capabilities)
        self.assertEqual(caught.exception.code, "unsupported_preprocessing")
        shared = parse_preprocessing({"deslant": True}, "preprocessing")
        with self.assertRaises(ContractError) as caught:
            check_preprocessing(shared, capabilities)
        self.assertEqual(caught.exception.code, "unsupported_preprocessing")

    def test_wildcard_accepts_any_param_key(self):
        capabilities = parse_capabilities(_capabilities(params_accepted=["*"]))
        parsed = parse_preprocessing({"params": {"custom_knob": True}}, "preprocessing")
        check_preprocessing(parsed, capabilities)
        with self.assertRaises(ContractError) as caught:
            parse_capabilities(_capabilities(params_accepted=["*", "beam_width"]))
        self.assertEqual(caught.exception.code, "invalid_request")

    def test_response_must_cover_every_requested_line(self):
        request = parse_recognize_request(_request())
        parsed = parse_recognize_response(_response(), request)
        self.assertEqual(parsed.results[0].text, "transcribed line")
        self.assertEqual(parsed.provenance.model_version, "1")
        self.assertIsNone(parse_recognize_response(
            _response(results=[{
                "line_id": "1",
                "text": "",
                "confidence": None,
                "timing_ms": 0,
                "warnings": [],
            }]),
            request,
        ).results[0].confidence)

    def test_omitted_line_is_line_failed(self):
        request = parse_recognize_request(_request())
        payload = _response(results=[])
        with self.assertRaises(ContractError) as caught:
            parse_recognize_response(payload, request)
        self.assertEqual(caught.exception.code, "line_failed")
        self.assertEqual(caught.exception.line_id, "1")

    def test_extra_line_is_invalid(self):
        request = parse_recognize_request(_request())
        payload = _response()
        payload["results"].append({
            "line_id": "2",
            "text": "extra",
            "confidence": None,
            "timing_ms": 1,
            "warnings": [],
        })
        with self.assertRaises(ContractError) as caught:
            parse_recognize_response(payload, request)
        self.assertEqual(caught.exception.code, "invalid_request")

    def test_confidence_above_one_is_invalid(self):
        payload = _response()
        payload["results"][0]["confidence"] = 1.2
        with self.assertRaises(ContractError):
            parse_recognize_response(payload)

    def test_provenance_must_match_the_response_header(self):
        payload = _response()
        payload["provenance"]["engine"] = "other"
        with self.assertRaises(ContractError) as caught:
            parse_recognize_response(payload)
        self.assertEqual(caught.exception.code, "invalid_request")


class ErrorTests(unittest.TestCase):
    def test_each_known_code_parses(self):
        for code in sorted(ERROR_CODES):
            parsed = parse_error({
                "api_version": API_VERSION,
                "error": {
                    "code": code,
                    "message": "failed",
                    "retryable": code in {"busy", "unavailable"},
                    "line_id": None,
                },
            })
            self.assertEqual(parsed.code, code)

    def test_unknown_code_is_invalid(self):
        with self.assertRaises(ContractError):
            parse_error({
                "api_version": API_VERSION,
                "error": {
                    "code": "nope",
                    "message": "failed",
                    "retryable": False,
                    "line_id": None,
                },
            })


if __name__ == "__main__":
    unittest.main()
