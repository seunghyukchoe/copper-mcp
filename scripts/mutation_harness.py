#!/usr/bin/env python3
"""Apply hand-written mutants from a committed spec, and fail loudly when one does not apply.

Every mutation claim in this repository before ADR-0098 was produced by a harness that lived in
an agent's scratch directory and was deleted with it. Nobody -- including this project -- could
re-run a single one of those claims from the repository. Worse, the scratch harnesses shared a
defect: a mutant that changes a file *without changing its byte count*, applied or restored within
the same filesystem second as the previous write, is invisible to CPython's default
``(mtime, size)`` bytecode-invalidation check, so a stale ``__pycache__`` entry silently runs the
wrong code. A stale *mutant* poisoning the next invocation produces a false kill, which makes
"0 survivors" the optimistic side of the error. ADR-0098 records the audit.

This harness is the committed replacement. Its rules:

1. **An anchor must match exactly once.** A mutant is written as an exact source substring
   (``anchor``) and its replacement. Zero matches means the mutant has gone stale against the
   edited source; two means it is ambiguous. Both are ``stale_anchor``, reported loudly and
   failing the run -- never counted as killed, and never skipped in silence.
2. **Bytecode caches are purged around every application and every restoration**, and every test
   subprocess runs with ``PYTHONDONTWRITEBYTECODE=1``. Belt and braces: the purge removes any
   cache a previous tool left behind; the environment variable stops this harness from creating
   the next one.
3. **A kill is proved in both directions.** With the mutant applied, the named killing tests must
   fail; with the source restored (verified byte-identical), the same tests must pass. A kill
   whose control run fails is ``control_failed`` -- the failure was not caused by the mutant --
   and fails the whole run.
4. **A mutant that cannot compile tests nothing** and is refused as ``invalid_syntax`` rather
   than counted as a kill-by-import-error.
5. **A declared-equivalent mutant must say why** (``equivalence_argument``) and must actually
   survive its tests; the harness records it as ``survived_declared_equivalent`` and never as a
   kill.
6. **No mutant is applied until the unmutated killing tests pass**, and **only pytest exit 1
   counts as a kill**. The first harness run on PR #154 reported 11/11 killed while executing
   zero tests, because a mistyped test path made pytest exit 4 (usage error) every time and any
   non-zero exit was read as a kill. The general defect is the class, not the mechanism: a kill
   is only evidence if the run that produced it was capable of reporting a survivor. So the
   baseline must be green before anything mutates, and a mutant run that exits with anything
   but 0 or 1 is ``invalid_run``, never ``killed``.

The spec format and the rule for what a mutation claim must state to be auditable live in
``docs/mutants/README.md``. Outcomes are a closed vocabulary; a mutant the harness never reached
is reported as ``not_run``, never omitted.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SPEC_FORMAT = "mutation-harness/1"

# The closed outcome vocabulary. `not_run` is the one-value literal for a mutant the harness
# never reached (an earlier hard failure aborted the run); it is always reported, never omitted.
OUTCOME_KILLED = "killed"
OUTCOME_SURVIVED = "survived"
OUTCOME_SURVIVED_DECLARED_EQUIVALENT = "survived_declared_equivalent"
OUTCOME_STALE_ANCHOR = "stale_anchor"
OUTCOME_INVALID_SYNTAX = "invalid_syntax"
OUTCOME_CONTROL_FAILED = "control_failed"
OUTCOME_INVALID_RUN = "invalid_run"
OUTCOME_NOT_RUN = "not_run"

EXPECTATION_KILLED = "killed"
EXPECTATION_EQUIVALENT = "equivalent"

# Directories never scanned for `__pycache__`: not project code, or not this checkout's code.
_PURGE_SKIP_DIRS = frozenset({".git", ".venv", ".claude", "node_modules"})


class SpecError(ValueError):
    """A mutant spec that cannot be trusted enough to run."""


@dataclass(frozen=True)
class Mutant:
    """One hand-written mutant: an exact anchor, its replacement, and the expected verdict."""

    mutant_id: str
    file: str
    anchor: str
    replacement: str
    expectation: str
    killing_tests: tuple[str, ...]
    equivalence_argument: str | None


@dataclass(frozen=True)
class HarnessSpec:
    """A committed mutation spec: the harness's whole input."""

    path: Path
    sha256: str
    pytest_args: tuple[str, ...]
    mutants: tuple[Mutant, ...]


