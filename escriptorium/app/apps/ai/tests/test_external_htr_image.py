"""External HTR line-image encoder. No Django, no network, no transcription write."""
import ast
import base64
import io
import math
import unittest
from pathlib import Path

from PIL import Image

from ai.external_htr_image import (
    CODE_BAD_GEOMETRY,
    CODE_BAD_IMAGE,
    CODE_BAD_LIMIT,
    CODE_BAD_MODE,
    CODE_BAD_PADDING,
    CODE_NO_BASELINE,
    CODE_NO_MASK,
    CODE_TOO_LARGE,
    MAX_PAD,
    encode_line_image,
)
from ai.htr_engine_contract import parse_recognize_request

AI_DIR = Path(__file__).resolve().parents[1]
SECRET_PATH = "/tmp/secret-document"
TITLE = "Secret Title"
RAW = "decoder exploded"
RUNTIME = (
    "tasks.py",
    "views.py",
    "serializers.py",
    "pipeline.py",
    "backends.py",
    "dispatch.py",
    "models.py",
    "admin.py",
)


class _Line:
    def __init__(self, mask, baseline):
        self.mask = mask
        self.baseline = baseline
        self.title = TITLE
        self.path = SECRET_PATH


def _page(width=40, height=30):
    image = Image.new("RGB", (width, height), (255, 255, 255))
    image.filename = SECRET_PATH
    return image


def _rect(x0, y0, x1, y1):
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


_UNSET = object()


def _line(mask=_UNSET, baseline=_UNSET):
    if mask is _UNSET:
        mask = _rect(4, 6, 14, 12)
    if baseline is _UNSET:
        baseline = [[4, 9], [14, 9]]
    return _Line(mask, baseline)


def _png(result):
    raw = base64.standard_b64decode(result.image)
    opened = Image.open(io.BytesIO(raw))
    opened.load()
    return opened


