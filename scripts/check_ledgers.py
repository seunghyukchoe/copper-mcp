#!/usr/bin/env python3
"""Check that required project ledgers exist and remain structurally usable.

This checker owns three narrow questions.

1. Does every required ledger exist and carry its heading?
2. Does every committed benchmark artifact match its own self-digest?
3. Is every ledger identifier allocated exactly once, in order, and does the
   allocation registry in `docs/ledgers/README.md` still describe reality?

The third question is the reason this file grew. Ledger IDs were previously
enforced by review alone, and review does not see a merge: two branches that
each append a row numbered `D-137` produce no textual conflict, because the rows
land in different places in the same table. Git merges both, and the ledger
silently contains one number naming two decisions. The same failure happened to
ADR numbers (`docs/adr/0066-*`, see `scripts/check_adr_numbers.py`).

The rules, and what each one deliberately does *not* do:

- **Duplicates fail.** A number allocated twice is the defect this checker
  exists to catch, so it is an error rather than a warning.
- **Gaps do not fail.** `D-039`, `SEC-021`, and `B-006` are permanent: their
  entries were withdrawn before merge and the numbers are never recycled, so a
  gap is normal ledger history and is reported as information only.
- **Order is checked, not inferred.** The decision, risk, and security ledgers
  are strictly increasing in document order, so a row that goes backwards is an
  error. The benchmark ledger is organized by topic and is *not* monotonic in
  document order, so no ordering claim is made about it.
- **Two closed exception lists, never a suppression switch.** `REPLAY_SUB_ENTRIES`
  names each benchmark replay that legitimately reuses its parent's number, and
  `RECORDED_COLLISIONS` names each historical double-allocation that a dated
  correction note already records. Both are keyed to exact text, and an entry in
  either list that stops matching a real ledger line is itself a failure -- so an
  exception cannot be added and then quietly forgotten, and a genuinely new
  duplicate still fails until someone edits this file and says why. A recorded
  collision also excuses the document order it displaced, because merging two
  independently appended blocks necessarily leaves one behind the other, and one
  correction note records both facts.
- **The allocation registry is verified.** The `Highest allocated` / `Next free`
  table in `docs/ledgers/README.md` must equal what the ledgers actually contain.
  That table is one line per ID space, so two branches that both allocate the
  next number now collide *textually* on that line and Git refuses to merge them
  silently -- which is the collision this whole module is trying to prevent.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = {
    "docs/ledgers/decision-ledger.md": "# Decision Ledger",
    "docs/ledgers/risk-register.md": "# Risk Register",
    "docs/ledgers/release-ledger.md": "# Release Ledger",
    "docs/ledgers/benchmark-ledger.md": "# Benchmark Ledger",
    "docs/ledgers/security-ledger.md": "# Security Review Ledger",
}
MAX_BENCHMARK_BYTES = 2_000_000

LEDGER_README = "docs/ledgers/README.md"


@dataclass(frozen=True)
class LedgerIdSpace:
    """One ledger's private ID space and how its identifiers are written down."""

    document: str
    prefix: str
    # `table` ledgers put the ID in the first cell of a Markdown table row.
    # `heading` ledgers put it at the start of a `###`/`####` section heading.
    kind: Literal["table", "heading"]
    # Whether identifiers must strictly increase in document order.
    ordered: bool


LEDGER_ID_SPACES: tuple[LedgerIdSpace, ...] = (
    LedgerIdSpace("docs/ledgers/decision-ledger.md", "D", "table", ordered=True),
    LedgerIdSpace("docs/ledgers/risk-register.md", "R", "table", ordered=True),
    LedgerIdSpace("docs/ledgers/security-ledger.md", "SEC", "table", ordered=True),
    # Organized by topic, so B- numbers are intentionally not monotonic here.
    LedgerIdSpace("docs/ledgers/benchmark-ledger.md", "B", "heading", ordered=False),
)

