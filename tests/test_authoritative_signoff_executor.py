"""Tests for the coordinator-owned bounded sign-off executor.

No fake backend is installed anywhere in this file, by design. ADR-0118 refused caller-selected
authority and ADR-0119 kept that refusal, so a test that monkeypatched a passing DRC runner into
this module would be exercising the one path the design exists to make impossible. What is
covered instead is everything the executor decides *about* evidence -- agreement, suppression,
bounds, stop checks and redaction -- over synthetic `RouteCandidateDrcEvidence`, plus a real
end-to-end run that is skipped when `kicad-cli` is absent.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from copper_mcp.authoritative_signoff_executor import (
    DEFAULT_REPETITIONS,
    EXECUTED_DOMAIN,
    AuthoritativeSignoffExecutorError,
    _agreement,
    _stopped,
    _suppressed,
    execute_dfm_signoff,
)
from copper_mcp.config import Settings
from copper_mcp.kicad_cli import RouteCandidateDrcEvidence
from copper_mcp.models import DrcSummary
from copper_mcp.routing.authoritative_signoff import (
    SignoffCode,
    SignoffComparability,
    SignoffDomain,
    SignoffStatus,
    SurrogateAdvisory,
)
from tests.test_kicad_candidate_drc import _workspace_board

_DISCOVERED_KICAD_CLI = shutil.which("kicad-cli")


def _digest(fill: str) -> str:
    return f"sha256:{fill * 64}"


def _summary(
    *,
    board: str = "1",
    context: str = "2",
    error_count: int = 0,
    warning_count: int = 0,
    exclusion_count: int = 0,
    ignored_check_count: int = 0,
    unconnected_count: int = 0,
) -> DrcSummary:
    findings = error_count + warning_count + exclusion_count + unconnected_count
    return DrcSummary(
        base_revision=_digest(board),
        drc_context_revision=_digest(context),
        kicad_version="9.0.0",
        drc_schema="https://schemas.kicad.org/drc.v1.json",
        coordinate_units="mm",
        error_count=error_count,
        warning_count=warning_count,
        exclusion_count=exclusion_count,
        ignored_check_count=ignored_check_count,
        unconnected_count=unconnected_count,
        violation_type_counts={"clearance": findings} if findings else {},
        passed=error_count == 0 and unconnected_count == 0,
    )


def _observation(summary: DrcSummary | None = None) -> RouteCandidateDrcEvidence:
    resolved = summary if summary is not None else _summary()
    return RouteCandidateDrcEvidence(
        candidate_id=_digest("a"),
        candidate_base_revision=_digest("b"),
        source_revision=_digest("c"),
        patched_board_revision=resolved.base_revision,
        patched_drc_context_revision=resolved.drc_context_revision,
        summary=resolved,
    )


def test_the_executor_speaks_for_one_domain_and_needs_more_than_one_invocation() -> None:
    assert EXECUTED_DOMAIN is SignoffDomain.DFM
    assert DEFAULT_REPETITIONS == 2


@pytest.mark.parametrize("repetitions", [-1, 0, 1, 9, 1_000])
def test_a_repetition_count_that_cannot_disagree_is_a_caller_defect(repetitions: int) -> None:
    with pytest.raises(AuthoritativeSignoffExecutorError, match="invocations"):
        execute_dfm_signoff(
            "board.kicad_pcb", object(), object(), object(), repetitions=repetitions
        )


@pytest.mark.parametrize("repetitions", [True, 2.0, "2", None])
def test_a_non_integer_repetition_count_is_refused_before_anything_runs(
    repetitions: object,
) -> None:
    with pytest.raises(AuthoritativeSignoffExecutorError):
        execute_dfm_signoff(
            "board.kicad_pcb",
            object(),
            object(),
            object(),
            repetitions=repetitions,  # type: ignore[arg-type]
        )


def test_a_candidate_that_is_not_an_immutable_route_candidate_never_reaches_the_backend() -> None:
    with pytest.raises(AuthoritativeSignoffExecutorError, match="immutable route candidate"):
        execute_dfm_signoff("board.kicad_pcb", {"paths": []}, object(), object())


def test_identical_invocations_earn_repeated_agreement() -> None:
    observations = [_observation(), _observation()]

    assert _agreement(observations) is SignoffComparability.REPEATED_AGREEMENT


def test_one_invocation_earns_only_an_observation() -> None:
    assert _agreement([_observation()]) is SignoffComparability.SINGLE_INVOCATION


def test_counts_that_moved_between_invocations_earn_disagreement() -> None:
    # B-107's measured failure mode: byte-identical inputs, a count that moved anyway.
    observations = [_observation(), _observation(_summary(warning_count=1))]

    assert _agreement(observations) is SignoffComparability.REPEATED_DISAGREEMENT


def test_a_patched_revision_that_moved_cannot_hide_behind_a_matching_summary() -> None:
    # The summaries agree on every count; the statements do not agree on what they are about.
    first = _observation()
    second = RouteCandidateDrcEvidence(
        candidate_id=first.candidate_id,
        candidate_base_revision=first.candidate_base_revision,
        source_revision=_digest("d"),
        patched_board_revision=first.patched_board_revision,
        patched_drc_context_revision=first.patched_drc_context_revision,
        summary=first.summary,
    )

    assert first.summary.to_dict() == second.summary.to_dict()
    assert _agreement([first, second]) is SignoffComparability.REPEATED_DISAGREEMENT


@pytest.mark.parametrize(
    ("summary", "expected"),
    [
        (_summary(), False),
        (_summary(warning_count=3), False),
        (_summary(ignored_check_count=1), True),
        (_summary(exclusion_count=2), True),
    ],
)
def test_suppression_is_read_from_skipped_and_excluded_checks(
    summary: DrcSummary, expected: bool
) -> None:
    assert _suppressed(_observation(summary)) is expected


def test_stop_checks_are_cooperative_and_a_misbehaving_check_stops() -> None:
    def explodes() -> bool:
        raise RuntimeError("secret backend detail")

    assert _stopped(None, None) is None
    assert _stopped(lambda: False, lambda: False) is None
    assert _stopped(lambda: True, None) is SignoffCode.CANCELLED
    assert _stopped(None, lambda: True) is SignoffCode.DEADLINE_EXCEEDED
    assert _stopped(explodes, None) is SignoffCode.CANCELLED
    assert _stopped(None, lambda: "yes") is SignoffCode.DEADLINE_EXCEEDED


def test_a_cancelled_run_refuses_before_the_first_invocation(tmp_path: Path) -> None:
    board, _source, profile, candidate = _workspace_board(tmp_path)
    settings = Settings(workspace=tmp_path, max_drc_report_bytes=4096)
    calls = 0

    def cancelled() -> bool:
        nonlocal calls
        calls += 1
        return True

    result = execute_dfm_signoff(board.name, candidate, profile, settings, cancelled=cancelled)

    assert result.code is SignoffCode.CANCELLED
    assert result.claimed is False
    assert result.candidate_id is None
    # One check, and no KiCad process: the stop is observed before the first invocation, not
    # after a run whose result would then have to be thrown away.
    assert calls == 1


def test_a_deadline_is_checked_before_every_invocation_and_stops_the_sequence(
    tmp_path: Path,
) -> None:
    board, _source, profile, candidate = _workspace_board(tmp_path)
    settings = Settings(
        workspace=tmp_path,
        kicad_cli=tmp_path / "absent-kicad-cli",
        max_drc_report_bytes=4096,
    )
    checks = 0

    def expired() -> bool:
        nonlocal checks
        checks += 1
        return True

    result = execute_dfm_signoff(
        board.name, candidate, profile, settings, repetitions=8, deadline=expired
    )

    assert result.code is SignoffCode.DEADLINE_EXCEEDED
    assert result.status is SignoffStatus.REFUSED
    # Eight invocations were asked for and none was started: the check gates the loop rather
    # than reporting on it afterwards.
    assert checks == 1


def test_a_backend_that_cannot_run_is_a_redacted_refusal_not_an_exception(
    tmp_path: Path,
) -> None:
    board, _source, profile, candidate = _workspace_board(tmp_path)
    settings = Settings(
        workspace=tmp_path,
        kicad_cli=tmp_path / "absent-kicad-cli",
        max_drc_report_bytes=4096,
    )

    # The configured executable does not exist, so the runner cannot execute. That is a
    # statement about this machine and never about the board, so it must not surface as a claim,
    # as an exception, or as the backend's own message.
    result = execute_dfm_signoff(board.name, candidate, profile, settings)

    assert result.status is SignoffStatus.REFUSED
    assert result.code is SignoffCode.BACKEND_FAILURE
    assert result.to_dict() == {
        "schema": "copper-mcp/authoritative-signoff/v1",
        "status": "refused",
        "domain": "dfm",
        "advisory_present": False,
        "code": "backend_failure",
        "diagnostic": "authoritative sign-off could not be completed",
    }


@pytest.mark.skipif(_DISCOVERED_KICAD_CLI is None, reason="kicad-cli is not installed")
def test_real_kicad_drc_exercises_the_supported_signoff_path_end_to_end(tmp_path: Path) -> None:
    """End to end against the real authority, on the committed two-pad fixture.

    This is the only test in the suite that can produce `SIGNED_OFF`, and it produces it by
    running KiCad twice and getting the same answer both times. It asserts the shape of whichever
    verdict the authority gives rather than demanding a pass, because demanding one would make
    the test a claim about the fixture instead of about the executor.
    """

    board, _source, profile, candidate = _workspace_board(tmp_path)
    settings = Settings(workspace=tmp_path, kicad_cli=Path(_DISCOVERED_KICAD_CLI or ""))

    result = execute_dfm_signoff(board.name, candidate, profile, settings, repetitions=2)

    assert result.domain is SignoffDomain.DFM
    assert result.claimed is (result.code is None)
    if result.claimed:
        assert result.comparability is SignoffComparability.REPEATED_AGREEMENT
        assert result.repetitions == 2
        assert result.candidate_id == candidate.candidate_id
        assert result.base_revision == candidate.base_revision
    else:
        assert result.code in {
            SignoffCode.FAILED_EVIDENCE,
            SignoffCode.SUPPRESSED_EVIDENCE,
            SignoffCode.UNCOMPARABLE_EVIDENCE,
            SignoffCode.BACKEND_FAILURE,
        }


def test_the_signoff_vocabulary_is_adr_0109s_vocabulary_unchanged() -> None:
    """The literals must stay identical to the ones ADR-0109 defined.

    The executor re-derives the literal rather than importing `comparability_of`, because
    production code may not import the benchmark package. A test may, so the equivalence that the
    re-derivation rests on is checked here instead of asserted in a comment.

    Order is not compared: the benchmark tuple is ordered weakest-to-strongest because `weakest`
    reads it as a ranking, and the enum has no such reader.
    """

    from copper_mcp.benchmarks.drc_comparability import COMPARABILITY_LITERALS

    assert {literal.value for literal in SignoffComparability} == set(COMPARABILITY_LITERALS)


def test_the_statement_carries_nothing_adr_0109_would_have_had_to_strip() -> None:
    """Byte-comparing statements is only sound if they hold no incomparable field.

    ADR-0052 put no wall clock, path or run label in the Statement, which is what lets agreement
    be read off the bytes. If that ever changes, two identical runs would stop agreeing and this
    catches it here rather than as a mystery refusal.
    """

    from copper_mcp.benchmarks.drc_comparability import INCOMPARABLE_KEYS

    statement = _observation().canonical_statement_bytes().decode("utf-8")

    for incomparable in INCOMPARABLE_KEYS:
        assert f'"{incomparable}"' not in statement


def test_a_malformed_advisory_is_graded_before_a_drc_run_is_spent_on_it(tmp_path: Path) -> None:
    board, _source, profile, candidate = _workspace_board(tmp_path)
    settings = Settings(
        workspace=tmp_path,
        kicad_cli=tmp_path / "absent-kicad-cli",
        max_drc_report_bytes=4096,
    )

    result = execute_dfm_signoff(
        board.name, candidate, profile, settings, surrogate={"rank": 1, "prompt": "secret"}
    )

    # Not `backend_failure`, which is what a run that was attempted would have produced here.
    assert result.code is SignoffCode.INVALID_ADVISORY
    assert result.to_dict()["advisory_present"] is False
    assert "secret" not in json.dumps(result.to_dict())


def test_a_well_formed_advisory_is_recorded_and_still_cannot_sign_off(tmp_path: Path) -> None:
    board, _source, profile, candidate = _workspace_board(tmp_path)
    settings = Settings(
        workspace=tmp_path,
        kicad_cli=tmp_path / "absent-kicad-cli",
        max_drc_report_bytes=4096,
    )

    result = execute_dfm_signoff(
        board.name,
        candidate,
        profile,
        settings,
        surrogate=SurrogateAdvisory(rank=1, score_milli=990),
    )

    assert result.code is SignoffCode.BACKEND_FAILURE
    assert result.to_dict()["advisory_present"] is True
    assert result.claimed is False
