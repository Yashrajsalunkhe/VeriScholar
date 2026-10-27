"""Canonical stop-list loader — file override, hashing, caching, cross-layer assert.

The DB-backed path is exercised against a fake session so these stay no-DB; the
file override and the pure hashing/assert helpers need no session at all.
"""

from __future__ import annotations

import pytest

from noplag_engine.retrieval import stop_list as sl


@pytest.fixture(autouse=True)
def _clear_cache():
    sl.clear_cache()
    yield
    sl.clear_cache()


class _Scalars:
    def __init__(self, value):
        self._value = value

    def scalar(self):
        return self._value

    def scalars(self):
        return self

    def all(self):
        return self._value


class _DFSession:
    """Returns a regclass on the first execute (table present), then the top-N rows."""

    def __init__(self, rows, present=True):
        self._rows = rows
        self._present = present
        self.calls = 0

    async def execute(self, *_args, **_kwargs):
        self.calls += 1
        if self.calls == 1:
            return _Scalars("public.fingerprint_df" if self._present else None)
        return _Scalars(self._rows)


def test_content_hash_is_order_independent():
    assert sl.content_hash([3, 1, 2]) == sl.content_hash([1, 2, 3])
    assert sl.content_hash([1, 2]) != sl.content_hash([1, 2, 3])


def test_content_hash_handles_signed_fingerprints():
    # bigint is signed; negatives must hash distinctly from their magnitude.
    assert sl.content_hash([-1]) != sl.content_hash([1])


async def test_load_from_file(tmp_path, monkeypatch):
    path = tmp_path / "stop.txt"
    path.write_text("100\n200\n# a comment\n\n300\n")
    monkeypatch.setenv("STOP_FINGERPRINTS_FILE", str(path))
    # File path returns before the session is touched, so None is safe.
    got = await sl.load_stop_list(None)
    assert got.fingerprints == frozenset({100, 200, 300})
    assert got.source == f"file:{path}"
    assert got.content_hash == sl.content_hash([100, 200, 300])


async def test_load_from_df_top_n(monkeypatch):
    monkeypatch.delenv("STOP_FINGERPRINTS_FILE", raising=False)
    session = _DFSession(rows=[10, 20, 30])
    got = await sl.load_stop_list(session, n=3)
    assert got.fingerprints == frozenset({10, 20, 30})
    assert got.source == "fingerprint_df"


async def test_absent_table_yields_empty(monkeypatch):
    monkeypatch.delenv("STOP_FINGERPRINTS_FILE", raising=False)
    session = _DFSession(rows=[], present=False)
    got = await sl.load_stop_list(session, n=5)
    assert got.fingerprints == frozenset()
    assert got.source == "empty"


async def test_result_is_cached_by_n(monkeypatch):
    monkeypatch.delenv("STOP_FINGERPRINTS_FILE", raising=False)
    session = _DFSession(rows=[1, 2])
    first = await sl.load_stop_list(session, n=2)
    again = await sl.load_stop_list(session, n=2)
    assert again is first
    # Only the first call hit the DB (2 executes: probe + select).
    assert session.calls == 2


async def test_force_reload_bypasses_cache(monkeypatch):
    monkeypatch.delenv("STOP_FINGERPRINTS_FILE", raising=False)
    session = _DFSession(rows=[1, 2])
    await sl.load_stop_list(session, n=2)
    await sl.load_stop_list(session, n=2, force_reload=True)
    assert session.calls == 4


async def test_missing_session_without_file_raises(monkeypatch):
    monkeypatch.delenv("STOP_FINGERPRINTS_FILE", raising=False)
    with pytest.raises(ValueError):
        await sl.load_stop_list(None)


def test_assert_hash_matches():
    s = sl._build([1, 2, 3], "fingerprint_df")
    sl.assert_hash_matches(s, s.content_hash)  # no raise
    with pytest.raises(ValueError):
        sl.assert_hash_matches(s, "deadbeef")


def test_membership_and_len():
    s = sl._build([7, 8], "fingerprint_df")
    assert 7 in s and 9 not in s
    assert len(s) == 2
