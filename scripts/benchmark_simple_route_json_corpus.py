#!/usr/bin/env python3
"""Route an external SimpleRouteJson corpus with the existing routers and record what happened.

This is the runner for the open-baseline benchmark.  It imports every board in a corpus through
``copper_mcp.benchmarks.simple_route_json``, issues one ordinary :class:`RouteRequest` per imported
net, and records the outcome of each — a candidate, an already-connected finding, or a typed
refusal — with no filtering of any kind.

Three things about it are deliberate.

1. **The refusal breakdown is a result, not an error path.**  A net the router declines is counted
   under its exact :class:`RouteFailureCode` and reported next to the successes.  A run in which
   most nets refuse is a valid, publishable run; a run that quietly excluded them would not be.
2. **Two grid policies are recorded, not one.**  The reference A* router searches a uniform lattice
   anchored at a pad centre, and a two-pin request additionally requires the pad-centre delta to
   divide by the grid step.  External boards do not respect either constraint, so the harness runs
   the whole corpus twice: once at one fixed step, and once with the step chosen per net as the
   largest ladder entry that divides that net's pad-centre delta.  Reporting both is what makes the
   trade visible — the second policy converts ``off_grid`` refusals into ``grid_budget_exceeded``
   ones rather than into routes, and that is the finding.
3. **A baseline that is not installed is recorded as ``not_run``.**  There is no estimated,
   inferred, or remembered FreeRouting number anywhere in the output.

Nothing here applies copper, writes a board, or claims DRC.  Every candidate is an unapplied
proposal whose geometry the router already validated against the same Board IR snapshot.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import shutil
import subprocess
import sys
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from copper_mcp.benchmarks.simple_route_json import (
    DEFAULT_IMPORT_POLICY,
    MM_TO_NM_RULE,
    SIMPLE_ROUTE_JSON_ADAPTER_VERSION,
    ImportedProblem,
    SimpleRouteJsonImportError,
    import_simple_route_json,
)
from copper_mcp.routing import ROUTER_VERSION, ROUTING_POLICY, AStarRouter
from copper_mcp.routing.contracts import AStarSettings, RouteRequest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = "scripts/benchmark_simple_route_json_corpus.py"
ADAPTER_PATH = "src/copper_mcp/benchmarks/simple_route_json.py"
CORPUS = ROOT / "benchmarks/corpora/tscircuit-benchmark"
DEFAULT_OUTPUT = ROOT / "benchmarks/results/routing/2026-08-06-simple-route-json-corpus-v1.json"
REPORT_SCHEMA = "copper-mcp/benchmark/simple-route-json-corpus/v1"

#: One fixed lattice step for the first configuration, and the ladder the second one selects from.
#: Both are declared here rather than derived, so a recorded number is bound to a stated policy.
FIXED_GRID_STEP_NM = 250_000
GRID_STEP_LADDER_NM: tuple[int, ...] = (
    250_000,
    200_000,
    125_000,
    100_000,
    50_000,
    25_000,
    20_000,
    10_000,
    5_000,
    2_000,
    1_000,
)
#: Resource ceilings every request shares, so no board gets a bigger budget than another.
ROUTER_LIMITS: dict[str, int] = {
    "max_grid_nodes": 250_000,
    "max_expansions": 100_000,
    "max_obstacles": 2_048,
    "max_obstacle_checks": 2_000_000,
}
SEED = 0
#: Baselines the harness knows how to look for.  Absent means ``not_run``, never an estimate.
BASELINE_EXECUTABLES: tuple[str, ...] = ("freerouting",)


class CorpusBenchmarkError(RuntimeError):
    """Raised when the harness itself is broken — a bad manifest, or a non-deterministic replay."""


@dataclass(frozen=True, slots=True)
class GridPolicy:
    """How a routing request's lattice step is chosen for one net."""

    name: str
    description: str

    def step_for(self, problem: ImportedProblem, pad_ids: tuple[str, ...]) -> int:
        """Return the lattice step this policy assigns, before any routing is attempted."""

        if self.name == "fixed":
            return FIXED_GRID_STEP_NM
        if len(pad_ids) != 2:
            return GRID_STEP_LADDER_NM[0]
        centres = [pad.center for pad in problem.snapshot.content.pads if pad.id in set(pad_ids)]
        if len(centres) != 2:
            return GRID_STEP_LADDER_NM[0]
        delta = math.gcd(abs(centres[0].x - centres[1].x), abs(centres[0].y - centres[1].y))
        if delta == 0:
            return GRID_STEP_LADDER_NM[0]
        for step in GRID_STEP_LADDER_NM:
            if delta % step == 0:
                return step
        return GRID_STEP_LADDER_NM[-1]


