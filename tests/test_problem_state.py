from pro2.problem_state import ProblemState


def test_problem_state_stores_identified_problems():
    state = ProblemState()

    assert state.problems == []


def test_problem_state_tracks_baseline_snapshot():
    state = ProblemState(baseline_id="baseline-001")

    assert state.baseline_id == "baseline-001"


def test_problem_state_starts_with_zero_exploration_iterations():
    state = ProblemState()

    assert state.iteration == 0


def test_problem_state_can_hold_multiple_problems():
    state = ProblemState(
        problems=[
            "GPU memory bottleneck",
            "Data loading bottleneck",
        ]
    )

    assert len(state.problems) == 2
    assert "GPU memory bottleneck" in state.problems
    assert "Data loading bottleneck" in state.problems
