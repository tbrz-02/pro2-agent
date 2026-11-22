set shell := ["bash", "-euo", "pipefail", "-c"]

default:
    @just --list

init:
    mkdir -p paper/extracted paper/figures docs state experiments src/pro2 tests scripts logs

status:
    @git status --short
    @tree -L 3

paper-check:
    @test -f paper/original.pdf && sha256sum paper/original.pdf || echo "Paper missing: paper/original.pdf"

test:
    @uv run pytest -q

lint:
    @uv run ruff check src tests scripts

format:
    @uv run ruff format src tests scripts

check:
    @just lint
    @just test

security:
    @semgrep scan --config auto src

watch:
    @watchexec -e py --restart -- uv run pytest -q

git-log:
    @git log --oneline --decorate -15

clean-cache:
    @rm -rf .pytest_cache .ruff_cache