FIXED_POLICY = GridPolicy(
    name="fixed",
    description=(
        f"every request uses a {FIXED_GRID_STEP_NM} nm lattice step, so a two-pin net whose "
        "pad-centre delta does not divide by it refuses with off_grid"
    ),
)
DIVISOR_POLICY = GridPolicy(
    name="divisor-aligned",
    description=(
        "a two-pin net uses the largest ladder step dividing the greatest common divisor of its "
        "pad-centre delta, decided from the net's geometry before routing and never by retrying; "
        "a wider net uses the ladder's first entry, because the router imposes no divisibility "
        "requirement on it"
    ),
)
POLICIES: tuple[GridPolicy, ...] = (FIXED_POLICY, DIVISOR_POLICY)


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
    """Return the SHA-256 of one repository file, prefixed the way every other record is."""

    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def load_manifest(corpus: Path = CORPUS) -> dict[str, Any]:
    """Read and structurally check a corpus manifest."""

    document = json.loads((corpus / "manifest.json").read_text(encoding="utf-8"))
    if not isinstance(document, dict) or document.get("schema") != "copper-mcp/benchmark-corpus/v1":
        raise CorpusBenchmarkError("corpus manifest schema is unsupported")
    if document.get("license_spdx") != "MIT" or document.get("redistribution_allowed") is not True:
        raise CorpusBenchmarkError("corpus manifest does not record redistribution permission")
    license_digest = hashlib.sha256((corpus / "LICENSE").read_bytes()).hexdigest()
    if document.get("license_sha256") != license_digest:
        raise CorpusBenchmarkError("committed corpus licence does not match its manifest digest")
    return document


def load_corpus(corpus: Path = CORPUS) -> tuple[dict[str, Any], tuple[tuple[str, bytes], ...]]:
    """Return the manifest and every committed sample whose bytes match their recorded digest.

    A file whose digest has drifted is a broken corpus, not a hard board, so it raises rather than
    being recorded as a refusal.
    """

    manifest = load_manifest(corpus)
    files = manifest["files"]
    if not isinstance(files, list):
        raise CorpusBenchmarkError("corpus manifest carries no file list")
    samples: list[tuple[str, bytes]] = []
    for entry in files:
        if not isinstance(entry, dict) or not entry.get("committed"):
            continue
        name = str(entry["name"])
        payload = (corpus / "samples" / name).read_bytes()
        if hashlib.sha256(payload).hexdigest() != entry["sha256"]:
            raise CorpusBenchmarkError(f"committed corpus file does not match its digest: {name}")
        samples.append((name, payload))
    if not samples:
        raise CorpusBenchmarkError("corpus manifest marks no file as committed")
    return manifest, tuple(sorted(samples))


