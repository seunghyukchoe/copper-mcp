"""The CI timeout-budget gate, against what would make it useless.

`scripts/check_ci_budgets.py` exists because `v0.7.0` shipped without provenance:
its release run was cancelled by a 20-minute `timeout-minutes` while the hosted
gate took 34m59s, and the 20 came from a *local* 20m29s measurement on a
different machine (`D-196`). The budget was never checked against anything the
job actually did on a runner.

So the cases here are organised by how the gate could be green and worthless:

* it could pass a budget nobody measured, or one measured on the wrong machine
  (`test_a_budget_with_no_calibration_entry_fails`);
* it could compare against an average, or against only the newest run, so that
  a slow observation retires by being outlived
  (`test_every_recorded_observation_binds_not_only_the_newest`);
* its calibration file could outlive the budgets it describes, becoming a
  record of a workflow that no longer exists
  (`test_a_calibration_entry_for_a_job_with_no_budget_fails`);
* it could silently skip a `timeout-minutes` written in a shape its reader does
  not model, and report "no budgets" identically to a repository that has none
  (`test_a_timeout_it_cannot_attribute_to_a_job_fails`,
  `test_a_timeout_whose_value_it_cannot_model_fails`,
  `test_a_workflow_directory_with_no_budgets_at_all_fails`);
* it could calibrate against a run that never finished the work, so that a
  two-minute setup failure buys a 35-minute job a four-minute ceiling
  (`test_a_failed_run_does_not_calibrate_a_budget`).

And one case is the real historical mutant: putting the release budget back to
20 must fail (`test_the_v0_7_0_budget_would_fail_against_the_recorded_gate`).
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location(
    "check_ci_budgets", ROOT / "scripts" / "check_ci_budgets.py"
)
assert _SPEC is not None and _SPEC.loader is not None
check_ci_budgets = importlib.util.module_from_spec(_SPEC)
sys.modules["check_ci_budgets"] = check_ci_budgets
_SPEC.loader.exec_module(check_ci_budgets)


_WORKFLOW = """name: Example

on:
  pull_request:

jobs:
  {job}:
    name: Example job
    runs-on: ubuntu-latest
{timeout}    steps:
      - name: Do the thing
        run: python -c "pass"
"""


def _workflow(job: str = "test", minutes: int | None = None) -> str:
    timeout = "" if minutes is None else f"    timeout-minutes: {minutes}\n"
    return _WORKFLOW.format(job=job, timeout=timeout)


def _observation(seconds: int, conclusion: str = "success") -> dict[str, Any]:
    return {
        "run_id": 1,
        "job_id": 2,
        "hosted_job_name": "Example job",
        "started_at": "2026-08-14T00:00:00Z",
        "completed_at": "2026-08-14T00:00:00Z",
        "conclusion": conclusion,
        "seconds": seconds,
    }


def _tree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    workflows: dict[str, str],
    calibration: list[dict[str, Any]] | None,
) -> tuple[list[str], list[str]]:
    directory = tmp_path / ".github" / "workflows"
    directory.mkdir(parents=True)
    for name, body in workflows.items():
        (directory / name).write_text(body, encoding="utf-8")
    if calibration is not None:
        (tmp_path / ".github" / "ci-budget-calibration.json").write_text(
            json.dumps({"schema": "ci-budget-calibration/1", "jobs": calibration}),
            encoding="utf-8",
        )
    monkeypatch.setattr(check_ci_budgets, "ROOT", tmp_path)
    failures: list[str] = []
    notes: list[str] = []
    check_ci_budgets._check(failures, notes)
    return failures, notes


def _entry(
    seconds: list[int],
    workflow: str = ".github/workflows/ci.yml",
    job: str = "test",
    conclusions: list[str] | None = None,
) -> dict[str, Any]:
    verdicts = ["success"] * len(seconds) if conclusions is None else conclusions
    return {
        "workflow": workflow,
        "job": job,
        "observations": [
            _observation(value, conclusion)
            for value, conclusion in zip(seconds, verdicts, strict=True)
        ],
    }


# ---------------------------------------------------------------------------
# The half rule
# ---------------------------------------------------------------------------


def test_the_boundary_is_inside_the_rule_and_one_second_past_it_is_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`At least` twice, so exactly twice passes and one second more does not.

    A rule stated as an inequality has two nearby wrong implementations -- strict
    where it should be inclusive, and rounded where it should be exact -- and
    both are invisible to a test that only samples the comfortable middle.
    """

    exact, _ = _tree(
        tmp_path / "exact", monkeypatch, {"ci.yml": _workflow(minutes=70)}, [_entry([2100])]
    )
    assert exact == []

    over, _ = _tree(
        tmp_path / "over", monkeypatch, {"ci.yml": _workflow(minutes=70)}, [_entry([2101])]
    )
    assert len(over) == 1
    assert "The half rule requires at least 71 minutes" in over[0]


