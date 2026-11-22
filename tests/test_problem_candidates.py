import pytest

from pro2.candidate_solution import CandidateSolution
from pro2.problem import Problem


def test_problem_starts_with_no_candidates():
    problem = Problem(problem_id="P001", description="GPU memory bottleneck")

    assert problem.candidate_solutions == []


def test_problem_can_add_candidate_solution():
    problem = Problem(problem_id="P001", description="GPU memory bottleneck")
    candidate = CandidateSolution(
        candidate_id="C001",
        description="Reduce batch size",
        belief=0.5,
    )

    problem.add_candidate(candidate)

    assert len(problem.candidate_solutions) == 1
    assert problem.candidate_solutions[0] is candidate


def test_problem_can_track_multiple_candidates():
    problem = Problem(problem_id="P001", description="GPU memory bottleneck")

    first = CandidateSolution("C001", "Reduce batch size", 0.5)
    second = CandidateSolution("C002", "Use gradient checkpointing", 0.7)

    problem.add_candidate(first)
    problem.add_candidate(second)

    assert len(problem.candidate_solutions) == 2


def test_problem_rejects_duplicate_candidate_ids():
    problem = Problem(problem_id="P001", description="GPU memory bottleneck")

    first = CandidateSolution("C001", "Solution A", 0.5)
    second = CandidateSolution("C001", "Solution B", 0.7)

    problem.add_candidate(first)

    with pytest.raises(ValueError):
        problem.add_candidate(second)


def test_candidates_belong_to_independent_problems():
    memory_problem = Problem("P001", "GPU memory")
    io_problem = Problem("P002", "Data loading")

    candidate = CandidateSolution("C001", "Optimize memory", 0.5)
    memory_problem.add_candidate(candidate)

    assert len(memory_problem.candidate_solutions) == 1
    assert io_problem.candidate_solutions == []
