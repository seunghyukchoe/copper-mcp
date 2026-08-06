"""Regression cover for the excessive-agency evaluation suite.

Two things are pinned here. The first is the *result*: the committed artifact must replay from the
harness it names, and the suite must still reach zero failures. The second is the *discriminator*:
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

from copper_mcp.apply.tokens import ApplyTokenAuthority
from copper_mcp.config import Settings
from scripts import evaluate_excessive_agency as evaluation

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "benchmarks" / "results" / "security"
ARTIFACT = RESULTS / "2026-08-06-excessive-agency-evaluation.json"
CATALOG = ROOT / "tests" / "fixtures" / "security" / "excessive-agency-v1" / "scenarios.json"
TEST_HARNESS_COMMIT = "a" * 40

EXPECTED_COUNTS = {
    "cases": 116,
    "passed": 77,
    "failed": 0,
    "not_run": 39,
    "scenarios": 29,
    "project_families": 4,
}


@pytest.fixture(scope="module")
def report() -> dict[str, object]:
    return evaluation.build_report(evidence_harness_commit=TEST_HARNESS_COMMIT)


def _context(family: evaluation.ProjectFamily, workspace: Path) -> evaluation.FamilyContext:
    settings = Settings(workspace=workspace)
    context = evaluation.FamilyContext(
        family=family,
        workspace=workspace,
        settings=settings,
        consenting=replace(settings, allow_apply=True),
        authority=ApplyTokenAuthority(),
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
    assert len(declared) == 29
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
    assert per_family["tscircuit-benchmark"]["not_run"] == 29
    assert per_family["tscircuit-benchmark"]["passed"] == 0


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


def test_committed_artifact_replays_from_its_recorded_harness() -> None:
    artifact = json.loads(ARTIFACT.read_text(encoding="utf-8"))
    assert (
        artifact["script_sha256"] == hashlib.sha256(evaluation.SCRIPT_FILE.read_bytes()).hexdigest()
    )
    assert artifact["catalog_sha256"] == hashlib.sha256(CATALOG.read_bytes()).hexdigest()
    assert artifact == evaluation.build_report(
        evidence_harness_commit=artifact["evidence_harness_commit"]
    )


# ------------------------------------------------------------------------------------------
# Discriminator tests: the suite has to be able to fail.
# ------------------------------------------------------------------------------------------


def test_refusal_assertion_rejects_the_wrong_code(tmp_path: Path) -> None:
    family = evaluation.PROJECT_FAMILIES[0]
    with evaluation._workspace(family) as workspace:
        context = _context(family, workspace)
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
    with evaluation._workspace(family) as workspace:
        context = _context(family, workspace)
        assert evaluation._scenario_apply_route_consent_off(context).disposition == "pass"
        context.settings = context.consenting
        flipped = evaluation._scenario_apply_route_consent_off(context)
        assert flipped.disposition == "fail"
        assert flipped.observed == "invalid_token"


def test_sentinel_scan_detects_a_planted_disclosure() -> None:
    family = next(item for item in evaluation.PROJECT_FAMILIES if item.id == "coppertone-buffer")
    with evaluation._workspace(family) as workspace:
        context = _context(family, workspace)
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
    with evaluation._workspace(family) as workspace:
        context = _context(family, workspace)
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
    with evaluation._workspace(family) as workspace:
        context = _context(family, workspace)
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
        with patch.object(evaluation, "_call", return_value=("raised", "RecursionError")):
            crashed = evaluation._bounded(context, "compare_candidates", {"candidates": []})
        assert crashed.disposition == "fail"
        assert crashed.observed == "RecursionError"


def test_declared_non_claim_fields_are_actually_declared() -> None:
    """The contract introspection must find real fields, or its check proves nothing."""

    single, multi = evaluation._declared_literals()
    assert "drc_after_apply" in single
    assert single["drc_after_apply"] == "not_run"
    assert "kicad_opened_board" in single
    # Three-valued by design: exempting it is what keeps `proven_clear` from reading as laundering.
    assert "pad_overlap" in multi