# Benchmark replays that legitimately reuse their parent benchmark's number, as
# `####` sub-entries beneath the `###` entry they replay. Keyed by (document,
# exact heading line); the value says what the replay re-measures. A replay that
# measures something new gets a new B- number instead and does not belong here.
#
# Being listed here is necessary but not sufficient: the heading must still be a
# `####` sub-entry, and the `###` entry it replays must already appear earlier in
# the same document. An accidental duplicate therefore cannot be legalized by
# adding it to this list alone.
REPLAY_SUB_ENTRIES: dict[tuple[str, str], str] = {
    (
        "docs/ledgers/benchmark-ledger.md",
        "#### B-007 replay — current-contract evidence at `d6ee84b`",
    ): "B-007 replayed after the board/snapshot revision-precondition hardening",
    (
        "docs/ledgers/benchmark-ledger.md",
        "#### B-034 replay — current schema-bound payload evidence",
    ): "B-034 replayed after the in-toto resource-digest schema became closed",
    (
        "docs/ledgers/benchmark-ledger.md",
        "#### B-035 replay — current implementation provenance",
    ): "B-035 replayed from the implementation commit",
    (
        "docs/ledgers/benchmark-ledger.md",
        "#### B-022 — complete workspace-preservation replay",
    ): "B-022 replayed with complete workspace-preservation evidence",
    (
        "docs/ledgers/benchmark-ledger.md",
        "#### B-026 — live layered CAS and client-closure replay",
    ): "B-026 replayed for live CAS and client closure",
    (
        "docs/ledgers/benchmark-ledger.md",
        "#### B-027 — layered topology verifier replay",
    ): "B-027 replayed against the layered topology verifier",
    (
        "docs/ledgers/benchmark-ledger.md",
        "#### B-031 — deterministic multi-pin ordering replay",
    ): "B-031 replayed for deterministic multi-pin ordering",
    (
        "docs/ledgers/benchmark-ledger.md",
        "#### B-032 — exact layered DRC provenance replay",
    ): "B-032 replayed with exact layered DRC provenance",
    (
        "docs/ledgers/benchmark-ledger.md",
        "#### B-033 — exact spatial-index identity replay",
    ): "B-033 replayed with exact spatial-index identity",
    (
        "docs/ledgers/benchmark-ledger.md",
        "#### B-090 — declared non-claim surface replay",
    ): (
        "B-090 replayed after ADR-0084 added one declared non-claim field to the contract "
        "module, which the non_claim_inference scenario counts"
    ),
    # ADR-0087 re-derived the router's obstacle budget, split it in three, and scoped the
    # obstacle model to a routing region. Five committed artifacts echo `AStarSettings` or a
    # candidate identity that records it, so each was regenerated rather than edited. Three of
    # them replay a `###` parent and are listed here; the other two regenerate artifacts that
    # are themselves `####` sub-entries, which cannot be replayed under their own number, so
    # they took B-097 and B-098. Only the B-088 replay carries a behavioural delta: the routed
    # count is unchanged and 9 of 11 `no_path` refusals are reclassified `no_path_in_region`.
    (
        "docs/ledgers/benchmark-ledger.md",
        "#### B-087 — region-scoped obstacle model replay",
    ): "B-087 replayed after the router's default budgets and obstacle model changed",
    (
        "docs/ledgers/benchmark-ledger.md",
        "#### B-095 — region-scoped obstacle model replay",
    ): "B-095 replayed after the router's default budgets and obstacle model changed",
    (
        "docs/ledgers/benchmark-ledger.md",
        "#### B-088 — region-scoped obstacle model replay",
    ): (
        "B-088 replayed under the region-scoped obstacle model: the routed count is unchanged "
        "and 9 of 11 no_path refusals are reclassified no_path_in_region"
    ),
}

