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


# ---------------------------------------------------------------------------
# The published-release gate (P0.2)
#
# D-196: `0.7.0` was authorized `Ready`, tagged and published, and no
# published-release row was ever written. `0.5.0` had the same gap. Both were
# repaired by hand after an audit swept the tags; nothing detected either at the
# time, and nothing would have detected the third.
#
# The cases below are organised by what could make this gate useless: it could
# read a readiness target or a `Blocked` row as an authorization, it could stop
# looking at published rows at all, or its outstanding marker could become a
# permanent suppression switch rather than a dated statement of an open
# obligation.
# ---------------------------------------------------------------------------

RELEASE = check_ledgers.RELEASE_LEDGER

_PUBLISHED_HEADER = (
    "# Release Ledger\n\n"
    "| Version | Date | Tag / commit | Artifacts | Validation | Security | Notes |\n"
    "|---|---|---|---|---|---|---|\n"
)
_AUTHORIZATION_HEADER = (
    "\n## Release authorization\n\n"
    "| Version | Date | Validated source commit | Full gate evidence | Status |\n"
    "|---|---|---|---|---|\n"
)


def _release_ledger(
    published: tuple[str, ...] = (),
    authorizations: tuple[tuple[str, str], ...] = (),
    markers: tuple[str, ...] = (),
    readiness: tuple[str, ...] = (),
) -> str:
    body = _PUBLISHED_HEADER
    for version in published:
        body += f"| {version} | 2026-08-13 | `v{version}` | wheel | run | verified | Published. |\n"
    body += _AUTHORIZATION_HEADER
    for version, status in authorizations:
        body += f"| {version} | 2026-08-13 | `abc1234` | Clean `make check`. | {status} |\n"
    body += "\n"
    for version in markers:
        body += (
            f"> **Outstanding publication — {version}:** the tag is not cut yet; this closes when "
            "the published-release row lands.\n\n"
        )
    if readiness:
        body += (
            "## Unreleased readiness\n\n"
            "| Target | Date | Source state | Completed validation | Outstanding release gates "
            "| Status |\n|---|---|---|---|---|---|\n"
        )
        for version in readiness:
            body += f"| {version} | 2026-08-13 | branch | some | some | Not a release. |\n"
    return body


def _run_published(root: Path, monkeypatch: pytest.MonkeyPatch, body: str) -> list[str]:
    monkeypatch.setattr(check_ledgers, "ROOT", root)
    _write(root, RELEASE, body)
    failures: list[str] = []
    check_ledgers._check_published_rows(failures)
    return failures


def test_a_ready_authorization_with_its_published_row_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = _release_ledger(published=("0.8.0",), authorizations=(("0.8.0", "Ready"),))

    assert _run_published(tmp_path, monkeypatch, body) == []


def test_a_ready_authorization_with_no_published_row_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D-196 mechanised: this is the exact shape of the 0.7.0 gap."""

    body = _release_ledger(published=(), authorizations=(("0.8.0", "Ready"),))

    failures = _run_published(tmp_path, monkeypatch, body)

    assert len(failures) == 1
    assert "authorizes 0.8.0 as `Ready` with no published-release row" in failures[0]
    assert "D-196" in failures[0]


def test_a_ready_authorization_for_a_version_nobody_published_fails_beside_the_real_ones(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A `Ready` row invented for a version that does not exist is exactly as loud."""

    body = _release_ledger(
        published=("0.7.0", "0.8.0"),
        authorizations=(("0.7.0", "Ready"), ("0.8.0", "Ready"), ("0.9.0", "Ready")),
    )

    failures = _run_published(tmp_path, monkeypatch, body)

    assert len(failures) == 1
    assert "authorizes 0.9.0 as `Ready`" in failures[0]


def test_an_outstanding_marker_excuses_a_ready_row_with_no_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = _release_ledger(authorizations=(("0.9.0", "Ready"),), markers=("0.9.0",))

    assert _run_published(tmp_path, monkeypatch, body) == []