class EncodeTests(unittest.TestCase):
    def test_rectangular_mask_is_a_raw_png_crop(self):
        image = _page()
        image.putpixel((4, 6), (1, 2, 3))
        image.putpixel((0, 0), (9, 9, 9))
        result = encode_line_image(image, _line())
        self.assertIsNone(result.code)
        self.assertFalse(result.image.startswith("data:"))
        self.assertEqual(len(result.image) % 4, 0)
        opened = _png(result)
        self.assertEqual(opened.format, "PNG")
        self.assertEqual(opened.size, (10, 6))
        self.assertEqual(opened.getpixel((0, 0)), (1, 2, 3))
        parsed = parse_recognize_request({
            "api_version": "1",
            "job_id": "job-1",
            "document_id": 7,
            "part_id": 8,
            "engine": "pylaia",
            "model_id": "example-model",
            "preprocessing": {},
            "lines": [{"line_id": "10", "image": result.image}],
        })
        self.assertEqual(parsed.lines[0].image, result.image)
        self._assert_clean(result)

    def test_slanted_mask_uses_the_bounding_box(self):
        image = _page()
        image.putpixel((5, 4), (4, 5, 6))
        line = _line(
            [[5, 10], [18, 4], [20, 12], [8, 16]],
            [[6, 12], [18, 8]],
        )
        result = encode_line_image(image, line)
        opened = _png(result)
        self.assertEqual(opened.size, (15, 12))
        self.assertEqual(opened.getpixel((0, 0)), (4, 5, 6))
        self.assertNotEqual(opened.size, image.size)

    def test_missing_and_zero_area_masks_fail(self):
        image = _page()
        for mask in (None, [], [[0, 0], [10, 0]], [[5, 5], [5, 5], [5, 5]]):
            result = encode_line_image(image, _line(mask=mask), pad=MAX_PAD)
            self.assertIsNone(result.image)
            self.assertEqual(result.code, CODE_NO_MASK)
            self._assert_clean(result)

    def test_missing_baseline_fails(self):
        image = _page()
        for baseline in (None, [], [[4, 9]]):
            result = encode_line_image(image, _line(baseline=baseline))
            self.assertIsNone(result.image)
            self.assertEqual(result.code, CODE_NO_BASELINE)
            self._assert_clean(result)

    def test_non_finite_coords_fail(self):
        image = _page()
        for value in (math.nan, math.inf, -math.inf):
            mask = _rect(4, 6, 14, 12)
            mask[0][0] = value
            result = encode_line_image(image, _line(mask=mask))
            self.assertIsNone(result.image)
            self.assertEqual(result.code, CODE_BAD_GEOMETRY)
            baseline = [[4, 9], [value, 9]]
            result = encode_line_image(image, _line(baseline=baseline))
            self.assertIsNone(result.image)
            self.assertEqual(result.code, CODE_BAD_GEOMETRY)
            self._assert_clean(result)

    def test_partial_overlap_is_clamped_and_not_the_page(self):
        image = _page()
        line = _line(_rect(-4, 2, 8, 10), [[0, 6], [8, 6]])
        result = encode_line_image(image, line)
        opened = _png(result)
        self.assertEqual(opened.size, (8, 8))
        self.assertNotEqual(opened.size, image.size)

    def test_mask_outside_the_page_is_not_replaced_with_the_page(self):
        image = _page()
        line = _line(_rect(-80, -40, -40, -20), [[-70, -30], [-50, -30]])
        result = encode_line_image(image, line, pad=MAX_PAD)
        self.assertIsNone(result.image)
        self.assertEqual(result.code, CODE_BAD_GEOMETRY)
        self._assert_clean(result)

    def test_padding_is_bounded_and_does_not_repair_a_bad_mask(self):
        image = _page(width=200, height=200)
        flat = _line(mask=[[0, 0], [10, 0], [10, 0]])
        rejected = encode_line_image(image, flat, pad=MAX_PAD)
        self.assertIsNone(rejected.image)
        self.assertEqual(rejected.code, CODE_NO_MASK)
        huge = encode_line_image(image, _line(), pad=MAX_PAD + 1)
        self.assertIsNone(huge.image)
        self.assertEqual(huge.code, CODE_BAD_PADDING)
        negative = encode_line_image(image, _line(), pad=-1)
        self.assertEqual(negative.code, CODE_BAD_PADDING)
        centered = _line(_rect(80, 90, 90, 96), [[80, 93], [90, 93]])
        padded = encode_line_image(image, centered, pad=MAX_PAD)
        self.assertEqual(_png(padded).size, (10 + 2 * MAX_PAD, 6 + 2 * MAX_PAD))
        self.assertNotEqual(_png(padded).size, image.size)
        self._assert_clean(rejected)
        self._assert_clean(huge)

    def test_crop_over_the_limit_fails(self):
        image = _page(width=80, height=80)
        line = _line(_rect(0, 0, 40, 20), [[0, 10], [40, 10]])
        result = encode_line_image(image, line, max_width=20)
        self.assertIsNone(result.image)
        self.assertEqual(result.code, CODE_TOO_LARGE)
        raised = encode_line_image(image, line, max_height=10_000)
        self.assertIsNone(raised.image)
        self.assertEqual(raised.code, CODE_BAD_LIMIT)
        self._assert_clean(result)

    def test_path_and_encoder_errors_stay_fixed(self):
        result = encode_line_image(SECRET_PATH, _line())
        self.assertIsNone(result.image)
        self.assertEqual(result.code, CODE_BAD_IMAGE)
        self._assert_clean(result)
        image = _page()

        def boom(box):
            raise RuntimeError(SECRET_PATH + " " + RAW)

        image.crop = boom
        failed = encode_line_image(image, _line())
        self.assertIsNone(failed.image)
        self.assertEqual(failed.code, CODE_BAD_IMAGE)
        self.assertNotIn(RAW, str(failed))
        self._assert_clean(failed)
        mode = encode_line_image(_page(), _line(), mode="CMYK")
        self.assertEqual(mode.code, CODE_BAD_MODE)

    def test_grayscale_mode_is_a_png(self):
        result = encode_line_image(_page(), _line(), mode="L")
        self.assertEqual(_png(result).mode, "L")
        self.assertIsNone(result.code)

    def _assert_clean(self, result):
        text = str(result)
        for secret in (SECRET_PATH, TITLE, RAW, "data:"):
            self.assertNotIn(secret, text)


class IsolationTests(unittest.TestCase):
    def test_helper_does_not_open_paths_or_call_an_engine(self):
        text = (AI_DIR / "external_htr_image.py").read_text()
        tree = ast.parse(text)
        top = []
        for node in tree.body:
            if isinstance(node, ast.ImportFrom) and node.module:
                top.append(node.module)
            elif isinstance(node, ast.Import):
                top.extend(alias.name for alias in node.names)
        self.assertEqual(top, [
            "__future__", "base64", "io", "math", "collections.abc",
            "dataclasses", "PIL",
        ])
        for banned in (
            "htr_engine_client",
            "recognize_lines",
            "LineTranscription",
            "ai_transcribe",
            "endpoint_url",
            "urllib",
            "requests",
            "celery",
            "socket",
            "crop_line",
            "django",
            "Image.open",
        ):
            self.assertNotIn(banned, text, banned)

    def test_stage1_does_not_import_the_helper(self):
        command = (AI_DIR / "management" / "commands" / "plan_external_htr.py").read_text()
        planner = (AI_DIR / "external_htr_plan_command.py").read_text()
        self.assertIn("no_line_image", command)
        self.assertIn("encode_line=no_line_image", command)
        self.assertIn("--encode-images", command)
        self.assertNotIn("external_htr_image", command)
        self.assertIn("encode_line_image", planner)
        for name in RUNTIME:
            text = (AI_DIR / name).read_text()
            self.assertNotIn("external_htr_image", text, name)
            self.assertNotIn("encode_line_image", text, name)


if __name__ == "__main__":
    unittest.main()
