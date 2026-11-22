# Pro2 Agent

An autonomous, local-first technical research and innovation engine. It does not
run ML benchmarks: it treats a real repository as the experimental substrate,
maintains an evolving **Problem State**, proposes hypotheses, implements them
with a CLI coding agent in isolated Git worktrees, verifies results
independently, and records what it learned in a persistent **Experience Bank**.

The implementation follows the mechanisms described in *Pro² Agent: Scientific
Problem Probing for Autonomous AI R&D* (arXiv 2611.25357) and is stdlib-only
Python 3.12+.

## What it does

```
objective + measurable metric
        │
        ▼
  baseline measurement ──► problem discovery (agent + diagnostics + research)
        │
        ▼
  priority = belief / (attempts + 1)
        │
        ▼
  isolated Git worktree ──► agent implements one hypothesis
        │
        ▼
  reviewer checks scope ──► commit
        │
        ▼
  fresh worktree ──► tests + metric (never trust the agent's claim)
        │
        ├── improved ──► accept, move refs/pro2/best, raise belief
        └── not improved ──► reject, delete branch, lower belief
        │
        ▼
  Experience Bank + event journal + report
        │
        ▼
  optional engineering phase: cost-aware MCTS/UCT over config parameters
```

## Install

```bash
uv sync --extra dev      # Python 3.12+, uv
uv run pro2 doctor --config pro2.json
```

`uv run pro2 ...` works without installation; `uv pip install -e .` also
installs the `pro2` console script.

## Quick start

Create `pro2.json` next to the target repository (or point `repo` anywhere):

```json
{
  "name": "api-latency",
  "description": "Reduce p95 latency of the request handler",
  "repo": ".",
  "success": {
    "metric": "p95_ms",
    "direction": "minimize",
    "min_delta": 5.0,
    "target": 120.0
  },
  "baseline": {
    "command": "python bench.py",
    "metric_regex": "p95_ms=([0-9.]+)",
    "repeat": 3
  },
  "verify": {
    "command": "python bench.py",
    "metric_regex": "p95_ms=([0-9.]+)",
    "repeat": 3
  },
  "diagnostics": [
    {"command": "python -m pytest -q"},
    {"command": "python -m cProfile -s cumtime bench.py"}
  ],
  "tests": [
    {"command": "python -m pytest -q"}
  ],
  "budget": {
    "max_experiments": 5,
    "max_iterations": 20,
    "max_wall_seconds": 3600,
    "max_experiment_seconds": 600,
    "max_cost_usd": 0,
    "max_consecutive_failures": 3,
    "max_disk_bytes": 0
  },
  "constraints": {
    "protected_paths": ["bench.py", "tests/**"],
    "max_changed_files": 3,
    "max_diff_lines": 200,
    "allow_test_edits": false
  },
  "research": {"enabled": true, "providers": ["github", "arxiv"]},
  "agent": {"provider": "auto", "timeout_seconds": 600}
}
```

```bash
uv run pro2 init    --config pro2.json   # baseline + problems + hypotheses
uv run pro2 run     --config pro2.json   # autonomous loop until a budget stops it
uv run pro2 status  --config pro2.json
uv run pro2 experiments --config pro2.json
uv run pro2 report  --config pro2.json
```

The target repository must be clean before `init` (or set
`constraints.require_clean_worktree` to `false`). The engine adds its own state
directory and the config file to `.git/info/exclude`, so they do not count as
dirty.

## CLI

