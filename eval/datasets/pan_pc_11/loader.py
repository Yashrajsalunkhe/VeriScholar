"""Iterate cases from the unpacked PAN-PC-11 corpus.

Consumed by `eval/run_eval.py`, which converts the annotations into
`plagdet.Passage` truths. Expects the corpus to be present at
`external-detection-corpus/` relative to this file; run `python fetch.py`
from this directory first.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET

CORPUS_DIR = Path(__file__).parent / "external-detection-corpus"


@dataclass(frozen=True)
class PlagiarismAnnotation:
    source_reference: str
    this_offset: int
    this_length: int
    source_offset: int
    source_length: int
    obfuscation: str | None = None


@dataclass(frozen=True)
class PanCase:
    suspicious_doc: Path
    annotations: tuple[PlagiarismAnnotation, ...]


def load_cases(
    part: str = "test",
    *,
    include_negatives: bool = False,
    corpus_dir: Path | None = None,
) -> Iterator[PanCase]:
    """Yield PanCase objects from the unpacked PAN-PC-11 corpus.

    `part` is the partition name (typically "test" or "training"). On-disk
    names vary by PAN-PC-11 release; pass `corpus_dir` to override if the
    default layout doesn't match.

    When `include_negatives` is True, suspicious documents without a paired
    XML annotation file are also yielded, with `annotations` as an empty
    tuple. The default False matches the eval-path callers (plagdet, CI),
    which only need annotated positives.

    Raises FileNotFoundError if the corpus hasn't been fetched.
    """
    base = (corpus_dir or CORPUS_DIR) / part
    if not base.is_dir():
        raise FileNotFoundError(
            f"Corpus partition not found at {base}. "
            f"Run `python fetch.py` from {CORPUS_DIR.parent} first."
        )

    susp_dir = base / "suspicious-document"
    if not susp_dir.is_dir():
        msg = f"suspicious-document directory not found under {base}."
        raise FileNotFoundError(msg)

    for susp_txt in sorted(susp_dir.rglob("*.txt")):
        xml_path = susp_txt.with_suffix(".xml")
        if not xml_path.exists():
            if include_negatives:
                yield PanCase(suspicious_doc=susp_txt, annotations=())
            continue
        anns = _parse_annotations(xml_path)
        yield PanCase(suspicious_doc=susp_txt, annotations=tuple(anns))


def _parse_annotations(xml_path: Path) -> list[PlagiarismAnnotation]:
    tree = ET.parse(xml_path)
    root = tree.getroot()
    out: list[PlagiarismAnnotation] = []
    for feat in root.findall(".//feature[@name='plagiarism']"):
        out.append(
            PlagiarismAnnotation(
                source_reference=feat.get("source_reference", ""),
                this_offset=int(feat.get("this_offset", "0")),
                this_length=int(feat.get("this_length", "0")),
                source_offset=int(feat.get("source_offset", "0")),
                source_length=int(feat.get("source_length", "0")),
                obfuscation=feat.get("obfuscation"),
            )
        )
    return out
