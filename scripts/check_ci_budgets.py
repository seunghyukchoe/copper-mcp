#!/usr/bin/env python3
"""Check every declared CI timeout budget against a recorded hosted duration.

`v0.7.0` published without provenance because its release run was cancelled by
its own 20-minute `timeout-minutes` (`D-196`). The budget was not wrong about
the machine it was measured on; it was measured on the wrong machine. A local
`make check` at 20m29s is not evidence about a hosted `ubuntu-latest` runner,
where the same gate takes 34m59s -- so the budget was 20 minutes for a job that
needed 35, and nothing in the repository could say so.

This checker closes that. It reads every explicit `timeout-minutes` in
`.github/workflows/` and requires each one to clear the **half rule**: a budget
must be at least twice the longest recorded duration of the job it bounds. That
is the rule the release workflow's own comment already states -- *when the
measured gate exceeds half of any CI timeout budget, raise the budget in the
same PR that grew the gate* -- turned from a comment into a gate.

The recorded durations live in `.github/ci-budget-calibration.json`, read from
the GitHub Actions API rather than from a local stopwatch. That file, not this
script, is what has to be updated when a job gets slower; its `update` field
says how, and `docs/development.md` says it again in prose.

Three deliberate choices, each because the lazy alternative would make the gate
weaker than it looks.

- **Every recorded observation binds, not only the newest.** The obligation is
  stated against the most recent duration, and enforcing it against the whole
  recorded sample is strictly stronger: a slow run cannot be retired by
  appending a fast one. Averaging would be weaker still, and an average is
  exactly the wrong summary for a ceiling.
- **A budget with no calibration entry fails, and an entry with no budget fails.**
  The same closed-list discipline `REPLAY_SUB_ENTRIES` and `EXEMPT_DRIFT` use:
  an exception that stops matching reality is itself a failure, so calibration
  cannot be added once and then quietly outlive the job it describes.
- **A `timeout-minutes` this parser cannot attribute to a job fails loudly.**
  A step-level budget, or one written in a shape the reader does not model, is
  reported rather than skipped. An absence is evidence only if the observation
  was capable of reporting a presence, and a parser that silently ignores what
  it does not understand reports "no budgets" identically to a repository that
  has none. The key is therefore detected independently of its value: a budget
  written as `${{ inputs.timeout }}`, or in any other shape the value reader
  does not model, is a named failure rather than a line that never matched.
- **Only a `success` observation calibrates a budget.** A run that failed or
  was cancelled stopped early, so its duration bounds the work from below
  instead of measuring it -- a two-minute setup failure would otherwise buy a
  35-minute job a four-minute ceiling, which is `D-196` with the sign flipped.
  A recorded non-success conclusion is rejected by name rather than dropped,
  for the same reason an unattributable budget is.

The workflow reader is deliberately small and strict rather than a YAML parser:
these files are 2-space block YAML, the shapes it accepts are pinned by tests,
and anything outside them is a failure rather than a guess. That keeps the
checker's dependency set at the standard library, which is the same reason
`check_schema_sets.py` reads JSON by hand.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = ".github/workflows"
CALIBRATION = ".github/ci-budget-calibration.json"
CALIBRATION_SCHEMA = "ci-budget-calibration/1"

# A budget must be at least this many times the longest recorded duration.
HALF_RULE_FACTOR = 2

# The only conclusion a recorded run may have. See the module docstring.
REQUIRED_CONCLUSION = "success"
PENDING_JOBS = frozenset(
    {
        (".github/workflows/ci.yml", "quality"),
        (".github/workflows/ci.yml", "compatibility"),
        (".github/workflows/ci.yml", "evidence"),
        (".github/workflows/ci.yml", "package"),
    }
)
PENDING_MINUTES = 120

_JOBS_KEY = re.compile(r"^jobs:\s*$")
_JOB_ID = re.compile(r"^ {2}(?P<job>[A-Za-z_][A-Za-z0-9_-]*):\s*$")
# The key is matched without regard to its value, so that a value shape this reader
# does not model reaches the failure below instead of never matching at all.
_TIMEOUT_KEY = re.compile(r"^(?P<indent> *)timeout-minutes:(?P<value>.*)$")
# The one value shape modelled: a plain integer, optionally with a trailing comment.
_TIMEOUT_VALUE = re.compile(r"^(?P<minutes>\d+)(?:\s*#.*)?$")
# The indentation a job-level key sits at: two for the job id, two more for its keys.
_JOB_KEY_INDENT = 4


@dataclass(frozen=True)
class Budget:
    """One explicit `timeout-minutes`, located where it was written."""

    workflow: str
    job: str
    line: int
    minutes: int


def _read_workflow(relative: str, text: str, failures: list[str]) -> list[Budget]:
    """Read one workflow's job-level timeout budgets, refusing what it cannot attribute."""

    budgets: list[Budget] = []
    in_jobs = False
    job: str | None = None
    seen_jobs = 0

    for index, line in enumerate(text.splitlines(), start=1):
        if _JOBS_KEY.match(line):
            in_jobs = True
            job = None
            continue
        if in_jobs and line and not line.startswith(" ") and not line.startswith("#"):
            # A new top-level key ends the `jobs:` block.
            in_jobs = False
            job = None
        if in_jobs:
            job_id = _JOB_ID.match(line)
            if job_id is not None:
                job = job_id.group("job")
                seen_jobs += 1
                continue

        timeout = _TIMEOUT_KEY.match(line)
        if timeout is None:
            continue
        indent = len(timeout.group("indent"))
        if not in_jobs or job is None or indent != _JOB_KEY_INDENT:
            failures.append(
                f"{relative}:{index} declares `timeout-minutes` this checker cannot attribute to "
                "a job (it is not a job-level key at four spaces of indentation). Move it to the "
                "job, or extend scripts/check_ci_budgets.py to model it -- an unattributed budget "
                "is an uncalibrated one"
            )
            continue
        value = timeout.group("value").strip()
        minutes = _TIMEOUT_VALUE.match(value)
        if minutes is None:
            failures.append(
                f"{relative}:{index} declares `timeout-minutes: {value}` on job {job!r}, a value "
                "this checker cannot model (it reads a plain integer, optionally followed by a `#` "
                "comment). Write the budget as a literal, or extend scripts/check_ci_budgets.py to "
                "model this shape -- a budget whose value cannot be read cannot be calibrated, and "
                "skipping the line would report it identically to a job that declares none"
            )
            continue
        budgets.append(
            Budget(workflow=relative, job=job, line=index, minutes=int(minutes.group("minutes")))
        )

    if seen_jobs == 0:
        failures.append(
            f"{relative} declares no jobs this checker can read; its budgets, if any, are unchecked"
        )
    return budgets


