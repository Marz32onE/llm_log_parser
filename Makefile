.PHONY: install format lint typecheck test check

# Prefer uv on PATH; fall back to the default install location.
UV := $(shell command -v uv 2>/dev/null || echo "$(HOME)/.local/bin/uv")

install:
	@command -v uv >/dev/null 2>&1 || test -x "$(HOME)/.local/bin/uv" || \
		(curl -LsSf https://astral.sh/uv/install.sh | sh)
	$(UV) venv --python 3.10 .venv
	$(UV) pip install -e ".[dev]"

format:
	.venv/bin/isort src tests
	.venv/bin/black src tests

lint:
	.venv/bin/isort --check-only --diff src tests
	.venv/bin/black --check --diff src tests
	.venv/bin/flake8 src tests
	.venv/bin/pylint src/llmlogs

typecheck:
	.venv/bin/mypy

test:
	.venv/bin/pytest --cov=llmlogs --cov-report=term-missing

check: format lint typecheck test
