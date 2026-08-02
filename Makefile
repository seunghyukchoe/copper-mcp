PYTHON ?= python3

.PHONY: install install-dev test lint format typecheck security build check clean

install:
	$(PYTHON) -m pip install -e .

install-dev:
	$(PYTHON) -m pip install -e ".[dev,security]"

test:
	PYTHONPATH=src $(PYTHON) -m pytest

lint:
	$(PYTHON) -m ruff check .
	$(PYTHON) scripts/check_version.py
	$(PYTHON) scripts/check_ledgers.py

format:
	$(PYTHON) -m ruff format .
	$(PYTHON) -m ruff check --fix .

typecheck:
	$(PYTHON) -m mypy src

security:
	$(PYTHON) scripts/check_secrets.py
	$(PYTHON) -m pip_audit

build:
	$(PYTHON) -m build

check: lint typecheck test security build

clean:
	$(PYTHON) -c "import shutil; [shutil.rmtree(p, ignore_errors=True) for p in ['build', 'dist', '.coverage', 'htmlcov', '.mypy_cache', '.pytest_cache', '.ruff_cache']]"
