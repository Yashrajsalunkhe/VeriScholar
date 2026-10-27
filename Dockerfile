FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /build

COPY pyproject.toml README.md LICENSE ./
COPY src/ ./src/

RUN pip install --upgrade pip \
    && pip wheel --wheel-dir /wheels .


FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/home/app/.local/bin:${PATH}"

WORKDIR /app

RUN useradd --create-home --shell /bin/bash app

USER app

COPY --from=builder /wheels /tmp/wheels
RUN pip install --user --no-cache-dir --no-index --find-links=/tmp/wheels noplag-engine

# Migration config + scripts + the bundled sample corpus, so the container
# can migrate the schema and seed the demo corpus on startup.
COPY --chown=app:app alembic.ini ./
COPY --chown=app:app migrations/ ./migrations/
COPY --chown=app:app scripts/ ./scripts/
COPY --chown=app:app data/ ./data/
COPY --chown=app:app docker/entrypoint.sh ./docker/entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["./docker/entrypoint.sh"]
CMD ["python", "-m", "uvicorn", "noplag_engine.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
