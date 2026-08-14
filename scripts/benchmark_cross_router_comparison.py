#!/usr/bin/env python3
"""Record a cross-router comparison table with a typed result for every declared baseline.

This is the artifact M2's closing condition 2 asks for: *"a cross-router comparison artifact
carrying typed results, ``not_run``-with-reason included. A typed ``not_run`` is a result; a
missing row is not."*  It is deliberately **not** a new measurement of CopperMCP's router — that
measurement exists as ``B-088`` and is replayed here unchanged.  What this program adds is the
three things a comparison needs and B-088's artifact does not carry:

1. **A declared roster.**  Every router baseline the licensing determination in
   ``docs/research/open-baseline-benchmarks-v1.md`` §3 names gets a row, and the roster is a frozen
   constant rather than a loop over whatever happens to be installed.  A baseline that vanishes
   from the roster is a silent missing row, which is the exact failure the condition forbids, so a
   test pins the roster's membership.
2. **A typed resolution rule.**  A baseline is ``measured`` only when *every* precondition it
   declares is observed to hold.  Absence of a precondition is ``not_run`` with the reason, and the
   reason is required to be a licence or environment fact already determined in §3 — never a fresh
   judgement made here, and never an estimate.
3. **What would change each row.**  A ``not_run`` that names no precondition is indistinguishable
   from an excuse.  Every row states the named, checkable conditions under which it would become a
   measurement.

**Nothing here compares anything yet, and the artifact says so in those words.**  Every external
baseline resolves to ``not_run``, so the table has one measured row.  A table with one measured row
supports no comparative conclusion whatsoever, and ``not_claimed`` records that first.

**No DRC metric is in the protocol, by construction.**  Comparing DRC counts across routers would
require a comparability argument this artifact does not make and this corpus cannot support: the
imported boards are SimpleRouteJson problems with no KiCad document behind them, and DRC counts are
not reproducible across environments in the first place.  The metric set is a closed tuple and a
test asserts no DRC-shaped metric is in it.

Offline: it reads only committed files, verified against the corpus digest manifest before routing.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from copper_mcp.routing import ROUTER_VERSION, ROUTING_POLICY
from scripts.benchmark_simple_route_json_corpus import (
    CORPUS,
    FIXED_GRID_STEP_NM,
    POLICIES,
    ROUTER_LIMITS,
    SEED,
    CorpusBenchmarkError,
    load_corpus,
    run_configuration,
)

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = "scripts/benchmark_cross_router_comparison.py"
CORPUS_RUNNER_PATH = "scripts/benchmark_simple_route_json_corpus.py"
DEFAULT_OUTPUT = ROOT / "benchmarks/results/routing/2026-08-14-cross-router-comparison-v1.json"
REPORT_SCHEMA = "copper-mcp/benchmark/cross-router-comparison/v1"
RESEARCH_NOTE = "docs/research/open-baseline-benchmarks-v1.md"

#: The metrics every row of the table reports, defined once so a baseline added later lands
#: comparable rather than adjacent.  It is a closed tuple: a metric not named here does not appear
#: in a row, and the absence of any DRC metric is a decision, not an oversight.
COMPARISON_METRICS: tuple[tuple[str, str], ...] = (
    (
        "nets_attempted",
        "nets offered to the router: every imported net with at least two pads. A net with one "
        "pad states no routing work and is counted separately, outside this denominator.",
    ),
    (
        "nets_completed",
        "nets for which the router returned a geometry joining every pad of the net. Any other "
        "outcome is a refusal carrying its own typed code and stays in the denominator.",
    ),
    (
        "completion_percent",
        "nets_completed * 100 / nets_attempted, over the denominator above and no other.",
    ),
    (
        "routed_wire_length_nm",
        "summed length of the geometries counted by nets_completed, in nanometres.",
    ),
    (
        "length_over_pad_gap_lower_bound",
        "routed_wire_length_nm divided by the provable pad-gap lower bound over the same nets. "
        "The bound ignores every obstacle, bend and detour, so the ratio is safe to publish and "
        "is not an optimality statement.",
    ),
    ("vias", "vias in the counted geometries."),
    ("bends", "direction changes in the counted geometries."),
    (
        "mean_wall_seconds",
        "mean wall time of one whole-corpus pass on the recording machine. Machine-bound, and "
        "comparable only between rows recorded in the same environment.",
    ),
)

#: Substrings that must not name a metric in ``COMPARISON_METRICS``.  Stated as data so the reason
#: for their absence travels with the check rather than living in a commit message.
EXCLUDED_METRIC_MARKERS: tuple[str, ...] = ("drc", "violation", "clean")

DRC_EXCLUSION_REASON = (
    "No DRC metric is in this protocol. The problem set is SimpleRouteJson, so no imported board "
    "has a KiCad document a design-rule check could be run against; and a DRC count is not "
    "reproducible across environments, so a count carried between two rows would not be a "
    "comparison. A cross-router DRC comparison needs a comparability argument made on its own "
    "terms, and this artifact does not make one."
)


class CrossRouterComparisonError(RuntimeError):
    """Raised when the harness itself is broken — a roster defect, or a divergent replay."""


@dataclass(frozen=True, slots=True)
class Precondition:
    """One named, checkable condition a baseline needs before it can be measured.

    ``observable`` travels with ``satisfied`` in the payload because they mean different things and
    the artifact must not let a reader collapse them.  This is the post-0.8.0 audit's own
    generalised rule applied here: *an absence is evidence only if the observation was capable of
    reporting a presence.*  A precondition this harness cannot observe reports ``satisfied: false``
    **and** ``observable: false``, so "not satisfied" is never read as a measurement it was not.
    """

    name: str
    description: str
    #: ``None`` means "this harness cannot observe it, so it is treated as unsatisfied".  An
    #: unobservable precondition is never optimistically assumed to hold.
    executable: str | None = None
    #: Argv run to confirm the executable on ``PATH`` is the thing it is named after.  Present
    #: because ``shutil.which`` is not always an observation capable of reporting a presence:
    #: macOS ships ``/usr/bin/java`` as a stub that exists whether or not a JRE does, and exits
    #: non-zero saying "Unable to locate a Java Runtime". A ``which`` hit would have reported that
    #: stub as a satisfied precondition and flipped a row off ``not_run`` on no evidence.
    probe_args: tuple[str, ...] | None = None

    def satisfied(self) -> bool:
        if self.executable is None:
            return False
        if shutil.which(self.executable) is None:
            return False
        if self.probe_args is None:
            return True
        try:
            completed = subprocess.run(  # noqa: S603 - argv is a frozen constant on this dataclass
                [self.executable, *self.probe_args],
                capture_output=True,
                timeout=20,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        return completed.returncode == 0

    def payload(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "satisfied": self.satisfied(),
            "observable": self.executable is not None,
            "probe": list(self.probe_args) if self.probe_args is not None else None,
        }


@dataclass(frozen=True, slots=True)
class BaselineRouter:
    """One declared router baseline, with the §3 determination that governs it."""

    id: str
    project: str
    version_considered: str
    license_spdx: str
    #: The row of the research note's §3 table that determines this baseline's availability.
    determination_row: str
    #: ``licence`` or ``environment`` — which kind of §3 fact keeps this row unmeasured.
    reason_kind: str
    reason: str
    preconditions: tuple[Precondition, ...]
    #: Recorded even for a row that is not run, because it constrains what the row could mean if
    #: it ever were run.
    comparability_caveat: str | None = None

    def resolve(self) -> dict[str, Any]:
        """Return this baseline's typed row.

        ``measured`` requires every declared precondition to hold.  There is no partial state and
        no remembered number: a baseline whose preconditions are not all observed to hold is
        ``not_run``, and the row carries the reason and the unmet preconditions by name.
        """

        preconditions = tuple(condition.payload() for condition in self.preconditions)
        unmet = tuple(entry["name"] for entry in preconditions if not entry["satisfied"])
        row: dict[str, Any] = {
            "id": self.id,
            "role": "baseline",
            "project": self.project,
            "version_considered": self.version_considered,
            "license_spdx": self.license_spdx,
            "determination_row": self.determination_row,
            "preconditions": list(preconditions),
            "unmet_preconditions": list(unmet),
        }
        if unmet:
            row["status"] = "not_run"
            row["reason_kind"] = self.reason_kind
            row["reason"] = self.reason
            row["what_would_change_it"] = [
                entry["description"] for entry in preconditions if not entry["satisfied"]
            ]
            row["results"] = None
        else:
            # No declared baseline reaches this branch in the recording environment. It exists so
            # that an environment arriving before its driver does cannot read as ``not_run`` for a
            # reason that has stopped being true.
            row["status"] = "runnable_but_unbridged"
            row["reason_kind"] = "environment"
            row["reason"] = (
                "every declared precondition now holds, and this harness still has no code that "
                "drives this baseline. That is a harness gap, and it is reported rather than "
                "silently reported as not_run."
            )
            row["what_would_change_it"] = ["a driver for this baseline in this harness"]
            row["results"] = None
        if self.comparability_caveat is not None:
            row["comparability_caveat"] = self.comparability_caveat
        return row


#: Every router baseline §3 names.  Membership is pinned by a test: dropping a row here is exactly
#: the "missing row" M2's condition 2 forbids, and it would otherwise be invisible.
BASELINE_ROSTER: tuple[BaselineRouter, ...] = (
    BaselineRouter(
        id="freerouting",
        project="https://github.com/freerouting/freerouting",
        version_considered="v2.2.4 (2026-05-13)",
        license_spdx="GPL-3.0-only",
        determination_row=(
            f"{RESEARCH_NOTE} §3, row 'freerouting/freerouting': GPL-3.0, invocable as an "
            "out-of-process baseline only, and not installed in the recording environment"
        ),
        reason_kind="environment",
        reason=(
            "FreeRouting is not installed in the recording environment. Its GPL-3.0 licence "
            "permits an out-of-process baseline only — linking or vendoring it into this "
            "Apache-2.0 repository would be licence-incompatible — and the contained provider "
            "that would supply it, issue #53, is parked behind an operator gate whose own text "
            "says the issue does not authorize the container-runtime installation it needs. No "
            "number is estimated, inferred, or carried over from B-069."
        ),
        preconditions=(
            Precondition(
                name="freerouting_executable",
                description=(
                    "a FreeRouting v2.2.4 executable present in the recording environment, "
                    "obtained under the operator approval issue #53 names and does not grant"
                ),
                executable="freerouting",
            ),
            Precondition(
                name="simple_route_json_to_dsn_bridge",
                description=(
                    "a SimpleRouteJson-to-Specctra-DSN bridge, so both routers read one common "
                    "input rather than two documents asserted to be equivalent"
                ),
            ),
            Precondition(
                name="java_runtime",
                description=(
                    "a Java runtime that actually runs, observed by executing it rather than by "
                    "finding a name on PATH — this host has /usr/bin/java and no JRE behind it"
                ),
                executable="java",
                probe_args=("-version",),
            ),
        ),
        comparability_caveat=(
            "Even fully run, this row would not be a neutral comparison on this problem set. The "
            "corpus was constructed with FreeRouting in the loop — upstream's own table reports "
            "FreeRouting clean on 35 of 36 boards — so scoring against FreeRouting here measures "
            "a set FreeRouting helped define. A FreeRouting row needs a corpus neither router "
            "helped construct."
        ),
    ),
    BaselineRouter(
        id="tscircuit-autorouting",
        project="https://github.com/tscircuit/autorouting",
        version_considered="archived 2025-08-15",
        license_spdx="NOASSERTION",
        determination_row=(
            f"{RESEARCH_NOTE} §3, row 'tscircuit/autorouting': no LICENSE file, no package.json "
            "license key, GitHub API license: null — all rights reserved, nothing redistributed"
        ),
        reason_kind="licence",
        reason=(
            "The repository carries no licence at all, so its solvers are all rights reserved: "
            "they may not be vendored, committed, or redistributed in this project's sdist, and "
            "this project does not run third-party code it has no licence to hold. The "
            "repository was archived on 2025-08-15, so the licence is unlikely to change. Its "
            "format specification is cited by the import adapter; no file of it is copied."
        ),
        preconditions=(
            Precondition(
                name="upstream_licence",
                description=(
                    "a licence published by tscircuit/autorouting covering its solver code, or "
                    "an independent reimplementation written from the format specification alone"
                ),
            ),
        ),
    ),
    BaselineRouter(
        id="pcbworld-evaluation",
        project="PCBWorld (arXiv:2607.05915)",
        version_considered="v2 preprint, datasets announced",
        license_spdx="CC-BY-4.0 (synthetic sets and evaluation code)",
        determination_row=(
            f"{RESEARCH_NOTE} §3, row 'PCBWorld': synthetic sets D1/D2, their generators and the "
            "evaluation code are CC-BY-4.0; announced, not released"
        ),
        reason_kind="licence",
        reason=(
            "The paper states its datasets and evaluation code 'will be released on a public "
            "repository upon publication'. No GitHub, HuggingFace, or Zenodo host exists. There "
            "is nothing to fetch, so there is nothing to run: recorded as announced rather than "
            "released. The CC0 on the arXiv submission covers the paper, not the code."
        ),
        preconditions=(
            Precondition(
                name="published_artifact",
                description=(
                    "PCBWorld's evaluation code and its CC-BY-4.0 synthetic sets published at a "
                    "resolvable host, so a fixed revision can be pinned and fetched"
                ),
            ),
        ),
    ),
)

#: Every corpus §3 names, with its typed determination.  Present so that no §3 row is silently
#: absent from this artifact: a reader checking condition 2's "a missing row is not a result"
#: against §3 finds all five rows accounted for, three as router baselines above and the rest here.
CORPORA_CONSIDERED: tuple[dict[str, str], ...] = (
    {
        "id": "dwiel-tscircuit-benchmark",
        "project": "https://github.com/dwiel/tscircuit-benchmark",
        "license_spdx": "MIT",
        "determination": "imported",
        "note": (
            "The problem set of this artifact. MIT, © 2026 Zach Dwiel, frozen at upstream commit "
            "be36518b5bf51755dae92c230061ab3cf4e3e063, 20 of 36 boards committed under a stated "
            "prefix rule with the upstream LICENSE and an ATTRIBUTION.md beside them."
        ),
    },
    {
        "id": "tscircuit-autorouting",
        "project": "https://github.com/tscircuit/autorouting",
        "license_spdx": "NOASSERTION",
        "determination": "not_redistributable",
        "note": (
            "No licence, archived 2025-08-15. Its tiered problems cannot enter an in-repo corpus. "
            "It also appears above as a router baseline, for the same reason."
        ),
    },
    {
        "id": "pcbench",
        "project": "https://github.com/PCBench/PCBench",
        "license_spdx": "MIT (aggregator only)",
        "determination": "not_redistributable",
        "note": (
            "Ruled out twice over, and independently. Licence: the MIT grant covers PCBench's own "
            "contribution, not the 164 boards it scraped from 1,018 repositories — 36 record no "
            "licence at all, 57 copyleft or CERN-OHL, 53 permissive (ADR-0107, D-199). Format: "
            "B-110 measured 0 of 164 converting through the KiCad intake, all refusing "
            "unsupported.version on 2023-era saves. Not a router baseline: PCBench publishes a "
            "dataset and an RL environment, and this artifact does not assert it ships a router."
        ),
    },
    {
        "id": "pcbworld",
        "project": "PCBWorld (arXiv:2607.05915)",
        "license_spdx": "CC-BY-4.0 (D1/D2) + heterogeneous (D3)",
        "determination": "not_released",
        "note": (
            "Announced, with no public host. Its 679 real D3 boards 'retain the license of their "
            "source repository' and are heterogeneous, so D3 would fall under ADR-0107's per-item "
            "rule even once published."
        ),
    },
)


def _git_commit() -> str:
    git = shutil.which("git")
    if git is None:
        return "unknown"
    try:
        return subprocess.run(  # noqa: S603 - fixed local Git executable and argv
            [git, "rev-parse", "HEAD"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def _file_digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def check_protocol() -> None:
    """Fail loudly if the metric set has grown a DRC-shaped metric, or lost its definitions."""

    names = [name for name, _definition in COMPARISON_METRICS]
    if len(set(names)) != len(names):
        raise CrossRouterComparisonError("a comparison metric is declared twice")
    for name, definition in COMPARISON_METRICS:
        if not definition.strip():
            raise CrossRouterComparisonError(f"comparison metric {name} carries no definition")
        lowered = f"{name} {definition}".lower()
        for marker in EXCLUDED_METRIC_MARKERS:
            if marker in lowered:
                raise CrossRouterComparisonError(
                    f"comparison metric {name} names an excluded marker {marker!r}"
                )


def check_roster(rows: tuple[dict[str, Any], ...]) -> None:
    """Fail loudly if any declared baseline lost its row, its reason, or its remedy."""

    if len(rows) != len(BASELINE_ROSTER):
        raise CrossRouterComparisonError("a declared baseline has no row in the comparison table")
    declared = {baseline.id for baseline in BASELINE_ROSTER}
    if {str(row["id"]) for row in rows} != declared:
        raise CrossRouterComparisonError("the comparison table does not match the declared roster")
    for row in rows:
        if row["status"] == "measured":
            continue
        if not str(row.get("reason", "")).strip():
            raise CrossRouterComparisonError(f"baseline {row['id']} records no reason")
        if row.get("reason_kind") not in {"licence", "environment"}:
            raise CrossRouterComparisonError(
                f"baseline {row['id']} records a reason that is neither a licence nor an "
                "environment fact"
            )
        remedy = row.get("what_would_change_it")
        if not isinstance(remedy, list) or not remedy:
            raise CrossRouterComparisonError(
                f"baseline {row['id']} names nothing that would change it"
            )


def subject_row(
    samples: tuple[tuple[str, bytes], ...], repetitions: int
) -> tuple[dict[str, Any], dict[str, float]]:
    """Measure CopperMCP's single-layer router on the problem set, under both grid policies."""

    configurations: dict[str, Any] = {}
    wall_times: dict[str, float] = {}
    for policy in POLICIES:
        first: dict[str, Any] | None = None
        elapsed = 0.0
        for _ in range(repetitions):
            started = time.perf_counter()
            metrics = run_configuration(samples, policy)
            elapsed += time.perf_counter() - started
            if first is None:
                first = metrics
            elif metrics != first:
                raise CrossRouterComparisonError(
                    f"deterministic replay diverged for grid policy {policy.name}"
                )
        assert first is not None
        configurations[policy.name] = _protocol_metrics(first)
        wall_times[policy.name] = elapsed / repetitions
    return {
        "id": "coppermcp-astar-grid",
        "role": "subject",
        "project": "this repository",
        "version_considered": ROUTER_VERSION,
        "license_spdx": "Apache-2.0",
        "routing_policy": ROUTING_POLICY,
        "status": "measured",
        "results": configurations,
    }, wall_times


