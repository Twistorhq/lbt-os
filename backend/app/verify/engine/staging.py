"""Image staging: where raw captures live until decision (TW-658).

Spec S5.7: raw document and selfie images are processed, then
destroyed on decision, with a 72-hour hard ceiling for the
fraud-review window; never in backups beyond the staging lifecycle,
never in logs. Two implementations of one seam:

- EncryptedFileStaging: production shape. Bytes land on disk only
  as AES-GCM ciphertext under an opaque ref; wipe unlinks the file.
- InMemoryStaging: test shape. Bytes never leave the process except
  through local_path, below. Used with stubbed ports in tests.

The FOSS model libraries read files, so staging can materialize a
plaintext local path for a ref. That plaintext exists only inside
the verify service's staging area, only between capture and
decision, and wipe() removes it together with the stored blob.
Nothing outside the service ever sees a path or a byte.
"""

from __future__ import annotations

import hashlib
import tempfile
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


class InMemoryStaging:
    def __init__(self):
        self._blobs: dict[str, bytes] = {}
        self._paths: dict[str, str] = {}

    def put(self, data: bytes) -> str:
        ref = hashlib.sha256(data).hexdigest()
        self._blobs[ref] = data
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
        path = self._paths.pop(ref, None)
        if path:
            Path(path).unlink(missing_ok=True)

    def refs(self) -> list[str]:
        return sorted(self._blobs)


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
