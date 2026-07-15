from __future__ import annotations

import os
import zlib

from cryptography.fernet import Fernet, InvalidToken


def encrypt_session_config(plaintext: str) -> str:
    # Tool JSON schemas and chat history compress well and dispatch metadata has
    # a finite size. Compress before encryption without exposing plaintext.
    compressed = zlib.compress(plaintext.encode("utf-8"), level=9)
    return _fernet().encrypt(compressed).decode("ascii")


def decrypt_session_config(ciphertext: str) -> str:
    try:
        compressed = _fernet().decrypt(ciphertext.encode("ascii"), ttl=15 * 60)
        return zlib.decompress(compressed).decode("utf-8")
    except (InvalidToken, zlib.error, UnicodeDecodeError) as error:
        raise ValueError("LiveKit session configuration is invalid or expired") from error


def _fernet() -> Fernet:
    key = os.getenv("BRAIN_CONFIG_KEY", "").strip()
    if not key:
        raise RuntimeError("BRAIN_CONFIG_KEY is required")
    return Fernet(key.encode("ascii"))
