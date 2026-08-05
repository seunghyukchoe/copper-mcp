#!/usr/bin/env python3
"""Check that every relative link in tracked Markdown resolves to a real path.

This checker owns one narrow question: does a relative Markdown link point at a
file or directory that exists in this repository? It deliberately refuses to
answer anything else. It does not fetch network URLs, does not validate heading
anchors, and does not judge whether a link is the *right* target -- only that a
reader following it offline will not land on a missing path.

One exception exists, and it is closed. `docs/ledgers/` is append-only: a
recorded entry is never rewritten, so a stale link target inside a historical
entry cannot be fixed in place. Those targets are listed in `EXEMPT_TARGETS`
below, each one keyed to its exact document and target string and each naming
the ledger entry that records where it was meant to point. The list is not a
suppression mechanism: an exemption that no longer matches a real link is itself
a failure, so a target cannot be silenced and then quietly forgotten, and a newly
broken link still fails until someone edits this file and says why.
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

# Unresolvable targets inside append-only ledger history, which may not be
# rewritten. Keyed by (document, exact target); the value names the ledger entry
# that records the corrected target. Adding to this list requires a ledger entry.
EXEMPT_TARGETS: dict[tuple[str, str], str] = {
    (
        "docs/ledgers/benchmark-ledger.md",
        "../../audio/fixtures/negotiated-crossing-v1.kicad_pcb",
    ): "B-036, corrected target recorded in B-076",
    (
        "docs/ledgers/benchmark-ledger.md",
        "HANDOFF-CODEX.md",
    ): "B-057, corrected target recorded in B-076",
}


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


def _check_document(path: Path, failures: list[str], used_exemptions: set[tuple[str, str]]) -> None:
    relative = path.relative_to(ROOT).as_posix()
    text = _strip_code_fences(path.read_text(encoding="utf-8"))
    for raw in _targets(text):
        target = raw.strip()
        if target.startswith("<") and target.endswith(">"):
            target = target[1:-1].strip()
        if not target or target.startswith("#") or _is_external(target):
            continue
        if (relative, target) in EXEMPT_TARGETS:
            used_exemptions.add((relative, target))
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
    used_exemptions: set[tuple[str, str]] = set()
    documents = _tracked_markdown()
    for path in documents:
        if path.is_file():
            _check_document(path, failures, used_exemptions)
    for key in sorted(set(EXEMPT_TARGETS) - used_exemptions):
        document, target = key
        failures.append(
            f"{document}: exemption for {target!r} ({EXEMPT_TARGETS[key]}) "
            "matched no link; remove it"
        )
    if failures:
        raise SystemExit("Documentation link check failed:\n- " + "\n- ".join(failures))
    print(
        f"Documentation link check passed ({len(documents)} Markdown files, "
        f"{len(EXEMPT_TARGETS)} recorded ledger-history exemptions)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