# Historical double-allocations that predate this checker. Ledgers are
# append-only, so a collision is recorded by a dated correction note rather than
# repaired by renumbering a merged row -- renumbering would break every external
# reference to the number and rewrite history to hide that the collision
# happened. Keyed by (document, identifier); the value names the correction.
#
# This list is closed and is not a way to accept new collisions: a new duplicate
# fails until someone lands a dated correction note and records it here.
RECORDED_COLLISIONS: dict[tuple[str, str], str] = {
    (
        "docs/ledgers/decision-ledger.md",
        "D-137",
    ): "documentation reorganization vs the ordered-layer seam; recorded by D-143",
    (
        "docs/ledgers/decision-ledger.md",
        "D-139",
    ): "route-aware placement scoring vs the ordered-layer correction; recorded by D-143",
    (
        "docs/ledgers/decision-ledger.md",
        "D-140",
    ): "route-aware interpretation correction vs the route bundle; recorded by D-143",
    (
        "docs/ledgers/benchmark-ledger.md",
        "B-076",
    ): "recorded-link corrections vs the ordered-layer oracle; recorded by B-084",
    (
        "docs/ledgers/benchmark-ledger.md",
        "B-078",
    ): "route-aware selection replay vs the capped ordered-layer differential; recorded by B-084",
    (
        "docs/ledgers/benchmark-ledger.md",
        "B-082",
    ): "route-aware claim correction vs the route-bundle provenance; recorded by B-084",
}

# `| [Decision ledger](decision-ledger.md) | `D-` | `D-137` | `D-138` |`
ALLOCATION_ROW = re.compile(
    r"^\|[^|]*\|\s*`(?P<prefix>[A-Z]+)-`\s*\|\s*`(?P<highest>[A-Z]+-\d+)`\s*"
    r"\|\s*`(?P<next_free>[A-Z]+-\d+)`\s*\|"
)
IDENTIFIER = re.compile(r"^(?P<prefix>[A-Z]+)-(?P<digits>\d+)$")


@dataclass(frozen=True)
class LedgerEntry:
    """One allocated identifier, located exactly where it was written."""

    identifier: str
    number: int
    line: int
    # Markdown heading depth for heading-style ledgers; ``None`` for table rows.
    level: int | None
    # The exact heading line, used to key `REPLAY_SUB_ENTRIES`.
    heading: str | None


def _reject_json_constant(value: str) -> Any:
    raise ValueError(f"unsupported JSON constant: {value}")


def _parse_finite_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError(f"non-finite JSON number: {value}")
    return parsed


