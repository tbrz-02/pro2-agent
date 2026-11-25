"""Research verification: provenance fields — DOI, commit hash, excerpt, primary source."""

from pro2.config import ResearchConfig
from pro2.research import (
    ResearchEngine,
    ResearchFinding,
    parse_arxiv,
    parse_github,
)

ARXIV_XML = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/2611.25357v2</id>
    <title>Pro Squared Agent</title>
    <summary>We propose Pro2 Agent, an autonomous research engine with a
    scientific problem probing loop and a paper-fidelity specification.</summary>
    <doi>10.48550/arXiv.2611.25357</doi>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/2501.01234</id>
    <title>Other Paper</title>
    <summary>A different result entirely.</summary>
  </entry>
</feed>
"""


def _engine(tmp_path):
    return ResearchEngine(ResearchConfig(enabled=True), tmp_path)


def test_parse_arxiv_extracts_doi_commit_hash_excerpt():
    findings = parse_arxiv(ARXIV_XML, "pro2")
    assert findings[0].doi == "10.48550/arXiv.2611.25357"
    # The versioned arXiv id is the stable source identifier (commit hash).
    assert findings[0].commit_hash == "2611.25357v2"
    assert findings[0].excerpt
    assert findings[0].excerpt.startswith("We propose Pro2 Agent")
    assert findings[1].doi == ""


def test_parse_github_findings_carry_url(tmp_path):
    import json

    payload = json.dumps(
        {"items": [{"html_url": "https://github.com/o/r", "full_name": "o/r", "description": "d"}]}
    )
    findings = parse_github(payload, "q")
    assert findings[0].url == "https://github.com/o/r"


def test_verify_finding_github_records_commit_hash(tmp_path, monkeypatch):
    engine = _engine(tmp_path)
    finding = ResearchFinding(
        finding_id="R-gh-1",
        title="o/r",
        url="https://github.com/o/r",
        summary="d",
        source="github",
        query="q",
        fetch_url="https://api.github.com/search/repositories?q=r",
        fetch_digest="abc",
    )
    import json

    search_payload = json.dumps(
        {"items": [{"html_url": "https://github.com/o/r", "description": "d"}]}
    )
    commits_payload = json.dumps({"sha": "deadbeefcafe"})

    def fake_fetch(url, timeout):
        if "commits/HEAD" in url:
            return commits_payload, "sha-digest"
        return search_payload, "search-digest"

    monkeypatch.setattr("pro2.research._fetch_with_digest", fake_fetch)
    assert engine._verify_finding(finding) is True
    assert finding.commit_hash == "deadbeefcafe"
    assert finding.excerpt


def test_verify_finding_arxiv_records_excerpt(tmp_path, monkeypatch):
    engine = _engine(tmp_path)
    finding = ResearchFinding(
        finding_id="R-arxiv-0",
        title="Pro Squared Agent",
        url="http://arxiv.org/abs/2611.25357v2",
        summary="We propose Pro2 Agent.",
        source="arxiv",
        query="q",
        fetch_url="http://export.arxiv.org/api/query?search_query=all:pro2",
    )
    monkeypatch.setattr("pro2.research._fetch_with_digest", lambda url, t: (ARXIV_XML, "digest"))
    assert engine._verify_finding(finding) is True
    assert finding.excerpt


def test_verify_finding_contradiction_returns_false(tmp_path, monkeypatch):
    engine = _engine(tmp_path)
    finding = ResearchFinding(
        finding_id="R-arxiv-0",
        title="Completely Different Title",
        url="http://arxiv.org/abs/2611.25357v2",
        summary="x",
        source="arxiv",
        query="q",
        fetch_url="http://export.arxiv.org/api/query",
    )
    monkeypatch.setattr("pro2.research._fetch_with_digest", lambda url, t: (ARXIV_XML, "digest"))
    assert engine._verify_finding(finding) is False


def test_verify_doi_true_when_resolvable(tmp_path, monkeypatch):
    engine = _engine(tmp_path)
    monkeypatch.setattr("pro2.research._fetch_with_digest", lambda url, t: ("<html/>", "digest"))
    assert engine._verify_doi("10.48550/arXiv.2611.25357") is True


def test_verify_doi_none_when_unreachable(tmp_path, monkeypatch):
    engine = _engine(tmp_path)

    def boom(url, timeout):
        raise OSError("no network")

    monkeypatch.setattr("pro2.research._fetch_with_digest", boom)
    assert engine._verify_doi("10.48550/arXiv.2611.25357") is None
    assert engine._verify_doi("") is None


def test_search_verify_primary_downgrades_on_doi_contradiction(tmp_path, monkeypatch):
    engine = ResearchEngine(ResearchConfig(enabled=True, verify_primary=True), tmp_path)
    finding = ResearchFinding(
        finding_id="R-1",
        title="t",
        url="https://example.com/paper",
        summary="s",
        source="arxiv",
        query="q",
        doi="10.48550/arXiv.2611.25357",
    )
    monkeypatch.setattr(engine, "_query_providers", lambda q: [finding])
    monkeypatch.setattr(engine, "_verify_finding", lambda f: True)
    monkeypatch.setattr(engine, "_verify_doi", lambda doi, title="": False)
    results = engine.search(["q"])
    assert results and results[0].verified is False
