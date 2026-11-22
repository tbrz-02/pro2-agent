import pytest

from pro2.problem import Problem
from pro2.problem_state import ProblemState


def test_state_starts_with_no_identified_problems():
    state = ProblemState()

    assert state.identified_problems == []


def test_state_can_register_problem():
    state = ProblemState()
    problem = Problem("P001", "GPU memory bottleneck")

    state.add_problem(problem)

    assert len(state.identified_problems) == 1
    assert state.identified_problems[0] is problem


def test_state_can_register_multiple_problems():
    state = ProblemState()

    memory = Problem("P001", "GPU memory bottleneck")
    data_loading = Problem("P002", "Data loading bottleneck")

    state.add_problem(memory)
    state.add_problem(data_loading)

    assert len(state.identified_problems) == 2


def test_state_rejects_duplicate_problem_ids():
    state = ProblemState()

    state.add_problem(Problem("P001", "GPU memory"))
    duplicate = Problem("P001", "Another memory problem")

    with pytest.raises(ValueError):
        state.add_problem(duplicate)


def test_state_can_retrieve_problem_by_id():
    state = ProblemState()
    problem = Problem("P001", "GPU memory bottleneck")

    state.add_problem(problem)

    assert state.get_problem("P001") is problem


def test_state_returns_none_for_unknown_problem():
    state = ProblemState()

    assert state.get_problem("UNKNOWN") is None


def test_registered_problem_preserves_its_candidates():
    state = ProblemState()
    problem = Problem("P001", "GPU memory bottleneck")

    problem.add_candidate(
        __import__("pro2.candidate_solution", fromlist=["CandidateSolution"]).CandidateSolution(
            "C001", "Reduce batch size", 0.5
        )
    )

    state.add_problem(problem)

    retrieved = state.get_problem("P001")

    assert retrieved is problem
    assert retrieved.candidate_solutions[0].candidate_id == "C001"
