#!/usr/bin/env python3
"""Check that relative Markdown links resolve, and name the record they point at.

This checker owns two narrow questions.

**Does the target exist?** Does a relative Markdown link point at a file or
directory that exists in this repository? It deliberately refuses to answer more
than that: it does not fetch network URLs and does not validate heading anchors.

**Does the label name the target?** A resolving link can still be wrong. When a
link's *label* names a record (`ADR-NNNN`, `D-NNN`, `R-NNN`, `SEC-NNN`, `B-NNN`)
and its *target* is a path that identifies a record, the two must agree. This
class survived target-only checking three times in two days -- `[ADR-0079]`
pointing at `0076-segment-assembled-edge-cuts-outline.md` in the changelog, the
same mismatch in `D-155`'s ledger row, and `[ADR-0077]` on the same file in the
Board IR contract -- because every one of those targets resolves. A reader
following the link lands on a real document that is not the record they were
told they were reading, and every downstream citation of the label inherits the
error.

The rule is deliberately narrow, so that it fires on a wrong record and stays
quiet on a legitimate cross-reference:

* A path under `docs/adr/` names one exact ADR, so a label carrying ADR numbers
  must carry that one among them. `[ADR-0076, ADR-0087](../adr/0076-...)` passes;
  `[ADR-0077](../adr/0076-...)` does not.
* A ledger path names a record *space*, not a row -- rows have no anchors -- so a
  label carrying ledger identifiers must carry at least one from that ledger's
  space. `[R-117](risk-register.md)` passes; `[R-117](decision-ledger.md)` does
  not.
* A label carrying no record identifier at all is never judged. Prose labels are
  the normal case and the checker has no opinion about them.

Two exception lists exist, and both are closed. `docs/ledgers/` is append-only: a
recorded entry is never rewritten, so neither a stale target nor a misnamed label
inside a historical entry can be fixed in place. `EXEMPT_TARGETS` covers the
former and `EXEMPT_LABEL_RECORDS` the latter, each entry keyed to its exact
document and link and each naming the ledger entry that records the correction.
Neither list is a suppression mechanism: an exemption that no longer matches a
real link is itself a failure, so an entry cannot be silenced and then quietly
forgotten, and a newly broken or newly misnamed link still fails until someone
edits this file and says why.

Untracked Markdown files are refused rather than skipped. The document set comes
from `git ls-files`, so a brand-new note that has not been staged yet would
otherwise be excluded from both the judgement and the count it prints -- a green
run that means nothing about the file it was meant to judge. Such files fail the
run by name until they are staged (or removed).
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Inline links `[text](target)` and reference definitions `[label]: target`.
INLINE_LINK = re.compile(
    r"\[([^\]]*)\]\(\s*(<[^>]*>|[^()\s]*(?:\([^()]*\)[^()\s]*)*)\s*(?:\"[^\"]*\")?\)"
)
REFERENCE_LINK = re.compile(r"^\s{0,3}\[([^\]^\]]+)\]:\s*(\S+)", re.MULTILINE)
FENCE = re.compile(r"^\s*(```|~~~)")

# A record identifier as it is written in prose. ADR numbers are four digits and
# ledger identifiers are three, zero-padded, per `docs/ledgers/README.md`.
RECORD_IDENTIFIER = re.compile(r"\b(ADR-\d{4}|D-\d{3}|R-\d{3}|SEC-\d{3}|B-\d{3})\b")

# An ADR filename opens with the number the document is.
ADR_FILENAME = re.compile(r"(\d{4})-.*\.md\Z")

# Each ledger owns one identifier space. The file names the space, not a row.
LEDGER_RECORD_SPACES = {
    "decision-ledger.md": "D",
    "risk-register.md": "R",
    "security-ledger.md": "SEC",
    "benchmark-ledger.md": "B",
}

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

# Links inside append-only ledger history whose label names one record and whose
# target names another. Keyed by (document, label identifier, exact target); the
# value names the ledger entry that records the correction. Same discipline as
# `EXEMPT_TARGETS`: adding to this list requires a ledger entry, and an entry
# that matches no real link is a failure.
EXEMPT_LABEL_RECORDS: dict[tuple[str, str, str], str] = {
    (
        "docs/ledgers/decision-ledger.md",
        "ADR-0079",
        "../adr/0076-segment-assembled-edge-cuts-outline.md",
    ): "D-155, corrected reference recorded in D-182",
}


def _tracked_markdown(root: Path = ROOT) -> list[Path]:
    git = shutil.which("git")
    if git is None:
        raise SystemExit("git is required to enumerate tracked Markdown files")
    result = subprocess.run(  # noqa: S603
        [git, "ls-files", "-z", "*.md"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return [root / name for name in result.stdout.split("\0") if name]


def _untracked_markdown(root: Path = ROOT) -> list[str]:
    """Markdown files present in the working tree but unknown to Git.

    A checker that reports over tracked files alone passes vacuously on a
    document the author just wrote but has not staged yet: the count it prints
    silently excludes the one file the run was meant to judge (#244). The loud
    answer is to refuse success while such files exist, naming each one,
    rather than to report "no broken links" over a set that omits them.
    """
    git = shutil.which("git")
    if git is None:
        raise SystemExit("git is required to enumerate untracked Markdown files")
    result = subprocess.run(  # noqa: S603
        [git, "ls-files", "--others", "--exclude-standard", "-z", "--", "*.md"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return sorted(name for name in result.stdout.split("\0") if name)


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


def _links(text: str) -> list[tuple[str, str]]:
    """Return every `(label, target)` pair, inline links first."""
    found = [(match.group(1), match.group(2)) for match in INLINE_LINK.finditer(text)]
    found.extend((match.group(1), match.group(2)) for match in REFERENCE_LINK.finditer(text))
    return found


def _is_external(target: str) -> bool:
    lowered = target.lower()
    return lowered.startswith(EXTERNAL_SCHEMES) or lowered.startswith("//")


def _record_space(identifier: str) -> str:
    """`ADR-0076` -> `ADR`, `SEC-134` -> `SEC`."""
    return identifier.rsplit("-", 1)[0]


def _record_named_by(resolved: Path) -> tuple[str, str | None] | None:
    """Describe the record a path identifies, as `(space, exact identifier)`.

    An ADR file names one exact ADR. A ledger file names an identifier space and
    nothing narrower, because ledger rows carry no anchors to link at. Anything
    else names no record and is returned as `None`.
    """
    if resolved.parent == (ROOT / "docs" / "adr").resolve():
        match = ADR_FILENAME.match(resolved.name)
        if match is not None:
            return "ADR", f"ADR-{match.group(1)}"
        return None
    if resolved.parent == (ROOT / "docs" / "ledgers").resolve():
        space = LEDGER_RECORD_SPACES.get(resolved.name)
        if space is not None:
            return space, None
    return None


def _check_label_names_target(
    relative: str,
    label: str,
    target: str,
    resolved: Path,
    failures: list[str],
    used_label_exemptions: set[tuple[str, str, str]],
) -> None:
    """Fail when a label names one record and the target identifies another."""
    named = _record_named_by(resolved)
    if named is None:
        return
    space, exact = named
    identifiers = RECORD_IDENTIFIER.findall(label)
    if not identifiers:
        # A prose label makes no claim about which record this is.
        return
    exempt = [
        identifier
        for identifier in identifiers
        if (relative, identifier, target) in EXEMPT_LABEL_RECORDS
    ]
    if exempt:
        for identifier in exempt:
            used_label_exemptions.add((relative, identifier, target))
        return
    in_space = [identifier for identifier in identifiers if _record_space(identifier) == space]
    if not in_space:
        failures.append(
            f"{relative}: link label {label!r} names {', '.join(sorted(set(identifiers)))} "
            f"but target {target!r} belongs to the {space}- record space"
        )
        return
    if exact is not None and exact not in in_space:
        failures.append(
            f"{relative}: link label {label!r} names {', '.join(sorted(set(in_space)))} "
            f"but target {target!r} is {exact}"
        )


def _check_document(
    path: Path,
    failures: list[str],
    used_exemptions: set[tuple[str, str]],
    used_label_exemptions: set[tuple[str, str, str]],
) -> None:
    relative = path.relative_to(ROOT).as_posix()
    text = _strip_code_fences(path.read_text(encoding="utf-8"))
    for label, raw in _links(text):
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
            continue
        _check_label_names_target(
            relative, label, target, resolved, failures, used_label_exemptions
        )


def main() -> int:
    failures: list[str] = []
    used_exemptions: set[tuple[str, str]] = set()
    used_label_exemptions: set[tuple[str, str, str]] = set()
    documents = _tracked_markdown()
    for path in documents:
        if path.is_file():
            _check_document(path, failures, used_exemptions, used_label_exemptions)
    for name in _untracked_markdown():
        failures.append(
            f"{name}: untracked Markdown file was not checked; stage it and re-run "
            "so the link check judges what exists rather than what is tracked"
        )
    for key in sorted(set(EXEMPT_TARGETS) - used_exemptions):
        document, target = key
        failures.append(
            f"{document}: exemption for {target!r} ({EXEMPT_TARGETS[key]}) "
            "matched no link; remove it"
        )
    for label_key in sorted(set(EXEMPT_LABEL_RECORDS) - used_label_exemptions):
        document, identifier, target = label_key
        failures.append(
            f"{document}: label exemption for {identifier} -> {target!r} "
            f"({EXEMPT_LABEL_RECORDS[label_key]}) matched no link; remove it"
        )
    if failures:
        raise SystemExit("Documentation link check failed:\n- " + "\n- ".join(failures))
    print(
        f"Documentation link check passed ({len(documents)} Markdown files; "
        f"recorded ledger-history exemptions: {len(EXEMPT_TARGETS)} target, "
        f"{len(EXEMPT_LABEL_RECORDS)} label)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
