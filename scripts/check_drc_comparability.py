#!/usr/bin/env python3
"""Fail when a benchmark artifact publishes a KiCad DRC count without its comparability literal.

`B-107` ran the corpus runner twice at the same commit over byte-identical boards and nine
boards' records still differed, every one of them only in the `drc` section. A DRC count is
therefore not a function of the bytes it was taken over, and
[ADR-0109](../docs/adr/0109-a-drc-count-carries-the-comparability-it-was-taken-with.md) requires
every published count to say which of `single_invocation`, `repeated_agreement` and
`repeated_disagreement` it is.

**What this checker owns**, and each half exists because the other cannot see it:

1. **The artifact sweep.** Every DRC section in every `benchmarks/results/**/*.json` must carry a
   valid `drc_comparability` literal. This is the half that catches a *new* runner: it does not
   need to know the runner exists, only that its output landed.
2. **The differential prohibition.** A DRC section may not publish a delta -- a key ending
   `_delta` or `_change` -- unless its literal is `repeated_agreement`. `B-108` measured
   baseline-against-baseline disagreement reaching an absolute 32 against 109 across a real
   change, so the noise is the same order of magnitude as a signal rather than smaller than one.
3. **The runner registry.** Every script in `DRC_RECORDING_RUNNERS` must import the enforcement
   module, so the gate runs at emission rather than only at review. **This table is
   reviewer-owned and the checker says so**: a runner added without an entry is not detected
   here. It is detected by (1), when its artifact lands. A registry entry naming a file that does
   not exist, or a file that no longer imports the module, fails the run.

   `DEFERRED_RUNNERS` is the one exception, and it is a record rather than a hole. A runner whose
   committed artifact pins `script_sha256` of the runner itself cannot be edited without
   invalidating that binding, so the emission call waits for the regeneration the artifact's own
   version-binding already schedules. Half (1) covers that runner's output in the meantime, which
   is why the deferral costs nothing a reader has to take on trust.

**Exemptions follow `check_doc_links.py` and `check_schema_sets.py` exactly.** `PRE_POLICY` is
keyed `(artifact, section path)` and each entry names the ledger row that qualifies the counts it
covers. The counts published before this policy are **qualified and not retracted**, following
the `B-102` pattern: rewriting a committed artifact to add a field would change its `run_id`,
which is a digest of its own content, and a re-derived `run_id` over numbers nobody re-measured
would be a worse record than the honest exemption. An entry matching nothing **fails the run**,
so an exemption cannot be added and then quietly forgotten.

**What it does not own.** It does not verify that a `repeated_agreement` claim is true -- that
N observations were taken at one commit over identical bytes is the runner's assertion, checked
by nothing here. It does not read prose: a ledger row or a research note quoting a bare count is
outside its reach, and `B-111` is the record that qualifies the ones already written. And it has
no opinion about whether any count is correct.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:  # pragma: no cover - import bootstrap
    sys.path.insert(0, str(ROOT / "src"))

from copper_mcp.benchmarks.drc_comparability import (  # noqa: E402
    COMPARABILITY_LITERALS,
    LITERAL_KEY,
    drc_sections,
)

RESULTS = Path("benchmarks/results")
MODULE = "copper_mcp.benchmarks.drc_comparability"

#: Suffixes that make a key inside a DRC section a before/after difference.
DIFFERENTIAL_SUFFIXES = ("_delta", "_change")

#: The one literal a differential may be computed from.
ADMISSIBLE_IN_DIFFERENTIAL = "repeated_agreement"

#: Scripts that record a KiCad DRC count into an artifact. Reviewer-owned; see the module
#: docstring for why a checker cannot derive this set, and for what catches an omission.
DRC_RECORDING_RUNNERS: frozenset[str] = frozenset(
    {
        "scripts/benchmark_freerouting_comparison.py",
        "scripts/benchmark_layered_kicad_drc.py",
        "scripts/benchmark_placement_drc.py",
        "scripts/benchmark_public_placement_drc.py",
        "scripts/benchmark_real_board_capability.py",
    }
)

#: A DRC-recording runner that **cannot** be wired to the emission gate yet, with the reason and
#: the event that lifts it. `benchmarks/results/routing/2026-08-05-route-bundle-v1.json` records
#: `script_sha256` of its own runner, so editing that runner invalidates the committed artifact's
#: binding to the source that produced it -- and the artifact is regenerated only in the release
#: commit that bumps the version, because its evidence is version-bound by construction. Adding
#: the emission call now would trade a published binding for a gate the **artifact sweep above
#: already provides** for the same file. The entry is here rather than absent so that the omission
#: is a record and not a gap; `tests/test_check_drc_comparability.py` asserts the file exists and
#: still carries the pin that is the reason.
#:
#: **`benchmark_freerouting_comparison.py` was checked against this same reason and does not have
#: it**, so it is registered above rather than deferred here. Its committed artifact
#: (`benchmarks/results/routing/2026-08-05-freerouting-common-two-pad.json`) records no
#: `script_sha256` and no `script` field of any kind: its only self-binding is `run_id`, a digest
#: of the artifact's *own* content, which the emission call does not touch because it neither
#: edits the committed file nor changes what a re-run of the unmodified runner would compute from
#: the same inputs. A deferral is a cost paid to protect a real binding; there is no binding here
#: to protect, so deferring would have been a hole wearing a record's clothes.
DEFERRED_RUNNERS: dict[str, str] = {
    "scripts/benchmark_route_bundle.py": (
        "its committed artifact pins script_sha256; wire at the next version-bump regeneration"
    ),
}

#: Counts published before ADR-0109, keyed `(artifact, section path)` and each naming the record
#: that qualifies it. Every one of these is `single_invocation` in fact -- none of the runs behind
#: them repeated the invocation -- and `B-111` says so in the ledger rather than in the file.
PRE_POLICY: dict[tuple[str, str], str] = {
    **{
        (
            "benchmarks/results/capability/2026-08-07-real-board-tier2-v1.json",
            f"/boards[{index}]/drc",
        ): "B-111 qualifies B-099's per-board DRC counts as one invocation's answer"
        for index in range(12)
    },
    (
        "benchmarks/results/capability/2026-08-07-real-board-tier2-v1.json",
        "/totals",
    ): "B-111 qualifies B-099's DRC totals, which aggregate twelve single invocations",
    (
        "benchmarks/results/placement/2026-08-05-public-placement-drc.json",
        "/metrics",
    ): "B-111: B-044's public placement DRC run counts predate the literal",
    (
        "benchmarks/results/placement/2026-08-05-public-placement-drc.json",
        "/metrics/aggregate_counts",
    ): "B-111: B-044's aggregate counts predate the literal",
    (
        "benchmarks/results/placement/2026-08-05-public-placement-drc-historical-b044.json",
        "/metrics",
    ): "B-111: the retained historical B-044 artifact predates the literal",
    (
        "benchmarks/results/routing/2026-08-05-layered-kicad-drc.json",
        "/metrics",
    ): "B-111: the layered candidate DRC metrics predate the literal",
    (
        "benchmarks/results/routing/2026-08-05-placement-drc.json",
        "/metrics",
    ): "B-111: the placement candidate DRC metrics predate the literal",
    (
        "benchmarks/results/routing/2026-08-05-route-bundle-v1.json",
        "/authoritative_kicad_drc",
    ): "B-111: B-103's route-bundle authority counts predate the literal",
    # The FreeRouting comparison publishes its KiCad counts under the second vocabulary
    # (`hard_violations` / `unconnected` / `footprint_errors`). The first version of ADR-0109's
    # section table named only the first vocabulary, so these three sections were swept over in
    # silence; they are exempted here on exactly the same terms as the six artifacts above --
    # `single_invocation` in fact, qualified by `B-111`, bytes untouched because `run_id` is a
    # digest of the artifact's own content.
    **{
        (
            "benchmarks/results/routing/2026-08-05-freerouting-common-two-pad.json",
            path,
        ): (
            "B-111: B-069's FreeRouting comparison DRC counts predate the literal, and its "
            "count vocabulary predates the section table that now recognises it"
        )
        for path in ("/results[0]/drc", "/results[1]/drc", "/source_drc")
    },
}


def _artifacts() -> list[Path]:
    return sorted(path for path in (ROOT / RESULTS).rglob("*.json") if path.is_file())


def _check_artifacts(failures: list[str], used: set[tuple[str, str]]) -> int:
    sections = 0
    for path in _artifacts():
        relative = path.relative_to(ROOT).as_posix()
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            failures.append(f"{relative}: cannot be read as JSON ({error})")
            continue
        for section_path, section in drc_sections(document):
            sections += 1
            key = (relative, section_path)
            literal = section.get(LITERAL_KEY)
            if literal in COMPARABILITY_LITERALS:
                if key in PRE_POLICY:
                    failures.append(
                        f"{relative}: the DRC section at {section_path} now carries a "
                        f"{LITERAL_KEY!r} literal; remove its pre-policy exemption"
                    )
                    used.add(key)
            elif key in PRE_POLICY:
                used.add(key)
            else:
                failures.append(
                    f"{relative}: the DRC section at {section_path} publishes a count without a "
                    f"{LITERAL_KEY!r} literal (got {literal!r})"
                )
                continue
            deltas = sorted(
                name
                for name in section
                if name.endswith(DIFFERENTIAL_SUFFIXES) and name != LITERAL_KEY
            )
            if deltas and literal != ADMISSIBLE_IN_DIFFERENTIAL:
                failures.append(
                    f"{relative}: the DRC section at {section_path} publishes the differential "
                    f"{deltas[0]!r} on a {literal!r} count; only "
                    f"{ADMISSIBLE_IN_DIFFERENTIAL!r} counts are comparable across runs"
                )
    return sections


def _check_runners(failures: list[str]) -> None:
    for runner in sorted(DRC_RECORDING_RUNNERS):
        path = ROOT / runner
        if not path.is_file():
            failures.append(f"{runner}: registered as a DRC-recording runner but does not exist")
            continue
        if MODULE not in path.read_text(encoding="utf-8"):
            failures.append(
                f"{runner}: records a KiCad DRC count but does not import {MODULE}, so nothing "
                "refuses an artifact whose DRC section is unqualified"
            )


def main() -> int:
    failures: list[str] = []
    used: set[tuple[str, str]] = set()

    sections = _check_artifacts(failures, used)
    _check_runners(failures)

    for key in sorted(set(PRE_POLICY) - used):
        failures.append(
            f"{key[0]}: pre-policy exemption for {key[1]} ({PRE_POLICY[key]}) matched no DRC "
            "section; remove it"
        )

    if failures:
        raise SystemExit("DRC comparability check failed:\n- " + "\n- ".join(failures))
    print(
        f"DRC comparability check passed ({sections} DRC sections across "
        f"{len(_artifacts())} artifacts, {len(DRC_RECORDING_RUNNERS)} registered runners, "
        f"recorded pre-policy exemptions: {len(PRE_POLICY)})."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
