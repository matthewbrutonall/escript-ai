"""Loopback HTTP wrapper for the PyLaia skeleton.

Run it with ``python -m ai.external_engines.pylaia.server``. It serves the
skeleton handler on 127.0.0.1 for local contract checks. It is not a
recognizer, not for production, and not started by Docker or by
transcription. It refuses to bind to any host outside the loopback range.
"""
from __future__ import annotations

import argparse
import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

from ai.external_engines.pylaia.engine import _MAX_BODY, handle

CONTENT_TYPE = "application/json; charset=utf-8"
_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 8766
_INTERNAL = {
    "api_version": "1",
    "error": {
        "code": "internal",
        "message": "pylaia wrapper failed",
        "retryable": False,
        "line_id": None,
    },
}


def respond(method, path, body: bytes | None = None, backend=None) -> tuple[int, bytes]:
    """Return an HTTP status and a JSON body. Does not open a socket."""
    try:
        status, payload = handle(method, path, _body_bytes(body), backend)
    except Exception:
        status, payload = 500, _INTERNAL
    try:
        raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    except (TypeError, ValueError):
        raw = json.dumps(_INTERNAL, separators=(",", ":")).encode("utf-8")
        status = 500
    return status, raw


def serve(host: str = _DEFAULT_HOST, port: int = _DEFAULT_PORT, backend=None) -> None:
    """Bind the skeleton. Raises before listening if the host is not loopback."""
    if not _loopback(host) or isinstance(port, bool) or not isinstance(port, int) or not 0 < port < 65536:
        raise ValueError("pylaia skeleton binds to loopback only")
    server = HTTPServer((host, port), _handler(backend))
    try:
        server.serve_forever()
    finally:
        server.server_close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Local PyLaia wrapper skeleton. Not a recognizer.",
    )
    parser.add_argument("--host", default=_DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=_DEFAULT_PORT)
    args = parser.parse_args(argv)
    try:
        serve(args.host, args.port)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2
    return 0


def _handler(backend):
    class SkeletonHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        bound_backend = backend

        def handle_one_request(self):
            try:
                self.raw_requestline = self.rfile.readline(65537)
                if len(self.raw_requestline) > 65536 or not self.raw_requestline:
                    self.close_connection = True
                    return
                if not self.parse_request():
                    return
                body = _read_body(self.rfile, self.headers.get("Content-Length"))
                status, raw = respond(self.command, self.path, body, self.bound_backend)
                self.send_response(status)
                self.send_header("Content-Type", CONTENT_TYPE)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
            except Exception:
                self.close_connection = True

        def log_message(self, format, *args):
            return

    return SkeletonHandler


def _body_bytes(body) -> bytes | None:
    if body is None:
        return None
    if isinstance(body, bytearray):
        return bytes(body)
    if isinstance(body, bytes):
        if len(body) > _MAX_BODY:
            return body[: _MAX_BODY + 1]
        return body
    return b""


def _read_body(rfile, content_length) -> bytes:
    length = _content_length(content_length)
    if length is None or length > _MAX_BODY:
        return b"x" * (_MAX_BODY + 1)
    if length == 0:
        return b""
    return rfile.read(length)


def _content_length(value):
    if value is None or value == "":
        return 0
    if not isinstance(value, str) or not value.isdigit():
        return None
    return int(value)


def _loopback(host) -> bool:
    if not isinstance(host, str):
        return False
    if host in {"localhost", "::1"}:
        return True
    parts = host.split(".")
    if len(parts) != 4 or parts[0] != "127":
        return False
    for part in parts:
        if not part.isdigit() or part != str(int(part)) or not 0 <= int(part) <= 255:
            return False
    return True


if __name__ == "__main__":
    sys.exit(main())
