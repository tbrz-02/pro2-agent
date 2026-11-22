import subprocess
import sys
from pathlib import Path

import pytest


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@pytest.fixture
def toy_repo(tmp_path):
    repo = tmp_path / "toy"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "toy@example.test")
    git(repo, "config", "user.name", "Toy")

    (repo / "config.json").write_text('{"score": 1.0, "work": 1.0}\n')
    (repo / "run.py").write_text(
        "import json\n"
        "with open('config.json') as f:\n"
        "    cfg = json.load(f)\n"
        "print('SCORE=%s' % cfg['score'])\n"
        "print('WORK=%s' % cfg['work'])\n"
    )
    (repo / "tests").mkdir()
    (repo / "tests" / "test_score.py").write_text(
        "import json\n"
        "def test_score_positive():\n"
        "    assert json.load(open('config.json'))['score'] > 0\n"
    )
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "baseline")
    return repo


@pytest.fixture
def scripted_agent(tmp_path):
    path = tmp_path / "scripted_agent.py"
    path.write_text(SCRIPTED_AGENT)
    return [sys.executable, str(path)]


SCRIPTED_AGENT = r"""
import json
import os
import re
import sys

prompt = sys.stdin.read()
task = "UNKNOWN"
for marker in ("DIAGNOSE", "HYPOTHESIZE", "IMPLEMENT", "REVIEW", "SUPERVISE", "RESEARCH"):
    if "TASK: " + marker in prompt:
        task = marker

payload = {}
match = re.search(r"<hypothesis_json>\s*(.*?)\s*</hypothesis_json>", prompt, re.S)
if match:
    try:
        payload = json.loads(match.group(1))
    except Exception:
        payload = {}

if task == "DIAGNOSE":
    print(json.dumps({
        "problems": [{
            "description": "Toy score is limited by a fixed configuration value",
            "severity": 0.8,
            "evidence_ids": [],
            "candidates": [{
                "description": "Raise the configured score",
                "rationale": "The score is read directly from config.json",
                "references": [],
                "initial_belief": 0.6,
            }],
        }]
    }))
elif task == "HYPOTHESIZE":
    target = payload.get("target_score", 2.0)
    print(json.dumps({
        "hypotheses": [{
            "problem_id": payload.get("problem_id", "P001"),
            "candidate_id": payload.get("candidate_id", "C001"),
            "title": "Set score to %s" % target,
            "modification": "Set config.json score to %s" % target,
            "target_delta": 0.5,
            "instructions": "SCORE_TARGET=%s" % target,
        }]
    }))
elif task == "IMPLEMENT":
    target = 2.0
    m = re.search(r"SCORE_TARGET=([0-9.]+)", prompt)
    if m:
        target = float(m.group(1))
    if os.path.exists("config.json"):
        with open("config.json") as f:
            cfg = json.load(f)
        cfg["score"] = target
        with open("config.json", "w") as f:
            json.dump(cfg, f)
    print("implemented target %s" % target)
elif task == "REVIEW":
    print(json.dumps({
        "approved": True,
        "single_variable": True,
        "reasons": ["ok"],
        "violations": [],
    }))
elif task == "SUPERVISE":
    print(json.dumps({"continue": True, "notes": "on track"}))
elif task == "RESEARCH":
    print(json.dumps({"findings": []}))
else:
    print(json.dumps({"error": "unknown task"}))
"""
