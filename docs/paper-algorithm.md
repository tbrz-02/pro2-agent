# Pro² Agent — Verified Algorithm Specification

Source:
Pro² Agent: Scientific Problem Probing for Autonomous AI R&D
arXiv: 2611.25357

This specification separates verified paper mechanisms from implementation decisions.

Fidelity legend: `PAPER` = directly specified in the paper; `DERIVED` =
mathematically or structurally inferred from it; `ENGINEERING` = implementation
choice that goes beyond or relaxes the paper (the paper itself allows such
adaptations when explicitly marked); `UNVERIFIED` = mechanism not present in the
paper.

## 1. Problem Formulation

Pro² Agent optimizes an evolving Problem State rather than treating
research as pure code-space optimization.

The Problem State contains:

- Identified performance-limiting problems P_i
- Supporting profiling evidence E_i^p
- Candidate solutions C_{i,j}
- Belief confidence b_{i,j}
- Candidate solution evidence E_{i,j}^c

Every Problem State S_t maps to a version-controlled executable
snapshot M_t.

## 2. Phase 1 — Initialization

Objectives:

- Runtime profiling
- Bottleneck discovery
- Targeted literature synthesis
- Candidate solution generation
- Baseline construction
- Mini-batch sanity validation
- Resource and execution health validation

The initial state is S_0 and baseline is M_0.

Validation must be isolated from the experiment sandbox.

## 3. Phase 2 — Scientific Exploration

Scientific exploration uses a resource-bounded best-first hypothesis search.

Hypothesis:

H_t = (P_k, C_{k,m}, Δm_t) — PAPER

Priority:

Pri(H_t) = b_{k,m} / (N(P_k) + 1) — PAPER (`hypothesis_priority`)

Where:

- b_{k,m}: candidate solution belief — PAPER
- N(P_k): previous attempts against problem P_k — PAPER (`problem.record_attempt`)

### Experiment requirements

- Isolated version-controlled branch — PAPER (`git_sandbox.create_experiment`)
- Minimal code mutation — PAPER (budgets; `max_changed_files`, `max_diff_lines`)
- Single-variable isolation — PAPER (enforced by reviewer; DERIVED heuristic
  checks: protected paths, forbidden paths, test-file policy, budget).
- Reviewer approval before execution — PAPER (fail-closed; see §5).
- Resource-bounded execution — PAPER (timeouts, memory caps, process groups;
  resource budgets enforced by `Engine._budget_reason`).
