.PHONY: install format lint typecheck test check

install:
	uv venv --python 3.10 .venv
	uv pip install -e ".[dev]"

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
