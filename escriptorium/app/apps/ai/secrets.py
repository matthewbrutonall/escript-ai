"""Per-user API keys, encrypted at rest. Never log plaintext."""
from __future__ import annotations

import base64
import hashlib


def pick_api_key(*, is_local, user_key=None, env_key=None, default_key=None):
    """Precedence: per-user key → instance env → settings default."""
    if is_local:
        return None
    for key in (user_key, env_key, default_key):
        if key:
            return key
    return None


def _fernet(secret: str):
    from cryptography.fernet import Fernet
    digest = hashlib.sha256((secret or "escript-ai").encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_key(plaintext: str, secret: str) -> bytes:
    if not plaintext:
        raise ValueError("empty key")
    return _fernet(secret).encrypt(plaintext.encode())


def decrypt_key(token: bytes, secret: str) -> str:
    return _fernet(secret).decrypt(bytes(token)).decode()
