"""Envelope encryption for templates and match payloads (TW-658).

Face templates and the document match payload are AES-256-GCM
ciphertext everywhere outside the verify service's memory (spec
S5.7: bronze only, encrypted at rest, key never in the database).
AES-GCM's authentication tag means a tampered ciphertext fails
loudly on decrypt instead of yielding a quietly wrong template.
The `cryptography` library is imported lazily, like every heavy
dependency in this package.
"""

from __future__ import annotations

import os
from typing import Protocol

_NONCE_BYTES = 12


class Encryptor(Protocol):
    def encrypt(self, plaintext: bytes) -> bytes: ...

    def decrypt(self, ciphertext: bytes) -> bytes: ...


class AesGcmEncryptor:
    """AES-256-GCM with a random nonce prepended to each ciphertext."""

    def __init__(self, key: bytes):
        if len(key) != 32:
            raise ValueError("template encryption key must be 32 bytes (AES-256)")
        self._key = key

    def _aesgcm(self):
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        except ImportError as exc:  # pragma: no cover - env without the lib
            raise RuntimeError(
                "cryptography is not installed; the verify engine cannot "
                "encrypt templates on this machine"
            ) from exc
        return AESGCM(self._key)

    def encrypt(self, plaintext: bytes) -> bytes:
        nonce = os.urandom(_NONCE_BYTES)
        return nonce + self._aesgcm().encrypt(nonce, plaintext, None)

    def decrypt(self, ciphertext: bytes) -> bytes:
        nonce, body = ciphertext[:_NONCE_BYTES], ciphertext[_NONCE_BYTES:]
        return self._aesgcm().decrypt(nonce, body, None)
