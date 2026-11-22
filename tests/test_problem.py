from pro2.problem import Problem


def test_problem_has_unique_identifier():
    problem = Problem(problem_id="P001", description="GPU memory bottleneck")

    assert problem.problem_id == "P001"


def test_problem_stores_description():
    problem = Problem(problem_id="P001", description="GPU memory bottleneck")

    assert problem.description == "GPU memory bottleneck"


def test_problem_starts_with_empty_profiling_evidence():
    problem = Problem(problem_id="P001", description="GPU memory bottleneck")

    assert problem.profiling_evidence == []


def test_problem_starts_with_empty_candidate_solutions():
    problem = Problem(problem_id="P001", description="GPU memory bottleneck")

    assert problem.candidate_solutions == []


def test_problems_have_independent_evidence_lists():
    first = Problem(problem_id="P001", description="Memory")
    second = Problem(problem_id="P002", description="I/O")

    first.profiling_evidence.append("Peak memory: 9 GB")

    assert second.profiling_evidence == []