def test_a_budget_above_twice_the_recorded_duration_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    failures, notes = _tree(
        tmp_path,
        monkeypatch,
        {"ci.yml": _workflow(minutes=120)},
        [_entry([2088])],
    )

    assert failures == []
    assert len(notes) == 1
    assert "half rule needs 69.60 min" in notes[0]


def test_a_budget_below_twice_the_recorded_duration_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    failures, _ = _tree(
        tmp_path,
        monkeypatch,
        {"ci.yml": _workflow(minutes=60)},
        [_entry([2088])],
    )

    assert len(failures) == 1
    assert "The half rule requires at least 70 minutes" in failures[0]


def test_the_v0_7_0_budget_would_fail_against_the_recorded_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The historical instance, replayed: 20 minutes against a 34m59s hosted gate.

    This is the check that would have refused the number that cost `v0.7.0` its
    build-provenance attestation, in the pull request that set it rather than in
    the audit that found it seven days later.
    """

    failures, _ = _tree(
        tmp_path,
        monkeypatch,
        {"release.yml": _workflow(job="verify", minutes=20)},
        [_entry([2099], workflow=".github/workflows/release.yml", job="verify")],
    )

    assert len(failures) == 1
    assert "`timeout-minutes: 20`" in failures[0]
    assert "D-196" in failures[0]


def test_every_recorded_observation_binds_not_only_the_newest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A slow run cannot be retired by appending a fast one.

    The obligation is stated against the most recent duration; enforcing it
    against the whole recorded sample is strictly stronger and is what stops the
    calibration file from becoming a way to lower a ceiling without touching the
    ceiling. An average would be weaker still -- and an average is the wrong
    summary for a ceiling in any case.
    """

    failures, _ = _tree(
        tmp_path,
        monkeypatch,
        {"ci.yml": _workflow(minutes=60)},
        [_entry([2088, 60, 60, 60, 60, 60, 60, 60])],
    )

    assert len(failures) == 1
    assert "2088s" in failures[0]


# ---------------------------------------------------------------------------
# The calibration file is a closed list in both directions
# ---------------------------------------------------------------------------


def test_a_budget_with_no_calibration_entry_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    failures, _ = _tree(tmp_path, monkeypatch, {"ci.yml": _workflow(minutes=120)}, [])

    assert any("records no calibrated jobs" in failure for failure in failures)
    assert any("with no entry in" in failure for failure in failures)


def test_a_calibration_entry_with_no_observations_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    failures, _ = _tree(
        tmp_path,
        monkeypatch,
        {"ci.yml": _workflow(minutes=120)},
        [{"workflow": ".github/workflows/ci.yml", "job": "test", "observations": []}],
    )

    assert any("with no observations" in failure for failure in failures)


def test_a_failed_run_does_not_calibrate_a_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A run that stopped early measures nothing about the work it did not do.

    This is `D-196` with the sign flipped. There the budget was measured on the
    wrong machine; here it would be measured on the right machine during the
    wrong run -- a two-minute setup failure recorded as the job's duration, and
    a four-minute ceiling derived from it that clears the half rule while the
    successful run takes thirty-five. Validating `seconds` and ignoring
    `conclusion` accepts exactly that.
    """

    failures, notes = _tree(
        tmp_path,
        monkeypatch,
        {"ci.yml": _workflow(minutes=4)},
        [_entry([120], conclusions=["failure"])],
    )

    assert notes == []
    rejected = [failure for failure in failures if "conclusion is 'failure'" in failure]
    assert len(rejected) == 1
    assert "run 1 job 2" in rejected[0]
    # With its only observation refused, the budget is uncalibrated rather than
    # calibrated from nothing -- so the budget itself fails too.
    assert any("with no entry in" in failure for failure in failures)


def test_a_cancelled_run_is_rejected_by_name_rather_than_dropped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Silently discarding it would leave the remaining sample looking complete.

    The other observations here do clear the half rule, so a checker that merely
    filtered the cancelled run out would print a clean note and never say that
    the file records something it will not use.
    """

    failures, _ = _tree(
        tmp_path,
        monkeypatch,
        {"ci.yml": _workflow(minutes=120)},
        [_entry([2088, 60], conclusions=["success", "cancelled"])],
    )

    assert len(failures) == 1
    assert "conclusion is 'cancelled'" in failures[0]
    assert "not 'success'" in failures[0]


