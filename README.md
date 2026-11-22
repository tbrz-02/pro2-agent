# Pro2 Agent

A local-first implementation of scientific problem probing and autonomous AI R&D,
inspired by the research paper Pro2 Agent.

## Development

Python 3.12+
Environment and dependency management: uv
Tests: pytest
Linting and formatting: Ruff

## Commands

- `just test` — run tests
- `just lint` — lint source, tests, and scripts
- `just check` — run lint and tests
- `just format` — format code

## Design principles

- Evidence before belief
- Hypothesis-driven experiments
- Isolated experiment execution
- Git-backed reproducibility
- Budget-aware autonomy
- Tests before implementation
