import json

from pro2.research import (
    ResearchConfig,
    ResearchEngine,
    parse_arxiv,
    parse_duckduckgo,
    parse_github,
)


def test_parse_github_repositories():
    payload = {
        "items": [
            {
                "full_name": "owner/repo",
                "html_url": "https://github.com/owner/repo",
                "description": "A useful tool",
                "stargazers_count": 42,
            }
        ]
    }

    findings = parse_github(payload, "query")

    assert len(findings) == 1
    assert findings[0].title == "owner/repo"
    assert findings[0].source == "github"


def test_parse_arxiv_entries():
    xml = """<?xml version="1.0"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <title>Fast Caching for Python</title>
        <id>http://arxiv.org/abs/1234.5678</id>
        <summary>We cache things.</summary>
      </entry>
    </feed>"""

    findings = parse_arxiv(xml, "caching")

    assert len(findings) == 1
    assert findings[0].title == "Fast Caching for Python"
    assert findings[0].source == "arxiv"


def test_parse_duckduckgo_results():
    html = '<a class="result__a" href="https://example.test/a">Example Result</a>'

    findings = parse_duckduckgo(html, "query")

    assert len(findings) == 1
    assert findings[0].url == "https://example.test/a"


def test_disabled_research_returns_nothing(tmp_path):
    engine = ResearchEngine(ResearchConfig(enabled=False), tmp_path / "research")

    assert engine.search(["anything"]) == []


def test_research_cache_is_used(tmp_path, monkeypatch):
    calls = []

    def fake_get(url, timeout):
        calls.append(url)
        return json.dumps(
            {
                "items": [
                    {
                        "full_name": "a/b",
                        "html_url": "https://github.com/a/b",
                        "description": "d",
                        "stargazers_count": 1,
                    }
                ]
            }
        )

    import pro2.research as research

    monkeypatch.setattr(research, "_http_get", fake_get)
    engine = ResearchEngine(
        ResearchConfig(enabled=True, providers=["github"], max_results_per_query=1),
        tmp_path / "research",
    )

    first = engine.search(["cache"])
    second = engine.search(["cache"])

    assert len(first) == 1
    assert len(second) == 1
    assert len(calls) == 1


def test_research_survives_provider_errors(tmp_path, monkeypatch):
    def boom(url, timeout):
        raise OSError("offline")

    import pro2.research as research

    monkeypatch.setattr(research, "_http_get", boom)
    engine = ResearchEngine(
        ResearchConfig(enabled=True, providers=["github", "arxiv"], max_results_per_query=1),
        tmp_path / "research",
    )

    assert engine.search(["cache"]) == []
