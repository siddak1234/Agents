# realty-lead-gen — developer tasks
# All targets use `uv` for hermetic Python management. First-time setup: `make setup`.

.PHONY: help setup hooks fmt lint typecheck test clean

help:  ## show this help
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

# NOT `pre-commit install`. This folder shares one .git with the repository,
# and a git repo holds exactly one pre-commit hook — installing from here
# rewrites it to point at this folder's config, which silently disables every
# root gate (secret scan, lint, both integration checks) for all later
# commits. Proven, not hypothetical. The root config owns the installed hook;
# this folder's hooks run on demand via `make hooks`.
setup:  ## create venv and install all extras
	uv sync --all-extras

hooks:  ## run this folder's pre-commit hooks (does not touch the installed hook)
	uv run pre-commit run --all-files

fmt:  ## format code
	uv run ruff format src tests
	uv run ruff check --fix src tests

lint:  ## static checks (ruff + mypy)
	uv run ruff check src tests
	uv run ruff format --check src tests
	uv run mypy src

typecheck:  ## mypy only
	uv run mypy src

test:  ## run unit tests
	uv run pytest -m unit

# --- Housekeeping ------------------------------------------------------------
clean:  ## remove build artifacts and caches
	rm -rf .pytest_cache .mypy_cache .ruff_cache dist build
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
