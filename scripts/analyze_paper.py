#!/usr/bin/env python3

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "paper/extracted/full_text.txt"
OUT = ROOT / "paper/extracted"

if not SOURCE.exists():
    raise SystemExit(f"Paper text missing: {SOURCE}")

text = SOURCE.read_text(errors="replace")
pages = text.split("\f")

# Remove empty pages while retaining page numbering
pages = [(i + 1, p.strip()) for i, p in enumerate(pages) if p.strip()]

keywords = {
    "Algorithms": r"\balgorithm\b|\bpseudocode\b",
    "Agent architecture": r"\bagent\b|\bagents\b|\barchitecture\b",
    "Hypotheses": r"\bhypothes",
    "Experiments": r"\bexperiment\b|\bexperimental\b",
    "Evaluation": r"\bevaluation\b|\bevaluate\b|\bbenchmark\b",
    "Optimization": r"\boptim",
    "Search": r"\bsearch\b|\bexploration\b",
    "Planning": r"\bplanning\b|\bplan\b",
    "Memory and knowledge": r"\bmemory\b|\bknowledge\b|\bexperience\b",
    "State": r"\bstate\b|\bbelief\b",
    "Reward": r"\breward\b|\butility\b",
    "Metrics": r"\bmetric\b|\bmetrics\b|\bscore\b",
    "Implementation": r"\bimplementation\b|\bimplement\b",
    "Limitations": r"\blimitation\b|\blimitations\b",
}

# Basic heading detection
heading_patterns = [
    re.compile(r"^\s*(\d+(?:\.\d+)*\.?)\s+[A-Z][^\n]{2,120}$"),
    re.compile(r"^\s*[A-Z][A-Z\s]{2,80}$"),
]

headings = []

for page_num, page in pages:
    for line in page.splitlines():
        line = line.strip()
        if not line:
            continue

        if any(pattern.match(line) for pattern in heading_patterns):
            headings.append((page_num, line))

# Generate page keyword index
report = []

report.append("# Paper Analysis Index")
report.append("")
report.append(f"Source: {SOURCE.name}")
report.append(f"Total extracted pages: {len(pages)}")
report.append(f"Total characters: {len(text):,}")
report.append("")

report.append("## Detected headings")
report.append("")

for page_num, heading in headings:
    report.append(f"- Page {page_num}: {heading}")

report.append("")
report.append("## Keyword locations")
report.append("")

for label, pattern in keywords.items():
    regex = re.compile(pattern, re.IGNORECASE)
    matches = []

    for page_num, page in pages:
        if regex.search(page):
            matches.append(page_num)

    report.append(f"### {label}")
    report.append(f"Pages: {', '.join(map(str, matches)) if matches else 'None'}")
    report.append("")

# Save index
index_path = OUT / "paper_index.md"
index_path.write_text("\n".join(report))

# Create keyword-context snippets
snippets = []

for label, pattern in keywords.items():
    regex = re.compile(pattern, re.IGNORECASE)

    snippets.append(f"\n{'=' * 70}\n{label}\n{'=' * 70}\n")

    for page_num, page in pages:
        lines = page.splitlines()

        for i, line in enumerate(lines):
            if regex.search(line):
                start = max(0, i - 2)
                end = min(len(lines), i + 3)

                snippets.append(f"\n[Page {page_num}]\n" + "\n".join(lines[start:end]) + "\n")

snippets_path = OUT / "keyword_context.txt"
snippets_path.write_text("\n".join(snippets))

print("Paper analysis completed.")
print(f"Pages: {len(pages)}")
print(f"Characters: {len(text):,}")
print(f"Index: {index_path}")
print(f"Keyword contexts: {snippets_path}")
