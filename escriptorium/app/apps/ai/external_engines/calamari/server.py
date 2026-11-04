"""HTTP wrapper for the Calamari line contract.

Run it with ``python -m ai.external_engines.calamari.server``. The default
bind is 127.0.0.1. ``--allow-container-bind`` allows only ``0.0.0.0``.
This server is IPv4, so ``::1`` is refused. The default backend is
unavailable. ``--backend predict`` is the only way this process constructs
``PredictCalamariBackend``. It is not started by Compose or by transcription.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from ai.external_engines.calamari.backend import PredictCalamariBackend
from ai.external_engines.calamari.engine import _MAX_BODY, handle
from ai.external_engines.calamari.predictor import LayoutError, inspect_model_dir

CONTENT_TYPE = "application/json; charset=utf-8"
_DEFAULT_HOST = "127.0.0.1"
_CONTAINER_HOST = "0.0.0.0"
_DEFAULT_PORT = 8768
_DEFAULT_TIMEOUT = 30
_REJECTED = "predict backend was rejected"
_FLAGS = "predict flags require --backend predict"
_REQUIRED = "predict backend requires model-dir and work-root"
_STARTUP_MESSAGES = frozenset({_REJECTED, _FLAGS, _REQUIRED})
_LAYOUT_CODES = frozenset({
    "missing_checkpoint",
    "missing_weights",
    "missing_licence",
    "invalid_json",
    "model_id",
    "unsafe_path",
})
_INTERNAL = {
    "api_version": "1",
    "error": {
        "code": "internal",
        "message": "calamari wrapper failed",
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


_BIND_FAILED = "calamari wrapper failed to bind"


def serve(
    host: str = _DEFAULT_HOST,
    port: int = _DEFAULT_PORT,
    backend=None,
    allow_container_bind: bool = False,
) -> None:
    """Bind the wrapper. Raises before listening if the host is not allowed."""
    if (
        not _allowed_host(host, allow_container_bind)
        or isinstance(port, bool)
        or not isinstance(port, int)
        or not 0 < port < 65536
    ):
        raise ValueError("calamari wrapper binds to loopback only")
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
        description="Local Calamari line wrapper. The default is not a recognizer.",
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
        choices=("unavailable", "predict"),
        default="unavailable",
        help="Backend to construct. The default is unavailable.",
    )
    parser.add_argument("--model-dir", help="Checkpoint directory for --backend predict.")
    parser.add_argument("--work-root", help="Writable work directory for --backend predict.")
    parser.add_argument(
        "--timeout",
        type=int,
        help="Predict timeout in seconds for --backend predict. Default 30.",
    )
    parser.add_argument(
        "--require-licence",
        action="store_true",
        help="Refuse to listen when a checkpoint has no short licence label.",
    )
    parser.add_argument(
        "--device",
        choices=("cpu", "gpu"),
        default="cpu",
        help="cpu hides GPUs from the child. gpu keeps one numbered device.",
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
    """Return a predict backend, or None for the unavailable default.

    Callers pass flags. CPU mode does not read the process environment.
    GPU mode reads ``CUDA_VISIBLE_DEVICES`` and accepts one numbered device.
    """
    require_licence = bool(getattr(args, "require_licence", False))
    device = getattr(args, "device", "cpu")
    if device not in {"cpu", "gpu"}:
        raise _StartupRejected(_REJECTED)
    supplied = (
        args.model_dir is not None
        or args.work_root is not None
        or args.timeout is not None
        or require_licence
        or device == "gpu"
    )
    if args.backend != "predict":
        if supplied or args.backend != "unavailable":
            raise _StartupRejected(_FLAGS if args.backend == "unavailable" else _REJECTED)
        return None
    model_dir = _text(args.model_dir)
    work_root = _text(args.work_root)
    if model_dir is None or work_root is None:
        raise _StartupRejected(_REQUIRED)
    timeout = _DEFAULT_TIMEOUT if args.timeout is None else args.timeout
    if isinstance(timeout, bool) or not isinstance(timeout, int) or not 0 < timeout <= 3600:
        raise _StartupRejected(_REJECTED)
    try:
        model_path = _directory(model_dir)
        work_path = _directory(work_root)
        if model_path is None or work_path is None or _nested(model_path, work_path):
            raise _StartupRejected(_REJECTED)
        report = inspect_model_dir(model_path)
        if not report.checkpoints:
            raise _StartupRejected(_layout_failure(report.code))
        if require_licence and any(item.licence == "unspecified" for item in report.checkpoints):
            raise _StartupRejected(_layout_failure("missing_licence"))
        if device == "gpu" and not _one_gpu_device():
            raise _StartupRejected(_REJECTED)
        return PredictCalamariBackend(
            model_dir=model_path,
            work_root=work_path,
            timeout_seconds=timeout,
            device=device,
        )
    except _StartupRejected:
        raise
    except LayoutError as exc:
        raise _StartupRejected(_layout_failure(exc.code)) from None
    except Exception:
        raise _StartupRejected(_REJECTED) from None


def _one_gpu_device() -> bool:
    """True when the parent names exactly one numbered GPU device."""
    visible = os.environ.get("CUDA_VISIBLE_DEVICES", "")
    return visible.isdigit()


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
    class LineHandler(BaseHTTPRequestHandler):
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

    return LineHandler


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
    if length is None:
        return b"x" * (_MAX_BODY + 1)
    if length == 0:
        return b""
    if length > _MAX_BODY:
        remaining = length
        while remaining:
            chunk = rfile.read(min(remaining, 1024 * 1024))
            if not chunk:
                break
            remaining -= len(chunk)
        return b"x" * (_MAX_BODY + 1)
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
