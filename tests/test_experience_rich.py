"""Experience Bank: observation / causal hypothesis / verified fix / reproducibility."""

from pro2.experience import ExperienceBank


def test_entry_roundtrips_rich_fields(tmp_path):
    bank = ExperienceBank(tmp_path / "experience.jsonl")
    entry = bank.add(
        kind="success",
        signature="SIG",
        context="ctx",
        root_cause="cause",
        fix="fix",
        outcome="accepted",
        observation="the score did not move",
        causal_hypothesis="stale config value",
        verified_fix="bump config to 2.0",
        reproducible="commit=abc metric=2.0",
        evidence_refs=["E-1"],
    )
    assert entry.observation == "the score did not move"
    assert entry.causal_hypothesis == "stale config value"
    assert entry.verified_fix == "bump config to 2.0"
    assert entry.reproducible == "commit=abc metric=2.0"
    assert entry.evidence_refs == ["E-1"]


def test_rich_fields_persist_and_merge(tmp_path):
    bank = ExperienceBank(tmp_path / "experience.jsonl")
    bank.add(
        kind="success",
        signature="SIG",
        context="",
        root_cause="",
        fix="",
        outcome="",
        observation="obs1",
    )
    reloaded = ExperienceBank(tmp_path / "experience.jsonl")
    entry = reloaded.add(
        kind="success",
        signature="SIG",
        context="ctx2",
        root_cause="",
        fix="",
        outcome="",
        verified_fix="the fix",
        reproducible="commit=abc",
        evidence_refs=["E-1"],
    )
    assert entry.observation == "obs1"
    assert entry.verified_fix == "the fix"
    assert entry.reproducible == "commit=abc"
    assert entry.evidence_refs == ["E-1"]


def test_format_for_prompt_includes_rich_fields(tmp_path):
    bank = ExperienceBank(tmp_path / "experience.jsonl")
    bank.add(
        kind="failure",
        signature="SIG",
        context="",
        root_cause="cause",
        fix="fix",
        outcome="rejected",
        observation="obs",
        causal_hypothesis="hyp",
        verified_fix="vfix",
        reproducible="repro",
    )
    text = bank.format_for_prompt(bank.all())
    assert "observation: obs" in text
    assert "causal hypothesis: hyp" in text
    assert "verified fix: vfix" in text
    assert "reproducible: repro" in text


def test_search_matches_observation_and_verified_fix(tmp_path):
    bank = ExperienceBank(tmp_path / "experience.jsonl")
    bank.add(
        kind="failure",
        signature="unique-sig",
        context="",
        root_cause="",
        fix="",
        outcome="",
        observation="zephyr quixotic observation",
        verified_fix="perform diligent zephyr correction",
    )
    assert bank.search("zephyr")