| Command | Purpose |
| --- | --- |
| `pro2 init` | Measure the baseline, collect diagnostics/environment evidence, discover problems, generate hypotheses. |
| `pro2 run` | Run the full loop until budgets, failure limits, or the supervisor stop it; then run the engineering phase if configured. |
| `pro2 resume` | Recover an interrupted in-flight experiment, clean orphans, and continue. |
| `pro2 status` | Print status, baseline/best metrics, budget usage and stop reason. |
| `pro2 inspect` | Detailed problems, candidates, beliefs and hypotheses. |
| `pro2 experiments` | List experiments with status, metric, delta and reason. |
| `pro2 experience` | List Experience Bank entries. |
| `pro2 research` | List collected literature/repository findings. |
| `pro2 report` | Write and print `.pro2/report.md`. |
| `pro2 pause` | Create the `PAUSE` flag; the loop stops at the next safe boundary. |
| `pro2 clean` | Remove orphaned experiment worktrees and branches. |
| `pro2 doctor` | Check tools, commands, state and available agent providers. |

Every command accepts `--config PATH` (default `pro2.json`) and
`--state-dir PATH` to override the state directory.

## Agent providers

The engine is provider-agnostic. `provider: "auto"` picks the first available:

| Provider | Requirements | Notes |
| --- | --- | --- |
| `command` | `agent.command` is a list | Prompt is passed on stdin. Used by tests and scripted agents. |
| `opencode` | `opencode` CLI installed | Runs `opencode run --auto --archived --format json -m <model>`; parses streaming JSON, text and cost. `--auto` auto-approves permissions that are not explicitly denied — the engine still gates every diff through the fail-closed reviewer and protected-path checks, so do not widen `constraints` without review. |
| `claude` | `claude` CLI installed and logged in | Runs `claude -p --output-format json --dangerously-skip-permissions`. Unauthenticated installs fail with a clear error. |
| `http` | `OPENAI_API_KEY` or `ANTHROPIC_API_KEY` (or `agent.api_key_env`) | OpenAI-compatible `/chat/completions` or Anthropic `/v1/messages`; optional `base_url`, `model`, `cost_per_1k_tokens`. |
| `local` | none | No model call; the engine falls back to deterministic heuristic diagnosis and seeded hypotheses. |

If a provider fails to start or returns nothing usable (the `local` provider
case), the engine records the failure. The hard heuristic constraints still
gate the diff (protected paths, budgets, test policy) — but the reviewer is
fail-closed: missing, malformed, incomplete or failed reviews reject, and an
unavailable reviewer never silently approves. After
`Engine.MAX_REVIEWER_UNAVAILABLE = 3` (or `Engine.MAX_PI_MALFORMED = 3`
repeated ambiguous supervisor outputs) the run pauses with an explicit
escalation reason for human triage. Every approved experiment records reviewer
identity and a diff digest (`approval_recorded` events;
`Engine.approval_records()`). Reviewer feedback fed back to the implementer is
truncated so agent-written text stays bounded.

## How verification works

1. The implementer works in a throwaway worktree on branch `pro2/exp/<id>`,
   optionally isolated by `security.py` (fail-closed `IsolationError` when an
   isolation backend is requested but unavailable).
2. The engine detects if the agent modified the main checkout or the state
   dir or any other experiment worktree (fingerprint diff).
3. A reviewer (agent, plus hard constraints) checks protected paths, file and
   line budgets, test edits and single-variable intent. The hard constraints
   are a mandatory gate: a diff touching a protected or forbidden path, the
   test suite, or exceeding the diff budgets rejects the candidate even if the
   reviewer agent approves. The reviewer is fail-closed: missing, malformed,
   incomplete or failed reviews reject.
4. The engine commits, records the experiment's lineage and snapshot digest,
   then creates a **separate** detached worktree.
5. Tests and the metric command run there; the evaluator (verify spec + tests)
   is pinned at initialization and must be unchanged (`_assert_integrity`).
6. Acceptance requires the configured direction, `min_delta` and optional
   absolute `target`.
7. Accepted commits update `refs/pro2/best`; rejected worktrees and branches are
   deleted. Rollback is a Git operation, not a promise.
8. Every engineering trial measures against the persisted engineering root
   metric; after budget exhaustion the highest-validation engineering
   trajectory is merged back into the primary branch as the final reference
   model.