@dataclass
class MutantResult:
    """What actually happened to one mutant, with enough detail to audit the run."""

    mutant_id: str
    file: str
    outcome: str
    detail: str
    original_sha256: str | None = None
    mutated_sha256: str | None = None
    mutant_returncode: int | None = None
    control_returncode: int | None = None
    caches_purged: int = 0
    duration_ms: int = 0


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise SpecError(message)


def load_spec(path: Path) -> HarnessSpec:
    """Parse and validate a spec file, refusing anything structurally ambiguous."""
    raw_bytes = path.read_bytes()
    document = json.loads(raw_bytes)
    _require(isinstance(document, dict), "spec root must be a JSON object")
    _require(
        document.get("harness") == SPEC_FORMAT,
        f"spec must declare harness={SPEC_FORMAT!r}",
    )
    pytest_args = document.get("pytest_args", [])
    _require(
        isinstance(pytest_args, list) and all(isinstance(item, str) for item in pytest_args),
        "pytest_args must be a list of strings",
    )
    raw_mutants = document.get("mutants")
    _require(
        isinstance(raw_mutants, list) and len(raw_mutants) > 0,
        "spec must carry a non-empty mutants list",
    )
    mutants: list[Mutant] = []
    seen_ids: set[str] = set()
    for index, entry in enumerate(raw_mutants):
        where = f"mutants[{index}]"
        _require(isinstance(entry, dict), f"{where} must be an object")
        mutant_id = entry.get("id")
        _require(
            isinstance(mutant_id, str) and mutant_id != "",
            f"{where} needs a non-empty string id",
        )
        _require(mutant_id not in seen_ids, f"{where} reuses id {mutant_id!r}")
        seen_ids.add(mutant_id)
        file_name = entry.get("file")
        _require(
            isinstance(file_name, str) and file_name != "",
            f"{where} needs a non-empty file path",
        )
        anchor = entry.get("anchor")
        _require(isinstance(anchor, str) and anchor != "", f"{where} needs a non-empty anchor")
        replacement = entry.get("replacement")
        _require(isinstance(replacement, str), f"{where} needs a string replacement")
        _require(
            replacement != anchor,
            f"{where} replacement equals its anchor; that mutant changes nothing",
        )
        expectation = entry.get("expectation")
        _require(
            expectation in (EXPECTATION_KILLED, EXPECTATION_EQUIVALENT),
            f"{where} expectation must be {EXPECTATION_KILLED!r} or {EXPECTATION_EQUIVALENT!r}",
        )
        killing_tests = entry.get("killing_tests", [])
        _require(
            isinstance(killing_tests, list)
            and all(isinstance(item, str) and item != "" for item in killing_tests),
            f"{where} killing_tests must be a list of non-empty strings",
        )
        _require(
            len(killing_tests) > 0,
            f"{where} must name at least one test to run; a mutation claim without a "
            "mutant-to-test mapping is not auditable",
        )
        equivalence_argument = entry.get("equivalence_argument")
        if expectation == EXPECTATION_EQUIVALENT:
            _require(
                isinstance(equivalence_argument, str) and equivalence_argument != "",
                f"{where} declares equivalence and must argue it in equivalence_argument",
            )
        else:
            _require(
                equivalence_argument is None,
                f"{where} carries an equivalence_argument but expects a kill",
            )
        mutants.append(
            Mutant(
                mutant_id=mutant_id,
                file=file_name,
                anchor=anchor,
                replacement=replacement,
                expectation=str(expectation),
                killing_tests=tuple(killing_tests),
                equivalence_argument=(
                    equivalence_argument if isinstance(equivalence_argument, str) else None
                ),
            )
        )
    return HarnessSpec(
        path=path,
        sha256=hashlib.sha256(raw_bytes).hexdigest(),
        pytest_args=tuple(pytest_args),
        mutants=tuple(mutants),
    )


