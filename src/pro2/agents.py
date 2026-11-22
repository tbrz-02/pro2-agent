"""Provider-agnostic agent execution and structured prompt builders."""

from __future__ import annotations

import json
import os
import shutil
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pro2.config import AgentConfig, Objective
from pro2.hypothesis import Hypothesis
from pro2.problem import Problem
from pro2.runner import run_command
from pro2.security import IsolationError, SecurityConfig
from pro2.util import safe_id, truncate


class AgentError(RuntimeError):
    """Raised for agent configuration errors, not model/runtime failures."""


@dataclass
class AgentResult:
    ok: bool
    text: str = ""
    error: str = ""
    provider: str = ""
    model: str = ""
    duration_seconds: float = 0.0
    cost_usd: float = 0.0
    raw_path: Path | None = None
    session_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "provider": self.provider,
            "model": self.model,
            "duration_seconds": self.duration_seconds,
            "cost_usd": self.cost_usd,
            "error": self.error,
            "session_id": self.session_id,
            "raw_path": str(self.raw_path) if self.raw_path else "",
            "metadata": dict(self.metadata),
        }


def parse_json_response(text: str) -> dict[str, Any] | list[Any] | None:
    """Extract the first JSON object/array from a model response."""
    if not text:
        return None
    stripped = text.strip()
    for candidate in (stripped, _strip_fence(stripped)):
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, (dict, list)):
            return value

    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char not in "[{":
            continue
        try:
            value, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, (dict, list)):
            return value
    return None


def _strip_fence(text: str) -> str:
    if "```" not in text:
        return text
    start = text.find("```")
    end = text.rfind("```")
    if start == end:
        return text
    body = text[start + 3 : end]
    if body.startswith(("json", "JSON")):
        body = body[4:]
    return body.strip()


