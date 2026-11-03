"""Turn one HTRflow ALTO page into geometry and line text.

The trusted document and page ids are supplied by the caller. ALTO does
not contain them. This module does not open a network connection, read a
page image, or write a transcription. The page workflow is integrated.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from xml.etree import ElementTree

_MAX_POINTS = 256
_MAX_COORD = 20000
_ID_LIMIT = 256
_TEXT_LIMIT = 2048
_ROLES = ("regions", "lines", "recognition")
_SETTINGS = re.compile(r"([A-Za-z0-9_.-]+):\s*(\S+)")

CODE_RESPONSE = "invalid_response"
CODE_GEOMETRY = "no_geometry"
CODE_BOUNDS = "geometry_out_of_bounds"
CODE_DIMENSIONS = "page_dimensions"
CODE_LINE_ID = "line_id_missing"
CODE_TEXT = "line_text_too_long"
CODE_INCLUDED = "included"
CODE_SKIPPED = "skipped"
CODE_FAILED = "failed"


@dataclass(frozen=True)
class AltoLine:
    """One line outcome. Skipped lines keep an empty ``text``."""

    line_id: str
    text: str
    region_id: str
    polygon: tuple
    baseline: tuple
    reading_order: int
    confidence: float | None
    status: str
    code: str


@dataclass(frozen=True)
class AltoRegion:
    region_id: str
    polygon: tuple


@dataclass(frozen=True)
class AltoModel:
    role: str
    model: str
    model_class: str
    model_version: str


@dataclass(frozen=True)
class ParsedAlto:
    """A parsed page. ``ok`` does not mean a layer was written."""

    ok: bool
    code: str
    document_id: int | None
    part_id: int | None
    width: int | None
    height: int | None
    software_version: str
    models: tuple
    regions: tuple
    lines: tuple

    def layout(self) -> dict:
        """Polygons and engine ids only. No text, filename, or endpoint."""
        return {
            "document_id": self.document_id,
            "part_id": self.part_id,
            "page": {"width": self.width, "height": self.height},
            "software_version": self.software_version,
            "models": [
                {
                    "role": item.role,
                    "model": item.model,
                    "model_class": item.model_class,
                    "model_version": item.model_version,
                }
                for item in self.models
            ],
            "regions": [
                {"region_id": item.region_id, "polygon": [list(point) for point in item.polygon]}
                for item in self.regions
            ],
            "lines": [
                {
                    "line_id": item.line_id,
                    "region_id": item.region_id,
                    "polygon": [list(point) for point in item.polygon],
                    "baseline": [list(point) for point in item.baseline],
                    "reading_order": item.reading_order,
                }
                for item in self.lines
                if item.status == CODE_INCLUDED and len(item.polygon) >= 3
            ],
        }

    def planner_payload(self) -> dict:
        """Included lines only, in the shape ``plan_htrflow_import`` accepts."""
        grouped = {}
        order = []
        for item in self.lines:
            if item.status != CODE_INCLUDED:
                continue
            if item.region_id not in grouped:
                grouped[item.region_id] = []
                order.append(item.region_id)
            grouped[item.region_id].append({
                "line_id": item.line_id,
                "text": item.text,
                "reading_order": item.reading_order,
            })
        return {
            "document_id": self.document_id,
            "part_id": self.part_id,
            "regions": [{"lines": grouped[key]} for key in order],
        }


def parse_htrflow_alto(xml_text, document_id, part_id) -> ParsedAlto:
    """Parse ALTO for one trusted page.

    A bad page id or a body that is not ALTO returns ``ok`` false and no
    lines. The filename stored in ALTO is ignored.
    """
    document = _positive_int(document_id)
    part = _positive_int(part_id)
    if document is None or part is None:
        return _empty(False, "invalid_request", None, None)
    if not isinstance(xml_text, str) or not xml_text.strip():
        return _empty(False, CODE_RESPONSE, document, part)
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError:
        return _empty(False, CODE_RESPONSE, document, part)
    page = _first(root, "Page")
    if page is None:
        return _empty(False, CODE_RESPONSE, document, part)
    width, height = _page_size(page)
    if width is None or height is None:
        return _empty(False, CODE_DIMENSIONS, document, part)
    parents = _parents(root)
    blocks = [element for element in root.iter() if _local(element.tag) == "TextBlock"]
    lines = []
    regions = []
    seen_ids = set()
    order = 0
    for block in blocks:
        region_id = _identifier(block.get("ID")) or "region-%d" % (len(regions) + 1)
        polygon = _geometry(block)
        if polygon is not None and _fits(polygon, width, height):
            regions.append(AltoRegion(region_id[:_ID_LIMIT], tuple(tuple(point) for point in polygon)))
        for element in _lines_of(block, parents):
            parsed, order = _line(element, region_id, order, seen_ids, width, height)
            lines.append(parsed)
    for element in root.iter():
        if _local(element.tag) != "TextLine":
            continue
        if _nearest(element, parents, "TextBlock") is not None:
            continue
        parsed, order = _line(element, "page", order, seen_ids, width, height)
        lines.append(parsed)
    return ParsedAlto(
        ok=True,
        code="",
        document_id=document,
        part_id=part,
        width=width,
        height=height,
        software_version=_software_version(root),
        models=tuple(_models(root)),
        regions=tuple(regions),
        lines=tuple(lines),
    )


def _line(element, region_id, order, seen_ids, width, height):
    line_id = _identifier(element.get("ID"))
    polygon = _geometry(element)
    baseline = _baseline(element, polygon)
    if line_id is None or line_id in seen_ids:
        placeholder = "skipped-%d" % (order + 1)
        while placeholder in seen_ids:
            placeholder = "%s-x" % placeholder
        seen_ids.add(placeholder)
        return AltoLine(
            placeholder, "", region_id[:_ID_LIMIT], (), (), order, None, CODE_SKIPPED, CODE_LINE_ID,
        ), order + 1
    seen_ids.add(line_id)
    if polygon is None or baseline is None:
        return AltoLine(
            line_id, "", region_id[:_ID_LIMIT], (), (), order, None, CODE_SKIPPED, CODE_GEOMETRY,
        ), order + 1
    if not _fits(polygon, width, height) or not _fits(baseline, width, height):
        return AltoLine(
            line_id, "", region_id[:_ID_LIMIT], (), (), order, None, CODE_SKIPPED, CODE_BOUNDS,
        ), order + 1
    text, confidence = _string(element)
    if len(text) > _TEXT_LIMIT:
        return AltoLine(
            line_id, "", region_id[:_ID_LIMIT],
            tuple(tuple(point) for point in polygon),
            tuple(tuple(point) for point in baseline),
            order, confidence, CODE_FAILED, CODE_TEXT,
        ), order + 1
    return AltoLine(
        line_id, text, region_id[:_ID_LIMIT],
        tuple(tuple(point) for point in polygon),
        tuple(tuple(point) for point in baseline),
        order, confidence, CODE_INCLUDED, "",
    ), order + 1


def _string(element):
    parts = []
    confidence = None
    for child in element.iter():
        if _local(child.tag) != "String":
            continue
        content = child.get("CONTENT")
        if isinstance(content, str):
            parts.append(content)
        if confidence is None:
            confidence = _confidence(child.get("WC"))
    return "".join(parts), confidence


def _confidence(value):
    if not isinstance(value, str):
        return None
    try:
        number = float(value)
    except ValueError:
        return None
    if number != number or number in {float("inf"), float("-inf")} or not 0 <= number <= 1:
        return None
    return number


def _geometry(element):
    for child in element.iter():
        if _local(child.tag) != "Polygon":
            continue
        points = _points(child.get("POINTS"))
        if points is not None and len(points) >= 3:
            return points
    return _box(element)


def _baseline(element, polygon):
    box = _box(element)
    if box is not None:
        bottom = max(point[1] for point in box)
        left = min(point[0] for point in box)
        right = max(point[0] for point in box)
        if left != right:
            return [[left, bottom], [right, bottom]]
    if polygon is None:
        return None
    left = min(point[0] for point in polygon)
    right = max(point[0] for point in polygon)
    bottom = max(point[1] for point in polygon)
    if left == right:
        return None
    return [[left, bottom], [right, bottom]]


def _box(element):
    raw = [element.get(name) for name in ("HPOS", "VPOS", "WIDTH", "HEIGHT")]
    if any(value is None for value in raw):
        return None
    try:
        x, y, width, height = [int(round(float(value))) for value in raw]
    except (TypeError, ValueError, OverflowError):
        return None
    if width < 1 or height < 1:
        return None
    points = [[x, y], [x + width, y], [x + width, y + height], [x, y + height]]
    if any(abs(coord) > _MAX_COORD for point in points for coord in point):
        return None
    return points


def _points(raw):
    if not isinstance(raw, str) or not raw.strip():
        return None
    points = []
    for token in raw.split():
        if token.count(",") != 1:
            return None
        left, right = token.split(",", 1)
        try:
            x = int(round(float(left)))
            y = int(round(float(right)))
        except (ValueError, OverflowError):
            return None
        if abs(x) > _MAX_COORD or abs(y) > _MAX_COORD:
            return None
        points.append([x, y])
        if len(points) > _MAX_POINTS:
            return None
    return points or None


def _models(root):
    role_index = 0
    found = []
    for element in root.iter():
        if _local(element.tag) != "processingStepSettings":
            continue
        settings = _settings(element.text)
        model = settings.get("model")
        if not model:
            continue
        if role_index >= len(_ROLES):
            break
        found.append(AltoModel(
            _ROLES[role_index],
            model[:_ID_LIMIT],
            settings.get("model_class", "")[:_ID_LIMIT],
            settings.get("model_version", "")[:_ID_LIMIT],
        ))
        role_index += 1
    return found


def _settings(text):
    if not isinstance(text, str):
        return {}
    return {key: value[:_ID_LIMIT] for key, value in _SETTINGS.findall(text)}


def _software_version(root):
    for element in root.iter():
        if _local(element.tag) == "softwareVersion" and isinstance(element.text, str):
            version = element.text.strip()
            if version:
                return version[:_ID_LIMIT]
    return ""


def _lines_of(block, parents):
    lines = []
    for element in block.iter():
        if _local(element.tag) == "TextLine" and _nearest(element, parents, "TextBlock") is block:
            lines.append(element)
    return lines


def _parents(root):
    found = {}
    for parent in root.iter():
        for child in list(parent):
            found[child] = parent
    return found


def _nearest(element, parents, name):
    current = parents.get(element)
    while current is not None:
        if _local(current.tag) == name:
            return current
        current = parents.get(current)
    return None


def _first(root, name):
    for element in root.iter():
        if _local(element.tag) == name:
            return element
    return None


def _local(tag):
    if not isinstance(tag, str):
        return ""
    if tag.startswith("{"):
        return tag.rsplit("}", 1)[-1]
    return tag


def _identifier(value):
    if not isinstance(value, str) or not value or value != value.strip():
        return None
    return value[:_ID_LIMIT]


def _page_size(page):
    try:
        width = int(round(float(page.get("WIDTH"))))
        height = int(round(float(page.get("HEIGHT"))))
    except (TypeError, ValueError, OverflowError):
        return None, None
    if width < 1 or height < 1 or width > _MAX_COORD or height > _MAX_COORD:
        return None, None
    return width, height


def _fits(points, width, height):
    """True when every point lies inside the ALTO page rectangle.

    The rectangle is the page's own WIDTH and HEIGHT, including its edges.
    This does not compare those coordinates with a source image.
    """
    for x, y in points:
        if x < 0 or y < 0 or x > width or y > height:
            return False
    return True


def _positive_int(value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return None
    return value


def _empty(ok, code, document_id, part_id):
    return ParsedAlto(ok, code, document_id, part_id, None, None, "", (), (), ())
