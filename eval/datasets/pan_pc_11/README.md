# PAN-PC-11 corpus

The [PAN Plagiarism Corpus 2011](https://zenodo.org/records/3250095) — the evaluation benchmark for the engine. The harness in `eval/` indexes the PAN source documents into a Postgres corpus, runs every suspicious document through the engine, and scores the detections with the `plagdet` metric.

Reference: Potthast, M., Stein, B., Barrón-Cedeño, A., & Rosso, P. (2010). *An Evaluation Framework for Plagiarism Detection.* COLING 2010, 997–1005.

## Running the eval

Fetch the corpus (below), point `DATABASE_URL` at a scratch Postgres with the engine migrations applied, then from the repo root:

```sh
python eval/run_eval.py --part test
python eval/run_eval.py --part test --output metrics.md
```

`--part` selects the corpus partition directory under `external-detection-corpus/`. `eval/build_pan_subset.py` can carve a smaller stratified partition (symlinks, no copy) for faster parameter sweeps:

```sh
python eval/build_pan_subset.py --part subset --verbatim 150 --obfuscated 150 --negative 100
python eval/run_eval.py --part subset
```

Without the corpus or a reachable Postgres, `run_eval.py` prints a `[NO CORPUS]` placeholder and exits 0.

## Interpreting the score

PAN-PC-11 is deliberately dominated by hard obfuscation — heavy random
paraphrase, machine translation, and summarization (see the `obfuscation`
attribute below). This engine detects verbatim and near-verbatim reuse only,
so expect a split result:

- high plagdet with precision ≥ 0.95 on `obfuscation="none"` cases and on
  the synthetic verbatim set in `eval/test_engine_predictor.py` (~0.8+);
- **near-zero recall — and therefore near-zero overall plagdet — on the
  full corpus**, because fingerprints cannot match text that has been
  rewritten. That is the expected, by-design result for an L0/L1 engine,
  not a bug; paraphrase and semantic layers are what would move it.

When reporting numbers from this harness, always state which partition and
obfuscation mix they were computed over.

## Layout

```
eval/datasets/pan_pc_11/
├── fetch.py                      # download + extract
├── loader.py                     # load_cases() iterator
├── README.md                     # this file
└── external-detection-corpus/    # GITIGNORED — produced by fetch.py
    └── <part>/
        ├── source-document/
        └── suspicious-document/
```

The corpus itself is several GB and is **not committed** — only `fetch.py`, `loader.py`, and this README are tracked. Directory names inside the archive vary slightly between PAN releases; `loader.py` accepts `corpus_dir` and `part` overrides if the unpacked layout doesn't match.

## Fetching

```sh
cd eval/datasets/pan_pc_11
python fetch.py
```

The Zenodo record ships the corpus as a **two-part RAR archive** (`pan-plagiarism-corpus-2011.part1.rar`, ~1.0 GB + `.part2.rar`, ~0.7 GB). Python's standard library cannot read RAR, so `fetch.py` shells out to an external extractor — install one before running:

- `unar` (preferred, handles multi-volume RAR): `brew install unar` / `apt install unar`
- `bsdtar` (libarchive; preinstalled on macOS)

`fetch.py`:

1. Lists the archive files via the Zenodo API (record `3250095`) unless `--url` is given.
2. Downloads all parts to this directory.
3. Verifies each part against the Zenodo-published checksum (md5 for this record).
4. Extracts with `unar`/`bsdtar` (or the stdlib for `.zip`/`.tar.*` overrides) and moves the `external-detection-corpus` directory into place.
5. Removes the archives unless `--keep-archive` is set.

Re-running is a no-op once the corpus is extracted.

Options:

| Flag / env var | Effect |
|---|---|
| `--url <URL>` / `PAN_PC_11_URL` | Override the download source with a single archive (skips Zenodo lookup) |
| `--sha256 <HEX>` / `PAN_PC_11_SHA256` | Expected SHA256 of the `--url` archive |
| `--no-verify` | Skip checksum verification |
| `--keep-archive` | Keep the downloaded archives after extraction |

### Manual download

If the extractor tooling isn't available (or the multi-volume extraction fails on your platform), fetch and unpack by hand:

1. Download both parts from <https://zenodo.org/records/3250095> into this directory.
2. Extract from the first volume — the second is picked up automatically:
   `unar pan-plagiarism-corpus-2011.part1.rar`
3. Move (or symlink) the archive's `external-detection-corpus` directory so it sits at `eval/datasets/pan_pc_11/external-detection-corpus/`, with `<part>/suspicious-document/` and `<part>/source-document/` beneath it.

## XML annotation schema

Each suspicious document `xyz.txt` has a sibling `xyz.xml` containing the ground-truth plagiarism annotations:

```xml
<document reference="suspicious-documentNNN.txt">
  <feature
    name="plagiarism"
    this_offset="2345"
    this_length="412"
    source_reference="source-documentMMM.txt"
    source_offset="8901"
    source_length="412"
    obfuscation="random"
    type="artificial"
  />
  <feature .../>
</document>
```

Each `<feature name="plagiarism">` is one annotated passage:

| Attribute | Meaning |
|---|---|
| `this_offset` / `this_length` | Character offset + length inside the suspicious document |
| `source_reference` | Filename of the source document the passage was copied from |
| `source_offset` / `source_length` | Character offset + length inside the source document |
| `obfuscation` | `none` / `random` / `translation` / `summary` — the corpus-author's paraphrase strategy |
| `type` | `artificial` / `simulated` — how the case was generated |

A suspicious document without an XML pair has no plagiarism (negative case). A single suspicious document can reference multiple source documents via multiple `<feature>` elements.

## Licensing and attribution

The PAN-PC-11 corpus is published on Zenodo by the PAN organizers (Webis group) under **CC BY 4.0**. Cite the paper above when reporting results on it. The corpus is not redistributed in this repository — `fetch.py` downloads it from the [Zenodo record](https://zenodo.org/records/3250095) directly.

## Loader

```python
from eval.datasets.pan_pc_11.loader import load_cases

# Default — only suspicious docs that carry a paired XML annotation
for case in load_cases(part="test"):
    print(case.suspicious_doc.name, "->", len(case.annotations), "annotations")
    for ann in case.annotations:
        print(" ", ann.source_reference, ann.obfuscation)

# Include negatives (suspicious docs with no XML) — yielded with empty
# annotations. Use for false-positive-rate tracking: the engine should
# produce zero matches on these.
for case in load_cases(part="test", include_negatives=True):
    if not case.annotations:
        ...  # negative case: verify engine produces no matches
```

`load_cases()` walks `external-detection-corpus/<part>/suspicious-document/`, pairs each `.txt` with its `.xml` annotation file, and yields `PanCase` objects. By default, suspicious documents without a paired XML are skipped — they are negative cases and don't contribute to recall/precision. Pass `include_negatives=True` to also yield them (with `annotations=()`) for false-positive tracking.

`PanCase.annotations` is a tuple of `PlagiarismAnnotation` frozen dataclasses. The annotation's `source_reference` is the source filename — resolve via `(corpus_dir / part / "source-document" / ann.source_reference)` if the source content is needed at eval time.
