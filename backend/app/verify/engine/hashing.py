"""Keyed HMAC hashing of identity fields (TW-658).

The PII doctrine (medallion, TW-547): extracted document fields are
hashed at ingestion with a keyed HMAC and stored only as hashes in
vfy_documents; the identity anchor is the same treatment over the
normalized name + birthdate + document number triple. The key lives
outside the database (secrets store / environment), so a bronze
dump alone identifies nobody.
"""

from __future__ import annotations

import hashlib
import hmac


class FieldHasher:
    """HMAC-SHA256 over normalized field values, domain-separated."""

    def __init__(self, key: bytes):
        if not key:
            raise ValueError("FieldHasher requires a non-empty key")
        self._key = key

    def field_hash(self, value: str) -> str:
        msg = f"field:{value}".encode("utf-8")
        return hmac.new(self._key, msg, hashlib.sha256).hexdigest()

    def anchor_hash(self, name: str, dob_iso: str, doc_number: str) -> str:
        """The identity anchor: one person, one verified identity."""
        msg = f"anchor:{name}|{dob_iso}|{doc_number}".encode("utf-8")
        return hmac.new(self._key, msg, hashlib.sha256).hexdigest()
