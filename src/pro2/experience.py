"""Persistent Experience Bank: error signatures, causes, fixes and outcomes."""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pro2.util import atomic_write_text, read_jsonl, utc_now


def normalize_error(text: str) -> str:
    """Normalize volatile details so equivalent failures share a signature."""
    lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
    if not lines:
        return ""
    signature = lines[-1]
    signature = re.sub(r"0x[0-9a-fA-F]+", "ADDR", signature)
    signature = re.sub(r"(?:/[\w.@+-]+)+", "<path>", signature)
    signature = re.sub(r"\bline\s+\d+\b", "line N", signature)
    signature = re.sub(r"\b\d{4,}\b", "<num>", signature)
    signature = re.sub(r"\s+", " ", signature)
    return signature.strip()


@dataclass
class ExperienceEntry:
    entry_id: str
    kind: str
    signature: str
    context: str
    root_cause: str
    fix: str
    outcome: str
    tags: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    uses: int = 0

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExperienceEntry:
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in data.items() if k in known})


class ExperienceBank:
    def __init__(self, path: str | Path, max_entries: int = 500) -> None:
        self.path = Path(path)
        self.max_entries = max_entries
        self._entries: list[ExperienceEntry] = [
            ExperienceEntry.from_dict(record) for record in read_jsonl(self.path)
        ]
        self.prune()

    def all(self) -> list[ExperienceEntry]:
        return list(self._entries)

    def add(
        self,
        kind: str,
        signature: str,
        context: str,
        root_cause: str,
        fix: str,
        outcome: str,
        tags: Iterable[str] | None = None,
        evidence: Iterable[str] | None = None,
    ) -> ExperienceEntry:
        normalized = normalize_error(signature) or signature.strip() or kind
        for entry in self._entries:
            if entry.signature == normalized:
                entry.context = context or entry.context
                entry.root_cause = root_cause or entry.root_cause
                entry.fix = fix or entry.fix
                entry.outcome = outcome
                entry.tags = sorted(set(entry.tags) | set(tags or []))
                entry.evidence = list(entry.evidence) + list(evidence or [])
                entry.updated_at = utc_now()
                entry.uses += 1
                self._persist()
                return entry

        entry = ExperienceEntry(
            entry_id=f"X-{uuid.uuid4().hex[:10]}",
            kind=kind,
            signature=normalized,
            context=context,
            root_cause=root_cause,
            fix=fix,
            outcome=outcome,
            tags=sorted(set(tags or [])),
            evidence=list(evidence or []),
        )
        self._entries.append(entry)
        self.prune()
        self._persist()
        return entry

    def search(
        self,
        query: str,
        limit: int = 3,
        tags: Iterable[str] | None = None,
    ) -> list[ExperienceEntry]:
        tokens = {token for token in re.findall(r"[a-zA-Z0-9_.+-]{2,}", query.lower())}
        tag_filter = {tag.lower() for tag in (tags or [])}
        scored: list[tuple[float, ExperienceEntry]] = []
        for entry in self._entries:
            haystack = " ".join(
                [
                    entry.signature,
                    entry.context,
                    entry.root_cause,
                    entry.fix,
                    " ".join(entry.tags),
                ]
            ).lower()
            score = sum(1 for token in tokens if token in haystack)
            if tag_filter and not tag_filter.intersection({t.lower() for t in entry.tags}):
                continue
            if tokens and score == 0:
                continue
            score += min(entry.uses, 5) * 0.1
            scored.append((score, entry))

        scored.sort(key=lambda pair: (pair[0], pair[1].updated_at), reverse=True)
        return [entry for _, entry in scored[:limit]]

    def format_for_prompt(self, entries: Iterable[ExperienceEntry]) -> str:
        lines = []
        for entry in entries:
            lines.append(
                f"- [{entry.kind}] {entry.signature}\n"
                f"  context: {entry.context}\n"
                f"  root cause: {entry.root_cause}\n"
                f"  fix: {entry.fix}\n"
                f"  outcome: {entry.outcome}"
            )
        return "\n".join(lines)

    def prune(self) -> None:
        if self.max_entries >= 0 and len(self._entries) > self.max_entries:
            self._entries = self._entries[-self.max_entries :]

    def _persist(self) -> None:
        text = "".join(json.dumps(entry.to_dict(), default=str) + "\n" for entry in self._entries)
        atomic_write_text(self.path, text)