def _read_workflows(failures: list[str]) -> list[Budget]:
    directory = ROOT / WORKFLOW_DIR
    if not directory.is_dir():
        failures.append(f"missing {WORKFLOW_DIR}")
        return []
    budgets: list[Budget] = []
    paths = sorted(p for p in directory.iterdir() if p.suffix in {".yml", ".yaml"})
    if not paths:
        failures.append(f"{WORKFLOW_DIR} contains no workflow files")
        return []
    for path in paths:
        relative = path.relative_to(ROOT).as_posix()
        budgets.extend(_read_workflow(relative, path.read_text(encoding="utf-8"), failures))
    return budgets


def _read_calibration(failures: list[str]) -> dict[tuple[str, str], list[dict[str, Any]] | None]:
    path = ROOT / CALIBRATION
    if not path.is_file():
        failures.append(f"missing {CALIBRATION}")
        return {}
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        failures.append(f"{CALIBRATION} is not strict JSON: {error}")
        return {}
    if not isinstance(document, dict) or document.get("schema") != CALIBRATION_SCHEMA:
        failures.append(f"{CALIBRATION} must declare schema {CALIBRATION_SCHEMA!r}")
        return {}

    recorded: dict[tuple[str, str], list[dict[str, Any]] | None] = {}
    entries = document.get("jobs")
    if not isinstance(entries, list) or not entries:
        failures.append(f"{CALIBRATION} records no calibrated jobs")
        return {}
    for entry in entries:
        if not isinstance(entry, dict):
            failures.append(f"{CALIBRATION} contains a calibration entry that is not an object")
            continue
        workflow = entry.get("workflow")
        job = entry.get("job")
        observations = entry.get("observations")
        status = entry.get("status", "measured")
        if not isinstance(workflow, str) or not isinstance(job, str):
            failures.append(f"{CALIBRATION} contains an entry with no workflow/job identity")
            continue
        key = (workflow, job)
        if key in recorded:
            failures.append(f"{CALIBRATION} calibrates {workflow}:{job} twice")
            continue
        if status == "pending":
            if observations not in (None, []):
                failures.append(
                    f"{CALIBRATION} marks {workflow}:{job} pending but records observations; "
                    "a provisional budget must not impersonate a measurement"
                )
                continue
            if entry.get("release_blocking") is not True or not isinstance(
                entry.get("reason"), str
            ):
                failures.append(
                    f"{CALIBRATION} marks {workflow}:{job} pending without a "
                    "release-blocking reason"
                )
                continue
            recorded[key] = None
            continue
        if status != "measured":
            failures.append(
                f"{CALIBRATION} has unknown calibration status {status!r} for {workflow}:{job}"
            )
            continue
        if not isinstance(observations, list) or not observations:
            failures.append(
                f"{CALIBRATION} calibrates {workflow}:{job} with no observations; a budget derived "
                "from nothing is the defect this file exists to end"
            )
            continue
        clean: list[dict[str, Any]] = []
        for observation in observations:
            seconds = observation.get("seconds") if isinstance(observation, dict) else None
            if isinstance(seconds, bool) or not isinstance(seconds, int) or seconds < 0:
                failures.append(
                    f"{CALIBRATION} records a non-integer duration for {workflow}:{job}"
                )
                continue
            conclusion = observation.get("conclusion")
            if conclusion != REQUIRED_CONCLUSION:
                failures.append(
                    f"{CALIBRATION} calibrates {workflow}:{job} from run "
                    f"{observation.get('run_id')} job {observation.get('job_id')}, whose recorded "
                    f"conclusion is {conclusion!r} and not {REQUIRED_CONCLUSION!r}. A run that "
                    "failed or was cancelled stopped early, so its duration bounds the work from "
                    "below rather than measuring it: a two-minute setup failure would calibrate a "
                    "four-minute ceiling for a job that needs thirty-five"
                )
                continue
            clean.append(observation)
        if clean:
            recorded[key] = clean
    return recorded


