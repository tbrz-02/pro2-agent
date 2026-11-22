# Pro2 Agent — Implementation Design

## Objective
Implement a modular, local-first research agent based on Pro2 Agent:
Scientific Problem Probing for Autonomous AI R&D (arXiv:2611.25357).

The primary research state must be the Problem State, not merely a code
snapshot or score history.

## Core Problem State
Represent:
- Performance-limiting problems P_i
- Diagnostic problem evidence E_i^p
- Candidate solutions C_i,j
- Belief confidence b_i,j in [0,1]
- Candidate-solution evidence E_i,j^c
- Historical problem exploration count N(P_i)
- Task constraints, evaluation metrics, compute budgets, and state history

Every experiment state must be associated with an identifiable code snapshot.

## Phase 1 — Initialization
1. Inspect task, dataset, environment, constraints, and evaluation protocol.
2. Run bounded diagnostic profiling.
3. Identify performance-limiting problems from actual evidence.
4. Search literature conditioned on identified problems.
5. Store candidate solutions, references, evidence, and initial beliefs.
6. Construct a modular baseline.
7. Run baseline sanity checks and record measurements.
8. Anchor baseline to Git.

## Phase 2 — Scientific Exploration
Hypothesis structure:
(P_k, C_k, m, delta_m_t)

Prioritization:
Priority(H_t) = b_k,m / (N(P_k) + 1)

Loop:
1. Select highest-priority hypothesis.
2. Increment problem exploration count.
3. Create an isolated Git branch from current best.
4. Apply minimal mutation.
5. Reviewer verifies single-variable isolation.
6. Execute within resource and time budgets.
7. Record metrics, telemetry, score delta, and evidence.
8. If target improvement is achieved, retain the branch and increase belief.
9. Otherwise or on crash/instability, rollback and decrease belief.
10. Distill insights and re-score pending hypotheses.

Belief update magnitudes are configurable because the paper does not
specify universal numeric values.

## Phase 3 — Engineering Optimization
Start from the best scientifically validated snapshot.

Use MCTS/UCT to explore engineering configurations, such as:
- Learning-rate schedules
- Batch size
- Weight decay
- Ensemble weights
- Execution efficiency parameters

Enforce pipeline invariance: engineering mutations must not alter
scientific architecture, algorithms, or data representation.

Cost-aware reward:
R(N_k) = delta_h(N_k) - gamma * ln(T_exec(N_k))

Keep reward metrics oriented so higher is better. Gamma must be
configurable and calibrated for the task.

## Multi-Agent Governance
PI Agent:
- Maintains Problem State
- Selects research goals
- Allocates budgets
- Supervises trajectory

Implementation Agent:
- Implements approved mutations
- Executes and diagnoses experiments
- Retrieves/writes experience

Reviewer Agent:
- Reviews code and hypothesis isolation
- Enforces phase-specific mutation restrictions
- Approves, requests revision, or requests fallback

Do not hard-code a specific model vendor into the core.

## Runtime Safety
- Isolated experiment workspaces
- Git-backed snapshots and rollback
- Time, memory, CPU/GPU and process budgets
- Timeout and stall detection
- NaN/Inf detection
- Explicit approval boundaries for risky actions
- No access to hidden evaluation labels
- No experiment allowed to silently overwrite the best-known solution

## Experience Bank
Persist structured:
- Error signatures
- Root causes
- Verified fixes
- Failed configurations
- Successful techniques
- Source/evidence
- Context and applicability

Retrieve before implementation. Store after verified resolution.

## Architecture
Implement deterministic core logic before LLM integration.

Suggested modules:
- models
- state
- hypothesis
- prioritization
- evidence
- experiment
- git_sandbox
- evaluation
- optimizer
- experience
- agents
- orchestration
- resource_monitor

## Engineering Constraints
- Python 3.12+
- Existing uv, pytest, Ruff, Git, just, watchexec
- Avoid unnecessary dependencies
- Type annotations
- Deterministic core where possible
- Tests before implementation (RED -> GREEN -> REFACTOR)
- Small modules and commits
- JSON-compatible persistence initially; database only when justified
- No unnecessary Kubernetes/cloud deployment for the initial local implementation

## Non-Goals for Initial Version
- Reproducing Google's exact proprietary agent stack
- Assuming access to Google's internal prompts or infrastructure
- Unbounded autonomous execution
- Multi-agent orchestration before core contracts are tested
- Fabricating paper details where the paper leaves parameters unspecified

## Definition of Done
- Each core mechanism has tests
- Hypothesis selection is reproducible
- Belief updates are bounded
- Experiment branches are isolated
- Rollback is tested
- Evidence is traceable to experiment records
- Optimizer obeys scientific-pipeline invariance
- Budgets and watchdogs are enforced
- Research state can survive process restarts
- Full test and lint checks pass