def _protocol_metrics(configuration: dict[str, Any]) -> dict[str, Any]:
    """Project one whole-corpus run onto the declared metric set, and check its accounting.

    The projection is where the denominator discipline is enforced: every attempted net must be
    accounted for by exactly one outcome, so a completion percentage can never be computed over a
    quietly shortened denominator — the failure mode tscircuit/autorouting's own harness has by
    default and this one inverts.
    """

    breakdown = dict(configuration["outcome_breakdown"])
    attempted = sum(count for code, count in breakdown.items() if code != "no_routing_work")
    if attempted != configuration["nets_attempted"]:
        raise CrossRouterComparisonError(
            "the outcome breakdown does not sum to the attempted count: a completion rate would "
            "be computed over a shortened denominator"
        )
    completed = breakdown.get("routed", 0)
    if completed != configuration["nets_routed"]:
        raise CrossRouterComparisonError("the routed outcome does not match the routed count")
    return {
        "grid_policy": configuration["grid_policy"],
        "boards_offered": configuration["boards_offered"],
        "boards_imported": configuration["boards_imported"],
        "import_refusals": configuration["import_refusals"],
        "nets_attempted": attempted,
        "nets_completed": completed,
        "completion_percent": (completed * 100 / attempted) if attempted else 0.0,
        "nets_stating_no_routing_work": breakdown.get("no_routing_work", 0),
        "outcome_breakdown": breakdown,
        "routed_wire_length_nm": configuration["routed_wire_length_nm"],
        "routed_pad_gap_lower_bound_nm": configuration["routed_pad_gap_lower_bound_nm"],
        "length_over_pad_gap_lower_bound": configuration["length_over_pad_gap_lower_bound"],
        "vias": configuration["vias"],
        "bends": configuration["bends"],
    }


