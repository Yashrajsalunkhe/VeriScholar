"""Scholarly metadata and ingestion pipeline.

Extends the corpus with scholarly metadata fields and an ingestion-job
tracking table for the VeriScholar scholarly pipeline.

Changes:

- ALTER TYPE corpus_source_type: add 'openalex', 'arxiv', 'core',
  'pubmed', 'crossref' so documents ingested from scholarly APIs carry
  their provenance.

- ALTER TABLE documents: add doi, authors (jsonb), abstract,
  publication_year, journal, license_id, full_text_available,
  openalex_id, arxiv_id, pmcid. These are nullable — existing rows
  (Wikipedia sample, user uploads) simply keep NULLs.

- CREATE TABLE scholarly_ingestion_jobs: tracks each scholarly ingestion
  run (OpenAlex harvest, arXiv bulk download, etc.) with status, progress
  counters, and error details. Resumable: a failed/interrupted job can be
  restarted from where it left off.

- CREATE TABLE users: minimal user management for authentication (Phase 6
  will add JWT; for now the table exists so the schema is forward-
  compatible).

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-27
"""

from __future__ import annotations

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Extend corpus_source_type enum ──────────────────────────────
    for value in ("openalex", "arxiv", "core", "pubmed", "crossref"):
        op.execute(
            f"ALTER TYPE corpus_source_type ADD VALUE IF NOT EXISTS '{value}'"
        )

    # ── Scholarly metadata on documents ─────────────────────────────
    op.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS doi text")
    op.execute(
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS authors jsonb"
    )
    op.execute(
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS abstract text"
    )
    op.execute(
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS publication_year integer"
    )
    op.execute(
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS journal text"
    )
    op.execute(
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS license_id text"
    )
    op.execute(
        "ALTER TABLE documents "
        "ADD COLUMN IF NOT EXISTS full_text_available boolean NOT NULL DEFAULT false"
    )
    op.execute(
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS openalex_id text"
    )
    op.execute(
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS arxiv_id text"
    )
    op.execute(
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS pmcid text"
    )

    # Unique indexes for scholarly dedup (DOI is the primary key for
    # cross-source deduplication; OpenAlex/arXiv/PMC IDs are secondary).
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_documents_doi "
        "ON documents (doi) WHERE doi IS NOT NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_documents_openalex_id "
        "ON documents (openalex_id) WHERE openalex_id IS NOT NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_documents_arxiv_id "
        "ON documents (arxiv_id) WHERE arxiv_id IS NOT NULL"
    )

    # ── Scholarly ingestion jobs ────────────────────────────────────
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS scholarly_ingestion_jobs (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            source text NOT NULL,
            job_type text NOT NULL DEFAULT 'discovery',
            query_params jsonb NOT NULL DEFAULT '{}'::jsonb,
            status text NOT NULL DEFAULT 'pending'
                CHECK (status IN (
                    'pending', 'running', 'paused',
                    'completed', 'failed', 'cancelled'
                )),
            total_discovered integer NOT NULL DEFAULT 0,
            total_downloaded integer NOT NULL DEFAULT 0,
            total_indexed integer NOT NULL DEFAULT 0,
            total_skipped integer NOT NULL DEFAULT 0,
            total_failed integer NOT NULL DEFAULT 0,
            cursor_state jsonb,
            error_message text,
            started_at timestamptz,
            completed_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE TRIGGER trg_scholarly_jobs_updated_at "
        "BEFORE UPDATE ON scholarly_ingestion_jobs "
        "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
    )

    # ── Users table (forward-compatible for Phase 6 auth) ──────────
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            email text NOT NULL UNIQUE,
            password_hash text NOT NULL,
            display_name text,
            role text NOT NULL DEFAULT 'user'
                CHECK (role IN ('admin', 'user', 'viewer')),
            is_active boolean NOT NULL DEFAULT true,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE TRIGGER trg_users_updated_at "
        "BEFORE UPDATE ON users "
        "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS users")
    op.execute("DROP TABLE IF EXISTS scholarly_ingestion_jobs")
    # Column drops — reverse of the adds above.
    for col in (
        "pmcid", "arxiv_id", "openalex_id", "full_text_available",
        "license_id", "journal", "publication_year", "abstract",
        "authors", "doi",
    ):
        op.execute(f"ALTER TABLE documents DROP COLUMN IF EXISTS {col}")
    # Note: ALTER TYPE ... DROP VALUE is not supported in PostgreSQL;
    # the enum values persist on downgrade. This is harmless.
