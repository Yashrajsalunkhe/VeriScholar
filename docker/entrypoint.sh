#!/bin/sh
# Container startup: migrate the schema, seed the sample corpus once,
# then hand off to the CMD (uvicorn).
#
# NOPLAG_LOAD_SAMPLE_CORPUS=0 skips the seed for deployments that bring
# their own corpus.
set -e

alembic upgrade head

if [ "${NOPLAG_LOAD_SAMPLE_CORPUS:-1}" != "0" ]; then
    python scripts/load_sample_corpus.py --if-empty
fi

exec "$@"
