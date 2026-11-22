import pytest

from pro2.git_sandbox import GitSandbox, ProtectedPathError


def test_head_returns_baseline_commit(toy_repo):
    sandbox = GitSandbox(toy_repo, toy_repo / ".pro2" / "worktrees", toy_repo / ".pro2" / "verify")
    head = sandbox.head()

    assert len(head) == 40


def test_create_worktree_and_commit(toy_repo):
    sandbox = GitSandbox(toy_repo, toy_repo / ".pro2" / "worktrees", toy_repo / ".pro2" / "verify")
    wt = sandbox.create_experiment("E1", sandbox.head())

    (wt.path / "config.json").write_text('{"score": 2.0, "work": 1.0}\n')
    commit = sandbox.commit_all(wt.path, "raise score")

    assert commit is not None
    assert sandbox.diff_stats(wt.path, wt.base_commit).changed_paths == ["config.json"]

    sandbox.remove_experiment(wt)
    assert not wt.path.exists()


def test_rollback_removes_branch(toy_repo):
    sandbox = GitSandbox(toy_repo, toy_repo / ".pro2" / "worktrees", toy_repo / ".pro2" / "verify")
    wt = sandbox.create_experiment("E1", sandbox.head())
    (wt.path / "config.json").write_text('{"score": 2.0, "work": 1.0}\n')
    sandbox.commit_all(wt.path, "raise score")

    sandbox.remove_experiment(wt)

    assert wt.branch not in sandbox.branches()
    assert not wt.path.exists()


def test_protected_paths_are_detected(toy_repo):
    sandbox = GitSandbox(toy_repo, toy_repo / ".pro2" / "worktrees", toy_repo / ".pro2" / "verify")
    wt = sandbox.create_experiment("E1", sandbox.head())
    (wt.path / "run.py").write_text("print('hacked')\n")
    sandbox.commit_all(wt.path, "hack runner")

    with pytest.raises(ProtectedPathError):
        sandbox.assert_not_protected(wt.path, wt.base_commit, ["run.py"])


def test_diff_stats_counts_lines(toy_repo):
    sandbox = GitSandbox(toy_repo, toy_repo / ".pro2" / "worktrees", toy_repo / ".pro2" / "verify")
    wt = sandbox.create_experiment("E1", sandbox.head())
    (wt.path / "config.json").write_text('{"score": 2.0, "work": 1.0, "extra": 3}\n')
    sandbox.commit_all(wt.path, "edit")

    stats = sandbox.diff_stats(wt.path, wt.base_commit)

    assert stats.added >= 1
    assert stats.removed >= 1
    assert stats.patch


def test_best_ref_is_updated(toy_repo):
    sandbox = GitSandbox(toy_repo, toy_repo / ".pro2" / "worktrees", toy_repo / ".pro2" / "verify")
    wt = sandbox.create_experiment("E1", sandbox.head())
    (wt.path / "config.json").write_text('{"score": 2.0, "work": 1.0}\n')
    commit = sandbox.commit_all(wt.path, "raise")

    sandbox.set_best(commit)

    assert sandbox.best_commit() == commit


def test_clean_check_detects_dirty_repo(toy_repo):
    sandbox = GitSandbox(toy_repo, toy_repo / ".pro2" / "worktrees", toy_repo / ".pro2" / "verify")
    (toy_repo / "untracked.txt").write_text("dirty")

    assert sandbox.is_clean() is False


def test_state_directory_is_git_excluded(toy_repo):
    sandbox = GitSandbox(toy_repo, toy_repo / ".pro2" / "worktrees", toy_repo / ".pro2" / "verify")
    sandbox.ensure_state_excluded()

    exclude = (toy_repo / ".git" / "info" / "exclude").read_text()
    assert ".pro2/" in exclude
    assert sandbox.is_clean() is True


def test_verification_worktree_is_independent(toy_repo):
    sandbox = GitSandbox(toy_repo, toy_repo / ".pro2" / "worktrees", toy_repo / ".pro2" / "verify")
    wt = sandbox.create_experiment("E1", sandbox.head())
    (wt.path / "config.json").write_text('{"score": 2.0, "work": 1.0}\n')
    commit = sandbox.commit_all(wt.path, "raise")

    verify_path = sandbox.create_verification_worktree(commit, "E1")

    assert verify_path != wt.path
    assert (verify_path / "config.json").read_text().strip() == '{"score": 2.0, "work": 1.0}'
    sandbox.remove_verification_worktree(verify_path)


def test_diff_stats_handles_no_changes(toy_repo):
    sandbox = GitSandbox(toy_repo, toy_repo / ".pro2" / "worktrees", toy_repo / ".pro2" / "verify")
    wt = sandbox.create_experiment("E1", sandbox.head())

    stats = sandbox.diff_stats(wt.path, wt.base_commit)

    assert stats.changed_paths == []
    assert stats.added == 0
    assert stats.removed == 0


def test_repository_fingerprint_changes_when_tracked_file_changes(toy_repo):
    sandbox = GitSandbox(toy_repo, toy_repo / ".pro2" / "worktrees", toy_repo / ".pro2" / "verify")
    before = sandbox.fingerprint()
    (toy_repo / "config.json").write_text('{"score": 9.0, "work": 1.0}\n')
    after = sandbox.fingerprint()

    assert before != after