def purge_bytecode_caches(root: Path) -> int:
    """Delete every ``__pycache__`` directory under ``root`` and return how many were removed.

    This is the direct countermeasure to the stale-``.pyc`` defect: after a purge there is no
    cached bytecode whose ``(mtime, size)`` key could collide with a byte-count-preserving,
    same-second source edit.
    """
    purged = 0
    for directory, subdirectories, _files in os.walk(root):
        subdirectories[:] = [name for name in subdirectories if name not in _PURGE_SKIP_DIRS]
        if "__pycache__" in subdirectories:
            shutil.rmtree(Path(directory) / "__pycache__")
            subdirectories.remove("__pycache__")
            purged += 1
    return purged


def anchor_occurrences(source: str, anchor: str) -> int:
    """How many times the anchor appears in the source, exactly."""
    return source.count(anchor)


def _run_tests(
    root: Path, pytest_args: tuple[str, ...], tests: tuple[str, ...]
) -> subprocess.CompletedProcess[bytes]:
    environment = dict(os.environ)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHONPATH"] = f"src{os.pathsep}."
    argv = [sys.executable, "-m", "pytest", *pytest_args, *tests]
    return subprocess.run(  # noqa: S603 - fixed interpreter, argv from the committed spec
        argv,
        cwd=root,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )


def run_mutant(root: Path, spec: HarnessSpec, mutant: Mutant) -> MutantResult:
    """Apply one mutant, prove its verdict in both directions, and always restore the source."""
    started = time.monotonic_ns()
    result = MutantResult(
        mutant_id=mutant.mutant_id, file=mutant.file, outcome=OUTCOME_NOT_RUN, detail=""
    )
    target = root / mutant.file
    if not target.is_file():
        result.outcome = OUTCOME_STALE_ANCHOR
        result.detail = f"target file {mutant.file} does not exist"
        return result
    original_bytes = target.read_bytes()
    result.original_sha256 = hashlib.sha256(original_bytes).hexdigest()
    source = original_bytes.decode("utf-8")
    occurrences = anchor_occurrences(source, mutant.anchor)
    if occurrences != 1:
        result.outcome = OUTCOME_STALE_ANCHOR
        result.detail = (
            f"anchor matches {occurrences} times in {mutant.file}; it must match exactly once. "
            "A mutant that silently stops applying is a passing check that tests nothing -- "
            "re-anchor it against the current source."
        )
        return result
    mutated_source = source.replace(mutant.anchor, mutant.replacement, 1)
    if mutant.file.endswith(".py"):
        try:
            compile(mutated_source, mutant.file, "exec")
        except SyntaxError as error:
            result.outcome = OUTCOME_INVALID_SYNTAX
            result.detail = (
                f"mutant does not compile ({error.msg} at line {error.lineno}); "
                "a stillborn mutant tests nothing"
            )
            return result
    mutated_bytes = mutated_source.encode("utf-8")
    result.mutated_sha256 = hashlib.sha256(mutated_bytes).hexdigest()
    try:
        result.caches_purged += purge_bytecode_caches(root)
        target.write_bytes(mutated_bytes)
        if target.read_bytes() == original_bytes:
            raise RuntimeError(f"{mutant.file} is byte-identical after applying {mutant.mutant_id}")
        mutant_run = _run_tests(root, spec.pytest_args, mutant.killing_tests)
        result.mutant_returncode = mutant_run.returncode
    finally:
        target.write_bytes(original_bytes)
        result.caches_purged += purge_bytecode_caches(root)
    restored = target.read_bytes()
    if restored != original_bytes:
        raise RuntimeError(
            f"{mutant.file} did not restore byte-identically after {mutant.mutant_id}"
        )
    control_run = _run_tests(root, spec.pytest_args, mutant.killing_tests)
    result.control_returncode = control_run.returncode
    if control_run.returncode != 0:
        result.outcome = OUTCOME_CONTROL_FAILED
        result.detail = (
            "the named tests fail on the restored source, so no verdict about the mutant is "
            "supported; the environment or the tests are broken"
        )
    elif result.mutant_returncode not in (0, 1):
        # pytest speaks in exit codes: 0 all passed, 1 tests failed, and everything else --
        # 2 interrupted, 3 internal error, 4 usage/collection error, 5 nothing collected --
        # means the tests never truly ran. A kill is only evidence if the run that produced
        # it was capable of reporting a survivor, and these runs were not.
        result.outcome = OUTCOME_INVALID_RUN
        result.detail = (
            f"pytest exited {result.mutant_returncode} with the mutant applied, which is not "
            "a verdict: only exit 1 (tests genuinely failed) counts as a kill. If the mutant "
            "broke collection itself, choose a killing test that exercises the behaviour "
            "instead of one that dies importing it"
        )
    elif mutant.expectation == EXPECTATION_EQUIVALENT:
        if result.mutant_returncode == 0:
            result.outcome = OUTCOME_SURVIVED_DECLARED_EQUIVALENT
            result.detail = mutant.equivalence_argument or ""
        else:
            result.outcome = OUTCOME_KILLED
            result.detail = (
                "declared equivalent, but the named tests killed it; the declaration is wrong"
            )
    elif result.mutant_returncode == 1:
        result.outcome = OUTCOME_KILLED
        result.detail = "the named tests fail with the mutant applied and pass without it"
    else:
        result.outcome = OUTCOME_SURVIVED
        result.detail = (
            "the named tests pass with the mutant applied; the claim's mapping is wrong "
            "or the coverage gap is real"
        )
    result.duration_ms = (time.monotonic_ns() - started) // 1_000_000
    return result


