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
- Isolation boundary — ENGINEERING (`security.enforce_isolation` wraps agent
  commands; see §11). Fail-closed: when isolation is enabled and no backend
  is available, execution is refused (`IsolationError`) rather than run
  unprotected.
- Evaluator pinning — ENGINEERING (`integrity.pin_evaluator` digests the
  verify spec + tests at `initialize`; `Engine._assert_integrity` refuses to
  continue before every verification if the evaluator changed mid-run).
- Out-of-sandbox detection — ENGINEERING (`git_sandbox.fingerprint(active=)`;
  compares the main checkout and other worktrees before/after each agent run;
  changes outside the experiment worktree abort it).
- Reproducible lineage — ENGINEERING (`Experiment.lineage`,
  `Experiment.snapshot_digest`; `integrity.assert_snapshot_integrity`).

### Search extensions (ENGINEERING)

- Hypothesis graph — DERIVED (`Hypothesis.edges`: supports/contradicts/
  derived_from; `Hypothesis.contradicts()`).
- Confidence — DERIVED (`Hypothesis.confidence`, `Hypothesis.adjust_confidence`):
  belief-adjacent signal used by adaptive rescheduling; the paper's
  selection formula stays PAPER (`hypothesis_priority`).
- Falsification — DERIVED (`Hypothesis.status = "falsified"`,
  `Hypothesis.falsified/falsification_reason`): a hypothesis that executed
  and produced a measured, non-improving outcome is falsified (distinct from
  an unverified rejection).