def problem_set(manifest: dict[str, Any], samples: tuple[tuple[str, bytes], ...]) -> dict[str, Any]:
    """Pin the frozen problem set every row of the table is or would be measured on."""

    return {
        "corpus_id": manifest["corpus_id"],
        "upstream_repository": manifest["upstream_repository"],
        "upstream_commit": manifest["upstream_commit"],
        "license_spdx": manifest["license_spdx"],
        "license_sha256": manifest["license_sha256"],
        "committed_subset_rule": manifest["committed_subset_rule"],
        "committed_boards": len(samples),
        "upstream_sample_count": manifest["upstream_sample_count"],
        "boards": [
            {"board": Path(name).stem, "sha256": hashlib.sha256(payload).hexdigest()}
            for name, payload in samples
        ],
        "frozen_and_redistributable": True,
        "why_this_satisfies_the_closing_corpus_condition": (
            "M2's closing condition 3 asks for the closing measurement to be taken on a frozen, "
            "redistributable corpus rather than on the designer's live tree (R-146: a live tree "
            "is edited under long runs, so every figure taken from it decays silently). This "
            "corpus is exactly that for the routing measurement: MIT, © 2026 Zach Dwiel, "
            "committed in-repo at a pinned upstream commit with a per-file digest manifest the "
            "runner verifies before routing, under a subset rule fixed in advance. It is frozen "
            "in the sense the condition requires — the bytes cannot change without failing the "
            "digest check — and redistributable in the sense the corpora README requires. It is "
            "not a substitute for a frozen corpus of real KiCad documents, which issue #110 "
            "still owes M4's evaluation and which R-153 still records as open."
        ),
        "provenance_limits": [
            "the boards are LLM-generated from plain-English specs, not human-engineered "
            "production hardware, so a result generalises to LLM-generated tscircuit boards and "
            "to nothing else",
            "FreeRouting was in the construction loop, so this corpus is not a neutral yardstick "
            "for FreeRouting",
            "every board is 2-layer with 3-35 components: no multi-layer, BGA, differential-pair "
            "or width-constrained case exists in it",
            "the committed 20 are the first 20 in upstream lexical order, and upstream orders "
            "roughly by growing component count, so the prefix is the easier half",
        ],
    }