def _matches_expectation(mutant: Mutant, result: MutantResult) -> bool:
    if mutant.expectation == EXPECTATION_KILLED:
        return result.outcome == OUTCOME_KILLED
    return result.outcome == OUTCOME_SURVIVED_DECLARED_EQUIVALENT


def baseline_tests(spec: HarnessSpec) -> tuple[str, ...]:
    """The union of every mutant's killing tests, in first-appearance order."""
    seen: dict[str, None] = {}
    for mutant in spec.mutants:
        for test in mutant.killing_tests:
            seen.setdefault(test)
    return tuple(seen)


def run_spec(root: Path, spec: HarnessSpec) -> tuple[list[MutantResult], bool, int]:
    """Run every mutant. Returns the results, whether the run met its expectations, and the
    baseline exit code.

    **No mutant is applied until the unmutated tests pass.** A harness whose baseline is not
    green measures nothing, whatever the mutants appear to say: the first run of PR #154's
    scratch harness reported 11/11 killed while executing zero tests, because a mistyped test
    file made pytest exit 4 every time and exit 4 read as "killed". If the baseline fails,
    every mutant is reported ``not_run`` and the run fails.

    A hard failure aborts the run — continuing to mutate files after, say, a failed restore
    would be reckless — but it aborts *into the report*, not past it: the mutant that raised is
    recorded as ``not_run`` carrying the error, and every mutant after it is recorded as
    ``not_run`` too. No mutant is ever omitted from the results.
    """
    purge_bytecode_caches(root)
    baseline = _run_tests(root, spec.pytest_args, baseline_tests(spec))
    if baseline.returncode != 0:
        print(
            f"BASELINE FAILED (pytest exit {baseline.returncode}): the unmutated tests do not "
            "pass, so this harness can measure nothing. No mutant was applied.",
            file=sys.stderr,
        )
        return (
            [
                MutantResult(
                    mutant_id=mutant.mutant_id,
                    file=mutant.file,
                    outcome=OUTCOME_NOT_RUN,
                    detail=(
                        f"baseline failed with pytest exit {baseline.returncode} before any "
                        "mutant was applied; a green baseline is the precondition for every "
                        "verdict"
                    ),
                )
                for mutant in spec.mutants
            ],
            False,
            baseline.returncode,
        )
    results: list[MutantResult] = []
    aborted: str | None = None
    for mutant in spec.mutants:
        if aborted is not None:
            results.append(
                MutantResult(
                    mutant_id=mutant.mutant_id,
                    file=mutant.file,
                    outcome=OUTCOME_NOT_RUN,
                    detail=f"not reached: the run aborted at {aborted}",
                )
            )
            continue
        try:
            result = run_mutant(root, spec, mutant)
        except Exception as error:  # broad on purpose: the report must survive any failure
            aborted = mutant.mutant_id
            result = MutantResult(
                mutant_id=mutant.mutant_id,
                file=mutant.file,
                outcome=OUTCOME_NOT_RUN,
                detail=(
                    f"hard failure, no verdict: {type(error).__name__}: {error}; "
                    "the run aborted here and every later mutant is not_run"
                ),
            )
        results.append(result)
        marker = "ok" if _matches_expectation(mutant, result) else "FAIL"
        print(f"[{marker}] {mutant.mutant_id} ({mutant.file}): {result.outcome} -- {result.detail}")
    passed = all(
        _matches_expectation(mutant, result)
        for mutant, result in zip(spec.mutants, results, strict=True)
    )
    return results, passed, baseline.returncode


