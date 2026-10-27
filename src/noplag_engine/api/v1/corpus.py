"""HTTP API for corpus document ingestion.

Four endpoints, mounted under /v1/corpus:

- POST   /v1/corpus/documents        → 201 + the ingested document's metadata
- GET    /v1/corpus/documents        → paginated metadata list
- GET    /v1/corpus/documents/{id}   → one document, including extracted text
- DELETE /v1/corpus/documents/{id}   → 204 (cascade-deletes the doc's chunks)

Upload extracts a plain-text layer (PDF / DOCX / TXT), normalizes it, and
writes a `documents` row in fingerprint-ready form — `extracted_text`
populated, `fingerprint_status = 'pending'`. A background task then chunks
`extracted_text`, writes `chunks` + fingerprints, and flips the status to
'fingerprinted', at which point the document is matchable by checks.

The raw upload bytes go to the `ObjectStorage` abstraction (local
filesystem by default; swap in an S3-compatible implementation for
durable storage).
"""

from __future__ import annotations

import hashlib
import logging
from uuid import UUID, uuid4

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    HTTPException,
    Response,
    UploadFile,
    status,
)
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from noplag_engine.api.deps import get_session, get_tenant_id
from noplag_engine.api.v1.corpus_schemas import (
    CorpusDocument,
    CorpusDocumentDetail,
    CorpusDocumentListResponse,
)
from noplag_engine.db import get_sessionmaker
from noplag_engine.ingestion import (
    ExtractionFailed,
    NoExtractableText,
    ObjectStorage,
    UnsupportedMediaType,
    extract_text,
    get_object_storage,
    normalize_text,
    resolve_mime_type,
)
from noplag_engine.workflows.corpus import fingerprint_document

logger = logging.getLogger(__name__)

router = APIRouter()

# Cap uploads well below memory pressure. 10 MiB comfortably holds a
# book-length text-layer PDF; larger ingests arrive through the bulk
# ingester script, not this per-file endpoint.
_MAX_UPLOAD_BYTES = 10 * 1024 * 1024

_MAX_LIST_LIMIT = 100
_DEFAULT_LIST_LIMIT = 20

# Metadata columns shared by the create response and the list rows — the
# full `extracted_text` is only returned from the detail endpoint.
_METADATA_COLUMNS = (
    "id, source_type, filename, mime_type, size_bytes, sha256, "
    "char_length, language, fingerprint_status, created_at, updated_at"
)


@router.post(
    "/documents",
    status_code=status.HTTP_201_CREATED,
    response_model=CorpusDocument,
)
async def upload_document(
    response: Response,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    tenant_id: UUID = Depends(get_tenant_id),
    session: AsyncSession = Depends(get_session),
    storage: ObjectStorage = Depends(get_object_storage),
) -> CorpusDocument:
    data = await file.read()
    if len(data) == 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "empty upload")
    if len(data) > _MAX_UPLOAD_BYTES:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"upload exceeds {_MAX_UPLOAD_BYTES} bytes",
        )

    # Dedup: a byte-identical re-upload is idempotent — return the existing
    # document (200) without re-storing, re-extracting, or re-fingerprinting.
    # The unique index on sha256 is the backstop for a concurrent-upload race.
    sha256 = hashlib.sha256(data).hexdigest()
    existing = (
        await session.execute(
            sql_text(
                f"SELECT {_METADATA_COLUMNS} FROM documents "
                "WHERE sha256 = :sha256 AND tenant_id = :t"
            ),
            {"sha256": sha256, "t": tenant_id},
        )
    ).one_or_none()
    if existing is not None:
        response.status_code = status.HTTP_200_OK
        return _row_to_metadata(existing)

    try:
        mime_type = resolve_mime_type(file.content_type, file.filename)
    except UnsupportedMediaType as exc:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, str(exc)
        ) from exc

    try:
        text = normalize_text(extract_text(data, mime_type))
    except (NoExtractableText, ExtractionFailed) as exc:
        # Well-formed-but-empty and corrupt-but-typed both mean "we can't
        # produce text from this" — 422, never a 500 on user input.
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)
        ) from exc

    document_id = uuid4()
    # Raw bytes go to object storage first; `source_uri` records where.
    # Keep the original bytes so a document can be re-extracted when the
    # extractors improve, without asking the user to re-upload.
    source_uri = storage.put(f"{tenant_id}/{document_id}", data)

    await session.execute(
        sql_text(
            "INSERT INTO documents "
            "(id, tenant_id, source_type, filename, "
            " mime_type, size_bytes, sha256, source_uri, extracted_text, "
            " char_length) "
            "VALUES (:id, :tenant_id, "
            " CAST(:source_type AS corpus_source_type), :filename, "
            " :mime_type, :size_bytes, :sha256, :source_uri, "
            " :extracted_text, :char_length)"
        ),
        {
            "id": document_id,
            "tenant_id": tenant_id,
            "source_type": "user_upload",
            "filename": file.filename,
            "mime_type": mime_type,
            "size_bytes": len(data),
            "sha256": sha256,
            "source_uri": source_uri,
            "extracted_text": text,
            "char_length": len(text),
        },
    )
    await session.commit()

    # The document lands 'pending'; kick off the fingerprint pass so it
    # becomes matchable without a manual step.
    background_tasks.add_task(_fingerprint_in_background, document_id)

    return await _load_document(session, document_id)


