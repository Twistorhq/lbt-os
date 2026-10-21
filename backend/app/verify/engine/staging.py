"""Image staging: where raw captures live until decision (TW-658).

Spec S5.7: raw document and selfie images are processed, then
destroyed on decision, with a 72-hour hard ceiling for the
fraud-review window; never in backups beyond the staging lifecycle,
never in logs. Two implementations of one seam:

- EncryptedFileStaging: production shape. Bytes land on disk only
  as AES-GCM ciphertext under an opaque ref; wipe unlinks the file.
- InMemoryStaging: test shape. Bytes never leave the process except
  through local_path, below. Used with stubbed ports in tests.

The ceiling is a mechanism, not a constant: sweep_expired destroys
every ref older than the TTL (by put time in memory, by file age on
disk), and the pipeline exposes it as sweep_staging so the service
can run it on a schedule. A ref orphaned by a crashed stage dies
there, at the latest.

The FOSS model libraries read files, so staging can materialize a
plaintext local path for a ref. That plaintext exists only inside
the verify service's staging area, only between capture and
decision, and wipe() removes it together with the stored blob.
Nothing outside the service ever sees a path or a byte.
"""

from __future__ import annotations

import hashlib
import tempfile
import time
import uuid
from pathlib import Path
from typing import Protocol

from .crypto import Encryptor


class Staging(Protocol):
    def put(self, data: bytes) -> str: ...

    def get(self, ref: str) -> bytes: ...

    def local_path(self, ref: str) -> str: ...

    def wipe(self, ref: str) -> None: ...

    def refs(self) -> list[str]: ...

    def sweep_expired(self, ttl_hours: float, *, now: float | None = None) -> list[str]:
        """Destroy every ref older than the TTL; return their refs."""
        ...


class InMemoryStaging:
    def __init__(self):
        self._blobs: dict[str, bytes] = {}
        self._paths: dict[str, str] = {}
        self._put_at: dict[str, float] = {}

    def put(self, data: bytes) -> str:
        # Unique per put: a content-addressed ref would let two
        # sessions share one blob, and one session's wipe would
        # destroy the other's capture (Rosa's n1).
        ref = f"{hashlib.sha256(data).hexdigest()}-{uuid.uuid4().hex}"
        self._blobs[ref] = data
        self._put_at[ref] = time.time()
        return ref

    def get(self, ref: str) -> bytes:
        return self._blobs[ref]

    def local_path(self, ref: str) -> str:
        if ref not in self._paths:
            handle = tempfile.NamedTemporaryFile(
                delete=False, suffix=".img", prefix="vfy-stage-"
            )
            handle.write(self._blobs[ref])
            handle.close()
            self._paths[ref] = handle.name
        return self._paths[ref]

    def wipe(self, ref: str) -> None:
        self._blobs.pop(ref, None)
        self._put_at.pop(ref, None)
        path = self._paths.pop(ref, None)
        if path:
            Path(path).unlink(missing_ok=True)

    def refs(self) -> list[str]:
        return sorted(self._blobs)

    def sweep_expired(self, ttl_hours: float, *, now: float | None = None) -> list[str]:
        cutoff = (time.time() if now is None else now) - ttl_hours * 3600
        expired = [ref for ref, at in self._put_at.items() if at <= cutoff]
        for ref in expired:
            self.wipe(ref)
        return sorted(expired)


class EncryptedFileStaging:
    def __init__(self, root: Path, encryptor: Encryptor):
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)
        self._encryptor = encryptor

    def put(self, data: bytes) -> str:
        ref = f"{uuid.uuid4().hex}.enc"
        (self._root / ref).write_bytes(self._encryptor.encrypt(data))
        return ref

    def get(self, ref: str) -> bytes:
        path = self._root / ref
        if not path.exists():
            raise KeyError(ref)
        return self._encryptor.decrypt(path.read_bytes())

    def local_path(self, ref: str) -> str:
        tmp = self._root / f"{ref}.tmp"
        if not tmp.exists():
            tmp.write_bytes(self.get(ref))
        return str(tmp)

    def wipe(self, ref: str) -> None:
        (self._root / ref).unlink(missing_ok=True)
        (self._root / f"{ref}.tmp").unlink(missing_ok=True)

    def refs(self) -> list[str]:
        return sorted(p.name for p in self._root.glob("*.enc"))

    def sweep_expired(self, ttl_hours: float, *, now: float | None = None) -> list[str]:
        cutoff = (time.time() if now is None else now) - ttl_hours * 3600
        expired = []
        for path in self._root.glob("*.enc"):
            if path.stat().st_mtime <= cutoff:
                expired.append(path.name)
                self.wipe(path.name)
        return sorted(expired)