def build_report(
    root: Path,
    spec: HarnessSpec,
    results: list[MutantResult],
    passed: bool,
    baseline_returncode: int,
) -> dict[str, object]:
    """A machine-checkable record of the run, suitable for committing beside a claim."""
    outcomes = [result.outcome for result in results]
    return {
        "harness": SPEC_FORMAT,
        "spec_path": str(spec.path),
        "spec_sha256": spec.sha256,
        "root": str(root),
        "pytest_args": list(spec.pytest_args),
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "baseline_returncode": baseline_returncode,
        "passed": passed,
        "summary": {outcome: outcomes.count(outcome) for outcome in sorted(set(outcomes))},
        "mutants": [
            {
                "id": result.mutant_id,
                "file": result.file,
                "expectation": mutant.expectation,
                "killing_tests": list(mutant.killing_tests),
                "outcome": result.outcome,
                "detail": result.detail,
                "original_sha256": result.original_sha256,
                "mutated_sha256": result.mutated_sha256,
                "mutant_returncode": result.mutant_returncode,
                "control_returncode": result.control_returncode,
                "caches_purged": result.caches_purged,
                "duration_ms": result.duration_ms,
            }
            for mutant, result in zip(spec.mutants, results, strict=True)
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("spec", type=Path, help="path to a mutation-harness/1 JSON spec")
    parser.add_argument(
        "--root",
        type=Path,
        default=ROOT,
        help="project root the spec's file paths and tests resolve against (default: this repo)",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="write the machine-checkable JSON report here",
    )
    arguments = parser.parse_args(argv)
    try:
        spec = load_spec(arguments.spec)
    except (OSError, json.JSONDecodeError, SpecError) as error:
        print(f"spec refused: {error}", file=sys.stderr)
        return 2
    results, passed, baseline_returncode = run_spec(arguments.root.resolve(), spec)
    report = build_report(arguments.root.resolve(), spec, results, passed, baseline_returncode)
    if arguments.report is not None:
        arguments.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], indent=2))
    if not passed:
        print(
            "mutation run FAILED: at least one mutant did not meet its expectation", file=sys.stderr
        )
        return 1
    print(f"mutation run passed: {len(results)} mutants, every one matched its expectation")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
