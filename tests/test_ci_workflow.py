"""What the hosted CI workflow actually runs.

Two checkers -- `check_audio_benchmarks.py` and `check_circuit_intents.py` --
lived in `make lint` from before this workflow existed and had **never run
hosted**. Nothing said so, and nothing would have said so: a checker that is not
in the workflow file produces exactly the same green pull request as one that is
and passes. The commit-message checker had the same shape one layer out, running
only as a client-side `commit-msg` hook whose rejection the loop driving the
commit could swallow -- which it did, twice.

The class is not "someone forgot". It is that a *wiring* is invisible to every
other test in this repository: `tests/test_check_ledgers.py` proves the ledger
gate bites, and proves nothing at all about whether CI ever calls it. So the
wiring gets a test of its own, and the test reads the workflow file rather than
trusting a comment in it.

This asserts presence, and presence is all it can assert. That a step *fails*
when its input is broken is proved by each checker's own tests; that the step
*exists* is proved here; that it runs on a real hosted runner is proved by the
pull request this lands in and by nothing offline.
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CI = ROOT / ".github" / "workflows" / "ci.yml"
WORKFLOW = CI.read_text(encoding="utf-8")

# Only what the workflow actually *executes*. Reading the whole file would let a
# checker named in a comment satisfy the assertion, which is not a hypothetical:
# mutating the `check_ci_budgets.py` step to a no-op left this test green,
# because the script is also named in the `timeout-minutes` comment that explains
# where the budget came from. The mutant found it; this is the fix.
#
# Single-line `run:` steps only. A `run: |` block would need a real reader, and
# `test_no_step_hides_its_command_in_a_block_scalar` fails rather than letting one
# slip past unread.
RUN_COMMANDS = "\n".join(
    line.split("run:", 1)[1] for line in WORKFLOW.splitlines() if line.strip().startswith("run:")
)


# Why each checker has to be here, kept beside the assertion so a future reader
# knows which of these were the audit's finding and which were already fine.
WHY = {
    "check_version.py": "the metadata gate, wired since this workflow was written",
    "check_ledgers.py": (
        "the ledger gate, which now also carries the published-release check (P0.2)"
    ),
    "check_adr_numbers.py": "ADR number allocation",
    "check_doc_links.py": "documentation links",
    "check_schema_sets.py": "published schema accepted sets",
    "check_drc_comparability.py": "published benchmark DRC comparability literals (P4.1)",
    "check_ci_budgets.py": "the CI timeout budgets declared in this very directory (P0.3)",
    "check_audio_benchmarks.py": (
        "in `make lint` since before this workflow existed and never hosted until P0.5"
    ),
    "check_circuit_intents.py": (
        "the same, and it needs PYTHONPATH=src because it imports the package"
    ),
    "check_secrets.py": "the repository secret scan",
}

# Exactly what `make lint` runs. Asserted as an equality rather than as a subset,
# because the audit's finding runs the other way too: dropping a checker from
# `lint` is as silent as never adding it to CI, and a contributor running
# `make lint` before pushing should be running what CI runs.
LINT_SCRIPTS = frozenset(
    {
        "check_version.py",
        "check_ledgers.py",
        "check_adr_numbers.py",
        "check_doc_links.py",
        "check_schema_sets.py",
        "check_drc_comparability.py",
        "check_ci_budgets.py",
        "check_audio_benchmarks.py",
        "check_circuit_intents.py",
    }
)


@pytest.mark.parametrize("script", sorted(WHY))
def test_every_repository_checker_runs_in_ci(script: str) -> None:
    """`make lint` and the hosted workflow must not disagree about what is checked."""

    assert f"scripts/{script}" in RUN_COMMANDS, f"{script} does not run in CI ({WHY[script]})"


def test_the_commit_message_checker_runs_over_the_pull_requests_own_commits() -> None:
    """P0.4: the server twin, invoked in range mode on the commits under review.

    The file mode alone would be a hook with extra steps. What makes this a gate
    is that it reads the range between the pull request's base and head, where
    nothing can swallow the exit code.
    """

    assert "scripts/check_commit_message.py --range" in RUN_COMMANDS
    assert "github.event.pull_request.base.sha" in WORKFLOW
    assert "github.event.pull_request.head.sha" in WORKFLOW
    assert "github.event_name == 'pull_request'" in WORKFLOW


def test_the_full_checkout_the_range_and_the_tag_reads_both_need_is_still_there() -> None:
    """`fetch-depth: 0`, and two independent reasons for it.

    `check_schema_sets.py` reads every release tag with `git show`, and the
    commit-message range needs the pull request's base commit. A shallow checkout
    breaks the first loudly and would make the second refuse rather than pass --
    but only because the script treats an unresolvable range as a failure, which
    is a property worth keeping tied to this line.
    """

    assert "fetch-depth: 0" in WORKFLOW


def test_b141_ancestry_guard_receives_the_exact_pull_request_base() -> None:
    """PR validation must not mistake the synthetic merge ref for the durable default branch."""

    expected_step = (
        "      - name: Unit tests\n"
        "        env:\n"
        "          COPPER_MCP_DEFAULT_BRANCH_REF: "
        "${{ github.event.pull_request.base.sha || "
        "format('refs/remotes/origin/{0}', github.event.repository.default_branch) }}\n"
        "        run: python -m pytest"
    )
    assert expected_step in WORKFLOW


def test_the_lint_target_and_the_workflow_check_the_same_scripts() -> None:
    """Neither list may quietly become the shorter one.

    The audit's finding was one-directional -- two checkers in `make lint` and not
    in CI -- but the reverse gap is just as silent, and a contributor running
    `make lint` before pushing should be running what CI runs.
    """

    lint = (ROOT / "Makefile").read_text(encoding="utf-8").split("lint:", 1)[1].split("\n\n", 1)[0]
    in_lint = {
        line.split("scripts/")[1].split()[0] for line in lint.splitlines() if "scripts/" in line
    }

    assert in_lint == set(LINT_SCRIPTS), (
        "the `make lint` target's checker list moved; update LINT_SCRIPTS in the same change, and "
        "make sure the workflow moved with it"
    )
    for script in sorted(in_lint):
        assert f"scripts/{script}" in RUN_COMMANDS, f"{script} is in `make lint` but not in CI"


def test_no_step_hides_its_command_in_a_block_scalar() -> None:
    """`RUN_COMMANDS` reads single-line `run:` steps, so a block scalar must fail here.

    A `run: |` step would be read as an empty command and every presence
    assertion above would go quiet -- the same shape as a parser that ignores what
    it does not understand. If one is ever needed, extend the reader in the same
    change rather than deleting this test.
    """

    assert "run: |" not in WORKFLOW
    assert "run: >" not in WORKFLOW
    assert RUN_COMMANDS.count("scripts/") >= len(LINT_SCRIPTS)
