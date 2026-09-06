"""Regression cover for the excessive-agency evaluation suite.

Two things are pinned here. The first is the *result*: the historical artifact stays immutable,
the current harness reproduces its outcomes, and the suite still reaches zero failures. The
second is the *discriminator*:
a suite that cannot fail is not evidence, so several tests deliberately break a boundary and
require the harness to record a failure rather than a pass.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest

from copper_mcp.apply import service as apply_service
from copper_mcp.apply.tokens import ApplyTokenAuthority
from copper_mcp.config import Settings
from scripts import evaluate_excessive_agency as evaluation

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "benchmarks" / "results" / "security"
ARTIFACT = RESULTS / "2026-08-06-excessive-agency-evaluation.json"
CATALOG = ROOT / "tests" / "fixtures" / "security" / "excessive-agency-v1" / "scenarios.json"
TEST_HARNESS_COMMIT = "a" * 40

EXPECTED_COUNTS = {
    "cases": 136,
    "passed": 90,
    "failed": 0,
    "not_run": 46,
    "scenarios": 34,
    "project_families": 4,
    "controls": 4,
    "controls_failed": 0,
}

#: A board that is in the repository and genuinely does not convert: the Board IR adapter refuses
#: it `syntax.invalid`. Used to exercise the `not_run` softening on a real refusal rather than on
#: a mocked one.
UNCONVERTIBLE_BOARD = "tests/fixtures/board-ir-v0.1/malformed-unbalanced.kicad_pcb"


@pytest.fixture(scope="module")
def report() -> dict[str, object]:
    return evaluation.build_report(evidence_harness_commit=TEST_HARNESS_COMMIT)


def _context(
    family: evaluation.ProjectFamily, workspace: Path, outside: Path | None = None
) -> evaluation.FamilyContext:
    settings = Settings(workspace=workspace)
    context = evaluation.FamilyContext(
        family=family,
        workspace=workspace,
        settings=settings,
        consenting=replace(settings, allow_apply=True),
        authority=ApplyTokenAuthority(),
        outside_board=outside,
    )
    for relative in family.boards:
        name = Path(relative).name
        status, payload = evaluation._call(
            context,
            "inspect_board_ir",
            {"request": {"board": name, "constraints": dict(evaluation.CONSTRAINTS)}},
            kind="authorized_disclosure",
        )
        assert status == "ok"
        context.revisions[name] = str(payload["board_revision"])
        context.snapshots[name] = str(payload["snapshot_digest"])
    context.sentinels = evaluation._build_sentinels(context)
    return context


def test_every_runnable_scenario_reaches_its_predeclared_outcome(report: dict[str, object]) -> None:
    assert report["schema"] == "copper-mcp/security-evaluation/excessive-agency/v1"
    assert report["counts"] == EXPECTED_COUNTS
    assert report["failures"] == []
    assert report["execution"] == {
        "network": "not_invoked",
        "model": "not_invoked",
        "kicad": "not_invoked",
        "transport": "in_process_mcp_adapter",
        "board_mutation": "temporary_workspace_copies_only",
    }


def test_every_declared_scenario_runs_against_every_project_family(
    report: dict[str, object],
) -> None:
    cases = report["cases"]
    assert isinstance(cases, list)
    declared = {identifier for _, identifier, _ in evaluation.SCENARIOS}
    assert len(declared) == 34
    for family in evaluation.PROJECT_FAMILIES:
        covered = {case["scenario"] for case in cases if case["project_family"] == family.id}
        assert covered == declared, family.id


def test_not_run_rows_carry_a_reason_from_the_declared_vocabulary(
    report: dict[str, object],
) -> None:
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    declared = set(catalog["not_run_reasons"])
    # Sentinel availability is decided by the board, so its reasons are generated per sentinel
    # kind rather than enumerated in the catalog.
    generated = {
        f"no_{kind}_sentinel_available"
        for kind in ("board_identity", "coordinate", "absolute_path")
    }
    for case in report["cases"]:
        if case["disposition"] == "not_run":
            assert case["observed"] in declared | generated, case


def test_held_out_families_are_declared_and_include_external_data(
    report: dict[str, object],
) -> None:
    families = {record["id"]: record for record in report["project_families"]}
    assert families["development-fixtures"]["held_out"] is False
    for identifier in ("coppertone-buffer", "heldout-audio", "tscircuit-benchmark"):
        assert families[identifier]["held_out"] is True
    # The external corpus is recorded even though no MCP tool accepts its format: a held-out
    # family that could not be reached is a result, not an omission.
    assert families["tscircuit-benchmark"]["accepted_format"] is False
    assert families["tscircuit-benchmark"]["boards"] == []
    assert "corpus_manifest_sha256" in families["tscircuit-benchmark"]
    per_family = report["per_project_family"]
    assert per_family["tscircuit-benchmark"]["not_run"] == 34
    assert per_family["tscircuit-benchmark"]["passed"] == 0


def test_the_authorized_path_is_reached_and_recorded_as_a_permit(
    report: dict[str, object],
) -> None:
    """The suite must observe the server *permitting*, not only refusing.

    An evaluation that only ever observes refusals cannot distinguish a server that refuses
    correctly from one that refuses everything, including refusing when it should not. This is
    the row that separates them, so it is pinned by project family and by observed value rather
    than folded into the aggregate count.
    """

    permits = {
        case["project_family"]: case
        for case in report["cases"]
        if case["scenario"] == evaluation.PERMIT_SCENARIO
    }
    for identifier in ("development-fixtures", "heldout-audio"):
        assert permits[identifier]["disposition"] == "pass", identifier
        assert permits[identifier]["observed"] == "applied", identifier
        assert permits[identifier]["surface"] == "apply_candidate", identifier
    # Held out, and reached: a permit shown only on the fixtures the boundary was built against
    # would be no better than the refusals it is meant to discriminate from.
    assert evaluation.CONTROL_FAMILY == "development-fixtures"
    assert permits["heldout-audio"]["project_family"] != evaluation.CONTROL_FAMILY
    # The board this family affords no capability for is a recorded non-claim, not a quiet pass.
    assert permits["coppertone-buffer"]["disposition"] == "not_run"
    assert permits["coppertone-buffer"]["observed"] == "no_apply_capability_available"

    touched = {
        case["project_family"]: case
        for case in report["cases"]
        if case["scenario"] == "authorized-apply-changes-only-the-authorized-board"
    }
    for identifier in ("development-fixtures", "heldout-audio"):
        assert touched[identifier]["disposition"] == "pass", identifier
        assert touched[identifier]["observed"] == "only_the_authorized_board_and_its_pre_apply_copy"


def test_every_declared_escape_route_is_attempted_and_confined(report: dict[str, object]) -> None:
    routes = {
        identifier
        for family, identifier, _ in evaluation.SCENARIOS
        if family == "workspace_containment"
    }
    assert len(routes) == 3
    for case in report["cases"]:
        if case["scenario"] not in routes:
            continue
        if case["project_family"] == "tscircuit-benchmark":
            assert case["disposition"] == "not_run"
            continue
        assert case["disposition"] == "pass", case
        assert case["observed"] == "invalid_request", case


def test_controls_are_predeclared_and_all_hold(report: dict[str, object]) -> None:
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    declared = {entry["id"] for entry in catalog["controls"]}
    observed = {row["control"] for row in report["controls"]}
    assert declared == observed
    assert report["control_failures"] == []
    for entry in catalog["controls"]:
        assert entry["requires"]


def test_report_states_what_it_does_not_prove(report: dict[str, object]) -> None:
    claim = report["claim"]
    assert claim["classification"] == "adversarial-boundary-evaluation"
    joined = " ".join(claim["does_not_prove"]).lower()
    for phrase in ("no model is invoked", "coverage, not absence", "in-process"):
        assert phrase in joined


def test_run_id_is_deterministic_and_self_digested() -> None:
    first = evaluation.build_report(evidence_harness_commit=TEST_HARNESS_COMMIT)
    second = evaluation.build_report(evidence_harness_commit=TEST_HARNESS_COMMIT)

    assert first == second
    canonical = dict(first)
    run_id = canonical.pop("run_id")
    serialized = json.dumps(
        canonical,
        allow_nan=False,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    assert run_id == "sha256:" + hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def test_evaluation_validates_evidence_commit_shape() -> None:
    with pytest.raises(evaluation.EvaluationError, match="40 lowercase"):
        evaluation.build_report(evidence_harness_commit="INVALID")


def test_report_discloses_no_board_content(report: dict[str, object]) -> None:
    serialized = json.dumps(report, ensure_ascii=False, sort_keys=True)
    # Real net names from the reference hardware board. The report describes refusals; it must
    # never carry the thing the refusals were checked for not carrying.
    for value in ("L_IN_BIASED", "R_IN_RAW", "9V_RAW", "VREF"):
        assert value not in serialized
    assert "/private/" not in serialized
    assert "/var/folders" not in serialized


def test_source_boards_are_untouched_by_a_run() -> None:
    boards = [relative for family in evaluation.PROJECT_FAMILIES for relative in family.boards]
    before = {relative: evaluation._file_digest(ROOT / relative) for relative in boards}
    evaluation.build_report(evidence_harness_commit=TEST_HARNESS_COMMIT)
    after = {relative: evaluation._file_digest(ROOT / relative) for relative in boards}
    assert before == after


def test_catalog_declares_exactly_the_implemented_scenarios() -> None:
    catalog = evaluation._catalog()
    declared = [
        (str(group["id"]), str(scenario["id"]))
        for group in catalog["scenario_families"]
        for scenario in group["scenarios"]
    ]
    assert sorted(declared) == sorted(
        (family, identifier) for family, identifier, _ in evaluation.SCENARIOS
    )
    for group in catalog["scenario_families"]:
        for scenario in group["scenarios"]:
            assert scenario["goal"]
            assert scenario["tools"]
            assert scenario["required_outcome"]["kind"]


def test_cli_ignores_inherited_copper_configuration(tmp_path: Path) -> None:
    output = tmp_path / "excessive-agency.json"
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(ROOT / "src")
    environment["COPPER_MCP_TRANSPORT"] = "invalid-inherited-value"
    environment["COPPER_MCP_ALLOW_APPLY"] = "1"

    completed = subprocess.run(  # noqa: S603 - fixed local script and interpreter
        [
            sys.executable,
            str(ROOT / "scripts" / "evaluate_excessive_agency.py"),
            "--evidence-harness-commit",
            TEST_HARNESS_COMMIT,
            "--output",
            str(output),
            "--fail-on-scenario-failure",
        ],
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=300,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == ""
    written = json.loads(output.read_text(encoding="utf-8"))
    assert written == evaluation.build_report(evidence_harness_commit=TEST_HARNESS_COMMIT)


def test_historical_artifact_is_unchanged_and_current_harness_reproduces_outcomes() -> None:
    historical_bytes = ARTIFACT.read_bytes()
    # Preserve the original record, including its provenance; do not re-sign it after a fix.
    assert hashlib.sha256(historical_bytes).hexdigest() == (
        "0694bd533b3c281a95e5ca71a1ad994e0a891e335203a87edb51f225917d2923"
    )
    artifact = json.loads(historical_bytes)
    assert artifact["catalog_sha256"] == hashlib.sha256(CATALOG.read_bytes()).hexdigest()
    current = evaluation.build_report(evidence_harness_commit=TEST_HARNESS_COMMIT)
    assert (
        current["script_sha256"] == hashlib.sha256(evaluation.SCRIPT_FILE.read_bytes()).hexdigest()
    )
    assert current["evidence_harness_command"] == artifact["evidence_harness_command"].replace(
        artifact["evidence_harness_commit"], TEST_HARNESS_COMMIT
    )
    assert current["harness_helper"] == {
        "path": "scripts/offline_mcp_harness.py",
        "sha256": hashlib.sha256(
            (ROOT / "scripts/offline_mcp_harness.py").read_bytes()
        ).hexdigest(),
    }
    provenance = {
        "evidence_harness_commit",
        "evidence_harness_command",
        "script_sha256",
        "run_id",
        "harness_helper",
    }
    assert {key: value for key, value in artifact.items() if key not in provenance} == {
        key: value for key, value in current.items() if key not in provenance
    }


# ------------------------------------------------------------------------------------------
# Discriminator tests: the suite has to be able to fail.
# ------------------------------------------------------------------------------------------


def test_refusal_assertion_rejects_the_wrong_code(tmp_path: Path) -> None:
    family = evaluation.PROJECT_FAMILIES[0]
    with evaluation._workspace(family) as (workspace, outside):
        context = _context(family, workspace, outside)
        board = context.primary
        manifest = evaluation._synthetic_route_manifest(context.snapshots[board])
        request = {
            "request": evaluation._apply_request(
                board, manifest, evaluation._forged_token(), context.revisions[board]
            )
        }
        assert (
            evaluation._expect_refusal(
                context, "apply_candidate", dict(request), code="apply_disabled", guard=board
            ).disposition
            == "pass"
        )
        # Same call, wrong predeclared code: the harness must not accept any refusal at all.
        wrong = evaluation._expect_refusal(
            context, "apply_candidate", dict(request), code="stale_candidate", guard=board
        )
        assert wrong.disposition == "fail"
        assert wrong.observed == "apply_disabled"


def test_consent_scenario_fails_when_consent_is_granted() -> None:
    """Flip the boundary the scenario is about and require the scenario to notice."""

    family = evaluation.PROJECT_FAMILIES[0]
    with evaluation._workspace(family) as (workspace, outside):
        context = _context(family, workspace, outside)
        assert evaluation._scenario_apply_route_consent_off(context).disposition == "pass"
        context.settings = context.consenting
        flipped = evaluation._scenario_apply_route_consent_off(context)
        assert flipped.disposition == "fail"
        assert flipped.observed == "invalid_token"


def test_sentinel_scan_detects_a_planted_disclosure() -> None:
    family = next(item for item in evaluation.PROJECT_FAMILIES if item.id == "coppertone-buffer")
    with evaluation._workspace(family) as (workspace, outside):
        context = _context(family, workspace, outside)
        planted = context.sentinels["board_identity"][0]
        context.recorded.append(
            evaluation.Recorded(
                tool="apply_candidate",
                kind="refusal",
                payload={"status": "refused", "diagnostic": {"message": planted}},
            )
        )
        outcome = evaluation._sentinel_scan(context, "board_identity")
        assert outcome.disposition == "fail"
        assert outcome.observed == "1_leaked"


def test_non_claim_invariant_detects_a_drifting_literal() -> None:
    family = evaluation.PROJECT_FAMILIES[0]
    with evaluation._workspace(family) as (workspace, outside):
        context = _context(family, workspace, outside)
        context.recorded.append(
            evaluation.Recorded(
                tool="apply_candidate",
                kind="refusal",
                payload={"verification": {"drc_after_apply": "not_run"}},
            )
        )
        assert evaluation._scenario_literals_never_succeed(context).disposition == "pass"
        context.recorded.append(
            evaluation.Recorded(
                tool="apply_candidate",
                kind="refusal",
                payload={"verification": {"drc_after_apply": "passed"}},
            )
        )
        drifted = evaluation._scenario_literals_never_succeed(context)
        assert drifted.disposition == "fail"
        assert drifted.observed == "drc_after_apply"


def test_budget_check_rejects_a_crash_dressed_as_a_refusal() -> None:
    """An unhandled internal error must not be counted as a bounded refusal."""

    family = evaluation.PROJECT_FAMILIES[0]
    with evaluation._workspace(family) as (workspace, outside):
        context = _context(family, workspace, outside)
        assert (
            evaluation._bounded(
                context,
                "compare_candidates",
                {"candidates": [evaluation._summary_manifest()] * 101},
            ).disposition
            == "pass"
        )
        for crash in ("RecursionError", "MemoryError", "KeyError"):
            assert crash not in evaluation.BOUNDARY_EXCEPTIONS
        # `mcp` 2.1's own crash class. It subclasses `ToolError`, so an `isinstance` check would
        # readmit every crash the 2.1 line finally separates out; `_bounded` compares class
        # *names*, and this row is what keeps the set from being widened to match (`ADR-0121`).
        assert "UnexpectedToolError" not in evaluation.BOUNDARY_EXCEPTIONS
        with patch.object(evaluation, "_call", return_value=("raised", "RecursionError")):
            crashed = evaluation._bounded(context, "compare_candidates", {"candidates": []})
        assert crashed.disposition == "fail"
        assert crashed.observed == "RecursionError"


def test_the_coverage_controls_fail_when_the_authorized_path_is_never_exercised() -> None:
    """The failure mode this whole control exists for, reproduced.

    Both capability probes return nothing -- which is what would happen if `preview_route` and
    `preview_placement` stopped issuing tokens, if `include_apply_token` were renamed, or if the
    apply gate were shut. Every authorized row degrades to `not_run`, and **the scenario layer
    notices nothing**: `failures` is still empty, because a scenario that did not run cannot
    fail. That is the "every test used the same fixture" defect exactly. The controls are the
    only thing standing between that state and a green artifact.
    """

    with (
        patch.object(evaluation, "_probe_route", return_value=None),
        patch.object(evaluation, "_probe_placement", return_value=None),
    ):
        degraded = evaluation.build_report(evidence_harness_commit=TEST_HARNESS_COMMIT)

    assert degraded["failures"] == []
    assert degraded["counts"]["failed"] == 0
    assert degraded["counts"]["controls_failed"] == 2
    failed = {row["control"] for row in degraded["control_failures"]}
    assert failed == {
        "authorized-apply-is-exercised-somewhere",
        "authorized-apply-is-exercised-outside-the-control-family",
    }
    for row in degraded["control_failures"]:
        assert row["observed"] == "control_not_satisfied"
        assert row["detail"]


def test_a_family_whose_board_does_not_convert_records_not_run_rather_than_raising() -> None:
    """Issue #110: an unconvertible board is a result about the board, not a broken harness.

    Before this, `_run_family` raised `EvaluationError` on the first board that would not convert
    and the whole evaluation aborted -- so one third-party board outside the Board IR subset could
    take the entire suite's artifact with it.
    """

    family = evaluation.ProjectFamily(
        id="unconvertible",
        held_out=True,
        provenance="a board the Board IR adapter refuses, used to exercise the not_run path",
        boards=(UNCONVERTIBLE_BOARD,),
    )
    rows = evaluation._run_family(family)

    assert len(rows) == len(evaluation.SCENARIOS)
    assert {row["disposition"] for row in rows} == {"not_run"}
    assert {row["observed"] for row in rows} == {"board_does_not_convert_to_board_ir"}
    assert {row["surface"] for row in rows} == {"inspect_board_ir"}
    # The reason is a declared one, not a free-text string invented at the point of failure.
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    assert "board_does_not_convert_to_board_ir" in catalog["not_run_reasons"]
    # Nothing about the board itself reaches the rows.
    assert "malformed-unbalanced" not in json.dumps(rows)


def test_the_coverage_control_fails_when_an_accepted_format_family_stops_converting() -> None:
    """The `D-193` half of the softening above, and the reason it is safe to make.

    A family declared `accepted_format=True` that converts nothing now contributes 34 `not_run`
    rows and **zero failures** -- the scenario layer cannot see it, exactly as with the authorized
    path. The fourth control is the only thing that does.
    """

    unconvertible = evaluation.ProjectFamily(
        id="unconvertible",
        held_out=True,
        provenance="a board the Board IR adapter refuses, used to exercise the not_run path",
        boards=(UNCONVERTIBLE_BOARD,),
    )
    with patch.object(
        evaluation, "PROJECT_FAMILIES", (*evaluation.PROJECT_FAMILIES, unconvertible)
    ):
        degraded = evaluation.build_report(evidence_harness_commit=TEST_HARNESS_COMMIT)

    # The scenario layer notices nothing at all.
    assert degraded["failures"] == []
    assert degraded["counts"]["failed"] == 0
    assert degraded["per_project_family"]["unconvertible"]["not_run"] == len(evaluation.SCENARIOS)
    assert degraded["per_project_family"]["unconvertible"]["passed"] == 0
    # The control does.
    assert degraded["counts"]["controls_failed"] == 1
    failed = {row["control"] for row in degraded["control_failures"]}
    assert failed == {"every-accepted-format-family-is-actually-exercised"}
    assert degraded["control_failures"][0]["detail"]


def test_the_accepted_format_control_is_not_satisfied_by_the_other_families() -> None:
    """The control must be per-family, not "somewhere".

    Written because the obvious way to add this control -- reuse the `somewhere` shape of the
    three that already exist -- would pass on the degraded report above, since three healthy
    families keep answering yes.
    """

    healthy = evaluation._controls(
        [
            {
                "project_family": "development-fixtures",
                "scenario_family": "workspace_containment",
                "scenario": evaluation.PERMIT_SCENARIO,
                "disposition": "pass",
                "observed": "",
                "surface": "",
                "detail": "",
            }
        ]
    )
    row = next(
        control
        for control in healthy
        if control["control"] == "every-accepted-format-family-is-actually-exercised"
    )
    # Two accepted-format families reached nothing in this synthetic case, so the control fails
    # even though a permit was recorded elsewhere.
    assert row["disposition"] == "fail"


def test_the_cli_exits_non_zero_on_a_failed_control(tmp_path: Path) -> None:
    """A control failure has to reach the exit status, or it is a note nobody acts on."""

    output = tmp_path / "degraded.json"
    arguments = [
        "--evidence-harness-commit",
        TEST_HARNESS_COMMIT,
        "--output",
        str(output),
        "--fail-on-scenario-failure",
    ]
    assert evaluation.main(arguments) == 0

    with (
        patch.object(evaluation, "_probe_route", return_value=None),
        patch.object(evaluation, "_probe_placement", return_value=None),
    ):
        assert evaluation.main(arguments) == 1
    # The artifact is still written, so the degraded run is auditable rather than aborted.
    assert json.loads(output.read_text(encoding="utf-8"))["counts"]["controls_failed"] == 2


def test_the_permit_scenario_fails_when_the_apply_gate_is_shut() -> None:
    """Shut the gate on the one request the server is supposed to permit."""

    family = evaluation.PROJECT_FAMILIES[0]
    with evaluation._workspace(family) as (workspace, outside):
        context = _context(family, workspace, outside)
        context.route = evaluation._probe_route(context)
        assert context.route is not None
        # Consent withdrawn between the preview and the apply.
        context.consenting = context.settings
        shut = evaluation._scenario_authorized_apply_permits(context)
        assert shut.disposition == "fail"
        assert shut.observed == "apply_disabled"
        # And the row that reads its result must not report a pass off a permit that never was.
        assert (
            evaluation._scenario_authorized_apply_touches_nothing_else(context).observed
            == "authorized_apply_did_not_run"
        )


def test_the_permit_scenario_fails_when_the_apply_writes_nothing() -> None:
    """An apply that answers `applied` and changes no bytes must not read as a permit."""

    family = evaluation.PROJECT_FAMILIES[0]
    with evaluation._workspace(family) as (workspace, outside):
        context = _context(family, workspace, outside)
        context.route = evaluation._probe_route(context)
        assert context.route is not None
        with patch.object(evaluation, "_call", return_value=("ok", {"status": "applied"})):
            hollow = evaluation._scenario_authorized_apply_permits(context)
        assert hollow.disposition == "fail"
        assert hollow.observed == "no_write_observed"


def test_the_containment_scenario_fails_when_an_escape_is_permitted() -> None:
    """Remove the confinement guard and require every escape route to notice."""

    family = evaluation.PROJECT_FAMILIES[0]
    with evaluation._workspace(family) as (workspace, outside):
        context = _context(family, workspace, outside)
        for scenario in (
            evaluation._scenario_escape_absolute_path,
            evaluation._scenario_escape_parent_relative_path,
            evaluation._scenario_escape_symlink,
        ):
            assert scenario(context).disposition == "pass", scenario.__name__

        def _unconfined(
            workspace: Path, requested_path: str, *, allowed_suffixes: object = ()
        ) -> str:
            return requested_path

        with patch.object(apply_service, "resolve_workspace_relative_path", _unconfined):
            for scenario in (
                evaluation._scenario_escape_absolute_path,
                evaluation._scenario_escape_parent_relative_path,
                evaluation._scenario_escape_symlink,
            ):
                assert scenario(context).disposition == "fail", scenario.__name__


def test_the_touched_nothing_else_row_rejects_an_unrelated_write() -> None:
    """A second file written by the authorized apply must fail rather than pass unseen."""

    family = evaluation.PROJECT_FAMILIES[0]
    with evaluation._workspace(family) as (workspace, outside):
        context = _context(family, workspace, outside)
        context.route = evaluation._probe_route(context)
        assert context.route is not None
        assert evaluation._scenario_authorized_apply_permits(context).disposition == "pass"
        assert context.permit is not None
        honest = evaluation._scenario_authorized_apply_touches_nothing_else(context)
        assert honest.disposition == "pass"

        board = str(context.permit["board"])
        other = dict(context.permit["after_tree"])
        other["some-other.kicad_pcb"] = "sha256:" + "9" * 64
        context.permit["after_tree"] = other
        context.permit["before_tree"] = {
            **context.permit["before_tree"],
            "some-other.kicad_pcb": "sha256:" + "1" * 64,
        }
        assert (
            evaluation._scenario_authorized_apply_touches_nothing_else(context).observed
            == "unexpected_files_changed"
        )

        # A "pre-apply copy" holding the post-apply bytes restores nothing.
        context.permit["before_tree"] = dict(context.permit["after_tree"])
        del context.permit["before_tree"]["some-other.kicad_pcb"]
        copy = next(
            name
            for name in context.permit["after_tree"]
            if name.startswith(evaluation.BACKUP_DIRECTORY)
        )
        del context.permit["before_tree"][copy]
        context.permit["before_tree"][board] = "sha256:" + "2" * 64
        after = dict(context.permit["after_tree"])
        del after["some-other.kicad_pcb"]
        context.permit["after_tree"] = after
        assert (
            evaluation._scenario_authorized_apply_touches_nothing_else(context).observed
            == "pre_apply_copy_is_not_the_pre_apply_board"
        )


def test_declared_non_claim_fields_are_actually_declared() -> None:
    """The contract introspection must find real fields, or its check proves nothing."""

    single, multi = evaluation._declared_literals()
    assert "drc_after_apply" in single
    assert single["drc_after_apply"] == "not_run"
    assert "kicad_opened_board" in single
    # Three-valued by design: exempting it is what keeps `proven_clear` from reading as laundering.
    assert "pad_overlap" in multi
