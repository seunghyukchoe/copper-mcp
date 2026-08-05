#!/usr/bin/env python3
"""Check that every relative link in tracked Markdown resolves to a real path.

This checker owns one narrow question: does a relative Markdown link point at a
file or directory that exists in this repository? It deliberately refuses to
answer anything else. It does not fetch network URLs, does not validate heading
anchors, and does not judge whether a link is the *right* target -- only that a
reader following it offline will not land on a missing path.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Inline links `[text](target)` and reference definitions `[label]: target`.
INLINE_LINK = re.compile(
    r"\[[^\]]*\]\(\s*(<[^>]*>|[^()\s]*(?:\([^()]*\)[^()\s]*)*)\s*(?:\"[^\"]*\")?\)"
)
REFERENCE_LINK = re.compile(r"^\s{0,3}\[[^\]^\]]+\]:\s*(\S+)", re.MULTILINE)
FENCE = re.compile(r"^\s*(```|~~~)")

# Targets this checker does not own.
EXTERNAL_SCHEMES = (
    "http://",
    "https://",
    "mailto:",
    "ftp://",
    "tel:",
    "data:",
    "pcb://",
)


def _tracked_markdown() -> list[Path]:
    git = shutil.which("git")
    if git is None:
        raise SystemExit("git is required to enumerate tracked Markdown files")
    result = subprocess.run(  # noqa: S603
        [git, "ls-files", "-z", "*.md"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return [ROOT / name for name in result.stdout.split("\0") if name]


def _strip_code_fences(text: str) -> str:
    """Blank out fenced code blocks so example snippets are not link-checked."""
    lines = text.splitlines()
    kept: list[str] = []
    in_fence = False
    for line in lines:
        if FENCE.match(line):
            in_fence = not in_fence
            kept.append("")
            continue
        kept.append("" if in_fence else line)
    return "\n".join(kept)


def _targets(text: str) -> list[str]:
    found = [match.group(1) for match in INLINE_LINK.finditer(text)]
    found.extend(match.group(1) for match in REFERENCE_LINK.finditer(text))
    return found


def _is_external(target: str) -> bool:
    lowered = target.lower()
    return lowered.startswith(EXTERNAL_SCHEMES) or lowered.startswith("//")


def _check_document(path: Path, failures: list[str]) -> None:
    relative = path.relative_to(ROOT).as_posix()
    text = _strip_code_fences(path.read_text(encoding="utf-8"))
    for raw in _targets(text):
        target = raw.strip()
        if target.startswith("<") and target.endswith(">"):
            target = target[1:-1].strip()
        if not target or target.startswith("#") or _is_external(target):
            continue
        # Drop any fragment; anchors are out of scope for this checker.
        location = target.split("#", 1)[0]
        if not location:
            continue
        if location.startswith("/"):
            failures.append(f"{relative}: root-relative link {target!r} is not portable")
            continue
        resolved = (path.parent / location).resolve()
        try:
            resolved.relative_to(ROOT)
        except ValueError:
            failures.append(f"{relative}: link {target!r} escapes the repository")
            continue
        if not resolved.exists():
            failures.append(f"{relative}: link {target!r} does not resolve")


def main() -> int:
    failures: list[str] = []
    documents = _tracked_markdown()
    for path in documents:
        if path.is_file():
            _check_document(path, failures)
    if failures:
        raise SystemExit("Documentation link check failed:\n- " + "\n- ".join(failures))
    print(f"Documentation link check passed ({len(documents)} Markdown files).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
