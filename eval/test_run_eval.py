import sys

import run_eval


def test_no_corpus_body_is_flagged():
    body = run_eval._format_no_corpus_body("missing corpus")
    assert "[NO CORPUS]" in body
    assert "status: no-corpus" in body
    assert "plagdet: 0.000" in body
    assert "missing corpus" in body


def test_main_exits_zero_when_corpus_missing(monkeypatch, tmp_path):
    def _raise(*args, **kwargs):
        raise FileNotFoundError("corpus partition not found")

    monkeypatch.setattr(run_eval, "load_cases", _raise)
    out = tmp_path / "metrics.md"
    monkeypatch.setattr(sys, "argv", ["run_eval.py", "--output", str(out)])

    assert run_eval.main() == 0
    body = out.read_text()
    assert "[NO CORPUS]" in body
    assert "status: no-corpus" in body


def test_main_exits_zero_when_corpus_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(run_eval, "load_cases", lambda *a, **k: iter([]))
    out = tmp_path / "metrics.md"
    monkeypatch.setattr(sys, "argv", ["run_eval.py", "--output", str(out)])

    assert run_eval.main() == 0
    assert "status: no-corpus" in out.read_text()


def test_main_propagates_non_filenotfound_errors(monkeypatch):
    def _raise(*args, **kwargs):
        raise ValueError("malformed annotation XML")

    monkeypatch.setattr(run_eval, "load_cases", _raise)
    monkeypatch.setattr(sys, "argv", ["run_eval.py"])

    # Real failures (parse errors, etc.) must still fail the run, not be
    # silently swallowed as a no-corpus placeholder.
    try:
        run_eval.main()
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError to propagate")