def test_an_outstanding_marker_naming_an_unauthorized_version_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The hatch cannot be opened before there is anything to excuse."""

    body = _release_ledger(
        published=("0.8.0",), authorizations=(("0.8.0", "Ready"),), markers=("0.9.0",)
    )

    failures = _run_published(tmp_path, monkeypatch, body)

    assert len(failures) == 1
    assert "no `Ready` authorization row exists for it" in failures[0]


def test_an_outstanding_marker_left_behind_after_publication_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """And it cannot be left open after the fact, which is how an exemption rots."""

    body = _release_ledger(
        published=("0.8.0",), authorizations=(("0.8.0", "Ready"),), markers=("0.8.0",)
    )

    failures = _run_published(tmp_path, monkeypatch, body)

    assert len(failures) == 1
    assert "still marks 0.8.0 as an outstanding publication" in failures[0]


def test_a_blocked_or_superseded_authorization_carries_no_publication_obligation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`Blocked` and `Superseded` authorize nothing, so they oblige nothing."""

    body = _release_ledger(
        published=("0.8.0",),
        authorizations=(("0.8.0", "Blocked"), ("0.8.0", "Ready"), ("0.9.0", "Superseded")),
    )

    assert _run_published(tmp_path, monkeypatch, body) == []


def test_a_published_row_without_an_authorization_row_is_not_a_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.1.0 predates the authorization discipline; the rule runs one way only."""

    body = _release_ledger(published=("0.1.0", "0.8.0"), authorizations=(("0.8.0", "Ready"),))

    assert _run_published(tmp_path, monkeypatch, body) == []


def test_a_readiness_target_is_not_read_as_a_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """All three tables start a row with a version, so the gate has to be section-aware.

    Without that, the unreleased-readiness row for 0.9.0 -- which says in its own
    words that it is not a release -- would satisfy the obligation created by the
    0.9.0 `Ready` row.
    """

    body = _release_ledger(
        published=("0.8.0",),
        authorizations=(("0.8.0", "Ready"), ("0.9.0", "Ready")),
        readiness=("0.9.0",),
    )

    failures = _run_published(tmp_path, monkeypatch, body)

    assert len(failures) == 1
    assert "authorizes 0.9.0 as `Ready`" in failures[0]


def test_a_release_ledger_with_no_ready_row_at_all_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An absence is evidence only if the observation could have reported a presence.

    A gate that reads a `Ready`-less ledger as clean would pass just as loudly if
    the authorization table were deleted, renamed, or reformatted past its own
    regular expression -- so the empty reading is a failure rather than a pass.
    """

    body = _release_ledger(published=("0.8.0",), authorizations=(("0.8.0", "Blocked"),))

    failures = _run_published(tmp_path, monkeypatch, body)

    assert failures == [f"{RELEASE} records no `Ready` release authorization"]


def test_the_committed_release_ledger_publishes_every_version_it_authorizes() -> None:
    """The real ledger, at this commit, with no fixture in the way."""

    failures: list[str] = []
    check_ledgers._check_published_rows(failures)

    assert failures == []

    rows = check_ledgers._read_release_ledger(
        (check_ledgers.ROOT / RELEASE).read_text(encoding="utf-8")
    )
    expected_ready = {
        "0.2.0",
        "0.3.0",
        "0.4.0",
        "0.5.0",
        "0.6.0",
        "0.7.0",
        "0.8.0",
        "0.9.0",
    }

    assert set(rows.ready) == expected_ready
    assert set(rows.ready) - set(rows.published) == set(rows.outstanding)


def test_main_runs_the_published_release_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    """A gate that is not called is not a gate.

    The three other checks here were wired into `main` when they were written and
    nothing pins that they still are, which is the same class as the two
    `make lint` checkers that had never run in CI. This asserts the call, so
    removing it fails a test rather than quietly returning the repository to the
    state D-196 describes.
    """

    monkeypatch.setattr(check_ledgers, "REPLAY_SUB_ENTRIES", REAL_REPLAYS)
    monkeypatch.setattr(check_ledgers, "RECORDED_COLLISIONS", REAL_COLLISIONS)
    called: list[int] = []

    def _spy(failures: list[str]) -> None:
        called.append(len(failures))

    monkeypatch.setattr(check_ledgers, "_check_published_rows", _spy)

    assert check_ledgers.main() == 0
    assert called == [0]
