"""HTTP wrapper for the PyLaia skeleton.

Run it with ``python -m ai.external_engines.pylaia.server``. The default
bind is 127.0.0.1 for local contract checks. ``--allow-container-bind``
allows only ``0.0.0.0``. This server is IPv4, so ``::1`` is refused.
The default backend is unavailable.
``--backend decode`` is the only way this process constructs
``DecodePyLaiaBackend``. It is not a recognizer, not for production, and
not started by Compose or by transcription.
"""
from __future__ import annotations

import argparse
import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from ai.external_engines.pylaia.backend import DecodePyLaiaBackend
from ai.external_engines.pylaia.decoder import check_model_layout
from ai.external_engines.pylaia.engine import _MAX_BODY, handle

CONTENT_TYPE = "application/json; charset=utf-8"
_DEFAULT_HOST = "127.0.0.1"
_CONTAINER_HOST = "0.0.0.0"
_DEFAULT_PORT = 8766
_DEFAULT_TIMEOUT = 30
_REJECTED = "decode backend was rejected"
_FLAGS = "decode flags require --backend decode"
_REQUIRED = "decode backend requires model-dir, model-id, and work-root"
_STARTUP_MESSAGES = frozenset({_REJECTED, _FLAGS, _REQUIRED})
_LAYOUT_CODES = frozenset({
    "missing_model",
    "missing_syms",
    "missing_checkpoint",
    "multiple_checkpoints",
    "unsafe_path",
})
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


_BIND_FAILED = "pylaia skeleton failed to bind"


def serve(
    host: str = _DEFAULT_HOST,
    port: int = _DEFAULT_PORT,
    backend=None,
    allow_container_bind: bool = False,
) -> None:
    """Bind the skeleton. Raises before listening if the host is not allowed."""
    if not _allowed_host(host, allow_container_bind) or isinstance(port, bool) or not isinstance(port, int) or not 0 < port < 65536:
        raise ValueError("pylaia skeleton binds to loopback only")
    try:
        server = HTTPServer((host, port), _handler(backend))
        try:
            server.serve_forever()
        finally:
            server.server_close()
    except OSError:
        raise ValueError(_BIND_FAILED) from None


class _StartupRejected(Exception):
    """A fixed startup refusal. The message is chosen from a closed set."""

    def __init__(self, message: str) -> None:
        if message not in _STARTUP_MESSAGES and not _layout_message(message):
            message = _REJECTED
        super().__init__(message)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Local PyLaia wrapper skeleton. Not a recognizer.",
    )
    parser.add_argument("--host", default=_DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=_DEFAULT_PORT)
    parser.add_argument(
        "--allow-container-bind",
        action="store_true",
        help="Allow host 0.0.0.0. Other non-loopback hosts stay refused.",
    )
    parser.add_argument(
        "--backend",
        choices=("unavailable", "decode"),
        default="unavailable",
        help="Backend to construct. The default is unavailable.",
    )
    parser.add_argument("--model-dir", help="Model directory for --backend decode.")
    parser.add_argument("--model-id", help="Model id for --backend decode. Not a path.")
    parser.add_argument("--work-root", help="Writable work directory for --backend decode.")
    parser.add_argument(
        "--timeout",
        type=int,
        help="Decode timeout in seconds for --backend decode. Default 30.",
    )
    args = parser.parse_args(argv)
    try:
        backend = _select_backend(args)
    except _StartupRejected as exc:
        print(exc, file=sys.stderr)
        return 2
    except Exception:
        print(_REJECTED, file=sys.stderr)
        return 2
    try:
        if backend is None:
            serve(args.host, args.port, allow_container_bind=args.allow_container_bind)
        else:
            serve(
                args.host,
                args.port,
                backend,
                allow_container_bind=args.allow_container_bind,
            )
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2
    return 0


def _select_backend(args):
    """Return a decode backend, or None for the unavailable default.

    Callers pass flags. This does not read the process environment.
    """
    supplied = (
        args.model_dir is not None
        or args.model_id is not None
        or args.work_root is not None
        or args.timeout is not None
    )
    if args.backend != "decode":
        if supplied or args.backend != "unavailable":
            raise _StartupRejected(_FLAGS if args.backend == "unavailable" else _REJECTED)
        return None
    model_dir = _text(args.model_dir)
    model_id = _text(args.model_id)
    work_root = _text(args.work_root)
    if model_dir is None or model_id is None or work_root is None:
        raise _StartupRejected(_REQUIRED)
    timeout = _DEFAULT_TIMEOUT if args.timeout is None else args.timeout
    if isinstance(timeout, bool) or not isinstance(timeout, int) or not 0 < timeout <= 3600:
        raise _StartupRejected(_REJECTED)
    try:
        layout = check_model_layout(Path(model_dir))
        if not layout.ok:
            raise _StartupRejected(_layout_failure(layout.code))
        model_path = _directory(model_dir)
        work_path = _directory(work_root)
        if model_path is None or work_path is None or _nested(model_path, work_path):
            raise _StartupRejected(_REJECTED)
        return DecodePyLaiaBackend(
            model_id=model_id,
            model_dir=model_path,
            work_root=work_path,
            timeout_seconds=timeout,
        )
    except _StartupRejected:
        raise
    except Exception:
        raise _StartupRejected(_REJECTED) from None


def _text(value) -> str | None:
    if not isinstance(value, str) or value == "" or value != value.strip():
        return None
    return value


def _directory(value: str) -> Path | None:
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts:
        return None
    try:
        resolved = path.resolve()
    except OSError:
        return None
    if not resolved.is_dir():
        return None
    return resolved


def _nested(model_root: Path, work_root: Path) -> bool:
    return (
        work_root == model_root
        or _inside(model_root, work_root)
        or _inside(work_root, model_root)
    )


def _inside(root: Path, path: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _layout_failure(code: str) -> str:
    token = code if code in _LAYOUT_CODES else "unsafe_path"
    return f"FAILED: {token}"


def _layout_message(message: str) -> bool:
    prefix = "FAILED: "
    if not message.startswith(prefix):
        return False
    return message[len(prefix):] in _LAYOUT_CODES


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


def _allowed_host(host, allow_container_bind) -> bool:
    if _loopback(host):
        return True
    return allow_container_bind is True and host == _CONTAINER_HOST


def _loopback(host) -> bool:
    if not isinstance(host, str):
        return False
    if host == "localhost":
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
