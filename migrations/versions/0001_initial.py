"""Initial schema.

Everything the engine needs in one revision: the default tenant, the
corpus (documents / chunks / chunks_text), and checks + per-source
results.

Notes on shape:

- `chunks.fingerprints` is a `bigint[]` with a GIN (array_ops) index —
  the `&&` overlap probe in L1 retrieval runs against it.
- Chunk text lives in the `chunks_text` side table so the `chunks` heap
  stays narrow: the GIN → heap candidate fetch touches fewer bytes, and
  text is read only for the top-K survivors.
- `tenant_id` is nullable on corpus tables: NULL marks platform-level
  corpus rows (the bundled sample corpus, folder ingests) that every
  check matches against. The engine runs single-tenant out of the box —
  one default tenant is seeded here.
- The `fingerprint_df` table is deliberately NOT created here. Retrieval
  probes for it with to_regclass() and falls back to full-union `&&`
  when absent — correct on small corpora. Build it with
  `scripts/compute_fingerprint_df.py` once your corpus is large enough
  that retrieval latency matters.

Revision ID: 0001
Revises:
Create Date: 2026-07-18
"""

from __future__ import annotations

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

DEFAULT_TENANT_ID = "00000000-0000-4000-8000-000000000001"


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    op.execute(
        "CREATE TYPE corpus_source_type AS ENUM "
        "('user_upload', 'folder_import', 'wikipedia', 'web')"
    )

    op.execute(
        """
        CREATE TABLE tenants (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            name text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        f"INSERT INTO tenants (id, name) VALUES ('{DEFAULT_TENANT_ID}', 'default')"
    )

    op.execute(
        """
        CREATE TABLE documents (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid REFERENCES tenants(id) ON DELETE CASCADE,
            source_type corpus_source_type NOT NULL DEFAULT 'user_upload',
            source_url text,
            filename text,
            mime_type text,
            size_bytes bigint,
            sha256 text,
            source_uri text,
            extracted_text text,
            char_length integer,
            language text,
            fingerprint_status text NOT NULL DEFAULT 'pending'
                CHECK (fingerprint_status IN ('pending', 'fingerprinted', 'failed')),
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    # Idempotent bulk ingestion: one row per (source_type, source_url).
    op.execute(
        "CREATE UNIQUE INDEX uq_documents_source ON documents (source_type, source_url) "
        "WHERE source_url IS NOT NULL"
    )
    # Idempotent per-tenant upload dedup by content hash.
    op.execute(
        "CREATE UNIQUE INDEX uq_documents_tenant_sha256 ON documents (tenant_id, sha256) "
        "WHERE tenant_id IS NOT NULL AND sha256 IS NOT NULL"
    )
    op.execute("CREATE INDEX ix_documents_tenant ON documents (tenant_id, created_at DESC)")

    op.execute(
        """
        CREATE TABLE chunks (
            id uuid PRIMARY KEY,
            document_id uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            tenant_id uuid,
            chunk_index integer NOT NULL,
            char_start integer NOT NULL,
            char_end integer NOT NULL,
            sentence_count integer NOT NULL,
            fingerprints bigint[] NOT NULL
        )
        """
    )
    op.execute("CREATE INDEX ix_chunks_document ON chunks (document_id)")
    op.execute(
        "CREATE INDEX ix_chunks_fingerprints ON chunks USING gin (fingerprints array_ops)"
    )

    op.execute(
        """
        CREATE TABLE chunks_text (
            chunk_id uuid PRIMARY KEY REFERENCES chunks(id) ON DELETE CASCADE,
            tenant_id uuid,
            text text NOT NULL
        )
        """
    )

    op.execute(
        """
        CREATE TABLE checks (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            status text NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'running', 'complete', 'failed')),
            stage text,
            title text,
            query_text_length integer NOT NULL,
            word_count integer,
            source_count integer,
            query_text text,
            total_matched_chars integer,
            overall_similarity_pct numeric(5, 2),
            error_message text,
            coverage text NOT NULL DEFAULT 'full',
            coverage_reason text,
            checked_chunks integer NOT NULL DEFAULT 0,
            total_chunks integer NOT NULL DEFAULT 0,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            completed_at timestamptz
        )
        """
    )
    op.execute("CREATE INDEX ix_checks_tenant ON checks (tenant_id, created_at DESC)")

    op.execute(
        """
        CREATE TABLE check_results (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            check_id uuid NOT NULL REFERENCES checks(id) ON DELETE CASCADE,
            source_document_id uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            matched_chars integer NOT NULL,
            similarity_pct numeric(5, 2) NOT NULL,
            passages jsonb NOT NULL DEFAULT '[]'::jsonb
        )
        """
    )
    op.execute("CREATE INDEX ix_check_results_check ON check_results (check_id)")

    # Keep updated_at honest on row updates.
    op.execute(
        """
        CREATE FUNCTION set_updated_at() RETURNS trigger AS $$
        BEGIN
            NEW.updated_at = now();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        "CREATE TRIGGER trg_documents_updated_at BEFORE UPDATE ON documents "
        "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
    )
    op.execute(
        "CREATE TRIGGER trg_checks_updated_at BEFORE UPDATE ON checks "
        "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS check_results")
    op.execute("DROP TABLE IF EXISTS checks")
    op.execute("DROP TABLE IF EXISTS chunks_text")
    op.execute("DROP TABLE IF EXISTS chunks")
    op.execute("DROP TABLE IF EXISTS documents")
    op.execute("DROP TABLE IF EXISTS tenants")
    op.execute("DROP TABLE IF EXISTS fingerprint_df")
    op.execute("DROP FUNCTION IF EXISTS set_updated_at()")
    op.execute("DROP TYPE IF EXISTS corpus_source_type")
