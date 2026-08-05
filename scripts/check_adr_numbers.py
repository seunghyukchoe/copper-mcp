#!/usr/bin/env python3
"""Check that every ADR number is allocated once and indexed exactly once.

An ADR number is a permanent external reference: it is cited from the decision
ledger, from other ADRs, from commit messages, and from issues. Nothing enforced
that it was unique. Two branches that each create `docs/adr/0066-<their-slug>.md`
produce no merge conflict, because the filenames differ -- Git merges both files
happily and the repository ends up with one number naming two different
decisions. That happened here: `0066-atomic-route-bundle-preview.md`,
`0066-bounded-ordered-layer-routing.md`, and `0066-route-aware-placement-ranking.md`
were each created as ADR-0066 on three concurrent branches, and the two later
ones had to be renumbered by hand after the fact.

This checker answers four questions and nothing else:

1. Does each ADR file's number appear on exactly one file?
2. Does each ADR's own `# ADR-NNNN:` heading match its filename?
3. Does each ADR appear exactly once in the `## Index` table of
   `docs/adr/README.md`, and does every index row point at a file that exists?
4. Does the "next unused number" the index advertises match reality?

Gaps are reported as information, never as failures. `0027` and `0067` are
permanently unused because the ADRs that claimed them never landed, and a number
is never recycled so that a stale external reference resolves to nothing rather
than to an unrelated decision.

Question 4 is the one that actually prevents the collision. The advertised next
number lives on a single line, so two branches that both allocate it now conflict
textually and Git refuses to merge them silently.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ADR_DIR = "docs/adr"
ADR_INDEX = "docs/adr/README.md"

# Files in `docs/adr/` that are not ADRs.
NON_ADR_FILES = frozenset({"README.md", "template.md"})

ADR_FILENAME = re.compile(r"^(?P<number>\d{4})-(?P<slug>[a-z0-9]+(?:-[a-z0-9*]+)*)\.md$")
ADR_HEADING = re.compile(r"^#\s+ADR-(?P<number>\d{4}):\s+\S")
INDEX_SECTION = re.compile(r"^##\s+Index\s*$")
NEXT_HEADING = re.compile(r"^##\s+")
# `| [0066](0066-atomic-route-bundle-preview.md) | Title | Accepted |`
INDEX_ROW = re.compile(r"^\|\s*\[(?P<number>\d{4})\]\((?P<target>[^)]+)\)\s*\|")
# `1. Copy [`template.md`](template.md) and assign the next unused number — currently **0069**.`
NEXT_UNUSED = re.compile(r"next unused number\s*—\s*currently\s*\*\*(?P<number>\d{4})\*\*")


@dataclass(frozen=True)
class AdrFile:
    """One ADR on disk, with the number claimed by its filename."""

    number: int
    relative: str
    path: Path


def _discover_adrs(failures: list[str]) -> list[AdrFile]:
    directory = ROOT / ADR_DIR
    if not directory.is_dir():
        failures.append(f"missing {ADR_DIR}")
        return []
    found: list[AdrFile] = []
    for path in sorted(directory.glob("*.md")):
        if path.name in NON_ADR_FILES:
            continue
        match = ADR_FILENAME.match(path.name)
        if match is None:
            failures.append(
                f"{ADR_DIR}/{path.name} is not named `NNNN-kebab-case-slug.md`; "
                "an ADR number must be four digits"
            )
            continue
        found.append(
            AdrFile(number=int(match.group("number")), relative=f"{ADR_DIR}/{path.name}", path=path)
        )
    return found


def _check_unique_numbers(adrs: list[AdrFile], failures: list[str]) -> dict[int, AdrFile]:
    """Reject a number claimed by more than one file, in filename order."""
    by_number: dict[int, AdrFile] = {}
    for adr in adrs:
        first = by_number.get(adr.number)
        if first is None:
            by_number[adr.number] = adr
            continue
        failures.append(
            f"{adr.relative} reuses ADR number {adr.number:04d}, which is already allocated by "
            f"{first.relative}; allocate the next unused number instead"
        )
    return by_number


def _check_headings(adrs: list[AdrFile], failures: list[str]) -> None:
    """Reject an ADR whose own heading disagrees with its filename."""
    for adr in adrs:
        heading: str | None = None
        for line in adr.path.read_text(encoding="utf-8").splitlines():
            if line.startswith("# "):
                heading = line.rstrip()
                break
        if heading is None:
            failures.append(f"{adr.relative} has no `# ADR-NNNN: <title>` heading")
            continue
        match = ADR_HEADING.match(heading)
        if match is None:
            failures.append(f"{adr.relative} heading {heading!r} is not `# ADR-NNNN: <title>`")
            continue
        if int(match.group("number")) != adr.number:
            failures.append(
                f"{adr.relative} is headed ADR-{match.group('number')} but its filename allocates "
                f"ADR-{adr.number:04d}; a renumbered ADR must have both changed"
            )


def _index_rows(text: str, failures: list[str]) -> list[tuple[int, int, str]]:
    """Return `(line, number, target)` for every row of the `## Index` table."""
    rows: list[tuple[int, int, str]] = []
    in_index = False
    for index, line in enumerate(text.splitlines(), start=1):
        if INDEX_SECTION.match(line):
            in_index = True
            continue
        if in_index and NEXT_HEADING.match(line):
            break
        if not in_index:
            continue
        match = INDEX_ROW.match(line)
        if match is not None:
            rows.append((index, int(match.group("number")), match.group("target")))
    if not in_index:
        failures.append(f"{ADR_INDEX} has no `## Index` section")
    return rows


def _check_index(
    by_number: dict[int, AdrFile], text: str, failures: list[str], notes: list[str]
) -> None:
    rows = _index_rows(text, failures)
    indexed: dict[int, int] = {}
    for line, number, target in rows:
        first = indexed.get(number)
        if first is not None:
            failures.append(
                f"{ADR_INDEX}:{line} indexes ADR-{number:04d} again; it is already indexed at "
                f"line {first}"
            )
            continue
        indexed[number] = line
        adr = by_number.get(number)
        if adr is None:
            failures.append(
                f"{ADR_INDEX}:{line} indexes ADR-{number:04d}, but no such file exists in "
                f"{ADR_DIR}/"
            )
            continue
        if target != adr.path.name:
            failures.append(
                f"{ADR_INDEX}:{line} links ADR-{number:04d} to {target!r}, but that ADR is "
                f"{adr.path.name}"
            )

    for number in sorted(set(by_number) - set(indexed)):
        failures.append(
            f"{ADR_INDEX} does not index {by_number[number].relative}; every ADR appears in the "
            "index exactly once"
        )

    ordered = [number for _, number, _ in rows]
    for previous, current in pairwise(ordered):
        if current <= previous:
            failures.append(
                f"{ADR_INDEX} lists ADR-{current:04d} after ADR-{previous:04d}; the index is "
                "ordered by number"
            )
            break

    if by_number:
        allocated = sorted(by_number)
        gaps = [
            f"ADR-{number:04d}"
            for number in range(allocated[0], allocated[-1] + 1)
            if number not in by_number
        ]
        if gaps:
            notes.append(
                f"{ADR_DIR}/: {len(gaps)} unallocated number(s) — {', '.join(gaps)}; "
                "a spent number is never recycled"
            )


def _check_next_unused(by_number: dict[int, AdrFile], text: str, failures: list[str]) -> None:
    match = NEXT_UNUSED.search(text)
    if match is None:
        failures.append(f"{ADR_INDEX} does not advertise the next unused ADR number")
        return
    if not by_number:
        return
    expected = max(by_number) + 1
    if int(match.group("number")) != expected:
        failures.append(
            f"{ADR_INDEX} advertises {match.group('number')} as the next unused ADR number, but "
            f"the highest allocated is ADR-{max(by_number):04d}, so it is {expected:04d}"
        )


def main() -> int:
    failures: list[str] = []
    notes: list[str] = []
    adrs = _discover_adrs(failures)
    by_number = _check_unique_numbers(adrs, failures)
    _check_headings(adrs, failures)
    index_path = ROOT / ADR_INDEX
    if not index_path.is_file():
        failures.append(f"missing {ADR_INDEX}")
    else:
        text = index_path.read_text(encoding="utf-8")
        _check_index(by_number, text, failures, notes)
        _check_next_unused(by_number, text, failures)
    for note in notes:
        print(f"note: {note}")
    if failures:
        raise SystemExit("ADR number check failed:\n- " + "\n- ".join(failures))
    highest = f"ADR-{max(by_number):04d}" if by_number else "none"
    print(f"ADR number check passed ({len(by_number)} records, highest allocated {highest}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
