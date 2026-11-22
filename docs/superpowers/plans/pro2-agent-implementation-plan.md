# Pro2 Agent — Implementation Plan

## Stage 0 — Foundation
- [ ] Verify project tooling and Git working state
- [ ] Establish clean baseline test/lint configuration
- [ ] Define persistent data formats and project conventions

## Stage 1 — Domain Model (TDD)
- [ ] Problem model and diagnostic evidence
- [ ] Candidate solution model and belief
- [ ] Hypothesis model
- [ ] Experiment and result models
- [ ] Research state model and serialization
- [ ] Validation of IDs, constraints, ranges, and malformed state

## Stage 2 — Problem State Engine
- [ ] Create/read/update problems
- [ ] Link evidence to problems
- [ ] Link candidate solutions to problems
- [ ] Persist and reload state
- [ ] State revision/history

## Stage 3 — Hypothesis Engine
- [ ] Generate atomic hypothesis representation
- [ ] Compute paper's frequency-penalized priority
- [ ] Deterministic tie-breaking
- [ ] Track per-problem attempts
- [ ] Re-score queue after new evidence

## Stage 4 — Evidence and Belief Updates
- [ ] Positive update with upper bound
- [ ] Negative update with lower bound
- [ ] Success threshold handling
- [ ] Crash/unstable outcomes
- [ ] Evidence provenance
- [ ] Configurable update magnitudes
- [ ] Contextual insight propagation

## Stage 5 — Git Experiment Sandbox
- [ ] Branch creation from current best
- [ ] Workspace isolation
- [ ] Mutation audit
- [ ] Commit/snapshot identification
- [ ] Rollback on failure
- [ ] Branch cleanup
- [ ] Recovery after interrupted execution

## Stage 6 — Experiment Runtime
- [ ] Experiment runner interface
- [ ] Timeout/watchdog
- [ ] Resource limits
- [ ] Telemetry capture
- [ ] Metric normalization
- [ ] Baseline comparison
- [ ] Result persistence

## Stage 7 — Scientific Exploration Loop
- [ ] Priority selection
- [ ] Experiment lifecycle
- [ ] Single-variable review interface
- [ ] Outcome routing
- [ ] Best-state promotion
- [ ] Budget-aware termination
- [ ] Resume after restart

## Stage 8 — Engineering Optimization
- [ ] Engineering mutation schema
- [ ] Scientific pipeline invariance checks
- [ ] MCTS node and edge state
- [ ] UCT selection
- [ ] Cost-aware reward
- [ ] Backpropagation
- [ ] Budget termination
- [ ] Best configuration selection

## Stage 9 — Experience Bank
- [ ] Structured error signatures
- [ ] Search/retrieval
- [ ] Verified fix records
- [ ] Failure memory
- [ ] Context applicability
- [ ] Reuse during experiment generation

## Stage 10 — Agent Governance
- [ ] PI agent interface
- [ ] Implementation agent interface
- [ ] Reviewer agent interface
- [ ] Revision/fallback protocol
- [ ] Periodic PI supervision
- [ ] Provider-agnostic model adapters

## Stage 11 — Autonomous Orchestrator
- [ ] Phase controller
- [ ] Budget partitioning
- [ ] Resource sentinel
- [ ] Pause/resume
- [ ] Recovery
- [ ] Audit trail
- [ ] Human approval boundaries

## Stage 12 — Validation
- [ ] Unit suite
- [ ] Integration tests
- [ ] Git rollback tests
- [ ] Crash recovery tests
- [ ] Resource guard tests
- [ ] Optimizer correctness tests
- [ ] End-to-end synthetic research task
- [ ] Performance benchmark
- [ ] Security audit

## Working Rules
- Use TDD for all behavior changes.
- Never claim a test passed without running it.
- Preserve the paper's mechanisms; distinguish paper requirements from
  engineering decisions.
- Complete one stage at a time and verify it before proceeding.
