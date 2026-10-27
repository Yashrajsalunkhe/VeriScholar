"""Run the engine over PAN-PC-11 and print a plagdet report.

Source documents are indexed into the engine's Postgres corpus, each
suspicious document is matched against it, and the merged matched regions
become plagdet detections. When the corpus or a Postgres isn't available
the run emits a [NO CORPUS] placeholder and exits 0, so the script is
safe to invoke before the corpus has been fetched.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from datasets.pan_pc_11.loader import load_cases  # noqa: E402
from plagdet import Passage, plagdet  # noqa: E402


def _load_source_texts(part: str) -> list[str]:
    """Read every source document for the partition into memory.

    Sources are the corpus the engine indexes; suspicious documents are
    matched against it. Returns an empty list if the source directory is
    absent (the caller treats that as no-corpus).
    """
    from datasets.pan_pc_11.loader import CORPUS_DIR

    src_dir = CORPUS_DIR / part / "source-document"
    if not src_dir.is_dir():
        return []
    return [
        p.read_text(encoding="utf-8", errors="replace")
        for p in sorted(src_dir.rglob("*.txt"))
    ]


def _database_reachable() -> bool:
    """True if DATABASE_URL points at a reachable Postgres. The engine
    predictor needs one to index the corpus; without it we fall back to
    the no-corpus placeholder rather than crash."""
    import os

    from sqlalchemy import create_engine
    from sqlalchemy import text as sql_text

    dsn = os.environ.get(
        "DATABASE_URL", "postgresql+psycopg://noplag:noplag@localhost:5432/noplag"
    )
    try:
        eng = create_engine(dsn)
        with eng.connect() as conn:
            conn.execute(sql_text("SELECT 1"))
        eng.dispose()
        return True
    except Exception:
        return False


def _format_body(
    result,
    positive: int,
    skipped: int,
) -> str:
    return (
        "## Engine eval (PAN-PC-11)\n"
        "\n"
        "```\n"
        f"plagdet: {result.plagdet:.3f}\n"
        f"precision: {result.precision:.3f}  "
        f"recall: {result.recall:.3f}  "
        f"granularity: {result.granularity:.3f}\n"
        f"Cases: {positive} positive, {skipped} skipped\n"
        "```\n"
    )


def _format_no_corpus_body(reason: str) -> str:
    """Report body for runs where no corpus was available.

    The corpus isn't checked into the repo and isn't present until
    `fetch.py` runs, so eval here would score against an empty document
    set. Rather than fail, we report a neutral plagdet=0.0 with an
    explicit no-corpus marker. The [NO CORPUS] tag makes clear the number
    is a placeholder, not a real measurement.
    """
    return (
        "## Engine eval (PAN-PC-11)\n"
        "\n"
        "[NO CORPUS] PAN-PC-11 was not available for this run, so the score "
        "below is a placeholder and not a meaningful measurement. Real scores "
        "appear once the corpus is fetched (see "
        "`eval/datasets/pan_pc_11/fetch.py`).\n"
        "\n"
        "```\n"
        "plagdet: 0.000  status: no-corpus\n"
        f"reason: {reason}\n"
        "```\n"
    )


def _emit(body: str, output: Path | None) -> None:
    print(body, end="")
    if output:
        output.write_text(body)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--part", default="test", help="Corpus partition to evaluate.")
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Write the report body to this file in addition to stdout.",
    )
    args = parser.parse_args()

    try:
        cases = list(load_cases(part=args.part, include_negatives=True))
    except FileNotFoundError as e:
        # Corpus directory missing or unreachable — expected before
        # fetch.py has run. Report a no-corpus placeholder and exit 0.
        # Other failures (XML parse errors, etc.) propagate and fail the
        # run as they should.
        _emit(_format_no_corpus_body(str(e)), args.output)
        return 0

    if not cases:
        # Directory exists but holds no suspicious documents. plagdet over
        # empty truth/detection sets would score a misleading 1.0, so treat
        # this the same as a missing corpus.
        _emit(
            _format_no_corpus_body(
                f"No suspicious documents found for partition '{args.part}'."
            ),
            args.output,
        )
        return 0

    source_texts = _load_source_texts(args.part)
    if not source_texts or not _database_reachable():
        # The engine predictor needs both the source corpus and a Postgres
        # to index it into. Either missing -> placeholder, exit 0.
        reason = (
            "No source documents found." if not source_texts
            else "DATABASE_URL is not reachable; the engine predictor needs Postgres."
        )
        _emit(_format_no_corpus_body(reason), args.output)
        return 0

    truths: list[Passage] = []
    positive = 0
    skipped = 0
    suspicious: list[tuple[str, str]] = []

    for case in cases:
        name = case.suspicious_doc.name
        if case.annotations:
            positive += 1
            for ann in case.annotations:
                truths.append(
                    Passage(doc_id=name, start=ann.this_offset, length=ann.this_length)
                )
        else:
            skipped += 1
        suspicious.append(
            (name, case.suspicious_doc.read_text(encoding="utf-8", errors="replace"))
        )

    # Engine-backed detection: index the sources, then match each
    # suspicious document against the corpus.
    import asyncio

    import engine_predictor

    regions = asyncio.run(engine_predictor.evaluate(source_texts, suspicious))
    detections = [
        Passage(doc_id=name, start=start, length=end - start)
        for name, spans in regions.items()
        for start, end in spans
    ]

    result = plagdet(truths, detections)
    body = _format_body(result, positive=positive, skipped=skipped)
    _emit(body, args.output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
