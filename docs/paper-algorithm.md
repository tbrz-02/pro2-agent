# Pro² Agent — Verified Algorithm Specification

Source:
Pro² Agent: Scientific Problem Probing for Autonomous AI R&D
arXiv: 2611.25357

This specification separates verified paper mechanisms from implementation decisions.

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

H_t = (P_k, C_{k,m}, Δm_t)

Priority:

Pri(H_t) = b_{k,m} / (N(P_k) + 1)

Where:

- b_{k,m}: candidate solution belief
- N(P_k): previous attempts against problem P_k

### Experiment requirements

- Isolated version-controlled branch
- Minimal code mutation
- Single-variable isolation
- Reviewer approval before execution
- Resource-bounded execution
- Blind evaluation
- Post-experiment telemetry and performance measurement

### Supported hypothesis

If Δh >= δ_target:

- Retain experiment branch
- Update best-performing baseline
- Increase belief:
  b_{k,m} = min(1, b_{k,m} + Δb_pos)

### Falsified or unstable hypothesis

If Δh < δ_target or execution crashes:

- Roll back
- Decrease belief:
  b_{k,m} = max(0, b_{k,m} - Δb_neg)

- Distill lessons
- Re-score pending hypotheses

## 4. Phase 3 — Engineering Optimization

Engineering optimization uses MCTS over engineering configurations.

The scientific root architecture must remain invariant.

Allowed optimization dimensions include:

- Learning-rate schedules
- Batch sizes
- Weight decay
- Ensemble weights

Protected dimensions:

- Model architecture
- Algorithm design
- Data representation

Audit invariant:

C_diff(M_root, M_child) ∩ {model, algo, data} = ∅

UCT is used to balance exploration and exploitation.

Cost-aware reward:

R(N_k) = Δh(N_k) - γ ln(T_exec(N_k))

Where:

- Δh(N_k): validation performance gain against root
- T_exec(N_k): execution duration in seconds
- γ: compute-cost penalty coefficient

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
