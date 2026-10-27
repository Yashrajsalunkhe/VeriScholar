"""Build the bundled sample corpus from English Wikipedia.

Fetches the plain-text extract of the level-3 vital articles (roughly a
thousand of the encyclopedia's most substantial entries) through the
public MediaWiki API and writes them to `data/sample_corpus.jsonl.gz` —
one JSON object per line: {"title", "url", "text"}.

The bundled artifact exists so `docker compose up` yields a working demo
with real matches on first run. It is NOT meant to be a serious corpus;
bring your own via `scripts/ingest_folder.py` or the /v1/corpus API.

Article text is CC BY-SA 4.0 (see data/SAMPLE_CORPUS_LICENSE.md); each
record keeps its source URL for attribution.

Run (network required; ~5 minutes):

    python scripts/build_sample_corpus.py
    python scripts/build_sample_corpus.py --limit 200   # smaller build
"""

from __future__ import annotations

import argparse
import gzip
import json
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

API = "https://en.wikipedia.org/w/api.php"
USER_AGENT = "noplag-engine sample-corpus builder (https://github.com/NoplagLabs/noplag-engine)"

# Per-article text cap. Keeps the artifact a few MB while retaining far
# more than enough text for demo matches (the article lead + early body).
MAX_CHARS = 12_000

OUT_PATH = Path(__file__).resolve().parent.parent / "data" / "sample_corpus.jsonl.gz"


def _get(params: dict) -> dict:
    """One API call, with polite retry/backoff on rate limiting."""
    query = urllib.parse.urlencode({**params, "format": "json", "maxlag": "5"})
    req = urllib.request.Request(f"{API}?{query}", headers={"User-Agent": USER_AGENT})
    for attempt in range(6):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as exc:
            if exc.code != 429 or attempt == 5:
                raise
            time.sleep(10 * (attempt + 1))
    raise RuntimeError("unreachable")


def _category_members(category: str) -> list[str]:
    members: list[str] = []
    cont: dict = {}
    while True:
        data = _get(
            {
                "action": "query",
                "list": "categorymembers",
                "cmtitle": category,
                "cmlimit": "500",
                **cont,
            }
        )
        members.extend(m["title"] for m in data["query"]["categorymembers"])
        cont = data.get("continue") or {}
        if not cont:
            return members


def vital_article_titles() -> list[str]:
    """Titles of the level-3 vital articles.

    The per-topic subcategories hold the articles' talk pages, so walk
    the topic tree and strip the Talk: prefix.
    """
    titles: list[str] = []
    for sub in _category_members("Category:Wikipedia level-3 vital articles by topic"):
        if not sub.startswith("Category:"):
            continue
        for title in _category_members(sub):
            if title.startswith("Talk:"):
                titles.append(title.removeprefix("Talk:"))
    return titles


def fetch_extract(title: str) -> dict | None:
    """Full plain-text extract for one article, capped at MAX_CHARS."""
    try:
        data = _get(
            {
                "action": "query",
                "prop": "extracts",
                "explaintext": "1",
                "redirects": "1",
                "titles": title,
            }
        )
        pages = data["query"]["pages"]
        page = next(iter(pages.values()))
        text = (page.get("extract") or "").strip()
        if len(text) < 1_000:
            return None  # stubs and misses add nothing to the demo
        return {
            "title": page["title"],
            "url": f"https://en.wikipedia.org/wiki/{urllib.parse.quote(page['title'].replace(' ', '_'))}",
            "text": text[:MAX_CHARS],
        }
    except Exception as exc:
        print(f"  fetch failed for {title!r}: {exc}")
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=None, help="cap the article count")
    parser.add_argument("--workers", type=int, default=2)
    args = parser.parse_args()

    print("listing vital articles…")
    titles = sorted(set(vital_article_titles()))
    if args.limit:
        titles = titles[: args.limit]
    print(f"fetching {len(titles)} articles…")

    started = time.monotonic()
    records: list[dict] = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for i, record in enumerate(pool.map(fetch_extract, titles), 1):
            if record is not None:
                records.append(record)
            if i % 100 == 0:
                print(f"  {i}/{len(titles)} ({time.monotonic() - started:.0f}s)")

    records.sort(key=lambda r: r["title"])
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(OUT_PATH, "wt", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    size_mb = OUT_PATH.stat().st_size / 1e6
    print(f"wrote {len(records)} articles -> {OUT_PATH} ({size_mb:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