def _route_problem(
    problem: ImportedProblem, policy: GridPolicy, router: AStarRouter
) -> dict[str, Any]:
    """Route every imported net of one problem under one grid policy."""

    outcomes: Counter[str] = Counter()
    steps: Counter[str] = Counter()
    routed_length_nm = 0
    routed_lower_bound_nm = 0
    two_pin_centre_manhattan_nm = 0
    vias = 0
    bends = 0
    candidate_ids: list[str] = []
    for net in problem.nets:
        if net.pad_count < 2:
            # Not a refusal and not a success: a one-pad net states nothing to route. It is
            # counted separately so the denominator of the success rate stays honest.
            outcomes["no_routing_work"] += 1
            continue
        step = policy.step_for(problem, net.pad_ids)
        steps[str(step)] += 1
        request = RouteRequest(
            board_revision=problem.snapshot.snapshot_digest,
            net_id=net.net_id,
            layer_id=net.layer_id,
            seed=SEED,
            settings=AStarSettings(grid_step_nm=step, **ROUTER_LIMITS),
        )
        result = router.propose(problem.snapshot, request)
        if result.candidate is not None:
            outcomes["routed"] += 1
            candidate_ids.append(result.candidate.candidate_id)
            routed_length_nm += result.candidate.metrics.wire_length_nm
            routed_lower_bound_nm += net.pad_gap_lower_bound_nm
            two_pin_centre_manhattan_nm += net.two_pin_centre_manhattan_nm or 0
            vias += result.candidate.metrics.vias
            bends += result.candidate.cost.bend_count
        elif result.connected is not None:
            outcomes["already_connected"] += 1
        else:
            assert result.diagnostic is not None
            outcomes[f"refused:{result.diagnostic.code}"] += 1
    return {
        "board": problem.name,
        "document_sha256": problem.document_sha256,
        "board_revision": problem.snapshot.snapshot_digest,
        "import": problem.statistics.payload(),
        "track_width_nm": problem.track_width_nm,
        "outcomes": dict(sorted(outcomes.items())),
        "grid_steps_nm": dict(sorted(steps.items(), key=lambda item: int(item[0]))),
        "routed_wire_length_nm": routed_length_nm,
        "routed_pad_gap_lower_bound_nm": routed_lower_bound_nm,
        "routed_two_pin_centre_manhattan_nm": two_pin_centre_manhattan_nm,
        "vias": vias,
        "bends": bends,
        "candidate_digest": hashlib.sha256("\n".join(candidate_ids).encode("utf-8")).hexdigest(),
    }


def run_configuration(samples: tuple[tuple[str, bytes], ...], policy: GridPolicy) -> dict[str, Any]:
    """Import and route the whole corpus once under one grid policy."""

    router = AStarRouter()
    boards: list[dict[str, Any]] = []
    import_refusals: Counter[str] = Counter()
    outcomes: Counter[str] = Counter()
    routed_length_nm = 0
    routed_lower_bound_nm = 0
    two_pin_centre_manhattan_nm = 0
    vias = 0
    bends = 0
    imported = 0
    for name, payload in samples:
        board_name = Path(name).stem
        try:
            problem = import_simple_route_json(board_name, payload)
        except SimpleRouteJsonImportError as refusal:
            import_refusals[str(refusal.code)] += 1
            boards.append({"board": board_name, "import_refusal": str(refusal.code)})
            continue
        imported += 1
        record = _route_problem(problem, policy, router)
        boards.append(record)
        for code, count in record["outcomes"].items():
            outcomes[code] += count
        routed_length_nm += record["routed_wire_length_nm"]
        routed_lower_bound_nm += record["routed_pad_gap_lower_bound_nm"]
        two_pin_centre_manhattan_nm += record["routed_two_pin_centre_manhattan_nm"]
        vias += record["vias"]
        bends += record["bends"]

    attempted = sum(count for code, count in outcomes.items() if code != "no_routing_work")
    routed = outcomes.get("routed", 0)
    return {
        "grid_policy": {"name": policy.name, "description": policy.description},
        "boards_offered": len(samples),
        "boards_imported": imported,
        "import_refusals": dict(sorted(import_refusals.items())),
        "nets_attempted": attempted,
        "nets_routed": routed,
        # Recorded as an exact fraction as well as a percentage: a reader should never have to
        # trust a float to reconstruct the count.
        "success_rate_percent": (routed * 100 / attempted) if attempted else 0.0,
        "outcome_breakdown": dict(sorted(outcomes.items())),
        "routed_wire_length_nm": routed_length_nm,
        "routed_pad_gap_lower_bound_nm": routed_lower_bound_nm,
        "routed_two_pin_centre_manhattan_nm": two_pin_centre_manhattan_nm,
        "length_over_pad_gap_lower_bound": (
            routed_length_nm / routed_lower_bound_nm if routed_lower_bound_nm else 0.0
        ),
        "vias": vias,
        "bends": bends,
        "boards": boards,
    }


def _baseline_status() -> dict[str, Any]:
    """Report each known baseline as ``present`` or ``not_run``; never as a remembered number."""

    baselines = {}
    for executable in BASELINE_EXECUTABLES:
        located = shutil.which(executable)
        baselines[executable] = {
            "status": "not_run",
            "reason": (
                "executable is on PATH but this harness has no SimpleRouteJson-to-DSN bridge yet"
                if located is not None
                else "executable is not installed in this environment"
            ),
            "on_path": located is not None,
        }
    return baselines


