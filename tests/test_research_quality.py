"""Research provenance: unverified findings are never attached as candidates."""

from pro2.candidate_solution import CandidateSolution
from pro2.orchestrator import Engine
from pro2.problem import Problem
from pro2.research import ResearchFinding
from test_orchestrator import make_objective


def test_attach_requires_verified_and_relevant_findings(toy_repo, scripted_agent):
    engine = Engine(make_objective(toy_repo, scripted_agent, research={"enabled": False}))
    problem = Problem(problem_id="P001", description="sparse matrix multiply on large inputs")
    problem.add_candidate(CandidateSolution("C001", "candidate", 0.5))
    engine.state.add_problem(problem)

    findings = [
        ResearchFinding(
            finding_id="R-1",
            title="sparse matrix optimization guide",
            url="https://a",
            summary="matrix multiply optimization",
            source="github",
            query="q",
            verified=True,
        ),
        ResearchFinding(
            finding_id="R-2",
            title="unrelated deployment notes",
            url="https://b",
            summary="s",
            source="github",
            query="q",
            verified=True,
        ),
        ResearchFinding(
            finding_id="R-3",
            title="sparse matrix tuning guide",
            url="https://d",
            summary="s",
            source="github",
            query="q",
            verified=True,
        ),
        ResearchFinding(
            finding_id="R-4",
            title="sparse matrix guide",
            url="https://c",
            summary="s",
            source="duckduckgo",
            query="q",
            verified=False,
        ),
    ]
    engine._attach_research_candidates(findings)

    refs = [r for c in problem.candidate_solutions for r in c.references]
    assert "https://a" in refs
    assert "https://d" in refs
    assert "https://b" not in refs  # no token overlap with the problem
    assert "https://c" not in refs  # unverified: never treated as fact