def build_report(repetitions: int = 2, corpus: Path = CORPUS) -> dict[str, Any]:
    """Build the canonical, self-digesting comparison report without writing it."""

    if not 1 <= repetitions <= 8:
        raise ValueError("repetitions must be between 1 and 8")
    check_protocol()
    manifest, samples = load_corpus(corpus)
    subject, wall_times = subject_row(samples, repetitions)
    baseline_rows = tuple(baseline.resolve() for baseline in BASELINE_ROSTER)
    check_roster(baseline_rows)
    measured = 1 + sum(1 for row in baseline_rows if row["status"] == "measured")

    report: dict[str, Any] = {
        "schema": REPORT_SCHEMA,
        "date_utc": "2026-08-14",
        "source_commit": _git_commit(),
        "environment": {"platform": platform.platform(), "python": sys.version.split()[0]},
        "configuration": {
            "router_version": ROUTER_VERSION,
            "routing_policy": ROUTING_POLICY,
            "router_limits": dict(ROUTER_LIMITS),
            "fixed_grid_step_nm": FIXED_GRID_STEP_NM,
            "seed": SEED,
            "runner_sha256": _file_digest(ROOT / SCRIPT_PATH),
            "corpus_runner_sha256": _file_digest(ROOT / CORPUS_RUNNER_PATH),
        },
        "metrics": {
            "problem_set": problem_set(manifest, samples),
            "protocol": {
                "common_input": (
                    "SimpleRouteJson bytes, verified against the corpus digest manifest before "
                    "any router sees them. A baseline that cannot read SimpleRouteJson needs a "
                    "bridge, and the bridge is named as a precondition on that baseline's row "
                    "rather than assumed away by declaring two documents equivalent."
                ),
                "metrics": [
                    {"name": name, "definition": definition}
                    for name, definition in COMPARISON_METRICS
                ],
                "accounting_identity": (
                    "every attempted net is accounted for by exactly one outcome, and the "
                    "breakdown sums to nets_attempted. The harness raises rather than reporting "
                    "a rate over a shortened denominator."
                ),
                "excluded_metrics": {
                    "markers": list(EXCLUDED_METRIC_MARKERS),
                    "reason": DRC_EXCLUSION_REASON,
                },
            },
            "routers": [subject, *baseline_rows],
            "measured_rows": measured,
            "declared_rows": 1 + len(BASELINE_ROSTER),
            "comparison_supported": measured >= 2,
            "corpora_considered": list(CORPORA_CONSIDERED),
            "deterministic_replays": True,
        },
        "timing": {
            "repetitions": repetitions,
            "mean_wall_seconds": {
                name: round(value, 3) for name, value in sorted(wall_times.items())
            },
        },
        "not_claimed": [
            "any comparison between routers. Every declared baseline resolves to not_run, so the "
            "table carries one measured row, and one row supports no comparative conclusion of "
            "any kind — not better, not worse, not comparable.",
            "any DRC, electrical, signal-integrity, thermal or fabrication result. No DRC metric "
            "is in the protocol and none is inferred from one that is.",
            "any route-quality claim. The recorded ratio is against a loose provable lower bound "
            "that ignores every obstacle, bend and detour, and is not an optimality statement.",
            "a whole-board completion result: each net is routed independently against the "
            "unrouted snapshot, so the candidates are not compatible with one another.",
            "generalisation beyond LLM-generated 2-layer tscircuit boards, or that the committed "
            "20-board prefix represents the full 36-board corpus.",
            "that this artifact re-measures anything. The subject row replays B-088's run "
            "unchanged and is expected to agree with it net for net.",
            "that a frozen SimpleRouteJson corpus discharges issue #110. It is a frozen corpus "
            "for the routing measurement and is not an externally authored KiCad family; R-153 "
            "stays open.",
        ],
    }
    canonical = json.dumps(report, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    report["run_id"] = "sha256:" + hashlib.sha256(canonical).hexdigest()
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--repetitions", type=int, default=2)
    parser.add_argument("--corpus", type=Path, default=CORPUS)
    arguments = parser.parse_args()
    try:
        report = build_report(arguments.repetitions, arguments.corpus)
    except (CrossRouterComparisonError, CorpusBenchmarkError) as error:
        print(f"cross-router comparison failed: {error}", file=sys.stderr)
        return 1
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
