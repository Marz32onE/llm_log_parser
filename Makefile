.PHONY: install format lint typecheck test check

install:
	uv venv --python 3.10 .venv
	uv pip install -e ".[dev]"

format:
	.venv/bin/ruff format src tests

lint:
	.venv/bin/ruff check src tests

typecheck:
	.venv/bin/mypy

test:
	.venv/bin/pytest --cov=logcmp --cov-report=term-missing

check: format lint typecheck test
