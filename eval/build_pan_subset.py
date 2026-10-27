#!/usr/bin/env python3
"""Build a stratified PAN-PC-11 subset partition for parameter sweeps.

The full corpus is 11,093 suspicious docs; sweeping winnowing parameters over
all of it takes hours per configuration. This carves a smaller,
*match-type-balanced* partition so chosen params don't overfit one match type:

  - verbatim   : suspicious docs whose plagiarism is all obfuscation="none"
  - obfuscated : docs with low/high (paraphrase) obfuscation
  - negative   : docs with no plagiarism (precision signal)

Critically, a PAN suspicious doc can plagiarize a source in any part, so the
subset MUST pull in each selected doc's referenced source documents — otherwise
recall is silently broken (the source isn't in the indexed corpus). Selection is
deterministic (seeded) so the baseline is reproducible.

Output: external-detection-corpus/<part>/{suspicious-document,source-document}/
filled with symlinks (no 4 GB copy). Run the eval with --part <part>.

    python eval/build_pan_subset.py --part subset --verbatim 150 --obfuscated 150 --negative 100
"""

from __future__ import annotations

import argparse
import random
from pathlib import Path
from xml.etree import ElementTree as ET

HERE = Path(__file__).parent
CORPUS = HERE / "datasets" / "pan_pc_11" / "external-detection-corpus"
SRC_TEST = CORPUS / "test" / "source-document"
SUSP_TEST = CORPUS / "test" / "suspicious-document"


def classify(xml: Path) -> tuple[str, set[str]]:
    """Return (bucket, referenced source filenames) for a suspicious xml."""
    obfs, refs = [], set()
    for feat in ET.parse(xml).getroot().findall(".//feature[@name='plagiarism']"):
        obfs.append((feat.get("obfuscation") or "none").lower())
        ref = feat.get("source_reference")
        if ref:
            refs.add(ref)
    if not obfs:
        return "negative", refs
    return ("verbatim" if all(o == "none" for o in obfs) else "obfuscated"), refs


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--part", default="subset")
    ap.add_argument("--verbatim", type=int, default=150)
    ap.add_argument("--obfuscated", type=int, default=150)
    ap.add_argument("--negative", type=int, default=100)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    buckets: dict[str, list[tuple[Path, set[str]]]] = {"verbatim": [], "obfuscated": [], "negative": []}
    for xml in sorted(SUSP_TEST.rglob("*.txt")):  # iterate by .txt, derive .xml
        xmlp = xml.with_suffix(".xml")
        if not xmlp.exists():
            continue
        bucket, refs = classify(xmlp)
        buckets[bucket].append((xml, refs))

    rng = random.Random(args.seed)
    want = {"verbatim": args.verbatim, "obfuscated": args.obfuscated, "negative": args.negative}
    picked: list[tuple[Path, set[str]]] = []
    for b, n in want.items():
        pool = buckets[b]
        picked += rng.sample(pool, min(n, len(pool)))
        print(f"{b:<11}: pool {len(pool):>5}  picked {min(n, len(pool))}")

    # Index source docs by basename so source_reference -> file path.
    src_index = {p.name: p for p in SRC_TEST.rglob("*.txt")}
    needed_src: set[str] = set()
    for _susp, refs in picked:
        needed_src |= refs
    found = [src_index[r] for r in sorted(needed_src) if r in src_index]
    missing = sorted(needed_src - set(src_index))
    print(f"\nreferenced sources: {len(needed_src)} (found {len(found)}, missing {len(missing)})")

    out_susp = CORPUS / args.part / "suspicious-document"
    out_src = CORPUS / args.part / "source-document"
    for d in (out_susp, out_src):
        d.mkdir(parents=True, exist_ok=True)
        for old in d.iterdir():
            old.unlink()

    def link(target: Path, into: Path) -> None:
        dst = into / target.name
        if not dst.exists():
            dst.symlink_to(target.resolve())

    for susp_txt, _refs in picked:
        link(susp_txt, out_susp)
        link(susp_txt.with_suffix(".xml"), out_susp)
    for s in found:
        link(s, out_src)

    print(f"\nsubset '{args.part}': {len(picked)} suspicious + {len(found)} source docs")
    print(f"  {out_susp}\n  {out_src}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
