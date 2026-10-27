#!/usr/bin/env python3
"""Download + extract the PAN-PC-11 corpus.

Idempotent: skips download/extract if the archives or extracted directory
are already present.

The Zenodo record ships the corpus as a two-part RAR archive
(pan-plagiarism-corpus-2011.part1.rar + .part2.rar). Python's stdlib
cannot read RAR, so extraction shells out to `unar` (preferred; handles
multi-volume archives) or `bsdtar`. Install one of them first:

    macOS:          brew install unar
    Debian/Ubuntu:  apt install unar
    fallback:       bsdtar (libarchive), preinstalled on macOS

Usage:
    python fetch.py
    python fetch.py --url https://example.com/alt.zip
    python fetch.py --no-verify --keep-archive

Env vars (used as defaults when CLI flags omitted):
    PAN_PC_11_URL     override the download URL (single archive)
    PAN_PC_11_SHA256  expected SHA256 of the --url archive
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path
from urllib.request import urlopen

HERE = Path(__file__).parent

# Canonical source: Zenodo record 3250095 (PAN-PC-11).
DEFAULT_ZENODO_RECORD = "3250095"
ZENODO_API = f"https://zenodo.org/api/records/{DEFAULT_ZENODO_RECORD}"

ARCHIVE_SUFFIXES = (".zip", ".tar.gz", ".tgz", ".tar", ".rar")

EXTRACTED_DIR = HERE / "external-detection-corpus"


def fetch_zenodo_files() -> list[tuple[str, str | None, str]]:
    """List (url, checksum, filename) for every archive in the Zenodo record.

    `checksum` keeps its algorithm prefix (`md5:...` / `sha256:...`) so the
    caller can verify with the right hash; Zenodo publishes md5 for this
    record. Multi-part RAR volumes come back as separate entries.
    """
    with urlopen(ZENODO_API, timeout=30) as r:
        meta = json.load(r)
    files = meta.get("files", [])
    if not files:
        msg = f"Zenodo record {DEFAULT_ZENODO_RECORD} has no files attached."
        raise RuntimeError(msg)
    out = []
    for f in sorted(files, key=lambda f: f.get("key", "")):
        key = f.get("key", "")
        if key.lower().endswith(ARCHIVE_SUFFIXES):
            url = f["links"].get("self") or f["links"].get("download")
            out.append((url, f.get("checksum") or None, key))
    if not out:
        msg = f"No archive files found in Zenodo record {DEFAULT_ZENODO_RECORD}."
        raise RuntimeError(msg)
    return out


def download(url: str, dest: Path) -> None:
    print(f"Downloading {url}")
    print(f"          -> {dest}")
    with urlopen(url, timeout=300) as r, dest.open("wb") as out:
        total = 0
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            out.write(chunk)
            total += len(chunk)
            print(f"  {total / (1 << 20):.1f} MB", end="\r", flush=True)
    print()


def digest_of(path: Path, algorithm: str) -> str:
    h = hashlib.new(algorithm)
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verify(path: Path, checksum: str) -> bool:
    """Check `path` against `checksum` ('md5:...' / 'sha256:...' / bare sha256)."""
    algorithm, _, expected = checksum.rpartition(":")
    algorithm = algorithm or "sha256"
    print(f"Verifying {path.name} ({algorithm})...")
    actual = digest_of(path, algorithm)
    if actual != expected:
        print("  FAIL: checksum mismatch", file=sys.stderr)
        print(f"        expected: {expected}", file=sys.stderr)
        print(f"        actual:   {actual}", file=sys.stderr)
        return False
    print(f"  OK: matches ({actual[:16]}...)")
    return True


def _extract_rar(first_volume: Path, into: Path) -> None:
    """Extract a (possibly multi-volume) RAR via unar or bsdtar.

    Both tools pick up subsequent .partN.rar volumes automatically as long
    as they sit next to the first one.
    """
    if shutil.which("unar"):
        cmd = ["unar", "-quiet", "-o", str(into), str(first_volume)]
    elif shutil.which("bsdtar"):
        cmd = ["bsdtar", "-x", "-f", str(first_volume), "-C", str(into)]
    else:
        raise RuntimeError(
            "The PAN-PC-11 archive is RAR and neither `unar` nor `bsdtar` is "
            "on PATH. Install one (e.g. `brew install unar` / `apt install "
            "unar`) or extract manually — see README.md."
        )
    subprocess.run(cmd, check=True)


def extract(archives: list[Path], into: Path) -> None:
    """Extract the archive set and normalize the corpus layout.

    Extraction goes to a staging directory first; the
    `external-detection-corpus` directory inside it (the Zenodo RAR wraps
    the corpus in a `pan-plagiarism-corpus-2011/` root) is then moved to
    `into`. If no such directory exists in the archive, the extracted root
    itself becomes `into`.
    """
    primary = archives[0]
    staging = into.parent / ".extract-staging"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    print(f"Extracting {primary.name} -> {into}")

    name = primary.name.lower()
    if name.endswith(".zip"):
        with zipfile.ZipFile(primary) as z:
            z.extractall(staging)
    elif name.endswith((".tar.gz", ".tgz", ".tar")):
        with tarfile.open(primary) as t:
            t.extractall(staging)
    elif name.endswith(".rar"):
        _extract_rar(primary, staging)
    else:
        raise RuntimeError(f"Unsupported archive format: {primary.name}")

    corpus_root = next(
        (d for d in staging.rglob("external-detection-corpus") if d.is_dir()),
        staging,
    )
    if into.exists():
        shutil.rmtree(into)
    shutil.move(str(corpus_root), str(into))
    if staging.exists():
        shutil.rmtree(staging)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Download + extract the PAN-PC-11 corpus.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--url",
        default=os.environ.get("PAN_PC_11_URL"),
        help="Override the download URL (skips Zenodo API lookup).",
    )
    parser.add_argument(
        "--sha256",
        default=os.environ.get("PAN_PC_11_SHA256"),
        help="Expected SHA256 of the --url archive.",
    )
    parser.add_argument(
        "--no-verify",
        action="store_true",
        help="Skip checksum verification entirely.",
    )
    parser.add_argument(
        "--keep-archive",
        action="store_true",
        help="Keep the downloaded archives after extraction.",
    )
    args = parser.parse_args()

    if EXTRACTED_DIR.is_dir() and any(EXTRACTED_DIR.iterdir()):
        print(f"Corpus already extracted at {EXTRACTED_DIR}; nothing to do.")
        return 0

    if args.url:
        sha = f"sha256:{args.sha256}" if args.sha256 else None
        files = [(args.url, sha, Path(args.url).name)]
    else:
        print(f"Looking up archive URLs from Zenodo record {DEFAULT_ZENODO_RECORD}...")
        files = fetch_zenodo_files()

    archives: list[Path] = []
    for url, checksum, filename in files:
        archive = HERE / filename
        if archive.exists():
            print(f"Archive already downloaded at {archive}.")
        else:
            download(url, archive)
        if args.no_verify:
            print(f"Skipping checksum verification for {filename} (--no-verify).")
        elif checksum:
            if not verify(archive, checksum):
                return 2
        else:
            print(f"No checksum available for {filename}; skipping verification.")
        archives.append(archive)

    extract(archives, EXTRACTED_DIR)

    if not args.keep_archive:
        for archive in archives:
            archive.unlink()
            print(f"Removed archive {archive.name} (use --keep-archive to retain).")

    print(f"\nCorpus ready at: {EXTRACTED_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
