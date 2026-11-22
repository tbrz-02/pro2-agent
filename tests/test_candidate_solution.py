import pytest

from pro2.candidate_solution import CandidateSolution


def test_candidate_has_unique_identifier():
    candidate = CandidateSolution(
        candidate_id="C001",
        description="Reduce GPU memory consumption",
        belief=0.5,
    )

    assert candidate.candidate_id == "C001"


def test_candidate_stores_description():
    candidate = CandidateSolution(
        candidate_id="C001",
        description="Reduce GPU memory consumption",
        belief=0.5,
    )

    assert candidate.description == "Reduce GPU memory consumption"


def test_candidate_stores_belief():
    candidate = CandidateSolution(
        candidate_id="C001",
        description="Reduce GPU memory consumption",
        belief=0.7,
    )

    assert candidate.belief == 0.7


def test_candidate_starts_with_empty_evidence():
    candidate = CandidateSolution(
        candidate_id="C001",
        description="Reduce GPU memory consumption",
        belief=0.5,
    )

    assert candidate.evidence == []


def test_candidates_have_independent_evidence():
    first = CandidateSolution(
        candidate_id="C001",
        description="Solution A",
        belief=0.5,
    )
    second = CandidateSolution(
        candidate_id="C002",
        description="Solution B",
        belief=0.5,
    )

    first.evidence.append("Experiment improved performance")

    assert second.evidence == []


def test_belief_rejects_values_outside_zero_to_one():
    with pytest.raises(ValueError):
        CandidateSolution(
            candidate_id="C001",
            description="Invalid belief",
            belief=1.5,
        )


def test_belief_rejects_negative_values():
    with pytest.raises(ValueError):
        CandidateSolution(
            candidate_id="C001",
            description="Invalid belief",
            belief=-0.1,
        )
