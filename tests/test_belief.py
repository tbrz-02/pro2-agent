import pytest

from pro2.candidate_solution import CandidateSolution


def test_positive_belief_update_is_bounded():
    candidate = CandidateSolution("C001", "fix", 0.95)

    candidate.adjust_belief(0.2)

    assert candidate.belief == 1.0


def test_negative_belief_update_is_bounded():
    candidate = CandidateSolution("C001", "fix", 0.05)

    candidate.adjust_belief(-0.2)

    assert candidate.belief == 0.0


def test_belief_update_preserves_float_precision():
    candidate = CandidateSolution("C001", "fix", 0.5)

    candidate.adjust_belief(0.1)

    assert candidate.belief == pytest.approx(0.6)


def test_candidate_serializes_roundtrip():
    candidate = CandidateSolution(
        "C001",
        "fix",
        0.5,
        evidence=["E1"],
        references=["https://example.test"],
        rationale="because",
        patch=None,
    )

    restored = CandidateSolution.from_dict(candidate.to_dict())

    assert restored.to_dict() == candidate.to_dict()
