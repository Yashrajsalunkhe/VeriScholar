"""Object storage for raw corpus uploads.

The extracted text lives in Postgres (`documents.extracted_text`); the
*original* bytes are kept too, so a document can be re-extracted when the
extractors improve (better PDF handling, OCR) without asking the user to
re-upload. The byte store is abstracted behind `ObjectStorage` so the
local-filesystem implementation can be swapped for an S3-compatible one
without touching the ingestion or API code.

`documents.source_uri` records the URI returned by `put`, so the row
knows where its raw bytes live regardless of backend.

Point `CORPUS_STORAGE_DIR` at a persistent volume for durable raw-byte
storage; on an ephemeral disk a restart loses the raw bytes, which only
costs the ability to re-extract later (the extracted text in Postgres is
the durable artifact).
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Protocol


class ObjectStorage(Protocol):
    """Minimal blob store: write bytes under a key, read or delete them.

    Keys are opaque to callers; the corpus path uses
    ``f"{tenant_id}/{document_id}"`` so objects are partitioned by tenant
    and named by the row that owns them.
    """

    def put(self, key: str, data: bytes) -> str:
        """Store `data` under `key`; return a URI for `documents.source_uri`."""
        ...

    def get(self, key: str) -> bytes:
        """Read the bytes previously stored under `key`."""
        ...

    def delete(self, key: str) -> None:
        """Remove the object at `key`. Idempotent — a missing key is fine."""
        ...


class LocalFilesystemStorage:
    """`ObjectStorage` backed by a directory tree under `root`.

    Disk writes are synchronous; uploads are capped well below a size
    where that blocks the event loop meaningfully. An async S3-compatible
    implementation of the same protocol can replace this for durable
    deployments.
    """

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        # Keys are server-generated (`tenant_id/document_id`), never user
        # input, so there's no traversal vector — but normalize anyway so
        # a future caller can't escape `root` with `..`.
        path = (self.root / key).resolve()
        root = self.root.resolve()
        if not str(path).startswith(str(root)):
            raise ValueError(f"key {key!r} escapes storage root")
        return path

    def put(self, key: str, data: bytes) -> str:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path.as_uri()

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)


def _default_root() -> str:
    """Local store root from `CORPUS_STORAGE_DIR`, else a temp subdir.

    Read at call time (not import time) so tests can repoint it with an
    env var without rebuilding the app.
    """
    configured = os.environ.get("CORPUS_STORAGE_DIR")
    if configured:
        return configured
    return str(Path(tempfile.gettempdir()) / "noplag-corpus")


def get_object_storage() -> ObjectStorage:
    """FastAPI dependency returning the configured object store.

    Today this is always the local-filesystem store rooted at
    `CORPUS_STORAGE_DIR` (or a temp subdir). Deployments wanting object
    storage override this dependency with an S3-backed implementation;
    tests point `CORPUS_STORAGE_DIR` at a tmp dir.
    """
    return LocalFilesystemStorage(_default_root())
