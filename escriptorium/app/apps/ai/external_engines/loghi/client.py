"""Local client for one already-loaded Loghi-HTR process.

The upstream API queues ``POST /predict`` and returns text later from
``GET /status/{identifier}/``. This client posts the line image only. It
does not send a model field, so the process keeps the model it loaded at
startup. Callers inject ``transport`` in tests. The default transport is
loopback HTTP and is not used by unit tests.
"""
from __future__ import annotations

import json
import shutil
import uuid
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

_PNG = b"\x89PNG\r\n\x1a\n"
_BOUNDARY = "EscriptLoghiBoundary"
_MAX_ATTEMPTS = 3
_POLL_SECONDS = 0.2


class ClientFailure(Exception):
    """A fixed failure code for the whole request."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class LineJob:
    """One line crop already encoded as PNG bytes."""

    line_id: str
    png: bytes


@dataclass(frozen=True)
class LineOutcome:
    """Text for one line. A warning means that line did not produce text."""

    line_id: str
    text: str
    confidence: float | None
    warnings: tuple[str, ...]


class LoghiClient:
    """Talk to the Loghi API on this machine and delete its output group."""

    def __init__(
        self,
        base_url: str,
        output_dir: Path,
        *,
        timeout_seconds: int = 30,
        transport=None,
        sleep=None,
        clock=None,
        identifier_factory=None,
        encoder=None,
    ) -> None:
        self._base = _loopback_base(base_url)
        if not isinstance(output_dir, Path) or not output_dir.is_absolute():
            raise ClientFailure("internal")
        self._output = output_dir
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int) or timeout_seconds < 1:
            raise ClientFailure("internal")
        self._timeout = timeout_seconds
        self._transport = transport or _UrllibTransport()
        self._sleep = sleep or _sleep
        self._clock = clock or _clock
        self._identifier = identifier_factory or _identifier
        self._encoder = encoder or _rgba_png

    def recognize(self, lines: tuple[LineJob, ...]) -> tuple[LineOutcome, ...]:
        """Return one outcome per line, in the same order."""
        if not isinstance(lines, tuple) or not lines:
            raise ClientFailure("internal")
        group_id = self._new_id()
        posts = []
        seen = set()
        for line in lines:
            if not isinstance(line, LineJob) or not isinstance(line.line_id, str) or line.line_id in seen:
                raise ClientFailure("internal")
            if not isinstance(line.png, bytes) or not line.png:
                raise ClientFailure("internal")
            seen.add(line.line_id)
            identifier = self._new_id()
            image = line.png if _is_rgba_png(line.png) else self._encoder(line.png)
            if not isinstance(image, bytes) or not _is_rgba_png(image):
                raise ClientFailure("internal")
            posts.append((line.line_id, identifier, image))
        try:
            self._submit(group_id, posts)
            return self._collect(posts)
        finally:
            _remove_group(self._output, group_id)

    def _new_id(self) -> str:
        value = self._identifier()
        if not isinstance(value, str) or not value or not value.isascii() or not value.replace("-", "").isalnum():
            raise ClientFailure("internal")
        if "/" in value or "\\" in value or ".." in value or '"' in value:
            raise ClientFailure("internal")
        return value

    def _submit(self, group_id: str, posts) -> None:
        identifiers = [identifier for _line_id, identifier, _image in posts]
        if len(identifiers) != len(set(identifiers)):
            raise ClientFailure("internal")
        for _line_id, identifier, image in posts:
            self._post(group_id, identifier, image)

    def _post(self, group_id: str, identifier: str, image: bytes) -> None:
        content_type, body = _multipart((
            ("group_id", group_id),
            ("identifier", identifier),
            ("image", (identifier + ".png", image, "image/png")),
        ))
        if b'name="model"' in body or b'name="whitelist"' in body:
            raise ClientFailure("internal")
        url = self._base + "/predict"
        last = "unavailable"
        for _attempt in range(_MAX_ATTEMPTS):
            status, _payload = self._call("POST", url, body, {"Content-Type": content_type})
            if status == 202:
                return
            if status == 429:
                last = "busy"
                continue
            if status in {503, 0}:
                last = "unavailable"
                continue
            raise ClientFailure("unavailable")
        raise ClientFailure(last)

    def _collect(self, posts) -> tuple[LineOutcome, ...]:
        pending = {identifier: line_id for line_id, identifier, _image in posts}
        found = {}
        deadline = self._clock() + self._timeout
        failures = 0
        while pending and self._clock() < deadline:
            progress = False
            for identifier, line_id in list(pending.items()):
                status, payload = self._call(
                    "GET",
                    self._base + "/status/" + identifier + "/",
                    None,
                    {},
                )
                outcome = _interpret(line_id, status, payload)
                if outcome is None:
                    if status == 0:
                        failures += 1
                    continue
                progress = True
                failures = 0
                found[line_id] = outcome
                del pending[identifier]
            if not pending:
                break
            if failures >= _MAX_ATTEMPTS and not found:
                raise ClientFailure("unavailable")
            if not progress:
                self._sleep(min(_POLL_SECONDS, max(0.0, deadline - self._clock())))
        results = []
        for line_id, _identifier, _image in posts:
            results.append(found.get(line_id) or LineOutcome(line_id, "", None, ("timeout",)))
        return tuple(results)

    def _call(self, method: str, url: str, body, headers) -> tuple[int, dict]:
        try:
            status, raw = self._transport(method, url, body, headers, self._timeout)
        except ClientFailure:
            raise
        except Exception:
            return 0, {}
        if not isinstance(status, int) or isinstance(status, bool):
            return 0, {}
        if not isinstance(raw, (bytes, bytearray)):
            return status, {}
        try:
            payload = json.loads(bytes(raw).decode("utf-8")) if raw else {}
        except (UnicodeError, json.JSONDecodeError):
            return status, {}
        if not isinstance(payload, dict):
            return status, {}
        return status, payload


def _interpret(line_id: str, status: int, payload: dict) -> LineOutcome | None:
    if status == 404:
        return None
    if status != 200:
        return None
    state = payload.get("status")
    if state in {"queued", "processing", "running"}:
        return None
    if state == "finished":
        text = payload.get("predicted_text")
        if not isinstance(text, str):
            return LineOutcome(line_id, "", None, ("unrecognized",))
        return LineOutcome(line_id, text, _confidence(payload.get("confidence")), ())
    if state == "error":
        return LineOutcome(line_id, "", None, ("unrecognized",))
    return None


def _confidence(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if number != number or number in {float("inf"), float("-inf")}:
        return None
    if number < 0 or number > 1:
        return None
    return number


def _multipart(fields) -> tuple[str, bytes]:
    chunks = []
    for name, value in fields:
        chunks.append(("--" + _BOUNDARY + "\r\n").encode("ascii"))
        if isinstance(value, tuple):
            filename, data, content_type = value
            header = (
                f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
                f"Content-Type: {content_type}\r\n\r\n"
            )
            chunks.append(header.encode("ascii"))
            chunks.append(data)
            chunks.append(b"\r\n")
        else:
            header = f'Content-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'
            chunks.append(header.encode("ascii"))
    chunks.append(("--" + _BOUNDARY + "--\r\n").encode("ascii"))
    return "multipart/form-data; boundary=" + _BOUNDARY, b"".join(chunks)


def _is_rgba_png(png: bytes) -> bool:
    return (
        isinstance(png, bytes)
        and len(png) >= 26
        and png.startswith(_PNG)
        and png[12:16] == b"IHDR"
        and png[24] == 8
        and png[25] == 6
    )


def _rgba_png(png: bytes) -> bytes:
    import io

    from PIL import Image

    with Image.open(io.BytesIO(png)) as image:
        converted = image.convert("RGBA")
        out = io.BytesIO()
        converted.save(out, format="PNG")
    saved = out.getvalue()
    if not _is_rgba_png(saved):
        raise ClientFailure("internal")
    return saved


def _remove_group(output_dir: Path, group_id: str) -> None:
    target = output_dir / group_id
    try:
        root = output_dir.resolve()
        path = target.resolve()
        path.relative_to(root)
    except (OSError, ValueError):
        return
    if path == root or not path.is_dir():
        return
    shutil.rmtree(path, ignore_errors=True)


def _loopback_base(url: str) -> str:
    if not isinstance(url, str) or not url.startswith("http://"):
        raise ClientFailure("internal")
    if url.endswith("/"):
        url = url[:-1]
    host = url[len("http://"):]
    if "/" in host or host == "":
        raise ClientFailure("internal")
    name, _sep, port = host.partition(":")
    if name not in {"127.0.0.1", "localhost"} or not port.isdigit() or not 0 < int(port) < 65536:
        raise ClientFailure("internal")
    return url


def _identifier() -> str:
    return uuid.uuid4().hex


def _clock() -> float:
    import time

    return time.monotonic()


def _sleep(seconds: float) -> None:
    import time

    time.sleep(seconds)


class _UrllibTransport:
    """Loopback HTTP. Redirects are refused by not using a redirect handler."""

    def __call__(self, method, url, body, headers, timeout):
        request = urllib.request.Request(url, data=body, headers=dict(headers), method=method)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(request, timeout=timeout) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            return exc.code, raw
