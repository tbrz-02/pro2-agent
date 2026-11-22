# Pro² Agent — Threat Model

This document maps the threat surface of the Pro² Agent R&D engine to the
controls implemented in `src/pro2/` and `docs/paper-algorithm.md`.

## Actors (from the security policy: trust levels, highest first)

1. **Maker/developer** — the engine itself, the objective config, `src/pro2/`.
2. **The user/owner** — the person running the engine against their repository.
3. **The target repository** — *untrusted*. Everything the agent reads, diffs,
   tests and writes is attacker-influenced data if the repo is a hostile target.
   Hypothesis text, patch text, and every file inside experiment worktrees are
   treated as untrusted input.
4. **Other tools, agents and environment** — LLM providers, terminals, CI
   runners. Their output is data to be verified, never instructions.

Rule of thumb: the target repository and anything it contains can lie. The
engine's design keeps lying data away from trusted surfaces: agents work in
throwaway worktrees, diffs are reviewed by a second authority, and the
verification contract is pinned before the first experiment.

## Threats and controls

### T1 — Candidate tampering (hypothesis, patch, diff, tests, metric)

The agent implementing a hypothesis is attacker-controlled if the target repo
is hostile (it edits arbitrary files, rewrites tests, edits the verify command).

Controls:

- **Isolation** — `security.py` (`enforce_isolation`, `scrub_environment`):
  agents run inside a namespace/container boundary (`unshare -rm` with bind
  mounts, or Docker with `--network none`, `--read-only`, `--cap-drop ALL`).
  See T7 for the fail-closed policy.
- **Single-variable gate** — reviewer (agent + hard heuristic constraints:
  `optimizer`/`_heuristic_review`): protected paths, forbidden paths,
  test-file policy, diff budgets.
- **Evaluator pinning** — `integrity.py`: `pin_evaluator` digests the verify
  spec + tests at `initialize`; `_assert_integrity` fails closed before every
  verification if the config changed mid-run.
- **Blind verification** — `verification.verify_commit`: tests + metric run in
  a fresh detached worktree, never in the implementer's sandbox.
- **Fingerprint check** — `git_sandbox.fingerprint(active=...)`: compares the
  main checkout and *other* worktrees before/after each agent run; changes
  outside the experiment worktree abort the experiment.
- **Lineage/snapshot** — every `Experiment` records `base_commit`,
  `snapshot_digest`, `lineage`; `assert_snapshot_integrity` fails closed when
  the base commit is not a real git object.

### T2 — Credential exfiltration / secret abuse

Agents have shells. Controls:

- **Environment scrubbing** — `scrub_environment` drops `SECRET|TOKEN|KEY|
  AUTH|...` variables from the agent environment in every local-agent run.
- **Credential paths** — `credential_paths` identifies `~/.ssh`, `~/.aws`,
  `~/.gnupg`, `~/.kube`, `~/.netrc`, `~/.docker`, `~/.gcloud`, `~/.azure`;
  `security.credential_protection` documents the intent, and isolation
  backends hide these paths (Docker mounts nothing but the project tree;
  `unshare` worktrees can additionally bind `/dev/null` over them via
  `credential_paths` consumption by operators).
- **No network when denied** — see T7.
- **No agent-given output trusted blindly** — all agent JSON is parsed by
  `parse_json_response` (fenced/first-JSON extraction), and every decision
  gate re-validates types and fail-closed conditions
  (`_review`, `supervise`, `_parse_problems`).

### T3 — Network abuse

- **Network denial** — `SecurityConfig.network = "deny"` (default): the
  selected backend isolates the agent from the network (Docker `--network
  none`; `unshare -n` when the kernel allows).
- **Research verification** — `research.py`: findings carry
  `fetch_url`/`fetch_digest` provenance; `_verify_finding` refetches the
  primary source (GitHub search API / arXiv feed) and confirms or contradicts.
- **Agent research tasks** — the engine never instructs the implementer agent
  to fetch URLs (`build_implement_prompt`; README documents this), and
  `Constraints.allow_network` remains an explicit operator setting.

### T4 — DoS / resource abuse

Controls (`runner.py`, `verification.py`, `config.py`, `state.py`):

- Timeouts (`timeout_seconds`) per run; process-group kill
  (`SIGTERM` then `SIGKILL` escalation).
- Memory rlimits via `RLIMIT_AS` + cgroup-less polling of `/proc` RSS
  (`_group_rss_bytes`), plus `MemoryError` detection.
