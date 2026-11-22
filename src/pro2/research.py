"""Bottleneck-conditioned literature and repository research."""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Iterable
from dataclasses import dataclass, field
from html import unescape
from pathlib import Path
from typing import Any

from pro2.config import ResearchConfig
from pro2.problem import Problem
from pro2.util import read_json, truncate, utc_now, write_json

__all__ = [
    "ResearchConfig",
    "ResearchEngine",
    "ResearchFinding",
    "parse_arxiv",
    "parse_duckduckgo",
    "parse_github",
]


@dataclass
class ResearchFinding:
    finding_id: str
    title: str
    url: str
    summary: str
    source: str
    query: str
    fetched_at: str = field(default_factory=utc_now)
    verified: bool = False

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ResearchFinding:
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in data.items() if k in known})


def _http_get(url: str, timeout: float) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "pro2-agent/0.1 (research; +https://github.com/)",
            "Accept": "application/json, application/atom+xml, text/html;q=0.9",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def parse_github(payload: str | dict[str, Any], query: str) -> list[ResearchFinding]:
    data = json.loads(payload) if isinstance(payload, str) else payload
    findings = []
    for index, item in enumerate(data.get("items", []) or []):
        url = str(item.get("html_url", ""))
        if not url:
            continue
        findings.append(
            ResearchFinding(
                finding_id=f"R-gh-{index}",
                title=str(item.get("full_name") or item.get("name") or url),
                url=url,
                summary=str(item.get("description") or ""),
                source="github",
                query=query,
                verified=True,
            )
        )
    return findings


def parse_arxiv(xml: str, query: str) -> list[ResearchFinding]:
    findings = []
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return findings
    namespace = "{http://www.w3.org/2005/Atom}"
    for index, entry in enumerate(root.findall(f"{namespace}entry")):
        title = (entry.findtext(f"{namespace}title") or "").strip()
        url = (entry.findtext(f"{namespace}id") or "").strip()
        summary = (entry.findtext(f"{namespace}summary") or "").strip()
        if not url:
            continue
        findings.append(
            ResearchFinding(
                finding_id=f"R-arxiv-{index}",
                title=re.sub(r"\s+", " ", title),
                url=url,
                summary=re.sub(r"\s+", " ", summary),
                source="arxiv",
                query=query,
                verified=True,
            )
        )
    return findings


def parse_duckduckgo(html: str, query: str) -> list[ResearchFinding]:
    pattern = re.compile(
        r'<a[^>]*class="[^"]*result__a[^"]*"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
        re.IGNORECASE | re.DOTALL,
    )
    findings = []
    for index, match in enumerate(pattern.finditer(html)):
        href = unescape(match.group(1))
        title = re.sub(r"<[^>]+>", "", unescape(match.group(2))).strip()
        parsed = urllib.parse.urlparse(href)
        if parsed.netloc.endswith("duckduckgo.com"):
            query_params = urllib.parse.parse_qs(parsed.query)
            href = (query_params.get("uddg") or [href])[0]
        if not href.startswith("http"):
            continue
        findings.append(
            ResearchFinding(
                finding_id=f"R-ddg-{index}",
                title=title or href,
                url=href,
                summary="",
                source="duckduckgo",
                query=query,
                verified=False,
            )
        )
    return findings


class ResearchEngine:
    def __init__(self, config: ResearchConfig, state_dir: str | Path) -> None:
        self.config = config
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.cache_path = self.state_dir / "cache.json"
        self._cache: dict[str, Any] = {}
        if self.cache_path.exists():
            try:
                loaded = read_json(self.cache_path)
                if isinstance(loaded, dict):
                    self._cache = loaded
            except (ValueError, OSError):
                self._cache = {}

    def search(self, queries: Iterable[str]) -> list[ResearchFinding]:
        if not self.config.enabled:
            return []
        findings: list[ResearchFinding] = []
        seen: set[str] = set()
        for query in list(queries)[: self.config.max_queries]:
            query = query.strip()
            if not query:
                continue
            cached = self._cache.get(self._cache_key(query))
            if cached:
                batch = [ResearchFinding.from_dict(x) for x in cached.get("findings", [])]
            else:
                batch = self._query_providers(query)
                self._cache[self._cache_key(query)] = {
                    "timestamp": utc_now(),
                    "findings": [f.to_dict() for f in batch],
                }
            for finding in batch:
                if finding.url in seen:
                    continue
                seen.add(finding.url)
                findings.append(finding)
        self._save_cache()
        return findings

    def search_problems(self, problems: Iterable[Problem]) -> list[ResearchFinding]:
        queries = []
        for problem in problems:
            words = re.findall(r"[A-Za-z0-9_.+-]{3,}", problem.description)
            if words:
                queries.append(" ".join(words[:12]))
        return self.search(queries)

    def _cache_key(self, query: str) -> str:
        providers = ",".join(sorted(self.config.providers))
        return f"{providers}:{query.lower()}"

    def _query_providers(self, query: str) -> list[ResearchFinding]:
        findings: list[ResearchFinding] = []
        encoded = urllib.parse.quote_plus(query)
        limit = self.config.max_results_per_query
        for provider in self.config.providers:
            try:
                if provider == "github":
                    url = (
                        "https://api.github.com/search/repositories"
                        f"?q={encoded}&sort=stars&order=desc&per_page={limit}"
                    )
                    findings.extend(
                        parse_github(_http_get(url, self.config.timeout_seconds), query)
                    )
                elif provider == "arxiv":
                    url = (
                        "http://export.arxiv.org/api/query"
                        f"?search_query=all:{encoded}&start=0&max_results={limit}"
                    )
                    findings.extend(parse_arxiv(_http_get(url, self.config.timeout_seconds), query))
                elif provider == "duckduckgo":
                    url = f"https://html.duckduckgo.com/html/?q={encoded}"
                    findings.extend(
                        parse_duckduckgo(_http_get(url, self.config.timeout_seconds), query)
                    )
            except Exception:
                continue
        return findings[:limit]

    def _save_cache(self) -> None:
        write_json(self.cache_path, self._cache)

    @staticmethod
    def format_for_prompt(findings: Iterable[ResearchFinding], limit: int = 8) -> str:
        lines = []
        for finding in list(findings)[:limit]:
            lines.append(
                f"- {finding.title} ({finding.source})\n"
                f"  {finding.url}\n"
                f"  {truncate(finding.summary, 300)}"
            )
        return "\n".join(lines)
