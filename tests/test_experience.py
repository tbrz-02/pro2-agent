from pro2.experience import ExperienceBank, normalize_error


def test_add_and_search_experience(tmp_path):
    bank = ExperienceBank(tmp_path / "experience.jsonl")

    bank.add(
        kind="failure",
        signature="ModuleNotFoundError: No module named 'foo'",
        context="running tests in a fresh worktree",
        root_cause="dependency not installed",
        fix="install foo or guard the import",
        outcome="rejected",
        tags=["python", "import"],
    )

    results = bank.search("ModuleNotFoundError foo")

    assert len(results) == 1
    assert results[0].kind == "failure"
    assert results[0].root_cause == "dependency not installed"


def test_search_returns_empty_for_unrelated_query(tmp_path):
    bank = ExperienceBank(tmp_path / "experience.jsonl")
    bank.add("success", "SCORE", "toy", "config", "raise value", "accepted", ["toy"])

    assert bank.search("kubernetes ingress") == []


def test_experience_is_persisted(tmp_path):
    path = tmp_path / "experience.jsonl"
    bank = ExperienceBank(path)
    bank.add("success", "SCORE", "toy", "config", "raise value", "accepted", ["toy"])

    reloaded = ExperienceBank(path)

    assert len(reloaded.all()) == 1


def test_prune_keeps_most_recent(tmp_path):
    bank = ExperienceBank(tmp_path / "experience.jsonl", max_entries=2)
    for i in range(5):
        bank.add("failure", f"sig-{i}", f"ctx-{i}", "cause", "fix", "rejected", [])

    assert len(bank.all()) == 2
    assert bank.all()[-1].signature == "sig-4"


def test_normalize_error_strips_noise():
    a = normalize_error('File "/tmp/x.py", line 12, in foo\nValueError: bad value 0xDEAD')
    b = normalize_error('File "/home/y.py", line 99, in foo\nValueError: bad value 0xBEEF')

    assert a == b
    assert "0x" not in a


def test_format_for_prompt_contains_actionable_fields(tmp_path):
    bank = ExperienceBank(tmp_path / "experience.jsonl")
    bank.add("failure", "ImportError", "tests", "missing dep", "install dep", "rejected", [])

    text = bank.format_for_prompt(bank.search("ImportError"))

    assert "ImportError" in text
    assert "install dep" in text


def test_duplicate_signature_updates_existing_entry(tmp_path):
    bank = ExperienceBank(tmp_path / "experience.jsonl")
    bank.add("failure", "ImportError", "tests", "missing dep", "install dep", "rejected", [])
    bank.add("success", "ImportError", "tests", "missing dep", "install dep", "accepted", [])

    assert len(bank.all()) == 1
    assert bank.all()[0].outcome == "accepted"