def _check_benchmark_artifacts(failures: list[str]) -> None:
    results = ROOT / "benchmarks" / "results"
    for path in sorted(results.rglob("*.json")):
        relative = path.relative_to(ROOT).as_posix()
        try:
            payload = path.read_bytes()
        except OSError as error:
            failures.append(f"{relative} cannot be read: {error}")
            continue
        if len(payload) > MAX_BENCHMARK_BYTES:
            failures.append(f"{relative} exceeds the benchmark artifact size limit")
            continue
        try:
            report = json.loads(
                payload,
                parse_constant=_reject_json_constant,
                parse_float=_parse_finite_float,
            )
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
            failures.append(f"{relative} is not strict JSON: {error}")
            continue
        if not isinstance(report, dict):
            failures.append(f"{relative} must contain one JSON object")
            continue
        run_id = report.pop("run_id", None)
        expected = (
            "sha256:"
            + hashlib.sha256(
                json.dumps(
                    report,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode()
            ).hexdigest()
        )
        if run_id != expected:
            failures.append(f"{relative} run_id does not match its canonical report content")


def _parse_entries(space: LedgerIdSpace, text: str, failures: list[str]) -> list[LedgerEntry]:
    """Read every identifier this ledger allocates, in document order."""
    if space.kind == "table":
        row = re.compile(rf"^\|\s*(?P<id>{space.prefix}-\S*?)\s*\|")
    else:
        row = re.compile(rf"^(?P<hashes>#{{3,4}})\s+(?P<id>{space.prefix}-\S*)")

    entries: list[LedgerEntry] = []
    for index, line in enumerate(text.splitlines(), start=1):
        match = row.match(line)
        if match is None:
            continue
        identifier = match.group("id")
        parsed = IDENTIFIER.match(identifier)
        if parsed is None:
            failures.append(
                f"{space.document}:{index} identifier {identifier!r} is not `{space.prefix}-NNN`"
            )
            continue
        number = int(parsed.group("digits"))
        canonical = f"{space.prefix}-{number:03d}"
        if identifier != canonical:
            failures.append(
                f"{space.document}:{index} identifier {identifier!r} is not zero-padded to three "
                f"digits; write {canonical!r}"
            )
            continue
        level = len(match.group("hashes")) if space.kind == "heading" else None
        entries.append(
            LedgerEntry(
                identifier=identifier,
                number=number,
                line=index,
                level=level,
                heading=line.rstrip() if space.kind == "heading" else None,
            )
        )
    return entries


def _classify_duplicate(
    space: LedgerIdSpace,
    entry: LedgerEntry,
    first: LedgerEntry,
    used_replays: set[tuple[str, str]],
    used_collisions: set[tuple[str, str]],
    excused_duplicates: set[tuple[str, str]],
    failures: list[str],
    notes: list[str],
) -> None:
    """Decide whether a repeated identifier is legal, and say why if it is not."""
    replay_key = (space.document, entry.heading or "")
    if replay_key in REPLAY_SUB_ENTRIES:
        used_replays.add(replay_key)
        if entry.level != 4:
            failures.append(
                f"{space.document}:{entry.line} replay {entry.identifier} must be a `####` "
                f"sub-entry, not a level-{entry.level} heading"
            )
        elif first.level != 3:
            failures.append(
                f"{space.document}:{entry.line} replay {entry.identifier} has no `###` parent "
                f"entry to replay (first allocation is at line {first.line})"
            )
        return

    collision_key = (space.document, entry.identifier)
    if collision_key in RECORDED_COLLISIONS:
        used_collisions.add(collision_key)
        # A correction note records exactly one double allocation, so the
        # exception spends itself on the second occurrence and any further
        # repeat of the same identifier is a new defect, not history.
        if collision_key in excused_duplicates:
            failures.append(
                f"{space.document}:{entry.line} {entry.identifier} repeats again after its "
                f"recorded collision; the correction note excuses exactly one duplicate, so "
                "allocate the next free number instead of reusing this one"
            )
            return
        excused_duplicates.add(collision_key)
        notes.append(
            f"{space.document}: {entry.identifier} is a recorded historical collision "
            f"(lines {first.line} and {entry.line}) — {RECORDED_COLLISIONS[collision_key]}"
        )
        return

    failures.append(
        f"{space.document}:{entry.line} {entry.identifier} is already allocated at line "
        f"{first.line}; allocate the next free number instead of reusing one"
    )


def _check_ledger_ids(failures: list[str], notes: list[str]) -> dict[str, int]:
    """Validate every ledger ID space and return the highest number each allocates."""
    highest: dict[str, int] = {}
    used_replays: set[tuple[str, str]] = set()
    used_collisions: set[tuple[str, str]] = set()
    excused_duplicates: set[tuple[str, str]] = set()

    for space in LEDGER_ID_SPACES:
        path = ROOT / space.document
        if not path.is_file():
            continue
        entries = _parse_entries(space, path.read_text(encoding="utf-8"), failures)
        if not entries:
            failures.append(f"{space.document} allocates no {space.prefix}- identifiers")
            continue

        first_seen: dict[int, LedgerEntry] = {}
        previous: LedgerEntry | None = None
        for entry in entries:
            seen = first_seen.get(entry.number)
            if seen is None:
                first_seen[entry.number] = entry
            else:
                _classify_duplicate(
                    space,
                    entry,
                    seen,
                    used_replays,
                    used_collisions,
                    excused_duplicates,
                    failures,
                    notes,
                )
            if space.ordered and previous is not None and entry.number < previous.number:
                # A recorded collision also displaces document order: merging two
                # branches that each appended a block leaves one block behind the
                # other. The correction note that records the collision records
                # the disorder with it, so do not report the same defect twice.
                collision = (space.document, entry.identifier)
                if collision in RECORDED_COLLISIONS:
                    used_collisions.add(collision)
                    notes.append(
                        f"{space.document}:{entry.line} {entry.identifier} follows "
                        f"{previous.identifier}; document order was displaced by the same recorded "
                        f"collision — {RECORDED_COLLISIONS[collision]}"
                    )
                else:
                    failures.append(
                        f"{space.document}:{entry.line} {entry.identifier} is out of order; it "
                        f"follows {previous.identifier} at line {previous.line} and this ledger is "
                        "strictly increasing in document order"
                    )
            previous = entry

        allocated = sorted(first_seen)
        highest[space.prefix] = allocated[-1]
        gaps = [
            f"{space.prefix}-{number:03d}"
            for number in range(allocated[0], allocated[-1] + 1)
            if number not in first_seen
        ]
        if gaps:
            notes.append(
                f"{space.document}: {len(gaps)} unallocated number(s) — {', '.join(gaps)}; "
                "a spent number is never recycled"
            )

    for key in sorted(set(REPLAY_SUB_ENTRIES) - used_replays):
        document, heading = key
        failures.append(
            f"{document}: replay exception for {heading!r} ({REPLAY_SUB_ENTRIES[key]}) matched no "
            "repeated identifier; remove it"
        )
    for key in sorted(set(RECORDED_COLLISIONS) - used_collisions):
        document, identifier = key
        failures.append(
            f"{document}: recorded collision for {identifier} ({RECORDED_COLLISIONS[key]}) matched "
            "no duplicate identifier; remove it"
        )
    return highest


def _check_allocation_registry(highest: dict[str, int], failures: list[str]) -> None:
    """Verify the `Highest allocated` / `Next free` table against the ledgers themselves."""
    path = ROOT / LEDGER_README
    if not path.is_file():
        failures.append(f"missing {LEDGER_README}")
        return

    declared: set[str] = set()
    for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        match = ALLOCATION_ROW.match(line)
        if match is None:
            continue
        prefix = match.group("prefix")
        declared.add(prefix)
        if prefix not in highest:
            failures.append(f"{LEDGER_README}:{index} declares unknown ID prefix {prefix!r}")
            continue
        expected_highest = f"{prefix}-{highest[prefix]:03d}"
        expected_next = f"{prefix}-{highest[prefix] + 1:03d}"
        if match.group("highest") != expected_highest:
            failures.append(
                f"{LEDGER_README}:{index} says the highest allocated {prefix}- identifier is "
                f"{match.group('highest')}, but the ledger allocates {expected_highest}"
            )
        if match.group("next_free") != expected_next:
            failures.append(
                f"{LEDGER_README}:{index} says the next free {prefix}- identifier is "
                f"{match.group('next_free')}, but it is {expected_next}"
            )

    for prefix in sorted(set(highest) - declared):
        failures.append(f"{LEDGER_README} does not declare the {prefix}- allocation state")


def main() -> int:
    failures: list[str] = []
    notes: list[str] = []
    for relative, heading in REQUIRED.items():
        path = ROOT / relative
        if not path.is_file():
            failures.append(f"missing {relative}")
            continue
        if heading not in path.read_text(encoding="utf-8"):
            failures.append(f"{relative} is missing heading {heading!r}")
    _check_benchmark_artifacts(failures)
    highest = _check_ledger_ids(failures, notes)
    _check_allocation_registry(highest, failures)
    for note in notes:
        print(f"note: {note}")
    if failures:
        raise SystemExit("Ledger check failed:\n- " + "\n- ".join(failures))
    allocated = ", ".join(f"{prefix}-{number:03d}" for prefix, number in sorted(highest.items()))
    print(f"Ledger check passed. Highest allocated: {allocated}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
