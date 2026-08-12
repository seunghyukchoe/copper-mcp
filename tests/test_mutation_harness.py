"""The committed mutation harness: loud anchors, purged bytecode, two-direction kills.

Two of these tests are the reason the harness exists at all.

``test_a_byte_count_preserving_edit_is_invisible_to_stale_bytecode_until_purged`` reproduces the
defect ADR-0098 records: CPython's default bytecode invalidation keys on ``(mtime, size)``, so an
edit that preserves a file's byte count and lands in the same filesystem second -- reconstructed
here exactly, with ``os.utime`` -- runs the *old* code from ``__pycache__``. In a mutation harness
that is a mutant silently failing to apply or un-apply, and a stale mutant poisoning the next run
is a false kill. The test then shows ``purge_bytecode_caches`` is sufficient to defeat it.

``test_committed_mutant_specs_stay_anchored_to_current_source`` is the standing gate: every spec
committed under ``docs/mutants/`` must keep anchoring exactly once against today's source, so a
claim whose mutants have gone stale fails the build instead of quietly becoming unreproducible.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.mutation_harness import (
    OUTCOME_CONTROL_FAILED,
    OUTCOME_INVALID_SYNTAX,
    OUTCOME_KILLED,
    OUTCOME_STALE_ANCHOR,
    OUTCOME_SURVIVED,
    OUTCOME_SURVIVED_DECLARED_EQUIVALENT,
    SpecError,
    anchor_occurrences,
    build_report,
    load_spec,
    purge_bytecode_caches,
    run_mutant,
    run_spec,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
MUTANT_SPEC_DIR = REPO_ROOT / "docs" / "mutants"

WIDGET_SOURCE = "def is_wide(value: int) -> bool:\n    return value >= 10\n"
BOUNDARY_TEST = (
    "from widget import is_wide\n"
    "\n"
    "\n"
    "def test_boundary() -> None:\n"
    "    assert is_wide(10)\n"
    "    assert not is_wide(9)\n"
)
OFF_BOUNDARY_TEST = (
    "from widget import is_wide\n"
    "\n"
    "\n"
    "def test_far_from_boundary() -> None:\n"
    "    assert is_wide(20)\n"
    "    assert not is_wide(1)\n"
)


def _write_project(root: Path, test_body: str) -> None:
    (root / "src").mkdir()
    (root / "src" / "widget.py").write_text(WIDGET_SOURCE, encoding="utf-8")
    (root / "tests").mkdir()
    (root / "tests" / "test_widget.py").write_text(test_body, encoding="utf-8")


def _write_spec(root: Path, mutants: list[dict[str, object]]) -> Path:
    spec_path = root / "spec.json"
    spec_path.write_text(
        json.dumps({"harness": "mutation-harness/1", "pytest_args": ["-q"], "mutants": mutants}),
        encoding="utf-8",
    )
    return spec_path


def _boundary_mutant(**overrides: object) -> dict[str, object]:
    mutant: dict[str, object] = {
        "id": "M1",
        "file": "src/widget.py",
        "anchor": "return value >= 10",
        "replacement": "return value >= 11",
        "expectation": "killed",
        "killing_tests": ["tests/test_widget.py::test_boundary"],
    }
    mutant.update(overrides)
    return mutant


class TestSpecValidation:
    """A spec that cannot be trusted is refused before anything runs."""

    def test_a_valid_spec_loads(self, tmp_path: Path) -> None:
        spec = load_spec(_write_spec(tmp_path, [_boundary_mutant()]))
        assert len(spec.mutants) == 1
        assert spec.mutants[0].expectation == "killed"
        assert spec.pytest_args == ("-q",)
        assert len(spec.sha256) == 64

    @pytest.mark.parametrize(
        ("mutants", "complaint"),
        [
            ([], "non-empty mutants"),
            ([_boundary_mutant(replacement="return value >= 10")], "changes nothing"),
            ([_boundary_mutant(killing_tests=[])], "at least one test"),
            ([_boundary_mutant(expectation="equivalent")], "must argue it"),
            (
                [_boundary_mutant(equivalence_argument="but it expects a kill")],
                "expects a kill",
            ),
            ([_boundary_mutant(), _boundary_mutant()], "reuses id"),
            ([_boundary_mutant(expectation="maybe")], "expectation must be"),
            ([_boundary_mutant(anchor="")], "non-empty anchor"),
        ],
    )
    def test_an_untrustworthy_spec_is_refused(
        self, tmp_path: Path, mutants: list[dict[str, object]], complaint: str
    ) -> None:
        with pytest.raises(SpecError, match=complaint):
            load_spec(_write_spec(tmp_path, mutants))

    def test_a_spec_not_declaring_the_format_is_refused(self, tmp_path: Path) -> None:
        path = tmp_path / "spec.json"
        path.write_text(json.dumps({"mutants": [_boundary_mutant()]}), encoding="utf-8")
        with pytest.raises(SpecError, match="mutation-harness/1"):
            load_spec(path)


class TestAnchors:
    """A mutant that does not apply exactly once is a loud failure, never a kill."""

    def test_an_absent_anchor_is_stale(self, tmp_path: Path) -> None:
        _write_project(tmp_path, BOUNDARY_TEST)
        spec = load_spec(_write_spec(tmp_path, [_boundary_mutant(anchor="return value >= 999")]))
        result = run_mutant(tmp_path, spec, spec.mutants[0])
        assert result.outcome == OUTCOME_STALE_ANCHOR
        assert "matches 0 times" in result.detail

    def test_an_ambiguous_anchor_is_stale(self, tmp_path: Path) -> None:
        _write_project(tmp_path, BOUNDARY_TEST)
        widget = tmp_path / "src" / "widget.py"
        widget.write_text(
            WIDGET_SOURCE + "\n\ndef also_wide(value: int) -> bool:\n    return value >= 10\n",
            encoding="utf-8",
        )
        spec = load_spec(_write_spec(tmp_path, [_boundary_mutant()]))
        result = run_mutant(tmp_path, spec, spec.mutants[0])
        assert result.outcome == OUTCOME_STALE_ANCHOR
        assert "matches 2 times" in result.detail

    def test_a_missing_file_is_stale(self, tmp_path: Path) -> None:
        _write_project(tmp_path, BOUNDARY_TEST)
        spec = load_spec(_write_spec(tmp_path, [_boundary_mutant(file="src/absent.py")]))
        result = run_mutant(tmp_path, spec, spec.mutants[0])
        assert result.outcome == OUTCOME_STALE_ANCHOR

    def test_a_stillborn_mutant_is_refused_without_touching_the_file(self, tmp_path: Path) -> None:
        _write_project(tmp_path, BOUNDARY_TEST)
        widget = tmp_path / "src" / "widget.py"
        before = widget.read_bytes()
        spec = load_spec(
            _write_spec(tmp_path, [_boundary_mutant(replacement="return value >= ((10")])
        )
        result = run_mutant(tmp_path, spec, spec.mutants[0])
        assert result.outcome == OUTCOME_INVALID_SYNTAX
        assert widget.read_bytes() == before

    def test_anchor_occurrences_counts_exact_substrings(self) -> None:
        assert anchor_occurrences("aa aa", "aa") == 2
        assert anchor_occurrences("aa aa", "aaa") == 0


class TestVerdicts:
    """A kill is proved in both directions, and every verdict restores the source."""

    def test_a_boundary_mutant_is_killed_and_the_source_restored(self, tmp_path: Path) -> None:
        _write_project(tmp_path, BOUNDARY_TEST)
        widget = tmp_path / "src" / "widget.py"
        before = widget.read_bytes()
        spec = load_spec(_write_spec(tmp_path, [_boundary_mutant()]))
        results, passed = run_spec(tmp_path, spec)
        assert passed is True
        assert results[0].outcome == OUTCOME_KILLED
        assert results[0].mutant_returncode != 0
        assert results[0].control_returncode == 0
        assert results[0].caches_purged >= 0
        assert widget.read_bytes() == before
        report = build_report(tmp_path, spec, results, passed)
        assert report["passed"] is True
        assert report["summary"] == {OUTCOME_KILLED: 1}
        mutant_row = report["mutants"][0]  # type: ignore[index]
        assert mutant_row["killing_tests"] == ["tests/test_widget.py::test_boundary"]
        assert mutant_row["original_sha256"] != mutant_row["mutated_sha256"]

    def test_a_surviving_mutant_fails_the_run(self, tmp_path: Path) -> None:
        _write_project(tmp_path, OFF_BOUNDARY_TEST)
        spec = load_spec(
            _write_spec(
                tmp_path,
                [_boundary_mutant(killing_tests=["tests/test_widget.py::test_far_from_boundary"])],
            )
        )
        results, passed = run_spec(tmp_path, spec)
        assert passed is False
        assert results[0].outcome == OUTCOME_SURVIVED

    def test_a_declared_equivalent_mutant_must_actually_survive(self, tmp_path: Path) -> None:
        _write_project(tmp_path, BOUNDARY_TEST)
        spec = load_spec(
            _write_spec(
                tmp_path,
                [
                    _boundary_mutant(
                        replacement="return 10 <= value",
                        expectation="equivalent",
                        equivalence_argument="`10 <= value` and `value >= 10` are the same "
                        "predicate over int",
                    )
                ],
            )
        )
        results, passed = run_spec(tmp_path, spec)
        assert passed is True
        assert results[0].outcome == OUTCOME_SURVIVED_DECLARED_EQUIVALENT

    def test_a_false_equivalence_declaration_is_exposed(self, tmp_path: Path) -> None:
        _write_project(tmp_path, BOUNDARY_TEST)
        spec = load_spec(
            _write_spec(
                tmp_path,
                [
                    _boundary_mutant(
                        expectation="equivalent",
                        equivalence_argument="wrongly claims the boundary does not matter",
                    )
                ],
            )
        )
        results, passed = run_spec(tmp_path, spec)
        assert passed is False
        assert results[0].outcome == OUTCOME_KILLED

    def test_a_broken_control_supports_no_verdict(self, tmp_path: Path) -> None:
        broken_test = (
            "from widget import is_wide\n\n\ndef test_boundary() -> None:\n    assert is_wide(9)\n"
        )
        _write_project(tmp_path, broken_test)
        spec = load_spec(_write_spec(tmp_path, [_boundary_mutant()]))
        results, passed = run_spec(tmp_path, spec)
        assert passed is False
        assert results[0].outcome == OUTCOME_CONTROL_FAILED


class TestBytecodeStaleness:
    """The defect itself, reconstructed deterministically, and the purge that defeats it."""

    @staticmethod
    def _import_value(root: Path) -> str:
        environment = dict(os.environ)
        environment["PYTHONPATH"] = "."
        environment.pop("PYTHONDONTWRITEBYTECODE", None)
        # S603 is judged differently by the repo's ruff (0.16, trusts this literal argv)
        # and pre-commit's pinned ruff (0.12, flags every subprocess call); RUF100 keeps
        # the newer one from calling the suppression the older one requires unused.
        completed = subprocess.run(  # noqa: S603, RUF100
            [sys.executable, "-c", "import mod; print(mod.VALUE)"],
            cwd=root,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=True,
        )
        return completed.stdout.decode("utf-8").strip()

    def test_a_byte_count_preserving_edit_is_invisible_to_stale_bytecode_until_purged(
        self, tmp_path: Path
    ) -> None:
        module = tmp_path / "mod.py"
        module.write_text("VALUE = 1\n", encoding="utf-8")
        assert self._import_value(tmp_path) == "1"
        assert (tmp_path / "__pycache__").is_dir()

        # Reconstruct the same-second, same-byte-count edit exactly: rewrite the file with equal
        # length and force the original timestamps back, which is what a fast harness loop does
        # implicitly when two writes land within one filesystem second.
        stat = module.stat()
        module.write_text("VALUE = 2\n", encoding="utf-8")
        os.utime(module, ns=(stat.st_atime_ns, stat.st_mtime_ns))

        # The stale cache wins: the interpreter still reports the old value.
        assert self._import_value(tmp_path) == "1"

        # The purge is sufficient: with the cache gone, the edit is visible.
        assert purge_bytecode_caches(tmp_path) == 1
        assert self._import_value(tmp_path) == "2"

    def test_purge_skips_foreign_directories(self, tmp_path: Path) -> None:
        (tmp_path / ".venv" / "__pycache__").mkdir(parents=True)
        (tmp_path / ".git" / "__pycache__").mkdir(parents=True)
        (tmp_path / "pkg" / "__pycache__").mkdir(parents=True)
        assert purge_bytecode_caches(tmp_path) == 1
        assert (tmp_path / ".venv" / "__pycache__").is_dir()
        assert (tmp_path / ".git" / "__pycache__").is_dir()
        assert not (tmp_path / "pkg" / "__pycache__").exists()


class TestCommittedSpecs:
    """Committed mutation claims must stay reproducible against today's source."""

    def test_committed_mutant_specs_stay_anchored_to_current_source(self) -> None:
        spec_paths = sorted(MUTANT_SPEC_DIR.glob("*.json"))
        assert spec_paths, "docs/mutants/ must carry at least one committed spec"
        for spec_path in spec_paths:
            spec = load_spec(spec_path)
            for mutant in spec.mutants:
                target = REPO_ROOT / mutant.file
                assert target.is_file(), f"{spec_path.name}:{mutant.mutant_id} names {mutant.file}"
                occurrences = anchor_occurrences(target.read_text(encoding="utf-8"), mutant.anchor)
                assert occurrences == 1, (
                    f"{spec_path.name}:{mutant.mutant_id} anchors {occurrences} times in "
                    f"{mutant.file}; a committed mutant must keep matching exactly once, "
                    "so re-anchor it (and re-run the spec) rather than letting the claim rot"
                )