## Configuration reference

- `success`: metric name, `maximize`/`minimize`, `min_delta`, optional `target`.
- `baseline` / `verify`: `command`, `metric_regex` (first capture group) or
  `json_path`, `timeout_seconds`, `repeat` (median is used), `env`,
  `divergence_regex` (kills on NaN/Inf/divergence patterns), `memory_limit_mb`
  (rlimit + RSS kill).
- `diagnostics`: commands run in a clean worktree during initialization.
- `tests`: commands that must pass during verification.
- `budget`: experiments, iterations, wall time, per-experiment time, cost,
  consecutive failures, disk cap (`max_disk_bytes`).
- `constraints`: protected/forbidden paths, changed-file and diff-line limits,
  test-edit policy, clean-worktree requirement.
- `research`: GitHub/arXiv/DuckDuckGo providers, query/result limits, cache.
- `engineering`: MCTS over JSON/env/text parameters; reward
  `Δmetric - γ·ln(T_exec)`; `direction` (`maximize`/`minimize`) selects the
  highest-validation node for best-candidate selection; trials are measured
  against the persisted engineering root metric; the best trajectory is
  merged into the primary branch when the budget ends.
- `experience`: persistent failure/success signatures with retrieval limits;
  engine-verified outcomes are certified (`certified`, `confidence`) and are
  the only entries retrieved for prompting (`certified_only`).
- `security`: `{enabled, backend: auto|unshare|docker|bwrap|nsjail|none,
  network: deny|allow, credential_protection}` — see "Security & isolation".
- `seed_problems` / `seed_hypotheses`: skip agent diagnosis and inject known
  starting points (useful for reproducible runs and tests).
- `belief_positive_delta` / `belief_negative_delta`: belief update sizes.

## Security & isolation

`security.enabled = true` runs every agent-facing command (implementation,
review, supervision, diagnostics, verification) inside a boundary provided by
the selected backend (`unshare -rm` native namespaces, or Docker with
`--network none`, `--read-only`, `--cap-drop ALL`; `bwrap`/`nsjail` supported
where present). Policy:

- **Fail-closed**: when isolation is enabled but no backend is available,
  agent execution raises `IsolationError` instead of running unprotected
  (`pro2 doctor` reports available backends).
- **Network**: `security.network = "deny"` (default) removes egress from the
  boundary; `"allow"` leaves the host network in place.
- **Credentials**: environment variables matching
  `SECRET|TOKEN|KEY|AUTH|SESSION|COOKIE|...` are scrubbed from the agent
  environment; credential paths (`~/.ssh`, `~/.aws`, `~/.gnupg`, `~/.kube`,
  `~/.netrc`, `~/.docker`, `~/.gcloud`, `~/.azure`) are identified via
  `security.credential_paths()`.
- **Filesystem**: agents work only inside their throwaway experiment worktree;
  the engine additionally fingerprints the main checkout and all other
  worktrees before/after each agent run and aborts on out-of-sandbox edits.

Known limits: `unshare` requires user namespaces; Docker requires a reachable
daemon and image; the `http` provider cannot be isolated (no process
boundary). See `docs/threat-model.md` for the full threat model.

## State layout

Everything lives under the objective's state directory (default `.pro2/`):

```
.pro2/
  state.json            atomic state (with state.json.bak fallback)
  events.jsonl          append-only event journal
  experience.jsonl      Experience Bank
  report.md             generated research report
  artifacts/            agent prompts/responses, command logs
  worktrees/            experiment worktrees (removed after rollback)
  verify/               temporary independent verification worktrees
  research/cache.json   provider response cache
  environment.json      observed repository/tool environment
  engine.lock           exclusive run lock
  PAUSE                 pause flag
```

State writes are atomic, a `fcntl` lock prevents concurrent engines, and an
interrupted experiment is marked `interrupted` and cleaned by `pro2 resume`.

