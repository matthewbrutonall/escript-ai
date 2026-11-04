"""Loghi-HTR contract, client, and layout. No TensorFlow and no live model."""
import base64
import json
import struct
import threading
import unittest
import zlib
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from ai.external_engines.loghi.backend import (
    ENGINE_NAME,
    PredictLoghiBackend,
    UnavailableLoghiBackend,
)
from ai.external_engines.loghi.client import (
    ClientFailure,
    LineJob,
    LineOutcome,
    LoghiClient,
)
from ai.external_engines.loghi.engine import handle
from ai.external_engines.loghi.layout import (
    CHANNELS,
    CHECKSUMS,
    DISPLAY_NAME,
    KERAS2_MODEL_SHA256,
    LICENCE,
    LINE_HEIGHT,
    MODEL_ID,
    MODEL_VERSION,
    SOURCE_CHECKSUMS,
    LayoutError,
    ModelRecord,
    _WEIGHT_FILES,
    inspect_model_dir,
)
from ai.external_engines.loghi.server import main, respond
from ai.htr_engine_contract import parse_error, parse_model_list, parse_recognize_request, parse_recognize_response

AI_DIR = Path(__file__).resolve().parents[1]
PACKAGE = AI_DIR / "external_engines" / "loghi"
SECRET = "token-must-not-leak"


def _chunk(tag, data):
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)


def _png(color_type):
    channels = {0: 1, 2: 3, 6: 4}[color_type]
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, color_type, 0, 0, 0)
    raw = b"\x00" + bytes([255]) * channels
    return _PNG_SIG + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", zlib.compress(raw)) + _chunk(b"IEND", b"")


_PNG_SIG = b"\x89PNG\r\n\x1a\n"
RGBA = _png(6)
RGB = _png(2)


def _record():
    return ModelRecord(MODEL_ID, MODEL_VERSION, DISPLAY_NAME, LICENCE, LINE_HEIGHT, CHANNELS)


def _request_body(lines, **overrides):
    payload = {
        "api_version": "1",
        "job_id": "job-1",
        "document_id": "9",
        "part_id": "4",
        "engine": "loghi",
        "model_id": MODEL_ID,
        "preprocessing": {},
        "lines": [
            {"line_id": line_id, "image": base64.b64encode(png).decode("ascii")}
            for line_id, png in lines
        ],
    }
    payload.update(overrides)
    return json.dumps(payload).encode("utf-8")


def _request(lines, **overrides):
    return parse_recognize_request(json.loads(_request_body(lines, **overrides)))


def _tree(root, *, channels=4, height=64, licence="CC-BY-4.0\n", charlist="a\n"):
    root.mkdir(parents=True, exist_ok=True)
    for name in _WEIGHT_FILES:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"weight-" + name.encode("utf-8"))
    config = {"args": {"channels": channels, "height": height, "use_mask": True}}
    (root / "config.json").write_text(json.dumps(config), encoding="utf-8")
    (root / "charlist.txt").write_text(charlist, encoding="utf-8")
    if licence is not None:
        (root / "licence.txt").write_text(licence, encoding="utf-8")


class UnavailableTests(unittest.TestCase):
    def test_default_backend_lists_nothing_and_does_not_recognize(self):
        status, body = handle("GET", "/v1/models")
        self.assertEqual(status, 200)
        self.assertEqual(body["models"], [])
        self.assertEqual(body["engine"], ENGINE_NAME)
        status, body = handle("POST", "/v1/recognize", _request_body([("L1", RGBA)]))
        self.assertEqual(status, 503)
        self.assertEqual(parse_error(body).code, "unavailable")
        self.assertTrue(parse_error(body).retryable)
        self.assertNotIn(SECRET, json.dumps(body))

    def test_preprocessing_is_rejected_before_a_backend_call(self):
        status, body = handle(
            "POST",
            "/v1/recognize",
            _request_body([("L1", RGBA)], preprocessing={"line_height": 64}),
        )
        self.assertEqual(status, 400)
        self.assertEqual(parse_error(body).code, "unsupported_preprocessing")

    def test_another_engine_name_is_model_not_found(self):
        status, body = handle(
            "POST",
            "/v1/recognize",
            _request_body([("L1", RGBA)], engine="calamari"),
        )
        self.assertEqual(status, 404)
        self.assertEqual(parse_error(body).code, "model_not_found")


