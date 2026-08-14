from __future__ import annotations

from pathlib import Path

import pytest

from scripts import check_ledgers

DECISION = "docs/ledgers/decision-ledger.md"
BENCHMARK = "docs/ledgers/benchmark-ledger.md"
README = check_ledgers.LEDGER_README

TABLE_HEADER = "| ID | Date | Status | Decision | Record |\n|---|---|---|---|---|\n"

# Captured before the autouse fixture empties them, so the repository-wide case
# can put the real exception lists back.
REAL_REPLAYS = dict(check_ledgers.REPLAY_SUB_ENTRIES)
REAL_COLLISIONS = dict(check_ledgers.RECORDED_COLLISIONS)


@pytest.fixture(autouse=True)
def _empty_exception_lists(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start every case with no replay or collision exceptions unless it adds one."""
    monkeypatch.setattr(check_ledgers, "REPLAY_SUB_ENTRIES", {})
    monkeypatch.setattr(check_ledgers, "RECORDED_COLLISIONS", {})


def _write(root: Path, relative: str, body: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def _decisions(*identifiers: str) -> str:
    rows = "".join(
        f"| {identifier} | 2026-08-06 | Accepted | A decision. | none |\n"
        for identifier in identifiers
    )
    return "# Decision Ledger\n\n" + TABLE_HEADER + rows


def _run_ids(root: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[list[str], list[str]]:
    monkeypatch.setattr(check_ledgers, "ROOT", root)
    failures: list[str] = []
    notes: list[str] = []
    check_ledgers._check_ledger_ids(failures, notes)
    return failures, notes


def _artifact(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str) -> list[str]:
    results = tmp_path / "benchmarks" / "results"
    results.mkdir(parents=True)
    (results / "census.json").write_text(body, encoding="utf-8")
    monkeypatch.setattr(check_ledgers, "ROOT", tmp_path)
    failures: list[str] = []
    check_ledgers._check_benchmark_artifacts(failures)
    return failures


def test_a_benchmark_artifact_whose_run_id_does_not_match_its_content_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The digest is what makes a committed measurement checkable rather than merely present.

    Written because nothing pinned this rule: the run_id check could be weakened to accept any
    well-formed digest and the whole suite stayed green, which is the state a benchmark artifact
    must never be able to reach. An artifact edited after the run that produced it -- a count
    corrected by hand, a board's verdict softened -- is exactly what this catches.
    """

    import hashlib
    import json

    content = {"schema": "test/v1", "boards": 164, "converts": 0}
    canonical = json.dumps(content, sort_keys=True, separators=(",", ":"), allow_nan=False)
    honest = dict(content, run_id="sha256:" + hashlib.sha256(canonical.encode()).hexdigest())

    assert _artifact(tmp_path, monkeypatch, json.dumps(honest)) == []


def test_a_hand_edited_benchmark_artifact_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The non-vacuous half: the same artifact with one number changed must fail."""

    import hashlib
    import json

    content = {"schema": "test/v1", "boards": 164, "converts": 0}
    canonical = json.dumps(content, sort_keys=True, separators=(",", ":"), allow_nan=False)
    run_id = "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()
    # The digest is left as-is and the measurement is edited underneath it.
    tampered = dict(content, converts=12, run_id=run_id)

    failures = _artifact(tmp_path, monkeypatch, json.dumps(tampered))

    assert len(failures) == 1
    assert "run_id does not match its canonical report content" in failures[0]


def test_a_benchmark_artifact_with_no_run_id_at_all_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An absent digest must not read as a satisfied one."""

    failures = _artifact(tmp_path, monkeypatch, '{"schema":"test/v1","boards":164}')

    assert len(failures) == 1
    assert "run_id does not match its canonical report content" in failures[0]


@pytest.mark.parametrize("number", ["1e999", "-1e999"])
def test_benchmark_artifacts_reject_overflowing_json_numbers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    number: str,
) -> None:
    results = tmp_path / "benchmarks" / "results"
    results.mkdir(parents=True)
    (results / "overflow.json").write_text(
        '{"run_id":"sha256:' + ("0" * 64) + '","runtime_seconds":' + number + "}",
        encoding="utf-8",
    )
    monkeypatch.setattr(check_ledgers, "ROOT", tmp_path)

    failures: list[str] = []
    check_ledgers._check_benchmark_artifacts(failures)

    assert len(failures) == 1
    assert "is not strict JSON: non-finite JSON number" in failures[0]