class AgentRunner:
    def __init__(
        self,
        config: AgentConfig,
        artifacts_dir: str | Path,
        security: SecurityConfig | None = None,
    ) -> None:
        self.config = config
        self.artifacts_dir = Path(artifacts_dir)
        self.security = security
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)

    def available_providers(self) -> list[str]:
        providers: list[str] = []
        if self.config.command:
            providers.append("command")
        if shutil.which("opencode") and not os.environ.get("PRO2_DISABLE_OPENCODE"):
            providers.append("opencode")
        if shutil.which("claude") and not os.environ.get("PRO2_DISABLE_CLAUDE"):
            providers.append("claude")
        if os.environ.get("OPENAI_API_KEY") or os.environ.get("ANTHROPIC_API_KEY"):
            providers.append("http")
        return providers

    def resolve_provider(self) -> str:
        if self.config.provider != "auto":
            return self.config.provider
        providers = self.available_providers()
        if not providers:
            return "local"
        return providers[0]

    def run(
        self,
        task: str,
        prompt: str,
        cwd: str | Path,
        timeout_seconds: float | None = None,
    ) -> AgentResult:
        cwd_path = Path(cwd)
        if not cwd_path.is_dir():
            raise AgentError(f"agent working directory does not exist: {cwd_path}")

        provider = self.resolve_provider()
        if self.security is not None and self.security.enabled and provider == "http":
            raise IsolationError(
                "isolation is enabled but the http provider cannot run isolated; "
                "use the opencode/claude/command providers or set security.enabled=false"
            )
        timeout = timeout_seconds or self.config.timeout_seconds
        token = uuid.uuid4().hex[:10]
        label = f"agent-{safe_id(task)}-{token}"
        prompt_path = self.artifacts_dir / f"{label}.prompt.txt"
        prompt_path.write_text(prompt, encoding="utf-8")

        if provider == "command":
            result = self._run_command(prompt, cwd_path, timeout, label)
        elif provider == "opencode":
            result = self._run_opencode(prompt, cwd_path, timeout, label)
        elif provider == "claude":
            result = self._run_claude(prompt, cwd_path, timeout, label)
        elif provider == "http":
            result = self._run_http(prompt, timeout, label)
        elif provider == "local":
            raise AgentError(
                "no LLM provider is configured; set agent.provider, install/authenticate "
                "opencode or claude, or set OPENAI_API_KEY/ANTHROPIC_API_KEY"
            )
        else:
            raise AgentError(f"unknown agent provider: {provider}")

        result.provider = provider
        result.raw_path = self.artifacts_dir / f"{label}.response.txt"
        result.raw_path.write_text(
            result.text + ("\n--- stderr ---\n" + result.error if result.error else "") + "\n",
            encoding="utf-8",
        )
        return result

    def ask_json(
        self,
        task: str,
        prompt: str,
        cwd: str | Path,
        timeout_seconds: float | None = None,
    ) -> tuple[dict[str, Any] | list[Any] | None, AgentResult]:
        result = self.run(task, prompt, cwd, timeout_seconds)
        if not result.ok:
            return None, result
        return parse_json_response(result.text), result

    def _run_command(self, prompt: str, cwd: Path, timeout: float, label: str) -> AgentResult:
        if not self.config.command:
            raise AgentError("agent provider 'command' requires agent.command")
        env = {
            "PRO2_TASK": label,
            "PRO2_CWD": str(cwd),
            "PRO2_MODEL": self.config.model,
            "PRO2_ARTIFACTS": str(self.artifacts_dir),
        }
        started = time.monotonic()
        run = run_command(
            self.config.command,
            cwd=cwd,
            timeout_seconds=timeout,
            artifacts_dir=self.artifacts_dir,
            label=label,
            env=env,
            stdin_text=prompt,
            isolation=self.security,
        )
        text = run.stdout
        cost = 0.0
        payload = parse_json_response(text)
        if isinstance(payload, dict):
            if isinstance(payload.get("text"), str):
                text = payload["text"]
            elif isinstance(payload.get("result"), str):
                text = payload["result"]
            if isinstance(payload.get("cost_usd"), (int, float)):
                cost = float(payload["cost_usd"])
        error = run.error or (run.stderr.strip() if run.exit_code != 0 else "")
        if not error and run.exit_code != 0:
            error = f"command exited with code {run.exit_code}"
        return AgentResult(
            ok=run.ok and bool(text.strip()),
            text=text.strip(),
            error=error,
            model=self.config.model,
            duration_seconds=time.monotonic() - started,
            cost_usd=cost,
            metadata={"exit_code": run.exit_code, "stdout_path": str(run.stdout_path or "")},
        )

    def _run_opencode(self, prompt: str, cwd: Path, timeout: float, label: str) -> AgentResult:
        command = [
            "opencode",
            "run",
            "--auto",
            "--archived",
            "--format",
            "json",
        ]
        if self.config.model:
            command.extend(["-m", self.config.model])
        command.append(prompt)

        started = time.monotonic()
        run = run_command(
            command,
            cwd=cwd,
            timeout_seconds=timeout,
            artifacts_dir=self.artifacts_dir,
            label=label,
            env={"PRO2_AGENT": "opencode"},
            isolation=self.security,
        )
        text_parts: list[str] = []
        cost = 0.0
        session_id = ""
        errors: list[str] = []
        for line in run.stdout.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            session_id = event.get("sessionID", session_id)
            if event.get("type") == "text":
                part = event.get("part") or {}
                if isinstance(part.get("text"), str):
                    text_parts.append(part["text"])
            elif event.get("type") == "step_finish":
                part = event.get("part") or {}
                if isinstance(part.get("cost"), (int, float)):
                    cost += float(part["cost"])
            elif event.get("type") == "error":
                errors.append(str(event.get("error") or event))

        text = "\n".join(text_parts).strip()
        error = run.error or ("; ".join(errors) if errors else "")
        if run.timed_out:
            error = f"agent timed out after {timeout}s"
        return AgentResult(
            ok=run.exit_code == 0 and bool(text) and not errors,
            text=text,
            error=error,
            model=self.config.model,
            duration_seconds=time.monotonic() - started,
            cost_usd=cost,
            session_id=session_id,
            metadata={"exit_code": run.exit_code},
        )

    def _run_claude(self, prompt: str, cwd: Path, timeout: float, label: str) -> AgentResult:
        command = [
            "claude",
            "-p",
            "--output-format",
            "json",
            "--dangerously-skip-permissions",
        ]
        if self.config.model:
            command.extend(["--model", self.config.model])
        command.append(prompt)

        started = time.monotonic()
        run = run_command(
            command,
            cwd=cwd,
            timeout_seconds=timeout,
            artifacts_dir=self.artifacts_dir,
            label=label,
            env={"PRO2_AGENT": "claude"},
            isolation=self.security,
        )
        text = run.stdout
        error = run.error
        cost = 0.0
        try:
            payload = json.loads(run.stdout)
            text = str(payload.get("result", ""))
            cost = float(payload.get("total_cost_usd", 0.0) or 0.0)
            if payload.get("is_error"):
                error = text or "claude reported an error"
        except json.JSONDecodeError:
            pass
        if run.exit_code != 0 and not error:
            error = run.stderr.strip()
        return AgentResult(
            ok=run.exit_code == 0 and bool(text.strip()) and not error,
            text=text.strip(),
            error=error,
            model=self.config.model,
            duration_seconds=time.monotonic() - started,
            cost_usd=cost,
            metadata={"exit_code": run.exit_code},
        )

    def _run_http(self, prompt: str, timeout: float, label: str) -> AgentResult:
        started = time.monotonic()
        api_key = os.environ.get(self.config.api_key_env) if self.config.api_key_env else None
        if not api_key:
            api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise AgentError("http provider requires OPENAI_API_KEY or ANTHROPIC_API_KEY")

        anthropic = bool(os.environ.get("ANTHROPIC_API_KEY")) and not os.environ.get(
            "OPENAI_API_KEY"
        )
        base_url = self.config.base_url or (
            "https://api.anthropic.com" if anthropic else "https://api.openai.com/v1"
        )
        model = self.config.model or ("claude-sonnet-4-5" if anthropic else "gpt-4o-mini")

        if anthropic:
            url = base_url.rstrip("/") + "/v1/messages"
            body = {
                "model": model,
                "max_tokens": 4096,
                "messages": [{"role": "user", "content": prompt}],
            }
            headers = {
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            }
        else:
            url = base_url.rstrip("/") + "/chat/completions"
            body = {
                "model": model,
                "messages": [
                    {"role": "system", "content": "You are a rigorous R&D engineer."},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.2,
            }
            headers = {
                "Authorization": f"Bearer {api_key}",
                "content-type": "application/json",
            }

        request = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            return AgentResult(
                ok=False,
                error=f"http agent failed: {exc}",
                provider="http",
                model=model,
                duration_seconds=time.monotonic() - started,
            )

        if anthropic:
            blocks = payload.get("content") or []
            text = "\n".join(block.get("text", "") for block in blocks if isinstance(block, dict))
            usage = payload.get("usage") or {}
            tokens = int(usage.get("input_tokens", 0)) + int(usage.get("output_tokens", 0))
        else:
            choices = payload.get("choices") or []
            text = choices[0].get("message", {}).get("content", "") if choices else ""
            tokens = int((payload.get("usage") or {}).get("total_tokens", 0))

        cost = tokens / 1000 * self.config.cost_per_1k_tokens
        return AgentResult(
            ok=bool(text.strip()),
            text=text.strip(),
            provider="http",
            model=model,
            duration_seconds=time.monotonic() - started,
            cost_usd=cost,
            metadata={"tokens": tokens},
        )


# ---------------------------------------------------------------------------
# Prompt builders. They demand machine-readable JSON where the engine needs it.
# ---------------------------------------------------------------------------

DIAGNOSE_SCHEMA = """{
  "problems": [
    {
      "description": "...",
      "severity": 0.0-1.0,
      "evidence_ids": ["..."],
      "candidates": [
        {
          "description": "...",
          "rationale": "...",
          "references": ["url"],
          "initial_belief": 0.0-1.0,
          "instructions": "concrete implementation guidance"
        }
      ]
    }
  ]
}"""


def build_diagnosis_prompt(
    objective: Objective,
    evidence_text: str,
    file_context: str,
    research_text: str = "",
) -> str:
    return f"""TASK: DIAGNOSE

You are the Principal Investigator of an autonomous R&D engine.
Objective: {objective.name}
Description: {objective.description}
Success metric: {objective.success.metric} ({objective.success.direction},
min delta {objective.success.min_delta})
Repository: {objective.repo}

Identify the concrete, evidence-backed problems that limit success. Do not invent evidence.
For each problem propose candidate solutions grounded in the evidence or provided research.
Return ONLY JSON matching this schema:
{DIAGNOSE_SCHEMA}

<evidence>
{truncate(evidence_text, 12000)}
</evidence>

<file_context>
{truncate(file_context, 8000)}
</file_context>

<research>
{truncate(research_text, 4000)}
</research>
"""


HYPOTHESIS_SCHEMA = """{
  "hypotheses": [
    {
      "problem_id": "P...",
      "candidate_id": "C...",
      "title": "short title",
      "modification": "single-variable change",
      "target_delta": 0.1,
      "instructions": "precise implementation instructions, including file and value"
    }
  ]
}"""


def build_hypothesis_prompt(
    objective: Objective,
    problem: Problem,
    candidate: Any,
    experience_text: str = "",
) -> str:
    context = {
        "problem_id": problem.problem_id,
        "problem": problem.description,
        "candidate_id": candidate.candidate_id,
        "candidate": candidate.description,
        "rationale": candidate.rationale,
        "instructions": candidate.instructions,
    }
    if candidate.metadata.get("target_score") is not None:
        context["target_score"] = candidate.metadata["target_score"]
    return f"""TASK: HYPOTHESIZE

Objective: {objective.name}
Create at most 2 concrete, atomic hypotheses for the candidate below.
Each hypothesis must be a single-variable change that can be implemented in one experiment.
Return ONLY JSON:
{HYPOTHESIS_SCHEMA}

<context_json>
{json.dumps(context, indent=2)}
</context_json>

<hypothesis_json>
{json.dumps(context, indent=2)}
</hypothesis_json>

<experience>
{experience_text}
</experience>
"""


def build_implement_prompt(
    objective: Objective,
    hypothesis: Hypothesis,
    problem: Problem,
    candidate: Any,
    evidence_text: str,
    experience_text: str = "",
    review_feedback: str = "",
) -> str:
    hypothesis_json = json.dumps(
        {
            "hypothesis_id": hypothesis.hypothesis_id,
            "problem_id": hypothesis.problem_id,
            "candidate_id": hypothesis.candidate_id,
            "title": hypothesis.title,
            "modification": hypothesis.modification,
            "target_delta": hypothesis.target_delta,
            "instructions": hypothesis.instructions,
            "problem": problem.description,
            "candidate": candidate.description,
        },
        indent=2,
    )
    return f"""TASK: IMPLEMENT

You are the Implementation Agent. Work ONLY inside the current working directory.
Do not read or modify anything outside it. Do not modify .git, .pro2, tests, or the
verification command. Do not commit; the engine commits for you.

Objective: {objective.name}
Success metric: {objective.success.metric} ({objective.success.direction})
Allowed change budget: at most {objective.constraints.max_changed_files} files and
{objective.constraints.max_diff_lines} changed lines. Make the smallest single-variable change.
Protected paths: {objective.constraints.protected_paths}
Test edits allowed: {objective.constraints.allow_test_edits}
Network allowed: {objective.constraints.allow_network}

Implement exactly this hypothesis:
<hypothesis_json>
{hypothesis_json}
</hypothesis_json>

<evidence>
{truncate(evidence_text, 8000)}
</evidence>

<experience>
{experience_text}
</experience>

<review_feedback>
{review_feedback}
</review_feedback>

After editing, run the fastest relevant local check if available. Then reply with a short summary.
"""


REVIEW_SCHEMA = """{
  "approved": true,
  "single_variable": true,
  "reasons": ["..."],
  "violations": ["..."]
}"""


def build_review_prompt(
    objective: Objective,
    hypothesis: Hypothesis,
    diff_text: str,
    diff_summary: str,
) -> str:
    return f"""TASK: REVIEW

You are the Reviewer Agent. Audit the diff for scientific rigor and cheating.
Reject if: multiple unrelated variables changed, protected paths touched, tests or the
verification command modified, the change is larger than the budget, or the change
does not implement the hypothesis.

Phase: {objective.name}
Hypothesis: {hypothesis.title}
Modification: {hypothesis.modification}
Limits: {objective.constraints.max_changed_files} files,
{objective.constraints.max_diff_lines} lines
Protected paths: {objective.constraints.protected_paths}
Test edits allowed: {objective.constraints.allow_test_edits}

Return ONLY JSON:
{REVIEW_SCHEMA}

<diff_summary>
{diff_summary}
</diff_summary>

<diff>
{truncate(diff_text, 20000)}
</diff>
"""


SUPERVISE_SCHEMA = """{
  "continue": true,
  "stop_reason": "",
  "notes": "...",
  "new_problems": []
}"""


def build_supervisor_prompt(objective: Objective, summary: str) -> str:
    return f"""TASK: SUPERVISE

You are the Principal Investigator supervising an autonomous research trajectory.
Review progress and decide whether to continue, stop, or add a problem.
Return ONLY JSON:
{SUPERVISE_SCHEMA}

<state>
{truncate(summary, 12000)}
</state>
"""


RESEARCH_SCHEMA = """{
  "findings": [
    {"title": "...", "url": "https://...", "summary": "...", "source": "agent"}
  ]
}"""


def build_research_prompt(problem: Problem) -> str:
    return f"""TASK: RESEARCH

Find concrete, applicable solutions for this engineering problem:
<problem>
{problem.description}
</problem>

Return ONLY JSON. Only include URLs you are confident exist:
{RESEARCH_SCHEMA}
"""
