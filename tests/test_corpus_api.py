"""Integration tests for the /v1/corpus API.

Real Postgres via `async_client`. These cover the upload → extract →
store round trip per format, list/detail/delete, and the error statuses.

Raw upload bytes go to a per-test tmp dir via `CORPUS_STORAGE_DIR`, which
`get_object_storage` reads at call time — so no app rebuild is needed to
isolate the object store.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from noplag_engine.api.deps import DEFAULT_TENANT_ID

FIXTURES = Path(__file__).resolve().parent / "fixtures"

PDF = ("sample.pdf", "application/pdf", "PDF ingestion marker charlie.")
DOCX = (
    "sample.docx",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "DOCX ingestion marker bravo.",
)
TXT = ("sample.txt", "text/plain", "TXT ingestion marker alpha.")


@pytest.fixture(autouse=True)
def _isolated_storage(tmp_path, monkeypatch):
    monkeypatch.setenv("CORPUS_STORAGE_DIR", str(tmp_path))
    return tmp_path


async def _upload(async_client, fixture) -> dict:
    name, content_type, _marker = fixture
    data = (FIXTURES / name).read_bytes()
    res = await async_client.post(
        "/v1/corpus/documents",
        files={"file": (name, data, content_type)},
    )
    assert res.status_code == 201, res.text
    return res.json()


# --- upload + round trip per format ---------------------------------


@pytest.mark.parametrize("fixture", [PDF, DOCX, TXT], ids=["pdf", "docx", "txt"])
async def test_upload_returns_pending_metadata(async_client, fixture):
    name, content_type, _marker = fixture
    doc = await _upload(async_client, fixture)

    assert doc["source_type"] == "user_upload"
    assert doc["filename"] == name
    assert doc["mime_type"] == content_type
    # Fingerprint-ready, not yet fingerprinted — the background pass's
    # input signal (it runs after the response is sent).
    assert doc["fingerprint_status"] == "pending"
    assert doc["char_length"] > 0
    assert doc["size_bytes"] > 0
    assert len(doc["sha256"]) == 64
    # The list/create shape never leaks the full text.
    assert "extracted_text" not in doc


@pytest.mark.parametrize("fixture", [PDF, DOCX, TXT], ids=["pdf", "docx", "txt"])
async def test_detail_round_trips_extracted_text(async_client, fixture):
    _name, _content_type, marker = fixture
    created = await _upload(async_client, fixture)

    res = await async_client.get(f"/v1/corpus/documents/{created['id']}")
    assert res.status_code == 200, res.text
    detail = res.json()
    assert marker in detail["extracted_text"]
    assert detail["char_length"] == len(detail["extracted_text"])


async def test_upload_persists_raw_bytes(async_client, _isolated_storage):
    doc = await _upload(async_client, TXT)
    stored = _isolated_storage / str(DEFAULT_TENANT_ID) / doc["id"]
    assert stored.exists()
    assert stored.read_bytes() == (FIXTURES / "sample.txt").read_bytes()


# --- list -----------------------------------------------------------


async def test_list_paginates_caller_documents(async_client):
    await _upload(async_client, TXT)
    await _upload(async_client, DOCX)

    res = await async_client.get("/v1/corpus/documents")
    assert res.status_code == 200
    body = res.json()
    assert body["total"] == 2
    assert {d["filename"] for d in body["documents"]} == {"sample.txt", "sample.docx"}
    assert body["limit"] == 20 and body["offset"] == 0


# --- delete ---------------------------------------------------------


async def test_delete_removes_row_and_object(async_client, _isolated_storage):
    doc = await _upload(async_client, TXT)
    stored = _isolated_storage / str(DEFAULT_TENANT_ID) / doc["id"]
    assert stored.exists()

    res = await async_client.delete(f"/v1/corpus/documents/{doc['id']}")
    assert res.status_code == 204

    gone = await async_client.get(f"/v1/corpus/documents/{doc['id']}")
    assert gone.status_code == 404
    assert not stored.exists()


async def test_delete_unknown_is_404(async_client):
    res = await async_client.delete(f"/v1/corpus/documents/{uuid4()}")
    assert res.status_code == 404


# --- error statuses -------------------------------------------------


async def test_unsupported_type_is_415(async_client):
    res = await async_client.post(
        "/v1/corpus/documents",
        files={"file": ("scan.png", b"\x89PNG\r\n", "image/png")},
    )
    assert res.status_code == 415


async def test_no_extractable_text_is_422(async_client):
    # A text file of only whitespace yields nothing to fingerprint.
    res = await async_client.post(
        "/v1/corpus/documents",
        files={"file": ("blank.txt", b"   \n\t  \n", "text/plain")},
    )
    assert res.status_code == 422


async def test_corrupt_file_is_422_not_500(async_client):
    # A .pdf with garbage bytes must not leak a pypdf error as a 500.
    res = await async_client.post(
        "/v1/corpus/documents",
        files={"file": ("broken.pdf", b"%PDF-1.4 garbage", "application/pdf")},
    )
    assert res.status_code == 422


async def test_empty_upload_is_400(async_client):
    res = await async_client.post(
        "/v1/corpus/documents",
        files={"file": ("empty.txt", b"", "text/plain")},
    )
    assert res.status_code == 400