## Repository structure

```
pro2-agent/
├── src/pro2/                  # engine core — stdlib-only Python 3.12
├── tests/                     # integration tests (pytest, 198)
├── docs/paper-algorithm.md    # verified paper-mechanism spec
├── docs/threat-model.md       # threat model and implemented controls
├── paper/                     # extracted paper: original.pdf, extracted/, figures/
├── scripts/analyze_paper.py   # paper extraction/analysis helper
├── experiments/               # experiment storage (gitignored, .gitkeep only)
├── logs/                      # runtime logs (gitignored, .gitkeep only)
├── state/                     # runtime state dirs (gitignored, .gitkeep only)
├── pyproject.toml             # package metadata + console script `pro2`
├── justfile                   # dev commands (test/lint/check/format/security/watch)
└── .gitignore, README.md
```

Core modules in `src/pro2/`:

| Module | Role |
| --- | --- |
| `config.py` | Objective config: metrics, budgets, constraints, agents, research, engineering, seeds |
| `records.py`, `experiment.py` | Evidence, Experiment, EngineeringTrial records |
| `problem.py`, `problem_state.py`, `candidate_solution.py`, `hypothesis.py` | Problem State, candidates, hypotheses; paper priority `Pri = belief/(attempts+1)` |
| `runner.py` | Bounded subprocess execution (timeout/memory/stall/divergence, process-group kill) |
| `git_sandbox.py` | Worktrees, branches, rollback, `refs/pro2/best`, protected paths |
| `verification.py` | Independent verification in a fresh worktree (tests + metric) |
| `agents.py` | Provider-agnostic agent layer + machine-readable prompts |
| `research.py` | GitHub/ArXiv/DuckDuckGo research with response cache |
| `evidence.py` | Environment observation, diagnostics, heuristic diagnosis |
| `optimizer.py` | Cost-aware MCTS/UCT over engineering parameters |
| `orchestrator.py` | Engine: the full loop, review, supervision, engineering, recovery, report |
| `experience.py` | Experience Bank (normalized signatures, search, persistence, certification) |
| `integrity.py` | Experimental integrity: pinned evaluator, lineage, snapshot digests, invariants |
| `security.py` | Isolation detection/wrapping, environment scrubbing, credential path identification |
| `cli.py`, `__main__.py` | `pro2` command and `python -m pro2` |

## Development

```bash
uv run pytest -q          # 198 tests
uv run ruff check .       # lint
uv run ruff format .      # format
just check                # lint + tests
```

The test suite includes real Git worktree isolation, real subprocess agents,
metric parsing, crash recovery, engineering MCTS, and a scripted end-to-end
accept/reject cycle. No network access or paid API is required.

## Scope and limitations

- No ML libraries, training loops or benchmark harnesses.
- Research providers are best-effort; offline runs simply produce no findings
  and never treat them as facts (unverified findings are not attached as
  candidates; `research.verify_primary` refetches and confirms against the
  primary source when reachable).
- Agent quality depends on the configured provider; deterministic heuristics
  keep the loop alive when a provider is unavailable.
- The reviewer is fail-closed; protected, forbidden and test paths are
  rejected even when a reviewer agent approves; repeated reviewer
  unavailability or ambiguous supervisor output pauses the run for human
  triage (`Engine.MAX_REVIEWER_UNAVAILABLE`, `Engine.MAX_PI_MALFORMED`).
- Isolation requires a usable backend (`unshare`, `docker`, `bwrap` or
  `nsjail`); when isolation is enabled and none is available the engine
  refuses to run agents (`IsolationError`). The `http` provider cannot be
  isolated. See `docs/threat-model.md`.
- Divergence detection and the sandbox fingerprint are heuristics on sampled
  output (polling/`stall_seconds`), not proofs.
- The engine never merges into your branch automatically. Accepted experiments
  are kept on branches and `refs/pro2/best`; integrating them is your decision.
