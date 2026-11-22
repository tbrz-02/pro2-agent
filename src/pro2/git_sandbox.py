"""Git-backed experiment isolation: worktrees, branches, rollback and best refs."""

from __future__ import annotations

import fnmatch
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path

from pro2.util import safe_id, sha256_text, truncate


class GitError(RuntimeError):
    """Raised when a git operation fails."""


class ProtectedPathError(GitError):
    """Raised when an experiment touches a protected path."""


@dataclass
class Worktree:
    experiment_id: str
    branch: str
    path: Path
    base_commit: str


@dataclass
class DiffStats:
    changed_paths: list[str]
    added: int
    removed: int
    patch: str
    untracked: list[str] | None = None


class GitSandbox:
    """Owns all experiment worktrees and the persistent best snapshot."""

    def __init__(
        self,
        repo: str | Path,
        worktree_root: str | Path,
        verify_root: str | Path,
    ) -> None:
        self.repo = Path(repo).resolve()
        self.worktree_root = Path(worktree_root).resolve()
        self.verify_root = Path(verify_root).resolve()
        self._git("rev-parse", "--git-dir")

    def _git(self, *args: str, cwd: Path | None = None, check: bool = True) -> str:
        result = subprocess.run(
            ["git", *args],
            cwd=str(cwd or self.repo),
            capture_output=True,
            text=True,
        )
        if check and result.returncode != 0:
            raise GitError(
                f"git {' '.join(args)} failed ({result.returncode}): "
                f"{result.stderr.strip() or result.stdout.strip()}"
            )
        return result.stdout

    def head(self) -> str:
        return self._git("rev-parse", "HEAD").strip()

    def is_clean(self) -> bool:
        return not self._git("status", "--porcelain").strip()

    def ensure_state_excluded(self, state_dir: str = ".pro2") -> None:
        self.ensure_excluded(state_dir.rstrip("/") + "/")

    def ensure_excluded(self, entry: str) -> None:
        """Keep engine-owned files (state dir, config) out of the dirty check."""
        normalized = entry.replace("\\", "/").strip()
        if not normalized:
            return
        exclude = self.repo / ".git" / "info" / "exclude"
        exclude.parent.mkdir(parents=True, exist_ok=True)
        text = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
        if normalized not in text.splitlines():
            with exclude.open("a", encoding="utf-8") as handle:
                if text and not text.endswith("\n"):
                    handle.write("\n")
                handle.write(normalized + "\n")

    def create_experiment(self, experiment_id: str, base_commit: str) -> Worktree:
        self.worktree_root.mkdir(parents=True, exist_ok=True)
        slug = safe_id(experiment_id)
        branch = f"pro2/exp/{slug}"
        path = self.worktree_root / slug
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)
        self._git("worktree", "prune")
        if branch in self.branches():
            self._git("branch", "-D", branch, check=False)
        self._git("worktree", "add", "-b", branch, str(path), base_commit)
        return Worktree(experiment_id, branch, path, base_commit)

    def commit_all(self, path: str | Path, message: str) -> str | None:
        worktree = Path(path)
        self._git("add", "-A", cwd=worktree)
        status = self._git("status", "--porcelain", cwd=worktree)
        if not status.strip():
            return None
        self._git(
            "-c",
            "user.name=Pro2 Agent",
            "-c",
            "user.email=pro2@local",
            "commit",
            "-m",
            message,
            cwd=worktree,
        )
        return self._git("rev-parse", "HEAD", cwd=worktree).strip()

    def diff_stats(self, path: str | Path, base_commit: str) -> DiffStats:
        worktree = Path(path)
        name_only = self._git("diff", "--name-only", f"{base_commit}..HEAD", cwd=worktree)
        numstat = self._git("diff", "--numstat", f"{base_commit}..HEAD", cwd=worktree)
        untracked = self._git("ls-files", "--others", "--exclude-standard", cwd=worktree)
        added = removed = 0
        for line in numstat.splitlines():
            parts = line.split("\t")
            if len(parts) >= 3:
                try:
                    added += int(parts[0]) if parts[0] != "-" else 0
                    removed += int(parts[1]) if parts[1] != "-" else 0
                except ValueError:
                    continue
        patch = self._git("diff", f"{base_commit}..HEAD", cwd=worktree)
        return DiffStats(
            changed_paths=[p for p in name_only.splitlines() if p.strip()],
            added=added,
            removed=removed,
            patch=truncate(patch, 20000),
            untracked=[p for p in untracked.splitlines() if p.strip()],
        )

    def assert_not_protected(
        self,
        path: str | Path,
        base_commit: str,
        patterns: list[str],
    ) -> None:
        stats = self.diff_stats(path, base_commit)
        changed = stats.changed_paths + (stats.untracked or [])
        violations = sorted(
            {
                changed_path
                for changed_path in changed
                for pattern in patterns
                if _matches(changed_path, pattern)
            }
        )
        if violations:
            raise ProtectedPathError("experiment touched protected paths: " + ", ".join(violations))

    def merge_into_primary(self, commit: str, message: str) -> str | None:
        """Merge a commit back into the primary branch; returns the merge commit or None.

        The paper requires the highest-validation engineering trajectory to be
        merged back into the primary branch as the final reference model.
        """
        branch = self._git("rev-parse", "--abbrev-ref", "HEAD").strip()
        if not branch or branch.startswith("(HEAD detached"):
            return None
        result = subprocess.run(
            ["git", "merge", "--no-edit", "-m", message, commit],
            cwd=str(self.repo),
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise GitError(
                f"git merge failed ({result.returncode}): "
                f"{result.stderr.strip() or result.stdout.strip()}"
            )
        if "already up to date" in result.stdout.lower():
            return None
        return self._git("rev-parse", "HEAD").strip()

    def remove_experiment(self, worktree: Worktree) -> None:
        self._remove_worktree_path(worktree.path)
        self._git("branch", "-D", worktree.branch, check=False)

    def create_verification_worktree(self, commit: str, experiment_id: str) -> Path:
        self.verify_root.mkdir(parents=True, exist_ok=True)
        path = self.verify_root / f"{safe_id(experiment_id)}-{uuid.uuid4().hex[:8]}"
        self._git("worktree", "add", "--detach", str(path), commit)
        return path

    def remove_verification_worktree(self, path: str | Path) -> None:
        self._remove_worktree_path(Path(path))

    def _remove_worktree_path(self, path: Path) -> None:
        if not path.exists():
            self._git("worktree", "prune", check=False)
            return
        result = subprocess.run(
            ["git", "worktree", "remove", "--force", str(path)],
            cwd=str(self.repo),
            capture_output=True,
            text=True,
        )
        if result.returncode != 0 and path.exists():
            shutil.rmtree(path, ignore_errors=True)
        self._git("worktree", "prune", check=False)

    def branches(self) -> list[str]:
        output = self._git("branch", "--format=%(refname:short)")
        return [line.strip() for line in output.splitlines() if line.strip()]

    def set_best(self, commit: str) -> None:
        self._git("update-ref", "refs/pro2/best", commit)
        self._git("update-ref", "refs/heads/pro2/best", commit)

    def best_commit(self) -> str:
        result = subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", "refs/pro2/best"],
            cwd=str(self.repo),
            capture_output=True,
            text=True,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
        return self.head()

    def fingerprint(self) -> dict[str, str]:
        status = self._git("status", "--porcelain")
        return {
            "head": self.head(),
            "status_hash": sha256_text(status),
            "status": truncate(status, 4000),
        }

    def cleanup_orphans(self, active_paths: set[str] | None = None) -> list[str]:
        """Remove only engine-owned worktrees/branches that are not active."""
        active = {str(Path(p).resolve()) for p in (active_paths or set())}
        removed: list[str] = []
        output = self._git("worktree", "list", "--porcelain")
        for block in output.split("\n\n"):
            path_line = next((ln for ln in block.splitlines() if ln.startswith("path ")), "")
            if not path_line:
                continue
            path = Path(path_line[5:].strip()).resolve()
            if path in active:
                continue
            if path.is_relative_to(self.worktree_root) or path.is_relative_to(self.verify_root):
                self._remove_worktree_path(path)
                removed.append(str(path))

        active_branches = {
            Path(p).name for p in active if Path(p).is_relative_to(self.worktree_root)
        }
        for branch in self.branches():
            if branch.startswith("pro2/exp/") and branch.split("/")[-1] not in active_branches:
                self._git("branch", "-D", branch, check=False)
                removed.append(branch)
        return removed

    def list_worktrees(self) -> list[Path]:
        output = self._git("worktree", "list", "--porcelain")
        return [
            Path(line[5:].strip())
            for line in output.splitlines()
            if line.startswith("path ") and Path(line[5:].strip()) != self.repo
        ]


def _matches(path: str, pattern: str) -> bool:
    normalized = path.replace("\\", "/").lstrip("./")
    pattern = pattern.replace("\\", "/").lstrip("./")
    if pattern.endswith("/**"):
        prefix = pattern[:-3].rstrip("/")
        return normalized == prefix or normalized.startswith(prefix + "/")
    if fnmatch.fnmatch(normalized, pattern):
        return True
    if "/" not in pattern and fnmatch.fnmatch(Path(normalized).name, pattern):
        return True
    return False
