PYTHON ?= python3

.PHONY: install install-dev test test-fast test-full test-compat test-evidence lint format typecheck security build pcm check \
	check-audio-benchmarks check-circuit-intents benchmark-audio benchmark-routing \
	benchmark-external-corpus benchmark-cross-router evaluate-excessive-agency clean

install:
	$(PYTHON) -m pip install -e .

install-dev:
	$(PYTHON) -m pip install -e ".[dev,security]"

test:
	PYTHONPATH=src $(PYTHON) -m pytest

test-fast:
	PYTHONPATH=src $(PYTHON) -m pytest -n 4 --dist loadfile --no-cov -m "not slow_evidence and not external_router and not networked_provider"

test-full:
	PYTHONPATH=src $(PYTHON) -m pytest

TEST_MARKER ?= not slow_evidence
COVERAGE_ARGS ?= --no-cov
test-compat:
	PYTHONPATH=src $(PYTHON) -m pytest -n 4 --dist loadfile -m "$(TEST_MARKER)" $(COVERAGE_ARGS)

test-evidence:
	PYTHONPATH=src $(PYTHON) -m pytest --no-cov tests/test_benchmark_negotiated_multipin_branch_repair.py::test_published_artifact_matches_one_current_source_bound_b141_recomputation

lint:
	$(PYTHON) -m ruff check .
	$(PYTHON) scripts/check_version.py
	$(PYTHON) scripts/check_ledgers.py
	$(PYTHON) scripts/check_adr_numbers.py
	$(PYTHON) scripts/check_doc_links.py
	$(PYTHON) scripts/check_sdist_tracked.py
	$(PYTHON) scripts/check_schema_sets.py
	PYTHONPATH=src $(PYTHON) scripts/check_drc_comparability.py
	$(PYTHON) scripts/check_ci_budgets.py
	$(PYTHON) scripts/check_audio_benchmarks.py
	PYTHONPATH=src $(PYTHON) scripts/check_circuit_intents.py

format:
	$(PYTHON) -m ruff format .
	$(PYTHON) -m ruff check --fix .

typecheck:
	$(PYTHON) -m mypy src

security:
	$(PYTHON) scripts/check_secrets.py
	$(PYTHON) -m pip_audit .

build:
	$(PYTHON) -m build

# The KiCad Plugin and Content Manager archive, written beside the wheel and sdist. Reproducible:
# the same source always produces the same bytes, so re-running this is a no-op on the digest.
pcm:
	$(PYTHON) scripts/build_pcm_package.py

check: lint typecheck test security build

benchmark-routing:
	PYTHONPATH=src $(PYTHON) scripts/benchmark_routing.py --iterations 7 --warmups 2

# Routes the committed MIT-licensed SimpleRouteJson corpus. Offline: it reads only files already
# in the tree and verifies each against the digest manifest before routing.
benchmark-external-corpus:
	PYTHONPATH=src $(PYTHON) scripts/benchmark_simple_route_json_corpus.py --repetitions 2

# Records the cross-router comparison table M2's closing condition 2 asks for. Offline: it reads the
# same committed corpus as the target above and looks for baselines rather than fetching them.
benchmark-cross-router:
	PYTHONPATH=src $(PYTHON) scripts/benchmark_cross_router_comparison.py

check-audio-benchmarks:
	$(PYTHON) scripts/check_audio_benchmarks.py

check-circuit-intents:
	PYTHONPATH=src $(PYTHON) scripts/check_circuit_intents.py

benchmark-audio: check-audio-benchmarks
	PYTHONPATH=src $(PYTHON) scripts/run_audio_benchmarks.py

# Replays the predeclared excessive-agency suite against every project family. Offline: it copies
# committed boards into a temporary workspace and never touches the source tree. Exits non-zero on
# a scenario failure; drop the flag to record one in the artifact instead.
EXCESSIVE_AGENCY_ARTIFACT ?= benchmarks/results/security/2026-08-06-excessive-agency-evaluation.json
evaluate-excessive-agency:
	PYTHONPATH=src $(PYTHON) scripts/evaluate_excessive_agency.py \
		--evidence-harness-commit $$(git rev-parse HEAD) \
		--output $(EXCESSIVE_AGENCY_ARTIFACT) \
		--fail-on-scenario-failure

clean:
	$(PYTHON) -c "import shutil; [shutil.rmtree(p, ignore_errors=True) for p in ['build', 'dist', '.coverage', 'htmlcov', '.mypy_cache', '.pytest_cache', '.ruff_cache']]"