def _check(failures: list[str], notes: list[str], *, require_calibrated: bool = False) -> None:
    budgets = _read_workflows(failures)
    recorded = _read_calibration(failures)
    if not budgets:
        failures.append(
            f"{WORKFLOW_DIR} declares no explicit `timeout-minutes` at all; this checker would "
            "pass vacuously, which is the one result it must never give"
        )
        return

    calibrated: set[tuple[str, str]] = set()
    for budget in sorted(budgets, key=lambda item: (item.workflow, item.job)):
        key = (budget.workflow, budget.job)
        if key not in recorded:
            failures.append(
                f"{budget.workflow}:{budget.line} sets `timeout-minutes: {budget.minutes}` on job "
                f"{budget.job!r} with no entry in {CALIBRATION}; record what the job actually "
                "costs on a hosted runner before declaring a ceiling for it"
            )
            continue
        calibrated.add(key)
        observations = recorded[key]
        if observations is None:
            if key not in PENDING_JOBS:
                failures.append(
                    f"{CALIBRATION} permits pending calibration only for explicit new CI jobs; "
                    f"{budget.workflow}:{budget.job} is not one"
                )
                continue
            if budget.minutes < PENDING_MINUTES:
                failures.append(
                    f"{budget.workflow}:{budget.line} has pending calibration but timeout "
                    f"{budget.minutes} is below the provisional {PENDING_MINUTES}-minute floor"
                )
                continue
            if require_calibrated:
                failures.append(
                    f"{budget.workflow}:{budget.job} remains pending hosted calibration; "
                    "release is blocked until successful observations replace it"
                )
                continue
            notes.append(
                f"{budget.workflow}:{budget.job} provisional budget {budget.minutes} min; "
                "pending hosted calibration is release-blocking"
            )
            continue
        assert observations
        worst = max(observations, key=lambda item: int(item["seconds"]))
        required = HALF_RULE_FACTOR * int(worst["seconds"]) / 60
        if budget.minutes < required:
            failures.append(
                f"{budget.workflow}:{budget.line} sets `timeout-minutes: {budget.minutes}` on job "
                f"{budget.job!r}, but its longest recorded hosted duration is "
                f"{int(worst['seconds'])}s ({int(worst['seconds']) / 60:.2f} min) in run "
                f"{worst.get('run_id')} job {worst.get('job_id')}. The half rule requires at least "
                f"{math.ceil(required)} minutes: a measured gate past half its budget is how "
                "v0.7.0's release run was cancelled (D-196)"
            )
            continue
        notes.append(
            f"{budget.workflow}:{budget.job} budget {budget.minutes} min against "
            f"{len(observations)} recorded run(s), worst {int(worst['seconds'])}s "
            f"({int(worst['seconds']) / 60:.2f} min); half rule needs {required:.2f} min"
        )

    for workflow, job in sorted(set(recorded) - calibrated):
        failures.append(
            f"{CALIBRATION} calibrates {workflow}:{job}, which declares no explicit "
            "`timeout-minutes`; a calibration entry that matches no budget is stale -- remove it "
            "or restore the budget it was recorded for"
        )


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--require-calibrated", action="store_true")
    args = parser.parse_args()
    failures: list[str] = []
    notes: list[str] = []
    _check(failures, notes, require_calibrated=args.require_calibrated)
    for note in notes:
        print(f"note: {note}")
    if failures:
        raise SystemExit("CI budget check failed:\n- " + "\n- ".join(failures))
    pending = sum("provisional budget" in note for note in notes)
    measured = len(notes) - pending
    print(
        "CI budget check passed. "
        f"{measured} measured calibration(s) clear the half rule; {pending} pending calibration(s)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
