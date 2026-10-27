# Contributing

Thanks for your interest in noplag-engine.

## Scope

The engine's job is verbatim / near-verbatim detection against a local
corpus: chunking, winnowing fingerprints, L1 retrieval, L0 alignment, and the
`/v1` API around them. Good contributions make that pipeline faster, more
accurate, easier to operate, or better documented.

Paraphrase detection, semantic matching, and AI-writing detection are on the
product roadmap but intentionally not in this codebase yet — please open an
issue to discuss before investing in a large feature in those directions.

## Setup

Requires **Python 3.12 or newer**.

```sh
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
docker compose up postgres -d
ruff check .
pytest -q
```

Postgres-backed tests skip automatically when no database is reachable at
`DATABASE_URL` (default `postgresql+psycopg://noplag:noplag@localhost:5432/noplag`).

## Pull requests

- Keep changes focused; separate refactors from behavior changes.
- Add or update tests for anything behavioral. The suite must pass with a
  local Postgres.
- Run `ruff check .` before pushing.
- Detection-quality changes (chunking, fingerprinting, retrieval, alignment)
  should include before/after numbers from the PAN-PC-11 harness in `eval/`
  — see `eval/datasets/pan_pc_11/README.md` for how to fetch the corpus and
  run it.
- A change to fingerprinting parameters or the winnowing algorithm breaks
  compatibility with existing corpora (fingerprints only match when the
  corpus side and query side agree). Call that out explicitly in the PR.

## How contributions are incorporated

noplag-engine is the open-source release of a detection engine that is also
developed and maintained internally. In practice:

- **This repository is where you contribute.** Open issues and pull requests
  here — this is the canonical public home of the engine.
- When a pull request is reviewed and accepted, it is **merged here and
  incorporated into our internal development tree**, then flows back out in
  subsequent releases. Your contribution is preserved and credited — accepted
  work is not discarded.
- Because public releases are periodic syncs from that tree, expect a short lag
  between merge and the next published release, and note that published commit
  history may be rebased or squashed on release. Author attribution is retained.
- **For anything non-trivial, open an issue first.** It lets us confirm the
  change fits the engine's scope and our direction before you invest time —
  and avoids a good PR stalling because it conflicts with in-flight internal
  work.

## Developer Certificate of Origin (DCO)

By contributing you certify the [Developer Certificate of Origin](https://developercertificate.org/):
that you wrote the change (or otherwise have the right to submit it) and agree
to license it under this project's **Apache-2.0** license. Sign off every commit
with:

```sh
git commit -s
```

which appends a line to the message:

```
Signed-off-by: Your Name <you@example.com>
```

No paperwork, no CLA — the sign-off is a lightweight, standard assurance (the
same one the Linux kernel and GitLab use) that contributed code can be used in
the project, including in the hosted product built on this engine.

## Reporting issues

Include the engine version (or commit), how you're running it (compose /
bare), the corpus size, and — for detection-quality issues — a minimal
query text + corpus document pair that reproduces the problem.