def test_an_observation_with_no_recorded_conclusion_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An absent conclusion is not a successful one; the file must say which run it read."""

    entry = _entry([2088])
    del entry["observations"][0]["conclusion"]
    failures, _ = _tree(tmp_path, monkeypatch, {"ci.yml": _workflow(minutes=120)}, [entry])

    assert any("conclusion is None" in failure for failure in failures)


def test_a_calibration_entry_for_a_job_with_no_budget_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A calibration that matches no budget is stale, exactly as an unused exemption is."""

    failures, _ = _tree(
        tmp_path,
        monkeypatch,
        {"ci.yml": _workflow(job="test", minutes=120), "old.yml": _workflow(job="gone")},
        [_entry([100]), _entry([100], workflow=".github/workflows/old.yml", job="gone")],
    )

    assert len(failures) == 1
    assert "matches no budget is stale" in failures[0]


def test_a_calibration_file_that_is_not_the_expected_schema_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / ".github" / "workflows"
    directory.mkdir(parents=True)
    (directory / "ci.yml").write_text(_workflow(minutes=120), encoding="utf-8")
    (tmp_path / ".github" / "ci-budget-calibration.json").write_text(
        json.dumps({"schema": "something-else/1", "jobs": []}), encoding="utf-8"
    )
    monkeypatch.setattr(check_ci_budgets, "ROOT", tmp_path)

    failures: list[str] = []
    check_ci_budgets._check(failures, [])

    assert any("must declare schema" in failure for failure in failures)


# ---------------------------------------------------------------------------
# The reader refuses what it cannot attribute
# ---------------------------------------------------------------------------


def test_a_timeout_it_cannot_attribute_to_a_job_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A step-level budget is a budget. Skipping it silently would be the old defect."""

    body = _workflow(minutes=120).replace(
        "      - name: Do the thing\n", "      - name: Do the thing\n        timeout-minutes: 5\n"
    )
    failures, _ = _tree(tmp_path, monkeypatch, {"ci.yml": body}, [_entry([100])])

    assert len(failures) == 1
    assert "cannot attribute to a job" in failures[0]


def test_a_timeout_with_a_trailing_comment_parses_as_its_integer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`timeout-minutes: 120 # calibrated` is valid YAML and a real budget.

    The value is still a plain integer; only the line has more on it. A reader
    that requires the number to end the line does not reject this budget -- it
    stops seeing it, which is the failure mode this file exists to refuse.
    """

    body = _workflow(minutes=120).replace(
        "timeout-minutes: 120", "timeout-minutes: 120 # calibrated against run 31784160677"
    )
    failures, notes = _tree(tmp_path, monkeypatch, {"ci.yml": body}, [_entry([2088])])

    assert failures == []
    assert len(notes) == 1
    assert "budget 120 min" in notes[0]


def test_a_timeout_whose_value_it_cannot_model_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An Actions expression is a budget this reader cannot evaluate, so it must say so.

    The key is recognised independently of its value on purpose. Matching the
    two together means an unmodelled value never matches at all: the line is
    skipped, and the global "some budgets exist" check still passes because the
    *other* workflows declare budgets -- so an uncalibrated ceiling ships while
    the checker reports full coverage. That is the same silent-skip defect as an
    unattributable budget, one level down.
    """

    expression = _workflow(job="build", minutes=30).replace(
        "timeout-minutes: 30", "timeout-minutes: ${{ inputs.timeout }}"
    )
    failures, notes = _tree(
        tmp_path,
        monkeypatch,
        {"ci.yml": _workflow(minutes=120), "build.yml": expression},
        [_entry([2088])],
    )

    assert len(failures) == 1
    assert failures[0].startswith(".github/workflows/build.yml:")
    assert "${{ inputs.timeout }}" in failures[0]
    assert "cannot model" in failures[0]
    # The calibrated budget in the other workflow is why the vacuous-pass check
    # stayed green in the defect: coverage looked complete because it was counted.
    assert len(notes) == 1


def test_a_workflow_directory_with_no_budgets_at_all_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one verdict this checker must never give is a vacuous pass.

    A repository that declares no budgets, and a parser that has stopped
    recognising the ones that are declared, produce the same reading. Only one of
    them is a state anyone wants, and neither is a clean run.
    """

    failures, _ = _tree(tmp_path, monkeypatch, {"ci.yml": _workflow()}, [])

    assert any("pass vacuously" in failure for failure in failures)