def test_duplicate_decision_id_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write(tmp_path, DECISION, _decisions("D-001", "D-002", "D-002"))

    failures, _ = _run_ids(tmp_path, monkeypatch)

    assert len(failures) == 1
    assert "D-002 is already allocated" in failures[0]


def test_historical_gap_passes_as_information(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(tmp_path, DECISION, _decisions("D-001", "D-003", "D-004"))

    failures, notes = _run_ids(tmp_path, monkeypatch)

    assert failures == []
    assert any("D-002" in note for note in notes)


def test_out_of_order_row_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write(tmp_path, DECISION, _decisions("D-001", "D-003", "D-002"))

    failures, _ = _run_ids(tmp_path, monkeypatch)

    assert len(failures) == 1
    assert "D-002 is out of order" in failures[0]


def test_unpadded_identifier_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write(tmp_path, DECISION, _decisions("D-001", "D-0002"))

    failures, _ = _run_ids(tmp_path, monkeypatch)

    assert len(failures) == 1
    assert "is not zero-padded to three digits" in failures[0]


def _benchmarks(*headings: str) -> str:
    body = "# Benchmark Ledger\n\n"
    for heading in headings:
        body += f"{heading}\n\n| Field | Recorded evidence |\n|---|---|\n| Metrics | none |\n\n"
    return body


def test_allowlisted_replay_sub_entry_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    replay = "#### B-001 — deterministic replay"
    monkeypatch.setattr(
        check_ledgers, "REPLAY_SUB_ENTRIES", {(BENCHMARK, replay): "B-001 replayed"}
    )
    _write(tmp_path, BENCHMARK, _benchmarks("### B-001 — a benchmark", replay, "### B-002 — next"))

    failures, _ = _run_ids(tmp_path, monkeypatch)

    assert failures == []


def test_unallowlisted_benchmark_duplicate_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(
        tmp_path,
        BENCHMARK,
        _benchmarks("### B-001 — a benchmark", "#### B-001 — an unannounced repeat"),
    )

    failures, _ = _run_ids(tmp_path, monkeypatch)

    assert len(failures) == 1
    assert "B-001 is already allocated" in failures[0]


def test_allowlisted_replay_without_parent_entry_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Listing a heading is necessary but not sufficient: it must replay a `###` entry."""
    replay = "#### B-001 — deterministic replay"
    monkeypatch.setattr(
        check_ledgers, "REPLAY_SUB_ENTRIES", {(BENCHMARK, replay): "B-001 replayed"}
    )
    _write(tmp_path, BENCHMARK, _benchmarks("#### B-001 — a sub-entry", replay))

    failures, _ = _run_ids(tmp_path, monkeypatch)

    assert len(failures) == 1
    assert "has no `###` parent entry to replay" in failures[0]


def test_replay_promoted_to_top_level_entry_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    replay = "### B-001 — deterministic replay"
    monkeypatch.setattr(
        check_ledgers, "REPLAY_SUB_ENTRIES", {(BENCHMARK, replay): "B-001 replayed"}
    )
    _write(tmp_path, BENCHMARK, _benchmarks("### B-001 — a benchmark", replay))

    failures, _ = _run_ids(tmp_path, monkeypatch)

    assert len(failures) == 1
    assert "must be a `####` sub-entry" in failures[0]


def test_unused_replay_exception_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        check_ledgers,
        "REPLAY_SUB_ENTRIES",
        {(BENCHMARK, "#### B-009 — a replay that is not there"): "stale"},
    )
    _write(tmp_path, BENCHMARK, _benchmarks("### B-001 — a benchmark"))

    failures, _ = _run_ids(tmp_path, monkeypatch)

    assert len(failures) == 1
    assert "matched no repeated identifier; remove it" in failures[0]


def test_recorded_collision_passes_as_information(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        check_ledgers, "RECORDED_COLLISIONS", {(DECISION, "D-002"): "recorded by D-003"}
    )
    _write(tmp_path, DECISION, _decisions("D-001", "D-002", "D-002", "D-003"))

    failures, notes = _run_ids(tmp_path, monkeypatch)

    assert failures == []
    assert any("recorded historical collision" in note for note in notes)


def test_recorded_collision_also_covers_the_order_it_displaced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Merging two appended blocks leaves one behind the other; that is the same defect."""
    monkeypatch.setattr(
        check_ledgers, "RECORDED_COLLISIONS", {(DECISION, "D-002"): "recorded by D-004"}
    )
    _write(tmp_path, DECISION, _decisions("D-001", "D-002", "D-003", "D-002", "D-004"))

    failures, notes = _run_ids(tmp_path, monkeypatch)

    assert failures == []
    assert any("document order was displaced" in note for note in notes)


def test_recorded_collision_does_not_excuse_an_unrelated_out_of_order_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        check_ledgers, "RECORDED_COLLISIONS", {(DECISION, "D-002"): "recorded by D-005"}
    )
    _write(tmp_path, DECISION, _decisions("D-001", "D-002", "D-004", "D-003", "D-002", "D-005"))

    failures, _ = _run_ids(tmp_path, monkeypatch)

    assert len(failures) == 1
    assert "D-003 is out of order" in failures[0]