def run_benchmark(
    repetitions: int = 2, corpus: Path = CORPUS
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run every declared configuration ``repetitions`` times and confirm the replays agree."""

    if not 1 <= repetitions <= 8:
        raise ValueError("repetitions must be between 1 and 8")
    manifest, samples = load_corpus(corpus)
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
                raise CorpusBenchmarkError(
                    f"deterministic replay diverged for grid policy {policy.name}"
                )
        assert first is not None
        configurations[policy.name] = first
        wall_times[policy.name] = elapsed / repetitions
    timing = {
        "repetitions": repetitions,
        "mean_wall_seconds": {name: round(value, 3) for name, value in wall_times.items()},
    }
    return {
        "corpus": {
            "corpus_id": manifest["corpus_id"],
            "upstream_repository": manifest["upstream_repository"],
            "upstream_commit": manifest["upstream_commit"],
            "license_spdx": manifest["license_spdx"],
            "license_sha256": manifest["license_sha256"],
            "committed_subset_rule": manifest["committed_subset_rule"],
            "committed_boards": len(samples),
            "upstream_sample_count": manifest["upstream_sample_count"],
        },
        "deterministic_replays": True,
        "configurations": configurations,
        "baselines": _baseline_status(),
    }, timing


def build_report(repetitions: int = 2, corpus: Path = CORPUS) -> dict[str, Any]:
    """Build the canonical, self-digesting benchmark report without writing it."""

    metrics, timing = run_benchmark(repetitions, corpus)
    report: dict[str, Any] = {
        "schema": REPORT_SCHEMA,
        "date_utc": "2026-08-06",
        "source_commit": _git_commit(),
        "environment": {"platform": platform.platform(), "python": sys.version.split()[0]},
        "configuration": {
            "adapter_version": SIMPLE_ROUTE_JSON_ADAPTER_VERSION,
            "router_version": ROUTER_VERSION,
            "routing_policy": ROUTING_POLICY,
            "import_policy": DEFAULT_IMPORT_POLICY.payload(),
            "millimetre_rule": MM_TO_NM_RULE,
            "fixed_grid_step_nm": FIXED_GRID_STEP_NM,
            "grid_step_ladder_nm": list(GRID_STEP_LADDER_NM),
            "router_limits": dict(ROUTER_LIMITS),
            "seed": SEED,
            # The two files that decide every number above, so a reader never has to guess which
            # revision of the harness or the adapter produced the run.
            "runner_sha256": _file_digest(ROOT / SCRIPT_PATH),
            "adapter_sha256": _file_digest(ROOT / ADAPTER_PATH),
        },
        "metrics": metrics,
        # Wall time is part of the recorded report and therefore part of ``run_id``, which is the
        # convention every other artifact under ``benchmarks/results`` follows and which
        # ``scripts/check_ledgers.py`` enforces. It means the identity is machine-bound; the
        # machine-independent claim is ``metrics``, and that is what the regression test replays.
        "timing": timing,
        "lower_bound_definition": (
            "pad_gap_lower_bound_nm is the sum, over routed nets, of max(0, max(pad.min_x) - "
            "min(pad.max_x)) + max(0, max(pad.min_y) - min(pad.max_y)). Any rectilinear tree "
            "touching every pad of the net must span at least that far in x and in y, so the sum "
            "is a provable lower bound on routed length. It is not tight: it ignores every "
            "obstacle, every bend, and every detour."
        ),
        "not_claimed": [
            "a comparison against FreeRouting, Electra, or any other router; every baseline in "
            "this run is recorded as not_run",
            "KiCad DRC, electrical correctness, signal integrity, thermal behaviour, or "
            "fabrication readiness for any imported board",
            "board mutation, apply authority, export, or live-editor behaviour",
            "a whole-board completion result: each net is routed independently against the "
            "unrouted snapshot, so the candidates are not compatible with one another",
            "generalisation beyond LLM-generated 2-layer tscircuit boards; the corpus was "
            "generated from plain-English specs by a language model and routed with FreeRouting "
            "as part of its construction",
            "that the committed 20-board subset represents the full 36-board corpus",
            "routing optimality: the recorded ratio is against a loose provable lower bound, not "
            "against an optimal route",
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
    report = build_report(arguments.repetitions, arguments.corpus)
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
