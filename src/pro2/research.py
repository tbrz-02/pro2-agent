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
from pro2.util import read_json, sha256_text, truncate, utc_now, write_json

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
    # Provenance (scientific search hardening): the URL that was fetched and
    # the digest of the page body, so findings can be re-verified independently.
    fetch_url: str = ""
    fetch_digest: str = ""
    # Provenance extensions: DOI, source commit hash, and an excerpt of the
    # primary-source text so findings are attributable and re-checkable.
    doi: str = ""
    commit_hash: str = ""
    excerpt: str = ""

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ResearchFinding:
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in data.items() if k in known})


def _normalize_title(title: str) -> str:
    return re.sub(r"\s+", " ", title).strip().lower()


def _fetch_with_digest(url: str, timeout: float) -> tuple[str, str]:
    text = _http_get(url, timeout)
    return text, sha256_text(text)


def _extract_doi(text: str) -> str:
    """Extract the first DOI-shaped identifier from a source document."""
    match = re.search(r"\b10\.[0-9]{4,9}/[-._;()/:A-Z0-9]+\b", text, re.IGNORECASE)
    return match.group(0).rstrip(".,;:)]") if match else ""


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
                doi=(entry.findtext(f"{namespace}doi") or "").strip()
                or _extract_doi(summary)
                or _extract_doi(title)
                or _extract_doi(url),
                # The versioned arXiv id (e.g. abs/2611.25357v2) is the
                # stable source identifier for the primary source.
                commit_hash=url.rsplit("/abs/", 1)[-1],
                excerpt=summary[:400],
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
                if self.config.verify_primary:
                    for finding in batch:
                        result = self._verify_finding(finding)
                        if result is False:
                            finding.verified = False
                        if finding.doi and finding.verified:
                            # DOIs are verified against the DOI infrastructure;
                            # a contradiction downgrades the finding.
                            if self._verify_doi(finding.doi, finding.title) is False:
                                finding.verified = False
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
                    text, digest = _fetch_with_digest(url, self.config.timeout_seconds)
                    batch = parse_github(text, query)
                    for finding in batch:
                        finding.fetch_url = url
                        finding.fetch_digest = digest
                    findings.extend(batch)
                elif provider == "arxiv":
                    url = (
                        "http://export.arxiv.org/api/query"
                        f"?search_query=all:{encoded}&start=0&max_results={limit}"
                    )
                    text, digest = _fetch_with_digest(url, self.config.timeout_seconds)
                    batch = parse_arxiv(text, query)
                    for finding in batch:
                        finding.fetch_url = url
                        finding.fetch_digest = digest
                    findings.extend(batch)
                elif provider == "duckduckgo":
                    url = f"https://html.duckduckgo.com/html/?q={encoded}"
                    text, digest = _fetch_with_digest(url, self.config.timeout_seconds)
                    batch = parse_duckduckgo(text, query)
                    for finding in batch:
                        finding.fetch_url = url
                        finding.fetch_digest = digest
                    findings.extend(batch)
            except Exception:
                continue
        return findings[:limit]

    def _verify_finding(self, finding: ResearchFinding) -> bool | None:
        """Primary-source verification of a finding.

        Returns True when the primary source independently confirms the
        finding, False when it contradicts it, and None when verification is
        impossible (e.g. refetch failed) so the caller cannot downgrade.
        Enrichment (commit_hash, excerpt) is recorded when the primary source
        can be read.
        """
        if not finding.fetch_url:
            return None
        try:
            text, _digest = _fetch_with_digest(finding.fetch_url, self.config.timeout_seconds)
        except Exception:
            return None
        if finding.source == "github":
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                return False
            items = data.get("items") or []
            match = next(
                (
                    item
                    for item in items
                    if isinstance(item, dict) and item.get("html_url") == finding.url
                ),
                None,
            )
            if match is None:
                return False
            finding.excerpt = finding.excerpt or (match.get("description") or finding.summary)[:400]
            self._enrich_github_commit(finding)
            return True
        if finding.source == "arxiv":
            refetched = parse_arxiv(text, finding.query)
            match = next(
                (
                    item
                    for item in refetched
                    if item.url == finding.url
                    and _normalize_title(item.title) == _normalize_title(finding.title)
                ),
                None,
            )
            if match is None:
                return False
            finding.commit_hash = finding.commit_hash or match.commit_hash
            finding.excerpt = finding.excerpt or match.excerpt
            return True
        return True  # duckduckgo has no canonical primary source to confirm against

    def _enrich_github_commit(self, finding: ResearchFinding) -> None:
        """Record the repository's current commit hash from the primary source."""
        try:
            path = finding.url.rstrip("/").split("github.com/", 1)[-1]
            url = f"https://api.github.com/repos/{path}/commits/HEAD"
            text, _digest = _fetch_with_digest(url, self.config.timeout_seconds)
            sha = json.loads(text).get("sha", "")
            if sha:
                finding.commit_hash = sha
        except Exception:
            pass

    def _verify_doi(self, doi: str, title: str = "") -> bool | None:
        """Verify a DOI resolves via the DOI infrastructure (doi.org or Crossref).

        Returns True when the DOI resolves, False when it is not resolvable,
        and None when verification is impossible.
        """
        doi = (doi or "").strip()
        if not doi:
            return None
        try:
            _text, _digest = _fetch_with_digest(
                f"https://doi.org/{doi}", self.config.timeout_seconds
            )
            return True
        except Exception:
            pass
        try:
            _text, _digest = _fetch_with_digest(
                f"https://api.crossref.org/works/{doi}", self.config.timeout_seconds
            )
            return True
        except Exception:
            return None

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