def test_a_workflow_whose_jobs_it_cannot_read_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    failures, _ = _tree(
        tmp_path,
        monkeypatch,
        {"ci.yml": _workflow(minutes=120), "odd.yml": "name: Odd\non:\n  push:\n"},
        [_entry([100])],
    )

    assert any("declares no jobs this checker can read" in failure for failure in failures)


# ---------------------------------------------------------------------------
# The committed tree
# ---------------------------------------------------------------------------


def test_the_committed_workflows_declare_calibrated_budgets() -> None:
    """Green here, and green because three real budgets clear the rule against real runs."""

    failures: list[str] = []
    notes: list[str] = []
    check_ci_budgets._check(failures, notes)

    assert failures == []
    assert len(notes) == 3


def test_every_committed_job_that_runs_the_suite_declares_a_budget() -> None:
    """`ci.yml` had no `timeout-minutes` at all, which is the other half of the drift.

    An unbounded job cannot be cancelled by a ceiling, but it also cannot tell
    anyone the suite has outgrown one. Pinned so that removing the budget is a
    test failure rather than a silent return to the previous state.
    """

    failures: list[str] = []
    budgets = check_ci_budgets._read_workflows(failures)

    assert failures == []
    assert {(budget.workflow, budget.job) for budget in budgets} == {
        (".github/workflows/ci.yml", "test"),
        (".github/workflows/release.yml", "verify"),
        (".github/workflows/release.yml", "publish"),
    }


def test_the_calibration_file_records_the_hosted_runs_it_claims_to() -> None:
    """The file pins the exact hosted runs it calibrates from, and nothing else.

    Re-pinned because `ci.yml:test` **changed shape**, not because the suite grew.
    Its `Unit tests` step now runs four xdist workers with `--dist loadfile`
    (D-244, B-143), so the six serial legs pinned at the v0.12.0 boundary measured
    a job that no longer exists and were replaced rather than kept. The v0.12.0
    sample was two post-merge `main` pushes; this one is `workflow_dispatch` runs
    on the branch that makes the change, because the four-worker job does not
    exist on `main` until it lands and no post-merge sample of it can exist yet.
    That is a real weakening of the sample, the calibration file's `note` says so,
    and it carries the instruction to re-record from `main` pushes next wave.

    The release sample is deliberately untouched at v0.11.0, and that matters
    beyond tidiness: `docs/mutants/2026-08-14-ci-budget-gate.json` anchors mutant
    CB11 on the string `"seconds": 2325`, which belongs to release.yml:verify.
    Replacing only the ci.yml:test observations keeps that anchor matching exactly
    once, which is the harness's requirement -- an observation that happened to be
    2325s would make it ambiguous, and the mutant would report a stale anchor
    instead of a kill.

    ci.yml:test is still the longest duration in the file, at 2745s against
    release.yml:verify's 2325s. Serially it was 3302s, which was 91.7% of the
    3600s half of the 120-minute ceiling; the parallel job needs 91.50 minutes and
    leaves 28.50 minutes of margin against that same unchanged ceiling.

    This assertion is why the calibration file cannot drift silently: re-recording
    it is a reviewed edit that must move these numbers too.
    """

    document = json.loads((ROOT / check_ci_budgets.CALIBRATION).read_text(encoding="utf-8"))
    by_job = {(entry["workflow"], entry["job"]): entry for entry in document["jobs"]}

    verify = by_job[(".github/workflows/release.yml", "verify")]
    assert [observation["seconds"] for observation in verify["observations"]] == [2325]
    assert verify["observations"][0]["run_id"] == 33162621059

    ci = by_job[(".github/workflows/ci.yml", "test")]
    assert len(ci["observations"]) == 3
    assert {observation["run_id"] for observation in ci["observations"]} == {33639322391}
    assert max(observation["seconds"] for observation in ci["observations"]) == 2745

    # The retired serial figure is not an observation any more, and no observation
    # may collide with the release sample the CB11 mutant anchors on.
    assert 3302 not in {observation["seconds"] for observation in ci["observations"]}
    assert 2325 not in {observation["seconds"] for observation in ci["observations"]}

    # Every committed observation is a completed successful run, which is the only
    # kind the checker will calibrate from.
    assert {
        observation["conclusion"]
        for entry in document["jobs"]
        for observation in entry["observations"]
    } == {check_ci_budgets.REQUIRED_CONCLUSION}