- Adaptive rescheduling — DERIVED (`hypothesis.adaptive_reschedule`:
  under-attempted problems with ≥2 falsified hypotheses get their attempts
  boosted; re-scored against the paper's `Pri = b/(N+1)` formula each step).
- Evidence-backed belief updates — DERIVED (`CandidateSolution.evidence`:
  experiment IDs appended on acceptance/rejection; belief still moves only
  through the paper's Δb_pos/Δb_neg in `adjust_belief`).

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

Best-candidate selection — ENGINEERING (`EngineeringOptimizer.best()`):
the highest-validation node wins when `EngineeringConfig.direction =
"maximize"`; the lowest wins for `"minimize"` (paper Eq. 3/4 semantics).

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
are physically purged). Mathematical regression tests: `tests/test_optimizer.py`
(maximize/minimize `best()`, failed-node no-reward, ancestor backpropagation,
selection order, reward formula).

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

Reviewer governance extensions — ENGINEERING (`Engine._review`, `Engine._escalate`):

- **Fail-closed**: missing, malformed, non-`True`-approved or ambiguous reviews
  reject; hard constraints reject even when they appear to approve.
  Reviewer unavailability is recorded (`EngineState.reviewer_unavailable`) and
  escalated (`Engine.MAX_REVIEWER_UNAVAILABLE = 3`).
- **Approval records**: approved experiments carry
  `Experiment.reviewer_id/reviewer_source/review_digest`; explicit
  `approval_recorded` events are journaled; `Engine.approval_records()` is the
  audit ledger; `state.metadata["last_approval"]` holds the latest one.
- **Bounded retry**: implementation agents get `agent.max_retries + 1`
  attempts with reviewer feedback; after that the experiment is rejected and
  the hypothesis's belief/confidence are lowered.
- **PI supervision**: `supervise()` malformed/ambiguous output is recorded
  (`EngineState.pi_malformed`) and escalated (`Engine.MAX_PI_MALFORMED = 3`);
  PI "stop" pauses the run with an explicit reason.

### Security Hardening (ENGINEERING, not in the paper)

`src/pro2/security.py` + `src/pro2/integrity.py`:

- **Isolation**: agents, verification tests and diagnostics run inside the
  detected backend (`unshare -rm`, `docker`, `bwrap`, `nsjail`).
  `enforce_isolation` is **fail-closed**: when isolation is enabled and no
  backend is available, execution raises `IsolationError` instead of running
  unprotected. Wired via `runner.run_command(..., isolation=...)` in
  `agents.py`, `verification.py` and `evidence.py`.
- **Environment scrubbing**: `scrub_environment` drops `SECRET|TOKEN|KEY|
  AUTH|SESSION|COOKIE|...` variables from the agent environment.
- **Credential protection**: `credential_paths` identifies `~/.ssh`,
  `~/.aws`, `~/.gnupg`, `~/.kube`, `~/.netrc`, `~/.docker`, `~/.gcloud`,
  `~/.azure`.
- **Evaluator pinning**: `integrity.pin_evaluator` digests the verify spec +
  tests at `initialize`; `Engine._assert_integrity` refuses to continue before
  every verification if the evaluator changed mid-run.
- **Snapshot integrity**: `integrity.assert_snapshot_integrity`; every
  `Experiment` records `snapshot_digest` + `lineage` (base commit → best).
- **Out-of-sandbox detection**: `git_sandbox.fingerprint(active=...)`;
  main-checkout and other-worktree status digests compared before/after each
  agent run; changes outside the experiment worktree abort it.

## 6. Runtime Resource Governance

Monitor:

- Data preprocessing
- Model training
- Blind evaluation

Required protections:

- Detect NaN/Inf divergence (`MetricSpec.divergence_regex`)
- Stop stalled I/O (`stall_seconds`; `divergence_regex` kills on pattern)
- Enforce preprocessing caps (`MetricSpec.memory_limit_mb` →
  `run_command(memory_limit_mb=...)`; `RLIMIT_AS` + `/proc` RSS polling)
- Support controlled soft-margin extensions (`MetricSpec`/`Constraint` knobs)
- Prevent runaway execution (timeouts, process-group termination with
  `SIGTERM` → `SIGKILL` escalation in `runner._kill_group`,
  `Budget.max_disk_bytes` + `StateStore.disk_usage_bytes()`)
- **Disk governance (ENGINEERING)**: `Budget.max_disk_bytes` +
  `StateStore.disk_usage_bytes()`; the run pauses with `disk budget
  exhausted` at the boundary.
- **Network governance (ENGINEERING)**: `security.network = "deny"` — the
  selected isolation backend removes egress; unisolated runs rely on the
  reviewer + evaluator-pin + fingerprint controls (see §11).

## 7. Experience Bank

Persistent memory stores structured engineering lessons.

Expected capabilities:

- Error signatures
- Failure context
- Resolved errors
- Reusable fixes
- Cross-iteration retrieval
- Preemptive experience retrieval
- **Certified lessons (ENGINEERING)**: `ExperienceEntry.certified` +
  `confidence` (0..1); only engine-verified outcomes are certified
  (`_record_experience`); `ExperienceBank.search(..., certified_only=True)`
  is used by `Engine._experience_for` so unverified lessons never become
  authoritative; confidence is a small positive search bonus.

## 8. Git-Backed Experiment Isolation

Every experiment must be recoverable.

Requirements:

- Isolated branches
- Deterministic rollback
- Baseline preservation
- Experiment reproducibility
- Successful experiments retained as validated snapshots

## 9. Implementation Status

Implemented (paper mechanisms + engineering hardening, all covered by tests
under `tests/`):

- Problem State schema, belief init/Δb, hypothesis lifecycle, priority,
  UCT selection (`hypothesis.py`, `problem_state.py`, `state.py`).
- Believed-based search; adaptive rescheduling + confidence + falsification
  (`adaptive_reschedule`, `EngineState` search extensions).
- Resource governance: timeouts, memory caps, stall/divergence detection,
  process-group `SIGTERM`→`SIGKILL`, disk budgets (`Budget.max_disk_bytes`).
- Reviewer fail-closed, approval records, bounded retry, escalation; PI
  supervision (`Engine._review`, `Engine._escalate`, `EngineState.escalations`).
- Experimental integrity: evaluator pinning, snapshot digest, lineage,
  out-of-sandbox fingerprint, invariants (`integrity.py`, `git_sandbox.py`,
  `orchestrator.py`).
- Security: isolation backends, fail-closed policy, env scrubbing, credential
  paths, network denial (`security.py`, `runner.py`, `agents.py`,
  `verification.py`, `evidence.py`).
- Experience Bank: certification + confidence + certified retrieval.
- Research: provenance (`fetch_url`/`fetch_digest`), primary-source
  verification (`ResearchEngine._verify_finding`), `verify_primary` config.
- Recovery: corrupt-state fallback to backup, interrupted experiment/trial
  marking, orphan cleanup (`state.py`, `git_sandbox.py`, `Engine.recover`).
- Engineering MCTS: `direction` maximize/minimize semantics; math regression
  tests in `tests/test_optimizer.py` and `tests/test_optimizer_fidelity.py`.
- End-to-end + adversarial tests: `tests/test_e2e_cli.py`,
  `tests/test_recovery.py`, `tests/test_security.py`,
  `tests/test_integrity.py`, `tests/test_scientific_search.py`,
  `tests/test_review_governance.py`, `tests/test_resource_governance.py`.

Implemented in this round (paper-fidelity gaps):

- **Three-state Problem taxonomy (DERIVED)** — `Problem.status` ∈
  {`unresolved` (default), `resource_limited`, `resolved`}; evidence-backed
  transitions recorded in `Problem.status_reasons` + journaled
  `problem_transition` events: `resolved` on an accepted candidate
  (`Engine._run_hypothesis`), `resource_limited` at budget exhaustion
  (`Engine.run` → `Engine._settle_problems`) and on failed experiments at the
  per-experiment time budget (`Engine._runaway_check`). `resolved` is
  terminal; `select_hypothesis` deprioritizes resolved problems while other
  work remains.
- **MCTS/UCT fidelity (DERIVED)** — `MCTSNode.accepted`; `record_result`
  backpropagates reward to ancestors (incl. root); `best()` selects only
  completed, accepted nodes — failed/pending/interrupted/rejected never win;
  `EngineeringOptimizer` persists the full tree with direction in
  `state.engineering` and `Engine.run` re-enters `run_engineering()` when
  resuming mid-engineering (`tests/test_mcts_fidelity.py`).
- **Noise-aware evaluation (ENGINEERING)** — `MetricMeasurement.stats`
  (count/mean/std/min/max), `compare_metrics(candidate_std, noise_weight)`
  (acceptance requires `delta >= min_delta + noise_weight * std`; default 0
  preserves paper semantics), wired via `MetricSpec.noise_weight` into
  `verify_commit` (`tests/test_noise_aware.py`).
- **Research verification extensions (DERIVED/ENGINEERING)** — `ResearchFinding`
  now carries `doi`, `commit_hash` (versioned arXiv id / GitHub SHA) and
  `excerpt`; `parse_arxiv` extracts DOIs; `ResearchEngine._verify_doi`
  verifies DOIs via doi.org/Crossref; `_verify_finding` reads the primary
  source and records commit hashes/excerpts (`tests/test_research_provenance.py`).
- **Experience Bank extensions (ENGINEERING)** — `ExperienceEntry.observation`,
  `causal_hypothesis`, `verified_fix`, `reproducible`, `evidence_refs`;
  `format_for_prompt` renders them; `Engine._record_experience` populates them
  (`tests/test_experience_rich.py`).
- **Sandbox hardening (ENGINEERING)** — `unshare` wrapper adds `-n` for
  network denial, binds host toolchain dirs read-only, and credential paths
  read-only; `docker` mounts the worktree writable (`-v path:path`), rootfs
  `--read-only` + `--cap-drop ALL` + `--network none` + `--pids-limit`; `bwrap`
  keeps `/` read-only and binds the worktree; `nsjail` runs as `--user 65534`;
  string commands are normalized in `enforce_isolation` (fixing a broken
  argv concat for string-command metrics).
- **PI governance (ENGINEERING)** — supervisor unavailability is counted
  (`EngineState.pi_unavailable`), journaled, and escalates at
  `Engine.MAX_PI_MALFORMED = 3` (`tests/test_governance.py`).

Resolved before coding: Problem State schema, belief deltas, δ_target,
hypothesis lifecycle, UCT equation (§2–§4 PAPER entries).

Remaining gaps / limitations (documented honestly in `docs/threat-model.md`):

- Isolation requires a working backend (`unshare` user namespaces or
  Docker); without one, execution fails closed.
- The `http` provider cannot be isolated (no process boundary exists).
- Divergence detection is a sampled-output heuristic, not a proof.
- Engineering `direction="minimize"` semantics are DERIVED from the paper's
  symmetric Eq. 3/4 (paper text specifies maximize only).

## 10. Fidelity Rule

Verified paper mechanisms must not be silently changed.

Engineering adaptations must be explicitly marked:

- PAPER: directly specified in paper
- DERIVED: mathematically or structurally inferred
- ENGINEERING: implementation choice
- UNVERIFIED: requires further source inspection

This document is a working specification, not proof of complete reproduction.

## 11. Threat Model

Summary of the threat surface and its controls:

| # | Threat | Control |
| --- | --- | --- |
| T1 | Candidate tampering (diff, tests, metric, patch) | isolation, reviewer hard constraints, evaluator pin, blind verification, fingerprint, lineage |
| T2 | Credential exfiltration / secret abuse | env scrubbing, credential path identification, isolation |
| T3 | Network abuse | `security.network = "deny"` isolation; research primary-source verification |
| T4 | DoS / resource abuse | timeouts, `SIGTERM`→`SIGKILL`, memory rlimits, stall + divergence detection, disk budget, cost budget |
| T5 | State tampering / journal integrity | atomic writes, `state.json.bak` fallback, exclusive lock, append-only event journal, invariants |
| T6 | Reviewer / PI governance downgrade | fail-closed reviewer, approval records, bounded retry, escalation (`MAX_REVIEWER_UNAVAILABLE`, `MAX_PI_MALFORMED`) |
| T7 | Isolation unavailability | fail-closed `IsolationError`; `pro2 doctor` reports backends |
| T8 | Experience Bank poisoning | certification + confidence, certified-only retrieval |
| T9 | Misleading science outputs | primary-source verification of findings, normalized signatures |
| T10 | Three-state Problem taxonomy | `unresolved`/`resource_limited`/`resolved` transitions with evidence (`problem.py`, `Engine._settle_problems`, `Engine._runaway_check`) |
| T11 | Noise-aware evaluation | repeated-run stats + noise-aware acceptance (`verification.compare_metrics`, `MetricSpec.noise_weight`) |
| T12 | Research provenance | DOI/commit-hash/excerpt preservation + verification (`research.py`) |
| T13 | PI supervision fail-closed | `EngineState.pi_unavailable`, escalation at `Engine.MAX_PI_MALFORMED` |

Full detail: `docs/threat-model.md` (threats, controls, trust boundaries,
documented limits).