def test_recorded_collision_does_not_excuse_a_second_duplicate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        check_ledgers, "RECORDED_COLLISIONS", {(DECISION, "D-002"): "recorded by D-003"}
    )
    _write(tmp_path, DECISION, _decisions("D-001", "D-002", "D-002", "D-003", "D-003"))

    failures, _ = _run_ids(tmp_path, monkeypatch)

    assert len(failures) == 1
    assert "D-003 is already allocated" in failures[0]


def test_recorded_collision_excuses_only_one_duplicate_of_its_own_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A correction note records exactly one double allocation, so a third row fails."""
    monkeypatch.setattr(
        check_ledgers, "RECORDED_COLLISIONS", {(DECISION, "D-002"): "recorded by D-003"}
    )
    _write(tmp_path, DECISION, _decisions("D-001", "D-002", "D-002", "D-002", "D-003"))

    failures, _ = _run_ids(tmp_path, monkeypatch)

    assert len(failures) == 1
    assert "excuses exactly one duplicate" in failures[0]


def test_unused_collision_exception_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        check_ledgers, "RECORDED_COLLISIONS", {(DECISION, "D-009"): "recorded by nothing"}
    )
    _write(tmp_path, DECISION, _decisions("D-001", "D-002"))

    failures, _ = _run_ids(tmp_path, monkeypatch)

    assert len(failures) == 1
    assert "matched no duplicate identifier; remove it" in failures[0]


def _registry(highest: str, next_free: str) -> str:
    return (
        "# Project Ledgers\n\n"
        "| Ledger | Prefix | Highest allocated | Next free |\n|---|---|---|---|\n"
        f"| [Decision ledger](decision-ledger.md) | `D-` | `{highest}` | `{next_free}` |\n"
    )


def test_stale_allocation_registry_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(check_ledgers, "ROOT", tmp_path)
    _write(tmp_path, README, _registry("D-001", "D-002"))

    failures: list[str] = []
    check_ledgers._check_allocation_registry({"D": 2}, failures)

    assert len(failures) == 2
    assert "highest allocated D- identifier is D-001, but the ledger allocates D-002" in failures[0]
    assert "next free D- identifier is D-002, but it is D-003" in failures[1]


def test_undeclared_allocation_registry_row_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(check_ledgers, "ROOT", tmp_path)
    _write(tmp_path, README, _registry("D-002", "D-003"))

    failures: list[str] = []
    check_ledgers._check_allocation_registry({"D": 2, "B": 7}, failures)

    assert failures == [f"{README} does not declare the B- allocation state"]


def test_repository_ledgers_allocate_identifiers_cleanly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The committed tree must satisfy every rule above, exceptions included."""
    monkeypatch.setattr(check_ledgers, "REPLAY_SUB_ENTRIES", REAL_REPLAYS)
    monkeypatch.setattr(check_ledgers, "RECORDED_COLLISIONS", REAL_COLLISIONS)
    failures: list[str] = []
    notes: list[str] = []
    highest = check_ledgers._check_ledger_ids(failures, notes)
    check_ledgers._check_allocation_registry(highest, failures)

    assert failures == []
    assert set(highest) == {"D", "R", "SEC", "B"}
