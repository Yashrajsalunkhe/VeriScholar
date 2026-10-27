"""FastAPI app factory for the engine.

VeriScholar AI extends the Noplag Engine with scholarly corpus management,
semantic retrieval, and hybrid scoring.  Original Noplag Engine code is
preserved — this app factory adds the new routes alongside the existing ones.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from noplag_engine.api.v1 import checks, corpus, scholarly

_DEMO_DIR = Path(__file__).resolve().parent.parent / "demo"


def create_app() -> FastAPI:
    product_name = os.environ.get("VS_PRODUCT_NAME", "VeriScholar AI")

    app = FastAPI(
        title=product_name,
        description=(
            "Open-source research plagiarism detection engine. "
            "Built on the Noplag Engine (Apache-2.0) with scholarly "
            "corpus ingestion, hybrid matching, and transparent scoring."
        ),
        version="0.2.0",
    )

    # ── CORS ────────────────────────────────────────────────────────
    cors_origins = os.environ.get(
        "VS_CORS_ORIGINS", "http://localhost:3000,http://localhost:5173"
    ).split(",")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in cors_origins if o.strip()],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Health ──────────────────────────────────────────────────────
    @app.get("/health")
    def health() -> dict[str, str]:
        """Liveness probe — no DB call. Used by uptime monitors and compose
        healthchecks."""
        return {"status": "ok", "engine": product_name}

    # ── Original Noplag Engine routes ───────────────────────────────
    app.include_router(checks.router, prefix="/v1/checks", tags=["checks"])
    app.include_router(corpus.router, prefix="/v1/corpus", tags=["corpus"])

    # ── VeriScholar scholarly routes ────────────────────────────────
    app.include_router(
        scholarly.router, prefix="/v1/scholarly", tags=["scholarly"]
    )

    # Bare demo page: paste text, run a check, see matched sources. Static
    # files only — the real UI belongs to whatever frontend you build on the
    # /v1 API.
    @app.get("/", include_in_schema=False)
    def demo_index() -> FileResponse:
        return FileResponse(_DEMO_DIR / "index.html")

    app.mount("/demo", StaticFiles(directory=_DEMO_DIR), name="demo")

    return app


# Module-level instance for uvicorn / ASGI servers: `uvicorn noplag_engine.api.app:app`.
app = create_app()
