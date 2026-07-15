from __future__ import annotations

import os

from cryptography.fernet import Fernet, InvalidToken


def encrypt_session_config(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_session_config(ciphertext: str) -> str:
    try:
        return _fernet().decrypt(ciphertext.encode("ascii"), ttl=15 * 60).decode("utf-8")
    except InvalidToken as error:
        raise ValueError("LiveKit session configuration is invalid or expired") from error


def _fernet() -> Fernet:
    key = os.getenv("BRAIN_CONFIG_KEY", "").strip()
    if not key:
        raise RuntimeError("BRAIN_CONFIG_KEY is required")
    return Fernet(key.encode("ascii"))

