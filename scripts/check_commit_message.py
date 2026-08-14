#!/usr/bin/env python3
"""Enforce a lightweight Conventional Commits subject line, locally and in CI.

Two modes, one pattern, and the second mode is the point.

    check_commit_message.py <commit-message-file>   # the commit-msg hook
    check_commit_message.py --range <a>..<b>        # every commit in a range

The file mode is the original: `pre-commit`'s `commit-msg` stage passes the
message file and a non-zero exit aborts the commit. It only ever protected
people who had the hook installed and who did not bypass it, and the class it
was supposed to stop reached `main` twice anyway -- a rejected hook whose
failure was swallowed by the loop driving the commit looks exactly like a hook
that passed. A check that runs on the author's machine at the author's
discretion is a convenience, not a gate.

The range mode is the server twin. CI runs it over the commits a pull request
proposes, where nothing can swallow the exit code.

Two rules about the range worth stating, because both are the difference
between a gate and a decoration:

- **A range that cannot be resolved is a failure, not a pass.** If `git` cannot
  read it -- a shallow checkout, a base commit that was never fetched -- the
  check reports that and exits non-zero rather than checking nothing.
- **An empty range is a failure too.** A pull request with no commits does not
  exist, so an empty reading means the range was wrong, and "no commits, so no
  bad commits" is precisely the shape of the swallowed rejection this mode was
  added to end. An absence is evidence only if the observation was capable of
  reporting a presence.

Merge commits are excluded from the range, matching the file mode's existing
`Merge ` exemption: their subjects are written by GitHub, not by an author.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

MAX_SUBJECT_BODY = 72

_PREFIX = (
    r"(?:feat|fix|docs|refactor|perf|test|build|ci|chore|revert|security)"
    r"(?:\([a-z0-9._/-]+\))?!?: "
)
PREFIX = re.compile(_PREFIX)
PATTERN = re.compile(_PREFIX + rf".{{1,{MAX_SUBJECT_BODY}}}$")

ADVICE = (
    "Commit subject must follow Conventional Commits, for example "
    "'feat(router): add deterministic net ordering'."
)


def subject_is_acceptable(subject: str) -> bool:
    """One place both modes ask the question, so the twin cannot drift from the hook."""

    return subject.startswith(("Merge ", 'Revert "')) or PATTERN.fullmatch(subject) is not None


def describe_refusal(subject: str) -> str:
    """Say which half of the convention a subject broke, so the fix is obvious from the log."""

    prefix = PREFIX.match(subject)
    if prefix is None:
        return "no `type(scope): ` prefix from the allowed set"
    body = subject[prefix.end() :]
    if not body:
        return "a prefix with no description after it"
    if len(body) > MAX_SUBJECT_BODY:
        return (
            f"the description is {len(body)} characters, over the "
            f"{MAX_SUBJECT_BODY}-character limit"
        )
    return "does not match the convention"


def _check_file(path: str) -> int:
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    subject = lines[0] if lines else ""
    if subject_is_acceptable(subject):
        return 0
    print(ADVICE, file=sys.stderr)
    return 1


def _git(*arguments: str) -> subprocess.CompletedProcess[str]:
    git = shutil.which("git")
    if git is None:
        raise SystemExit("git is required to check a commit range")
    return subprocess.run(  # noqa: S603
        [git, *arguments],
        capture_output=True,
        text=True,
        check=False,
    )


def _check_range(commit_range: str) -> int:
    listed = _git("rev-list", "--no-merges", commit_range)
    if listed.returncode != 0:
        print(
            f"Cannot resolve the commit range {commit_range!r}: "
            f"{listed.stderr.strip() or 'git failed'}. "
            "A range this check cannot read is a range it cannot check, so this is a failure "
            "rather than a pass -- fetch the base commit (checkout with fetch-depth: 0) and "
            "re-run.",
            file=sys.stderr,
        )
        return 1

    commits = [line for line in listed.stdout.split() if line]
    if not commits:
        print(
            f"The commit range {commit_range!r} contains no non-merge commits. A pull request "
            "with no commits does not exist, so this is a misresolved range rather than a clean "
            "result.",
            file=sys.stderr,
        )
        return 1

    failed: list[tuple[str, str]] = []
    for commit in commits:
        shown = _git("show", "--no-patch", "--format=%s", commit)
        if shown.returncode != 0:
            print(
                f"Cannot read the subject of {commit}: {shown.stderr.strip() or 'git failed'}",
                file=sys.stderr,
            )
            return 1
        subject = shown.stdout.splitlines()[0] if shown.stdout.splitlines() else ""
        if not subject_is_acceptable(subject):
            failed.append((commit, subject))

    if failed:
        print(ADVICE, file=sys.stderr)
        for commit, subject in failed:
            print(
                f"  {commit[:12]} {subject!r} — {describe_refusal(subject)}",
                file=sys.stderr,
            )
        return 1

    print(f"Commit message check passed over {len(commits)} commit(s) in {commit_range}.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("message_file", nargs="?", help="path to a commit message file")
    group.add_argument("--range", dest="commit_range", help="a git commit range, for example a..b")
    arguments = parser.parse_args(argv)

    if arguments.commit_range is not None:
        return _check_range(arguments.commit_range)
    return _check_file(arguments.message_file)


if __name__ == "__main__":
    raise SystemExit(main())