async def _fingerprint_in_background(document_id: UUID) -> None:
    """Run `fingerprint_document` post-response in its own session.

    Mirrors the check pipeline's background-task pattern (FastAPI
    BackgroundTasks): opens a session from the engine sessionmaker and
    delegates to the state-machine-owning `fingerprint_document` (which
    sets 'failed' + re-raises on error). A failure here only logs — the
    document keeps its 'failed' status for a later retry rather than
    blocking the upload response.
    """
    try:
        async with get_sessionmaker()() as bg_session:
            await fingerprint_document(document_id, bg_session)
    except Exception:
        logger.exception("fingerprinting failed for document %s", document_id)


@router.get(
    "/documents",
    response_model=CorpusDocumentListResponse,
)
async def list_documents(
    limit: int = _DEFAULT_LIST_LIMIT,
    offset: int = 0,
    tenant_id: UUID = Depends(get_tenant_id),
    session: AsyncSession = Depends(get_session),
) -> CorpusDocumentListResponse:
    """Paginated metadata for the uploaded (tenant-owned) corpus.

    Bulk-loaded platform corpus rows (tenant_id NULL — the sample corpus,
    folder ingests) are excluded: this endpoint manages what was uploaded
    through the API. Ordered `created_at DESC, id DESC` for a deterministic
    tie break, matching the checks list.
    """
    limit = max(1, min(limit, _MAX_LIST_LIMIT))
    offset = max(0, offset)

    total = (
        await session.execute(
            sql_text("SELECT count(*) FROM documents WHERE tenant_id = :t"),
            {"t": tenant_id},
        )
    ).scalar_one()

    rows = (
        await session.execute(
            sql_text(
                f"SELECT {_METADATA_COLUMNS} FROM documents "
                "WHERE tenant_id = :t "
                "ORDER BY created_at DESC, id DESC "
                "LIMIT :limit OFFSET :offset"
            ),
            {"t": tenant_id, "limit": limit, "offset": offset},
        )
    ).all()

    return CorpusDocumentListResponse(
        documents=[_row_to_metadata(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/documents/{document_id}",
    response_model=CorpusDocumentDetail,
)
async def get_document(
    document_id: UUID,
    tenant_id: UUID = Depends(get_tenant_id),
    session: AsyncSession = Depends(get_session),
) -> CorpusDocumentDetail:
    row = (
        await session.execute(
            sql_text(
                f"SELECT {_METADATA_COLUMNS}, extracted_text "
                "FROM documents WHERE id = :id AND tenant_id = :t"
            ),
            {"id": document_id, "t": tenant_id},
        )
    ).one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "document not found")
    return CorpusDocumentDetail(
        **_row_to_metadata(row).model_dump(),
        extracted_text=row.extracted_text,
    )


@router.delete(
    "/documents/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_document(
    document_id: UUID,
    tenant_id: UUID = Depends(get_tenant_id),
    session: AsyncSession = Depends(get_session),
    storage: ObjectStorage = Depends(get_object_storage),
) -> Response:
    # FK CASCADE (documents → chunks) removes any fingerprints with the
    # row. A rowcount of 0 is the not-found case.
    result = await session.execute(
        sql_text("DELETE FROM documents WHERE id = :id AND tenant_id = :t"),
        {"id": document_id, "t": tenant_id},
    )
    if result.rowcount == 0:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "document not found")
    await session.commit()
    # Best-effort raw-bytes cleanup; the row is already gone, so a storage
    # error shouldn't fail the request — log and move on.
    try:
        storage.delete(f"{tenant_id}/{document_id}")
    except OSError:
        logger.warning(
            "failed to delete stored object for document %s", document_id
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def _load_document(
    session: AsyncSession, document_id: UUID
) -> CorpusDocument:
    row = (
        await session.execute(
            sql_text(
                f"SELECT {_METADATA_COLUMNS} FROM documents WHERE id = :id"
            ),
            {"id": document_id},
        )
    ).one()
    return _row_to_metadata(row)


def _row_to_metadata(row) -> CorpusDocument:
    return CorpusDocument(
        id=row.id,
        source_type=row.source_type,
        filename=row.filename,
        mime_type=row.mime_type,
        size_bytes=row.size_bytes,
        sha256=row.sha256.strip() if row.sha256 is not None else None,
        char_length=row.char_length,
        language=row.language,
        fingerprint_status=row.fingerprint_status,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