class LayoutTests(unittest.TestCase):
    def test_pinned_digests_name_every_weight_file(self):
        self.assertEqual(set(CHECKSUMS), set(_WEIGHT_FILES))
        for digest in CHECKSUMS.values():
            self.assertEqual(len(digest), 64)
            int(digest, 16)
        self.assertEqual(len(SOURCE_CHECKSUMS), 8)
        self.assertEqual(
            SOURCE_CHECKSUMS["saved_model.pb"],
            "d5f4093672f51117afd2ad436c8db1573a8a301ab9b7ccd958a392d01d9a2797",
        )
        self.assertNotIn("model.keras", SOURCE_CHECKSUMS)
        self.assertEqual(
            KERAS2_MODEL_SHA256,
            "d542cbf1a98986202fb475b97687e0ccd9be05516e74780c57da99ce8b6c4662",
        )
        self.assertNotEqual(CHECKSUMS["model.keras"], KERAS2_MODEL_SHA256)

    def test_a_savedmodel_protobuf_is_refused(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "model"
            _tree(root)
            (root / "saved_model.pb").write_bytes(b"pb")
            with self.assertRaises(LayoutError) as caught:
                inspect_model_dir(root, checksums=None)
            self.assertEqual(caught.exception.code, "savedmodel_refused")
            self.assertNotIn(str(root), str(caught.exception))

    def test_a_matching_tree_can_skip_checksums_for_a_unit_fixture(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "model"
            _tree(root)
            record = inspect_model_dir(root, checksums=None)
        self.assertEqual(record.model_id, MODEL_ID)
        self.assertEqual(record.channels, 4)
        self.assertEqual(record.line_height, 64)
        self.assertEqual(record.licence, LICENCE)

    def test_wrong_channels_licence_and_checksum_are_fixed_codes(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "model"
            _tree(root, channels=1)
            with self.assertRaises(LayoutError) as caught:
                inspect_model_dir(root, checksums=None)
            self.assertEqual(caught.exception.code, "channel_mismatch")
            _tree(root, licence=None)
            (root / "licence.txt").unlink(missing_ok=True)
            with self.assertRaises(LayoutError) as missing:
                inspect_model_dir(root, checksums=None)
            self.assertEqual(missing.exception.code, "missing_licence")
            _tree(root)
            with self.assertRaises(LayoutError) as mismatch:
                inspect_model_dir(root, checksums={"charlist.txt": "0" * 64})
            self.assertEqual(mismatch.exception.code, "checksum_mismatch")
            self.assertNotIn(str(root), str(mismatch.exception))


class ClientTests(unittest.TestCase):
    def test_posts_rgba_without_a_model_field_and_keeps_line_identity(self):
        posted = {}

        def transport(method, url, body, headers, timeout):
            del headers, timeout
            if method == "POST":
                self.assertNotIn(b'name="model"', body)
                self.assertNotIn(b'name="whitelist"', body)
                self.assertIn(b'filename="', body)
                self.assertIn(b".png", body)
                ident = _form_value(body, "identifier")
                posted[ident] = body
                group = Path(self.output) / _form_value(body, "group_id").decode("ascii")
                group.mkdir(exist_ok=True)
                (group / (ident.decode("ascii") + ".txt")).write_text("temp", encoding="utf-8")
                return 202, b"{}"
            ident = url.rstrip("/").rsplit("/", 1)[-1]
            text = "alpha" if ident.startswith("b") else "beta"
            return 200, json.dumps({
                "status": "finished",
                "predicted_text": text,
                "confidence": 0.25,
            }).encode("utf-8")

        with TemporaryDirectory() as tmp:
            self.output = tmp
            encoded = []
            client = LoghiClient(
                "http://127.0.0.1:5000",
                Path(tmp),
                timeout_seconds=5,
                transport=transport,
                sleep=lambda _seconds: None,
                clock=lambda: 0,
                identifier_factory=_ids("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", "cccccccccccccccccccccccccccccccc"),
                encoder=lambda png: encoded.append(png) or RGBA,
            )
            image_a = b"LINE-A" + RGB
            image_b = b"LINE-B" + RGB
            results = client.recognize((LineJob("line-a", image_a), LineJob("line-b", image_b)))
            self.assertFalse(any(Path(tmp).iterdir()))
        self.assertEqual([(item.line_id, item.text, item.confidence) for item in results], [
            ("line-a", "alpha", 0.25),
            ("line-b", "beta", 0.25),
        ])
        self.assertEqual(encoded, [image_a, image_b])
        self.assertIn(RGBA, posted[b"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"])
        self.assertNotIn(image_a, posted[b"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"])

    def test_rgba_input_is_not_reencoded(self):
        seen = []

        def transport(method, url, body, headers, timeout):
            del url, headers, timeout
            if method == "POST":
                seen.append(body)
                return 202, b"{}"
            return 200, b'{"status":"finished","predicted_text":"ok","confidence":1}'

        with TemporaryDirectory() as tmp:
            client = LoghiClient(
                "http://127.0.0.1:5000",
                Path(tmp),
                transport=transport,
                sleep=lambda _seconds: None,
                clock=lambda: 0,
                identifier_factory=_ids("groupid000000000000000000000001", "lineid0000000000000000000000001"),
                encoder=lambda _png: (_ for _ in ()).throw(AssertionError("encoder")),
            )
            result = client.recognize((LineJob("only", RGBA),))
        self.assertEqual(result[0].text, "ok")
        self.assertEqual(result[0].confidence, 1)
        self.assertIn(RGBA, seen[0])

    def test_error_timeout_and_out_of_range_confidence_stay_on_their_lines(self):
        state = {"n": 0}

        def transport(method, url, body, headers, timeout):
            del body, headers, timeout
            if method == "POST":
                return 202, b"{}"
            state["n"] += 1
            ident = url.rstrip("/").rsplit("/", 1)[-1]
            if ident.endswith("1"):
                return 200, b'{"status":"finished","predicted_text":"kept","confidence":1.5}'
            if ident.endswith("2"):
                return 200, b'{"status":"error","predicted_text":"hidden","confidence":0.1}'
            return 404, b'{"status":"not found"}'

        with TemporaryDirectory() as tmp:
            client = LoghiClient(
                "http://127.0.0.1:5000",
                Path(tmp),
                timeout_seconds=5,
                transport=transport,
                sleep=lambda _seconds: None,
                clock=_clock(0, 0, 0, 9),
                identifier_factory=_ids("g", "id-1", "id-2", "id-3"),
            )
            results = client.recognize((
                LineJob("one", RGBA),
                LineJob("two", RGBA),
                LineJob("three", RGBA),
            ))
        self.assertEqual(results[0], LineOutcome("one", "kept", None, ()))
        self.assertEqual(results[1], LineOutcome("two", "", None, ("unrecognized",)))
        self.assertEqual(results[2], LineOutcome("three", "", None, ("timeout",)))

    def test_duplicate_identifiers_are_refused_before_a_post(self):
        calls = []

        def transport(method, url, body, headers, timeout):
            del method, url, body, headers, timeout
            calls.append(1)
            return 202, b"{}"

        with TemporaryDirectory() as tmp:
            client = LoghiClient(
                "http://127.0.0.1:5000",
                Path(tmp),
                transport=transport,
                identifier_factory=lambda: "same-id",
            )
            with self.assertRaises(ClientFailure) as caught:
                client.recognize((LineJob("one", RGBA), LineJob("two", RGBA)))
        self.assertEqual(caught.exception.code, "internal")
        self.assertEqual(calls, [])

    def test_a_full_queue_retries_then_reports_busy(self):
        calls = []

        def transport(method, url, body, headers, timeout):
            del method, url, body, headers, timeout
            calls.append(1)
            return 429, b"{}"

        with TemporaryDirectory() as tmp:
            client = LoghiClient(
                "http://127.0.0.1:5000",
                Path(tmp),
                transport=transport,
                identifier_factory=_ids("g", "line"),
            )
            with self.assertRaises(ClientFailure) as caught:
                client.recognize((LineJob("one", RGBA),))
        self.assertEqual(caught.exception.code, "busy")
        self.assertEqual(len(calls), 3)

    def test_a_down_service_is_unavailable(self):
        def transport(method, url, body, headers, timeout):
            del method, url, body, headers, timeout
            raise OSError("down")

        with TemporaryDirectory() as tmp:
            client = LoghiClient(
                "http://127.0.0.1:5000",
                Path(tmp),
                transport=transport,
                identifier_factory=_ids("g", "line"),
            )
            with self.assertRaises(ClientFailure) as caught:
                client.recognize((LineJob("one", RGBA),))
        self.assertEqual(caught.exception.code, "unavailable")
        self.assertNotIn("down", str(caught.exception))

    def test_the_upstream_url_must_be_loopback(self):
        with TemporaryDirectory() as tmp:
            with self.assertRaises(ClientFailure):
                LoghiClient("http://loghi.local:5000", Path(tmp))


class BackendTests(unittest.TestCase):
    def test_one_model_is_listed_and_a_different_id_is_not_sent(self):
        calls = []

        class Client:
            def recognize(self, lines):
                calls.append(lines)
                return tuple(LineOutcome(line.line_id, "text", None, ()) for line in lines)

        backend = PredictLoghiBackend(
            model_dir=Path("/tmp"),
            work_root=Path("/tmp"),
            client=Client(),
            record=_record(),
        )
        listed = parse_model_list(backend.list_models())
        self.assertEqual(listed.models[0].model_id, MODEL_ID)
        self.assertEqual(listed.models[0].licence, LICENCE)
        self.assertEqual(listed.models[0].input.line_height, 64)
        self.assertEqual(listed.models[0].display_name, DISPLAY_NAME)
        self.assertTrue(listed.models[0].experimental)
        self.assertEqual(listed.models[0].languages, ())
        missed = backend.get_model("other-model")
        self.assertEqual(missed.code, "model_not_found")
        refused = backend.recognize(_request([("L1", RGBA)], model_id="other-model"))
        self.assertEqual(refused.code, "model_not_found")
        self.assertEqual(calls, [])
        accepted = backend.recognize(_request([("L1", RGBA), ("L2", RGBA)]))
        parsed = parse_recognize_response(accepted, _request([("L1", RGBA), ("L2", RGBA)]))
        self.assertEqual([item.text for item in parsed.results], ["text", "text"])
        self.assertEqual(parsed.resources.device, "cpu")
        self.assertEqual(calls[0][0].png, RGBA)

    def test_a_second_call_is_busy_until_the_first_finishes(self):
        entered = threading.Event()
        release = threading.Event()

        class Client:
            def recognize(self, lines):
                entered.set()
                release.wait(2)
                return tuple(LineOutcome(line.line_id, "text", None, ()) for line in lines)

        backend = PredictLoghiBackend(
            model_dir=Path("/tmp"),
            work_root=Path("/tmp"),
            client=Client(),
            record=_record(),
        )
        request = _request([("L1", RGBA)])
        worker = threading.Thread(target=backend.recognize, args=(request,))
        worker.start()
        self.assertTrue(entered.wait(1))
        self.assertEqual(backend.recognize(request).code, "busy")
        release.set()
        worker.join(2)
        self.assertFalse(worker.is_alive())

    def test_partial_warnings_still_name_every_line(self):
        class Client:
            def recognize(self, lines):
                return (
                    LineOutcome(lines[0].line_id, "kept", 0.5, ()),
                    LineOutcome(lines[1].line_id, "", None, ("unrecognized",)),
                )

        backend = PredictLoghiBackend(
            model_dir=Path("/tmp"),
            work_root=Path("/tmp"),
            client=Client(),
            record=_record(),
        )
        request = _request([("L1", RGBA), ("L2", RGBA)])
        body = backend.recognize(request)
        parsed = parse_recognize_response(body, request)
        self.assertEqual(parsed.results[0].text, "kept")
        self.assertEqual(parsed.results[1].warnings, ("unrecognized",))
        self.assertEqual(parsed.provenance.model_id, MODEL_ID)

    def test_more_than_one_batch_stays_in_request_order(self):
        class Client:
            def __init__(self):
                self.sizes = []

            def recognize(self, lines):
                self.sizes.append(len(lines))
                return tuple(LineOutcome(line.line_id, line.line_id, None, ()) for line in lines)

        client = Client()
        backend = PredictLoghiBackend(
            model_dir=Path("/tmp"),
            work_root=Path("/tmp"),
            client=client,
            record=_record(),
        )
        lines = [(str(index), RGBA) for index in range(33)]
        request = _request(lines)
        body = backend.recognize(request)
        parsed = parse_recognize_response(body, request)
        self.assertEqual(client.sizes, [32, 1])
        self.assertEqual([item.text for item in parsed.results], [str(index) for index in range(33)])


class ServerTests(unittest.TestCase):
    def test_predict_flags_and_a_public_host_are_refused(self):
        err = StringIO()
        with redirect_stderr(err):
            self.assertEqual(main(["--host", "10.1.2.3", "--port", "9"]), 2)
            self.assertEqual(main(["--backend", "predict"]), 2)
            self.assertEqual(main(["--model-dir", "/tmp"]), 2)
        text = err.getvalue()
        self.assertIn("predict backend requires", text)
        self.assertNotIn("/tmp", text)
        self.assertNotIn(SECRET, text)

    def test_respond_does_not_open_a_socket(self):
        status, raw = respond("GET", "/v1/capabilities")
        self.assertEqual(status, 200)
        self.assertIn(b'"engine":"loghi"', raw)
        self.assertNotIn(b"tensorflow", raw)


class PackageTests(unittest.TestCase):
    def test_the_package_does_not_import_the_runtime_or_django(self):
        for path in PACKAGE.glob("*.py"):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("tensorflow", text, path.name)
            self.assertNotIn("import django", text, path.name)
            self.assertNotIn("recognize_page", text, path.name)


def _ids(*values):
    pending = iter(values)

    def factory():
        return next(pending)

    return factory


def _clock(*values):
    pending = iter(values)
    last = values[-1]

    def clock():
        nonlocal last
        try:
            last = next(pending)
        except StopIteration:
            pass
        return last

    return clock


def _form_value(body, name):
    marker = f'name="{name}"\r\n\r\n'.encode("ascii")
    start = body.index(marker) + len(marker)
    end = body.index(b"\r\n", start)
    return body[start:end]


if __name__ == "__main__":
    unittest.main()