- Stall detection (`stall_seconds`), divergence detection
  (`divergence_regex` — new `MetricSpec` fields wired into `measure_metric`).
- **Disk budget** — `Budget.max_disk_bytes` + `StateStore.disk_usage_bytes`;
  the engine stops with `disk budget exhausted` at the boundary.
- **Cost budget** — `Budget.max_cost_usd` (agent cost from provider reports).
- Orphan/worktree cleanup (`cleanup_orphans`, `Engine.recover`).

### T5 — State tampering / journal integrity

- `StateStore`: atomic writes (`atomic_write_text`), `state.json.bak`
  fallback (`load_state`), `fcntl` exclusive lock (`StateStore.lock`),
  append-only event journal (`events.jsonl`).
- `EngineState` new fail-closed fields: `escalations`, `reviewer_unavailable`,
  `pi_malformed` — bounded escalation (§6.3).
- `_assert_invariants`: phase ∈ {science, engineering, done}; `best_commit`
  must be a real git object.

### T6 — Reviewer / PI governance

- Fail-closed reviewer (`Engine._review`): missing, malformed,
  non-`True`-approved, or ambiguous reviews reject; hard constraints reject
  even on approval.
- Approval records: `Experiment.reviewer_id/reviewer_source/review_digest`,
  `approval_recorded` events, `Engine.approval_records()` audit ledger,
  `state.metadata["last_approval"]`.
- Bounded retry: `agent.max_retries + 1` implementation attempts with feedback;
  after that the experiment is rejected and the hypothesis's belief/confidence
  are lowered.
- Escalation: `Engine.MAX_REVIEWER_UNAVAILABLE = 3`,
  `Engine.MAX_PI_MALFORMED = 3`; repeated ambiguity → run `paused` with a
  human-escalation stop reason.
- PI (supervisor) ambiguity → recorded and escalated, never silently approved.

### T7 — Isolation unavailability (fail-closed policy)

`SecurityConfig(enabled=True)` + no usable backend → `IsolationError`:
agent runs are refused rather than executed unprotected. `pro2 doctor`
reports available backends. See `docs/paper-algorithm.md` §4.2 (Threat model)
for the current limits:

- `unshare`: requires user namespaces (`unshare -rm` works).
- `docker`: requires a reachable daemon + an available image (`alpine:latest`
  is used for the wrapper command).
- `http` agent provider cannot be isolated (no process boundary exists).
- When `security.enabled = false` (default), isolation is off: the engine
  relies on reviewer, diff, evaluator-pin and verification controls instead.
  See `docs/paper-algorithm.md` §11 and `docs/threat-model.md` (this
  document) for the current limits.

### T8 — Experience Bank poisoning / misleading science

- `ExperienceBank.add(..., certified, confidence)`; only engine-verified
  outcomes are certified (`_record_experience`).
- `ExperienceBank.search(..., certified_only=True)`: `Engine._experience_for`
  retrieves only certified entries, ranked with a small confidence bonus.
- Normalized signatures (`normalize_error`) group volatile errors by shape, so
  a single flaky failure cannot become authoritative.

### T9 — Reputation/brand attacks, data integrity of outputs

- The report (`write_report`) is generated from state; state is integrity
  checked per T5.
- Git operations use explicit `user.name`/`user.email` and never push.
- Research findings: `verified` means independently confirmed via the
  primary source (`_verify_finding`); unverified findings are never attached
  as candidates.

## Trust boundaries (summary)

| Data | Authority | If compromised |
| --- | --- | --- |
| Objective config (`pro2.json`) | Maker (developer) | Evaluator pin + `_assert_integrity` |
| Hypothesis/patch text from agent | Untrusted (target) | Reviewer, fingerprint, isolation |
| Target repo contents | Untrusted | Isolation, diff, protected paths |
| Research findings | Best-effort evidence | Primary-source verification (T3) |
| Experience Bank | Certified by engine | certification + confidence (T8) |
| Event journal / state | Engine | atomic writes, backup, lock (T5) |

## Known limits (documented honestly)

- `unshare -n` (network ns) may be unavailable in some kernels/containers;
  when it is, `auto` falls back to `docker` for `network=deny`, and if
  neither is usable, execution fails closed.
- The `http` provider is not isolatable.
- Docker isolation runs a minimal `alpine` wrapper; the process boundary is
  as strong as the backend, not a dedicated seccomp/apparmor profile.
- Divergence detection and the sandbox fingerprint are heuristics on sampled
  output (`stall_seconds`/loop polling), not proofs.
