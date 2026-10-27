from math import log2

from plagdet import Passage, plagdet


def test_no_truth_no_detection_is_perfect():
    r = plagdet([], [])
    assert r.precision == 1.0
    assert r.recall == 1.0
    assert r.granularity == 1.0
    assert r.plagdet == 1.0


def test_perfect_detection():
    truth = [Passage("doc1", 100, 50)]
    det = [Passage("doc1", 100, 50)]
    r = plagdet(truth, det)
    assert r.precision == 1.0
    assert r.recall == 1.0
    assert r.granularity == 1.0
    assert r.f_alpha == 1.0
    assert r.plagdet == 1.0


def test_no_detection_yields_zero():
    truth = [Passage("doc1", 100, 50)]
    r = plagdet(truth, [])
    assert r.precision == 1.0
    assert r.recall == 0.0
    assert r.f_alpha == 0.0
    assert r.plagdet == 0.0


def test_detection_without_truth_yields_zero():
    det = [Passage("doc1", 100, 50)]
    r = plagdet([], det)
    assert r.precision == 0.0
    assert r.recall == 1.0
    assert r.f_alpha == 0.0
    assert r.plagdet == 0.0


def test_partial_passage_overlap():
    truth = [Passage("doc1", 100, 100)]
    det = [Passage("doc1", 100, 50)]
    r = plagdet(truth, det)
    assert r.precision == 1.0
    assert r.recall == 0.5
    assert r.granularity == 1.0
    assert abs(r.f_alpha - (2 / 3)) < 1e-9
    assert abs(r.plagdet - (2 / 3)) < 1e-9


def test_fragmented_detection_penalized():
    truth = [Passage("doc1", 0, 100)]
    det = [
        Passage("doc1", 0, 50),
        Passage("doc1", 50, 50),
    ]
    r = plagdet(truth, det)
    assert r.precision == 1.0
    assert r.recall == 1.0
    assert r.granularity == 2.0
    assert r.f_alpha == 1.0
    expected = 1.0 / log2(3)
    assert abs(r.plagdet - expected) < 1e-9
    assert r.plagdet < 1.0


def test_over_detection_reduces_precision():
    truth = [Passage("doc1", 100, 50)]
    det = [
        Passage("doc1", 100, 50),
        Passage("doc1", 500, 50),
    ]
    r = plagdet(truth, det)
    assert r.precision == 0.5
    assert r.recall == 1.0
    assert r.granularity == 1.0
    assert abs(r.f_alpha - (2 / 3)) < 1e-9
    assert abs(r.plagdet - (2 / 3)) < 1e-9


def test_cross_document_detection_does_not_count():
    truth = [Passage("doc2", 100, 50)]
    det = [Passage("doc1", 100, 50)]
    r = plagdet(truth, det)
    assert r.precision == 0.0
    assert r.recall == 0.0
    assert r.plagdet == 0.0


def test_multi_document_aggregation():
    truth = [
        Passage("doc1", 0, 100),
        Passage("doc2", 200, 50),
    ]
    det = [
        Passage("doc1", 0, 100),
        Passage("doc2", 200, 25),
    ]
    r = plagdet(truth, det)
    assert r.precision == 1.0
    assert abs(r.recall - 125 / 150) < 1e-9
    assert r.granularity == 1.0


def test_alpha_weight_affects_f_score():
    truth = [Passage("doc1", 0, 100)]
    det = [Passage("doc1", 0, 50)]
    r1 = plagdet(truth, det, alpha=1.0)
    r2 = plagdet(truth, det, alpha=2.0)
    assert abs(r1.f_alpha - 2 / 3) < 1e-9
    assert r2.f_alpha < r1.f_alpha
