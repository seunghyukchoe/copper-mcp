"""The commit-subject checker, invoked as CI invokes it.

`scripts/check_commit_message.py` existed only as a `commit-msg` hook, which
protects an author who installed it and did not bypass it -- and a hook
rejection swallowed by the loop driving the commit is indistinguishable from a
hook that passed. The class reached `main` twice that way. `P0.4` adds the
server twin, and these tests exercise the script the way the workflow does: as a
subprocess, by exit code, over real commits in a real repository.

The cases are organised by what would make the twin decorative:

* it could accept an over-long subject, which is the concrete failure the
  audit names (`test_an_over_long_subject_fails_the_range`);
* it could drift from the hook, so that the two modes disagree about the same
  string (`test_both_modes_answer_the_same_question`);
* it could pass on a range it could not resolve, or on an empty one -- the two
  readings that look identical to a clean run and are not
  (`test_an_unresolvable_range_fails`, `test_an_empty_range_fails`).
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_commit_message.py"
GIT = shutil.which("git")

# 73 characters after `feat: `, one past the limit the pattern allows.
OVER_LONG = "feat: " + "a" * 73
ACCEPTABLE = "feat(router): add deterministic net ordering"

pytestmark = pytest.mark.skipif(GIT is None, reason="git is required to build a commit range")


def _run(*arguments: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed local interpreter and repository script
        [sys.executable, str(SCRIPT), *arguments],
        capture_output=True,
        text=True,
        cwd=cwd,
        check=False,
    )


def _git(repository: Path, *arguments: str) -> str:
    """Every git call in this module goes through here: fixed argv, writes only below tmp_path."""

    assert GIT is not None
    return subprocess.run(  # noqa: S603 - fixed local Git argv; does not contact a remote
        [GIT, *arguments],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _repository(tmp_path: Path, subjects: list[str]) -> tuple[Path, str, str]:
    """A throwaway repository whose commits carry exactly the given subjects."""

    repository = tmp_path / "repo"
    repository.mkdir()
    _git(repository, "init", "--quiet", "--initial-branch", "main")
    _git(repository, "config", "user.email", "test@example.invalid")
    _git(repository, "config", "user.name", "Test")
    _git(repository, "config", "commit.gpgsign", "false")

    revisions: list[str] = []
    for index, subject in enumerate(["chore: base", *subjects]):
        (repository / f"file{index}.txt").write_text(str(index), encoding="utf-8")
        _git(repository, "add", "-A")
        _git(repository, "commit", "--quiet", "--no-verify", "-m", subject)
        revisions.append(_git(repository, "rev-parse", "HEAD"))
    return repository, revisions[0], revisions[-1]


# ---------------------------------------------------------------------------
# The file mode, unchanged
# ---------------------------------------------------------------------------


def test_an_acceptable_subject_passes_in_file_mode(tmp_path: Path) -> None:
    message = tmp_path / "COMMIT_EDITMSG"
    message.write_text(f"{ACCEPTABLE}\n\nA body.\n", encoding="utf-8")

    assert _run(str(message)).returncode == 0


def test_an_over_long_subject_fails_in_file_mode(tmp_path: Path) -> None:
    message = tmp_path / "COMMIT_EDITMSG"
    message.write_text(f"{OVER_LONG}\n", encoding="utf-8")

    result = _run(str(message))

    assert result.returncode == 1
    assert "Conventional Commits" in result.stderr


# ---------------------------------------------------------------------------
# The range mode, which is what CI runs
# ---------------------------------------------------------------------------


def test_a_clean_range_passes(tmp_path: Path) -> None:
    repository, base, head = _repository(tmp_path, [ACCEPTABLE, "fix(ir): tighten a bound"])

    result = _run("--range", f"{base}..{head}", cwd=repository)

    assert result.returncode == 0
    assert "2 commit(s)" in result.stdout


def test_an_over_long_subject_fails_the_range(tmp_path: Path) -> None:
    """The proving check for P0.4, run through the script rather than around it."""

    repository, base, head = _repository(tmp_path, [ACCEPTABLE, OVER_LONG])

    result = _run("--range", f"{base}..{head}", cwd=repository)

    assert result.returncode == 1
    assert "Conventional Commits" in result.stderr
    assert "the description is 73 characters, over the 72-character limit" in result.stderr
    # The clean commit is not reported; only the offender is.
    assert result.stderr.count("—") == 1


def test_a_subject_with_no_conventional_prefix_fails_the_range(tmp_path: Path) -> None:
    repository, base, head = _repository(tmp_path, ["updated some things"])

    result = _run("--range", f"{base}..{head}", cwd=repository)

    assert result.returncode == 1
    assert "no `type(scope): ` prefix from the allowed set" in result.stderr


def test_a_merge_commit_is_not_judged(tmp_path: Path) -> None:
    """Merge subjects are written by the forge, not by an author."""

    repository, base, _ = _repository(tmp_path, [ACCEPTABLE])
    _git(repository, "checkout", "--quiet", "-b", "side", base)
    (repository / "side.txt").write_text("side", encoding="utf-8")
    _git(repository, "add", "-A")
    _git(repository, "commit", "--quiet", "--no-verify", "-m", "docs: a side note")
    _git(repository, "checkout", "--quiet", "main")
    _git(repository, "merge", "--quiet", "--no-ff", "-m", "Merge branch 'side'", "side")
    merged = _git(repository, "rev-parse", "HEAD")

    result = _run("--range", f"{base}..{merged}", cwd=repository)

    # The merge subject would fail the pattern; it is excluded rather than exempted at read time,
    # so the two authored commits are what the range reports.
    assert result.returncode == 0
    assert "2 commit(s)" in result.stdout


def test_an_unresolvable_range_fails(tmp_path: Path) -> None:
    """A range the check cannot read is a range it cannot check.

    A shallow checkout, or a base commit nobody fetched, is exactly the shape of
    a green run that observed nothing -- which is the swallowed hook rejection
    again, one layer up.
    """

    repository, _, _ = _repository(tmp_path, [ACCEPTABLE])

    result = _run("--range", "0" * 40 + "..HEAD", cwd=repository)

    assert result.returncode == 1
    assert "Cannot resolve the commit range" in result.stderr
    assert "fetch-depth: 0" in result.stderr


def test_an_empty_range_fails(tmp_path: Path) -> None:
    """A pull request with no commits does not exist, so an empty reading is a wrong range."""

    repository, _, head = _repository(tmp_path, [ACCEPTABLE])

    result = _run("--range", f"{head}..{head}", cwd=repository)

    assert result.returncode == 1
    assert "no non-merge commits" in result.stderr


# ---------------------------------------------------------------------------
# The two modes cannot drift
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "subject",
    [
        ACCEPTABLE,
        OVER_LONG,
        "feat: ok",
        "feat!: a breaking change",
        "feat(board-ir)!: a scoped breaking change",
        "Merge branch 'side'",
        'Revert "feat: something"',
        "updated some things",
        "feat:",
        "feat: ",
        "FEAT: shouting",
    ],
)
def test_both_modes_answer_the_same_question(tmp_path: Path, subject: str) -> None:
    """One predicate, two entry points -- so the CI twin cannot become more lenient.

    A twin that judged differently from the hook would be worse than no twin:
    contributors would learn to trust a local pass that CI does not honour.
    """

    message = tmp_path / "COMMIT_EDITMSG"
    message.write_text(f"{subject}\n", encoding="utf-8")
    file_mode = _run(str(message)).returncode

    if subject.strip() and "\n" not in subject:
        repository, base, head = _repository(tmp_path, [subject])
        range_mode = _run("--range", f"{base}..{head}", cwd=repository).returncode
        assert file_mode == range_mode, subject


def test_the_script_refuses_to_run_with_no_arguments() -> None:
    """Argument parsing is a gate too: a no-op invocation must not exit zero."""

    assert _run().returncode != 0