- Blind evaluation — PAPER (evaluation in a separate verification worktree,
  no access to the implementer's sandbox; `verification.verify_commit`).
- Post-experiment telemetry and performance measurement — PAPER
  (`experiment.verification`, `experiment.delta`).

Retry loop: PAPER — "Reviewer checks single-variable isolation; revise until
approved or fall back". ENGINEERING — the engine re-submits the same
hypothesis with the reviewer's feedback text (truncated) after rejection;
bounded by `agent.max_retries + 1` attempts.

### Supported hypothesis

If Δh >= δ_target: PAPER (Algorithm 1, lines 8–9).

- Retain experiment branch — PAPER
- Update best-performing baseline — PAPER (`state.best_commit/best_metric`)
- Increase belief:
  b_{k,m} = min(1, b_{k,m} + Δb_pos) — PAPER (`candidate_solution.adjust_belief`)

### Falsified or unstable hypothesis

If Δh < δ_target or execution crashes: PAPER (Algorithm 1, lines 10–12).

- Roll back — PAPER (worktree removed in `Engine._run_hypothesis.finally`;
  branch deleted by `git_sandbox.remove_experiment`)
- Decrease belief:
  b_{k,m} = max(0, b_{k,m} - Δb_neg) — PAPER
- Distill lessons — PAPER (`Engine._record_experience` → Experience Bank)
- Re-score pending hypotheses — PAPER (`select_hypothesis` re-run in
  `Engine._step_locked` after selection)

Crash recovery: PAPER — interrupted in-flight experiments are marked
`interrupted` and their worktrees deleted (`Engine.recover`,
`git_sandbox.cleanup_orphans`). ENGINEERING — interrupted engineering trials
and their MCTS nodes are also marked interrupted (`Engine.recover`).

### Operational health gates (initialization)

PAPER §3.2.1: baseline M0 is committed as the reference anchor only after the
framework executes it and checks pass under the operational health gates
(memory, GPU compute efficiency, loss stability). ENGINEERING —
`Engine.initialize` measures the baseline in a throwaway verification worktree
and requires a finite metric (`_measure_commit`); diagnostics run in another
throwaway worktree (`run_diagnostics`).

## 4. Phase 3 — Engineering Optimization

Engineering optimization uses MCTS over engineering configurations.

The scientific root architecture must remain invariant.

Allowed optimization dimensions include:

- Learning-rate schedules — PAPER
- Batch sizes — PAPER
- Weight decay — PAPER
- Ensemble weights — PAPER

Protected dimensions:

- Model architecture — PAPER (§3.2.1, "The scientific root architecture must
  remain invariant")
- Algorithm design — PAPER
- Data representation — PAPER

Audit invariant:

C_diff(M_root, M_child) ∩ {model, algo, data} = ∅ — PAPER.

ENGINEERING — enforced structurally at the source of every engineering change:
`apply_config` blocks paths escaping the worktree, and
`git_sandbox.assert_not_protected` rejects any diff (including untracked
files) touching protected/forbidden paths; violations are recorded as
`protected_violation` engineering trials and rejected.

UCT is used to balance exploration and exploitation — PAPER
(`optimizer.uct`: mean reward + exploration constant × sqrt(ln(parent)/n)).
ENGINEERING — unvisited nodes are explored first; `record_result` credits
failed trials no reward and backpropagates nothing (paper: failed branches
are physically purged).

Cost-aware reward:

R(N_k) = Δh(N_k) - γ ln(T_exec(N_k)) — PAPER (`optimizer.reward`).

Where:

- Δh(N_k): validation performance gain against root — PAPER (ENGINEERING:
  root metric is persisted in `state.engineering["root_metric"]` and passed
  to every engineering verification, so the comparison is against the
  engineering root M0, not a mutable baseline)
- T_exec(N_k): execution duration in seconds — PAPER
- γ: compute-cost penalty coefficient — PAPER (`EngineeringConfig.gamma`)

ENGINEERING — after the budget is exhausted, the highest-validation
engineering trajectory is merged back into the primary Git branch as the
final reference model (`git_sandbox.merge_into_primary`; event
`engineering_merged_best`).

## 5. Tripartite Agent Governance

### Principal Investigator (PI)

- Maintains Problem State
- Sets experimental goals and time budgets
- Supervises execution trajectory
- Inspects execution every 10 steps

### Implementation Agent

- Operates in sandbox
- Uses ReAct-style execution loop
- Reads and modifies files
- Runs diagnostics
- Implements and debugs experiments

### Reviewer Agent

- Audits modifications
- Enforces single-variable isolation in Phase 2
- Protects scientific modules in Phase 3
- Approves or rejects changes
- Provides targeted remediation feedback

## 6. Runtime Resource Governance

Monitor:

- Data preprocessing
- Model training
- Blind evaluation

Required protections:

- Detect NaN/Inf divergence
- Stop stalled I/O
- Enforce preprocessing caps
- Support controlled soft-margin extensions
- Prevent runaway execution

## 7. Experience Bank

Persistent memory stores structured engineering lessons.

Expected capabilities:

- Error signatures
- Failure context
- Resolved errors
- Reusable fixes
- Cross-iteration retrieval
- Preemptive experience retrieval

## 8. Git-Backed Experiment Isolation

Every experiment must be recoverable.

Requirements:

- Isolated branches
- Deterministic rollback
- Baseline preservation
- Experiment reproducibility
- Successful experiments retained as validated snapshots

## 9. Implementation Status

Not implemented yet.

The following must be formally resolved before coding:

- Exact Problem State schema
- Belief initialization
- Belief increments Δb_pos and Δb_neg
- δ_target definition
- Hypothesis lifecycle and queue representation
- Exact UCT selection equation
- Resource budget allocation rules
- Experience Bank schema
- Reviewer audit interface

## 10. Fidelity Rule

Verified paper mechanisms must not be silently changed.

Engineering adaptations must be explicitly marked:

- PAPER: directly specified in paper
- DERIVED: mathematically or structurally inferred
- ENGINEERING: implementation choice
- UNVERIFIED: requires further source inspection

This document is a working specification, not proof of complete reproduction.
