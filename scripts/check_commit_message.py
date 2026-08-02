#!/usr/bin/env python3
"""Enforce a lightweight Conventional Commits subject line."""

from __future__ import annotations

import re
import sys
from pathlib import Path

PATTERN = re.compile(
    r"^(?:feat|fix|docs|refactor|perf|test|build|ci|chore|revert|security)"
    r"(?:\([a-z0-9._/-]+\))?!?: .{1,72}$"
)


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: check_commit_message.py <commit-message-file>")
    subject = Path(sys.argv[1]).read_text(encoding="utf-8").splitlines()[0]
    if subject.startswith(("Merge ", 'Revert "')) or PATTERN.fullmatch(subject):
        return 0
    print(
        "Commit subject must follow Conventional Commits, for example "
        "'feat(router): add deterministic net ordering'.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
