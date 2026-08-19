#!/usr/bin/env python3
"""Census the negotiated coordinator, whole board, across the committed SimpleRouteJson corpus.

Issue #90's remaining gate is held-out evidence that the opt-in local-repair transaction
(ADR-0117) is worth enabling.  That transaction fires from exactly one place — the whole-set
physical-clearance gate returning ``CLEARANCE_VIOLATION`` on a *complete* allocation with at least
two violating nets — and nobody had ever measured how often a real board reaches it.  Before this
runner, ``negotiate_routes`` had only ever been run on synthetic two-net fixtures (B-036, B-087,
B-092).  This is the measure-first census: it runs the coordinator **without** ``repair_settings``
once per board over the 20 committed boards and records where each run actually stops.

Four things about it are deliberate.

1. **Repair is never enabled.**  The census measures the *frequency of repair's firing
   precondition* under the legacy no-repair coordinator, which is the number that decides whether
   enabling repair is worth a slice at all.  Enabling repair to find out would answer a different
   question and would spend the budget the answer is supposed to authorise.
2. **The precondition is observed, not inferred from the terminal status.**  The published
   ``NegotiatedRoutingResult`` does not carry the gate's per-iteration failure or its violating-net
   attribution, so a status alone cannot distinguish "the gate rejected a complete allocation" from
   "some net never routed".  The runner therefore installs a pure pass-through recorder on the
   coordinator's physical-gate symbol for the duration of one call and reads the gate's own
   results.  Because a recorder that changed behaviour would silently invent the census, every
   board is additionally run *uninstrumented* and the two published projections must be identical
   or the run fails.
3. **Admissibility is decided twice, independently.**  The runner computes, from public snapshot
   data alone, which of the coordinator's declared first-slice conjuncts hold for each board, and
   then requires the coordinator's own published refusal to agree.  A disagreement is a harness
   defect and raises rather than being recorded.
4. **The submitted set is pinned to B-088.**  The primary configuration submits exactly the nets
   B-088's fixed-policy run routed, re-derived here and checked net-count-for-net-count against the
   committed, self-digest-verified B-088 artifact.  That makes the negotiated completion number
   comparable with B-088's 70-of-117 per-net reference baseline rather than merely adjacent to it.

Nothing here applies copper, writes a board, runs KiCad, or claims DRC.  A completion count
recorded by this runner is the coordinator's behaviour at these declared budgets on this corpus and
is not a routing-quality claim about any router.
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
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from copper_mcp.benchmarks.simple_route_json import (
    SIMPLE_ROUTE_JSON_ADAPTER_VERSION,
    ImportedProblem,
    SimpleRouteJsonImportError,
    import_simple_route_json,
)
from copper_mcp.routing import ROUTER_VERSION, ROUTING_POLICY, AStarRouter
from copper_mcp.routing import congestion as coordinator
from copper_mcp.routing.congestion import (
    NEGOTIATED_ROUTING_POLICY,
    NegotiatedRoutingRequest,
    negotiate_routes,
)
from copper_mcp.routing.contracts import AStarSettings, RouteRequest
from copper_mcp.routing.physical_clearance import PhysicalClearanceFailure
from scripts import benchmark_simple_route_json_corpus as reference

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = "scripts/benchmark_negotiated_corpus_census.py"
REFERENCE_RUNNER_PATH = "scripts/benchmark_simple_route_json_corpus.py"
CORPUS = reference.CORPUS
REFERENCE_ARTIFACT = reference.DEFAULT_OUTPUT
DEFAULT_OUTPUT = ROOT / "benchmarks/results/routing/2026-08-20-negotiated-corpus-census-v1.json"
REPORT_SCHEMA = "copper-mcp/benchmark/negotiated-corpus-census/v1"

#: The grid policy the census inherits from B-088 unchanged, so the negotiated number and the
#: per-net reference number describe the same lattice.  Choosing a different step per board is
#: exactly the tuning this census is predeclared not to do.
FIXED_GRID_STEP_NM = reference.FIXED_GRID_STEP_NM
ROUTER_LIMITS: dict[str, int] = dict(reference.ROUTER_LIMITS)
SEED = reference.SEED

#: The negotiated envelope's shared budgets, written out rather than inherited from the dataclass
#: defaults so that a later default change cannot silently redefine what was measured here.
ENVELOPE_BUDGETS: dict[str, int] = {
    "max_iterations": 8,
    "present_penalty_nm": 20_000_000,
    "history_penalty_nm": 5_000_000,
    "max_total_expansions": 2_000_000,
    "max_total_obstacle_checks": 10_000_000,
    "max_total_physical_checks": 2_000_000,
}

#: The coordinator's declared first-slice admission conjuncts, in the order it evaluates them.
#: The first two are enforced by ``NegotiatedRoutingRequest.__post_init__`` and therefore refuse
#: before ``negotiate_routes`` is reachable at all; the last two are enforced inside the
#: coordinator and surface as an ``invalid_request`` result with a fixed diagnostic.
ADMISSION_CONJUNCTS: tuple[tuple[str, str, str], ...] = (
    (
        "at_least_two_requests",
        "envelope_construction",
        "a negotiated envelope carries between 2 and 32 distinct nets",
    ),
    (
        "one_selected_layer_and_grid_step",
        "envelope_construction",
        "the first negotiated slice requires one layer and one grid step across every request",
    ),
    (
        "exactly_two_selected_layer_pads_per_net",
        "coordinator_admission",
        "each negotiated net must expose exactly two pads on the selected layer",
    ),
    (
        "one_shared_world_grid",
        "coordinator_admission",
        "all negotiated pad centres must share one world-coordinate grid",
    ),
)
#: The exact coordinator diagnostics the two coordinator-side conjuncts must produce.  Pinning the
#: strings is what turns the independent predicate into a cross-check rather than a restatement.
COORDINATOR_DIAGNOSTICS: dict[str, str] = {
    "exactly_two_selected_layer_pads_per_net": (
        "each negotiated net must expose exactly two pads on the selected layer"
    ),
    "one_shared_world_grid": "all negotiated pad centres must share one world-coordinate grid",
}

#: Where a board's census run stopped.  Ordered from earliest to latest; only the last value means
#: the repair transaction of ADR-0117 would have had an input.
BLOCKING_STAGES: tuple[str, ...] = (
    "envelope_construction",
    "coordinator_admission",
    "no_physical_gate_call",
    "no_clearance_violation",
    "clearance_violation_on_incomplete_allocation",
    "clearance_violation_with_one_violating_net",
    "repair_precondition_reached",
)


class NegotiatedCensusError(RuntimeError):
    """Raised when the harness itself is broken, or when a recorded number cannot be trusted."""


@dataclass(frozen=True, slots=True)
class GateObservation:
    """One whole-set physical-clearance gate call, as the coordinator itself saw it."""

    candidates: int
    failure: str | None
    violating_nets: int
    pair_checks: int

    def payload(self) -> dict[str, Any]:
        return {
            "candidates": self.candidates,
            "failure": self.failure,
            "pair_checks": self.pair_checks,
            "violating_nets": self.violating_nets,
        }


@contextmanager
def observed_physical_gate() -> Iterator[list[GateObservation]]:
    """Record every whole-set physical-clearance call the coordinator makes, changing nothing.

    The recorder forwards the identical arguments to the identical function and returns the
    identical object, so it cannot alter an allocation, a budget, or a published field.  The runner
    does not take that on trust: :func:`census_board` runs each board with and without this
    recorder and refuses to publish if the two projections differ.
    """

    observations: list[GateObservation] = []
    original = coordinator.verify_negotiated_physical_clearance

    def recording(
        snapshot: object,
        candidates: object,
        *,
        layer_id: object,
        max_pair_checks: object,
        cancelled: Any = None,
    ) -> Any:
        result = original(
            snapshot,
            candidates,
            layer_id=layer_id,
            max_pair_checks=max_pair_checks,
            cancelled=cancelled,
        )
        observations.append(
            GateObservation(
                candidates=len(candidates) if isinstance(candidates, tuple) else -1,
                failure=None if result.failure is None else str(result.failure.value),
                violating_nets=len(result.violating_nets),
                pair_checks=result.pair_checks,
            )
        )
        return result

    coordinator.verify_negotiated_physical_clearance = recording  # type: ignore[assignment]
    try:
        yield observations
    finally:
        coordinator.verify_negotiated_physical_clearance = original  # type: ignore[assignment]


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


def load_reference_artifact(path: Path = REFERENCE_ARTIFACT) -> dict[str, Any]:
    """Return the committed B-088 artifact after checking it against its own self-digest.

    The census pins its submitted set to B-088's routed set, so a drifted or edited B-088 artifact
    would silently redefine what this census measured.  It is verified rather than read.
    """

    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise NegotiatedCensusError("the recorded reference artifact is not one JSON object")
    recorded = document.get("run_id")
    body = {key: value for key, value in document.items() if key != "run_id"}
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    if recorded != "sha256:" + hashlib.sha256(canonical).hexdigest():
        raise NegotiatedCensusError("the recorded reference artifact fails its own self-digest")
    if document.get("schema") != reference.REPORT_SCHEMA:
        raise NegotiatedCensusError("the recorded reference artifact carries an unexpected schema")
    return document


def _reference_routed_by_board(document: dict[str, Any]) -> dict[str, int]:
    """Project B-088's fixed-policy routed count for every board it imported."""

    configuration = document["metrics"]["configurations"]["fixed"]
    return {
        str(board["board"]): int(board["outcomes"].get("routed", 0))
        for board in configuration["boards"]
        if "outcomes" in board
    }


def _request_for(problem: ImportedProblem, net_id: str, layer_id: str) -> RouteRequest:
    return RouteRequest(
        board_revision=problem.snapshot.snapshot_digest,
        net_id=net_id,
        layer_id=layer_id,
        seed=SEED,
        settings=AStarSettings(grid_step_nm=FIXED_GRID_STEP_NM, **ROUTER_LIMITS),
    )


def _selected_layer_pads(problem: ImportedProblem, net_id: str, layer_id: str) -> list[Any]:
    return sorted(
        (
            pad
            for pad in problem.snapshot.content.pads
            if pad.net_id == net_id and layer_id in pad.layer_ids
        ),
        key=lambda pad: pad.id,
    )


@dataclass(frozen=True, slots=True)
class SubmittedNet:
    """One net the census offers to the negotiated coordinator, with its solo reference outcome."""

    net_id: str
    layer_id: str
    reference_outcome: str
    selected_layer_pads: int


def _solo_reference(problem: ImportedProblem, router: AStarRouter) -> tuple[SubmittedNet, ...]:
    """Route every multi-pad net of one board on its own, exactly as the B-088 runner does.

    This is the reference baseline the negotiated numbers are read against, and it is also how the
    census learns which nets are ``already_connected`` — a net the coordinator counts as allocated
    without producing a candidate, and therefore part of the completeness test below.
    """

    outcomes: list[SubmittedNet] = []
    for net in problem.nets:
        if net.pad_count < 2:
            continue
        result = router.propose(problem.snapshot, _request_for(problem, net.net_id, net.layer_id))
        if result.candidate is not None:
            outcome = "routed"
        elif result.connected is not None:
            outcome = "already_connected"
        else:
            assert result.diagnostic is not None
            outcome = f"refused:{result.diagnostic.code}"
        outcomes.append(
            SubmittedNet(
                net_id=net.net_id,
                layer_id=net.layer_id,
                reference_outcome=outcome,
                selected_layer_pads=len(_selected_layer_pads(problem, net.net_id, net.layer_id)),
            )
        )
    return tuple(sorted(outcomes, key=lambda item: item.net_id))


def _admission(problem: ImportedProblem, submitted: tuple[SubmittedNet, ...]) -> dict[str, bool]:
    """Decide each declared admission conjunct from public snapshot data alone.

    Nothing private to the coordinator is imported.  The point of computing this independently is
    that :func:`census_board` then requires the coordinator's own refusal to agree with it.
    """

    layers = {item.layer_id for item in submitted}
    held: dict[str, bool] = {
        "at_least_two_requests": 2 <= len(submitted) <= 32,
        "one_selected_layer_and_grid_step": len(layers) == 1,
        "exactly_two_selected_layer_pads_per_net": bool(submitted)
        and all(item.selected_layer_pads == 2 for item in submitted),
        "one_shared_world_grid": False,
    }
    if held["at_least_two_requests"] and held["one_selected_layer_and_grid_step"]:
        layer_id = next(iter(layers))
        centres: list[Any] = []
        for item in sorted(submitted, key=lambda entry: (entry.net_id, SEED)):
            centres.extend(
                pad.center for pad in _selected_layer_pads(problem, item.net_id, layer_id)
            )
        if centres and held["exactly_two_selected_layer_pads_per_net"]:
            origin = centres[0]
            held["one_shared_world_grid"] = all(
                (centre.x - origin.x) % FIXED_GRID_STEP_NM == 0
                and (centre.y - origin.y) % FIXED_GRID_STEP_NM == 0
                for centre in centres
            )
    return held


def _first_unmet(held: dict[str, bool]) -> tuple[str, str] | None:
    for name, stage, _description in ADMISSION_CONJUNCTS:
        if not held[name]:
            return name, stage
    return None


def _projection(result: Any) -> dict[str, Any]:
    """The published fields of one negotiated run, as the artifact records them."""

    return {
        "diagnostic": result.diagnostic,
        "iterations": result.iterations,
        "negotiated_candidates": len(result.candidates),
        "overflow_units": result.overflow_units,
        "ripups": result.ripups,
        "status": result.status.value,
        "total_physical_checks": result.total_physical_checks,
        "total_wire_length_nm": result.total_wire_length_nm,
        "unrouted_nets": list(result.unrouted_nets),
    }


def _stage_from_observations(
    observations: tuple[GateObservation, ...], *, submitted: int, connectable: int
) -> str:
    """Return the latest stage any gate call reached, per ADR-0117's exact firing precondition.

    ``connectable`` is the number of submitted nets the solo reference reported as
    ``already_connected``.  It is used as an **upper bound** on the connections the coordinator can
    hold, because the recorder sees the candidate tuple but not the connection map.  Over-counting
    connections can only make the completeness conjunct easier to satisfy, so it can never turn a
    reached precondition into an unreached one — a recorded zero is therefore not an artefact of
    this bound.
    """

    if not observations:
        return "no_physical_gate_call"
    stage = "no_clearance_violation"
    for observation in observations:
        if observation.failure != PhysicalClearanceFailure.CLEARANCE_VIOLATION.value:
            continue
        complete = observation.candidates + connectable >= submitted
        if not complete:
            stage = _later(stage, "clearance_violation_on_incomplete_allocation")
        elif observation.violating_nets < 2:
            stage = _later(stage, "clearance_violation_with_one_violating_net")
        else:
            stage = _later(stage, "repair_precondition_reached")
    return stage


def _later(current: str, candidate: str) -> str:
    return (
        candidate if BLOCKING_STAGES.index(candidate) > BLOCKING_STAGES.index(current) else current
    )


def census_board(
    problem: ImportedProblem,
    submitted: tuple[SubmittedNet, ...],
    router: AStarRouter,
) -> dict[str, Any]:
    """Run the coordinator once for one board's submitted set and record where it stopped."""

    held = _admission(problem, submitted)
    unmet = _first_unmet(held)
    record: dict[str, Any] = {
        "board": problem.name,
        "document_sha256": problem.document_sha256,
        "board_revision": problem.snapshot.snapshot_digest,
        "submitted_nets": len(submitted),
        "submitted_net_ids": [item.net_id for item in submitted],
        "reference_already_connected": sum(
            1 for item in submitted if item.reference_outcome == "already_connected"
        ),
        "admission_conjuncts": dict(sorted(held.items())),
        "first_unmet_conjunct": None if unmet is None else unmet[0],
        "envelope_constructed": False,
        "envelope_refusal": None,
    }

    if unmet is not None and unmet[1] == "envelope_construction":
        record["terminal_status"] = None
        record["negotiated"] = None
        record["physical_gate_calls"] = 0
        record["physical_gate_observations"] = []
        record["blocking_stage"] = "envelope_construction"
        record["repair_precondition_reached"] = False
        record["envelope_refusal"] = unmet[0]
        return record

    requests = tuple(
        _request_for(problem, item.net_id, item.layer_id)
        for item in sorted(submitted, key=lambda entry: entry.net_id)
    )
    try:
        envelope = NegotiatedRoutingRequest(
            board_revision=problem.snapshot.snapshot_digest,
            requests=requests,
            **ENVELOPE_BUDGETS,
        )
    except ValueError as refusal:  # pragma: no cover - the predicate above already ruled this out
        raise NegotiatedCensusError(
            f"{problem.name}: the envelope refused construction after the admission predicate "
            f"accepted it: {refusal}"
        ) from refusal
    record["envelope_constructed"] = True
    record["envelope_policy_digest"] = envelope.policy_digest

    # The control run. Repair is never enabled: `repair_settings` is not passed here or below.
    control = _projection(negotiate_routes(problem.snapshot, envelope, router=router))
    with observed_physical_gate() as observations:
        instrumented = _projection(negotiate_routes(problem.snapshot, envelope, router=router))
    if instrumented != control:
        raise NegotiatedCensusError(
            f"{problem.name}: the physical-gate recorder changed the published negotiated result"
        )

    seen = tuple(observations)
    connectable = int(record["reference_already_connected"])
    stage = _stage_from_observations(seen, submitted=len(submitted), connectable=connectable)
    if instrumented["status"] == "invalid_request":
        expected = COORDINATOR_DIAGNOSTICS.get(unmet[0]) if unmet is not None else None
        if expected is None or instrumented["diagnostic"] != expected:
            raise NegotiatedCensusError(
                f"{problem.name}: the coordinator's refusal does not match the independently "
                f"computed admission conjunct ({unmet} vs {instrumented['diagnostic']!r})"
            )
        stage = "coordinator_admission"
    elif unmet is not None:
        raise NegotiatedCensusError(
            f"{problem.name}: {unmet[0]} was computed unmet but the coordinator admitted the "
            "envelope"
        )

    record["terminal_status"] = instrumented["status"]
    record["negotiated"] = instrumented
    record["physical_gate_calls"] = len(seen)
    record["physical_gate_observations"] = [item.payload() for item in seen]
    record["blocking_stage"] = stage
    record["repair_precondition_reached"] = stage == "repair_precondition_reached"
    return record


@dataclass(frozen=True, slots=True)
class Configuration:
    """One declared rule for choosing which nets a board submits as its negotiated envelope."""

    name: str
    description: str

    def select(self, submitted: tuple[SubmittedNet, ...]) -> tuple[SubmittedNet, ...]:
        if self.name == "b088-routable":
            return tuple(item for item in submitted if item.reference_outcome == "routed")
        return tuple(item for item in submitted if item.selected_layer_pads == 2)


PRIMARY = Configuration(
    name="b088-routable",
    description=(
        "every net B-088's fixed 250,000 nm run routed, submitted as one whole-board negotiated "
        "envelope; this is the configuration issue #90's census was defined on, and its "
        "completion count is directly comparable with B-088's per-net reference baseline"
    ),
)
TWO_PAD_CONTROL = Configuration(
    name="two-pad-control",
    description=(
        "every net with exactly two selected-layer pads, regardless of whether the per-net "
        "reference routed it. This configuration was added after an exploratory probe found the "
        "primary one blocked at the two-pin conjunct, and it is declared here as a control rather "
        "than a result: it can only add a refusal reason, never convert a refusal into a route"
    ),
)
CONFIGURATIONS: tuple[Configuration, ...] = (PRIMARY, TWO_PAD_CONTROL)


def run_configuration(
    samples: tuple[tuple[str, bytes], ...],
    configuration: Configuration,
    reference_routed: dict[str, int],
) -> dict[str, Any]:
    """Run the whole corpus once under one declared configuration."""

    router = AStarRouter()
    boards: list[dict[str, Any]] = []
    stages: dict[str, int] = dict.fromkeys(BLOCKING_STAGES, 0)
    unmet: dict[str, int] = {name: 0 for name, _stage, _text in ADMISSION_CONJUNCTS}
    unmet["none"] = 0
    statuses: dict[str, int] = {}
    submitted_total = 0
    submitted_reference_routed = 0
    completed_total = 0
    reference_total = 0
    gate_calls = 0
    envelopes = 0
    admitted = 0
    imported = 0
    for name, payload in samples:
        board_name = Path(name).stem
        try:
            problem = import_simple_route_json(board_name, payload)
        except SimpleRouteJsonImportError as refusal:  # pragma: no cover - corpus imports cleanly
            boards.append({"board": board_name, "import_refusal": str(refusal.code)})
            continue
        imported += 1
        solo = _solo_reference(problem, router)
        routed_here = sum(1 for item in solo if item.reference_outcome == "routed")
        expected = reference_routed.get(board_name)
        if expected is None or routed_here != expected:
            raise NegotiatedCensusError(
                f"{board_name}: the re-derived reference routed count {routed_here} does not "
                f"match the committed B-088 artifact's {expected}"
            )
        reference_total += routed_here
        selected = configuration.select(solo)
        submitted_total += len(selected)
        submitted_reference_routed += sum(
            1 for item in selected if item.reference_outcome == "routed"
        )
        record = census_board(problem, selected, router)
        record["reference_nets_routed"] = routed_here
        record["submitted_nets_the_reference_routed"] = sum(
            1 for item in selected if item.reference_outcome == "routed"
        )
        boards.append(record)
        stages[str(record["blocking_stage"])] += 1
        unmet[str(record["first_unmet_conjunct"] or "none")] += 1
        status = record.get("terminal_status")
        statuses[str(status)] = statuses.get(str(status), 0) + 1
        gate_calls += int(record["physical_gate_calls"])
        envelopes += 1 if record["envelope_constructed"] else 0
        if record["envelope_constructed"] and record["terminal_status"] != "invalid_request":
            admitted += 1
        negotiated = record.get("negotiated")
        if isinstance(negotiated, dict) and negotiated["status"] == "completed":
            completed_total += int(negotiated["negotiated_candidates"])

    return {
        "configuration": {"name": configuration.name, "description": configuration.description},
        "boards_offered": len(samples),
        "boards_imported": imported,
        "boards_with_a_constructible_envelope": envelopes,
        "boards_admitted_by_the_coordinator": admitted,
        "boards_reaching_the_repair_precondition": stages["repair_precondition_reached"],
        "blocking_stage_breakdown": dict(sorted(stages.items())),
        "first_unmet_conjunct_breakdown": dict(sorted(unmet.items())),
        "terminal_status_breakdown": dict(sorted(statuses.items())),
        "nets_submitted": submitted_total,
        # Read this against ``nets_submitted``: it is the overlap between the population the
        # negotiated slice can represent and the population the per-net reference can route.
        "submitted_nets_the_reference_routed": submitted_reference_routed,
        "negotiated_nets_completed": completed_total,
        "reference_per_net_nets_routed": reference_total,
        "physical_gate_calls": gate_calls,
        "boards": boards,
    }


def run_census(
    repetitions: int = 2, corpus: Path = CORPUS
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run every declared configuration ``repetitions`` times and confirm the replays agree."""

    if not 1 <= repetitions <= 8:
        raise ValueError("repetitions must be between 1 and 8")
    manifest, samples = reference.load_corpus(corpus)
    recorded = load_reference_artifact()
    reference_routed = _reference_routed_by_board(recorded)
    configurations: dict[str, Any] = {}
    wall_times: dict[str, float] = {}
    for configuration in CONFIGURATIONS:
        first: dict[str, Any] | None = None
        elapsed = 0.0
        for _ in range(repetitions):
            started = time.perf_counter()
            metrics = run_configuration(samples, configuration, reference_routed)
            elapsed += time.perf_counter() - started
            if first is None:
                first = metrics
            elif metrics != first:
                raise NegotiatedCensusError(
                    f"deterministic replay diverged for configuration {configuration.name}"
                )
        assert first is not None
        configurations[configuration.name] = first
        wall_times[configuration.name] = elapsed / repetitions

    primary = configurations[PRIMARY.name]
    control = configurations[TWO_PAD_CONTROL.name]
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
            "committed_boards": len(samples),
            "upstream_sample_count": manifest["upstream_sample_count"],
        },
        "reference_baseline": {
            "benchmark": "B-088",
            "artifact": REFERENCE_ARTIFACT.relative_to(ROOT).as_posix(),
            "artifact_run_id": recorded["run_id"],
            "grid_policy": "fixed",
            "nets_routed": primary["reference_per_net_nets_routed"],
            "nets_attempted": recorded["metrics"]["configurations"]["fixed"]["nets_attempted"],
        },
        "headline": {
            "boards_offered": primary["boards_offered"],
            "boards_reaching_the_repair_precondition": primary[
                "boards_reaching_the_repair_precondition"
            ],
            "negotiated_nets_completed": primary["negotiated_nets_completed"],
            "reference_per_net_nets_routed": primary["reference_per_net_nets_routed"],
            "physical_gate_calls": primary["physical_gate_calls"],
            # The two populations the census turns out to be about, side by side. The negotiated
            # slice can only represent an exactly-two-pad net; the control counts those, and counts
            # how many of them the per-net reference routed. Their overlap is the census's real
            # subject, and it is reported rather than described.
            "two_pad_nets_offered": control["nets_submitted"],
            "two_pad_nets_the_reference_routed": control["submitted_nets_the_reference_routed"],
        },
        "deterministic_replays": True,
        "repair_settings_enabled": False,
        "configurations": configurations,
    }, timing


def build_report(repetitions: int = 2, corpus: Path = CORPUS) -> dict[str, Any]:
    """Build the canonical, self-digesting census report without writing it."""

    metrics, timing = run_census(repetitions, corpus)
    report: dict[str, Any] = {
        "schema": REPORT_SCHEMA,
        "date_utc": "2026-08-20",
        "source_commit": _git_commit(),
        "environment": {"platform": platform.platform(), "python": sys.version.split()[0]},
        "configuration": {
            "adapter_version": SIMPLE_ROUTE_JSON_ADAPTER_VERSION,
            "router_version": ROUTER_VERSION,
            "routing_policy": ROUTING_POLICY,
            "negotiated_routing_policy": NEGOTIATED_ROUTING_POLICY,
            "fixed_grid_step_nm": FIXED_GRID_STEP_NM,
            "router_limits": dict(ROUTER_LIMITS),
            "envelope_budgets": dict(ENVELOPE_BUDGETS),
            "seed": SEED,
            "repair_settings": None,
            "admission_conjuncts": [
                {"name": name, "stage": stage, "description": description}
                for name, stage, description in ADMISSION_CONJUNCTS
            ],
            "blocking_stages": list(BLOCKING_STAGES),
            "runner_sha256": _file_digest(ROOT / SCRIPT_PATH),
            "reference_runner_sha256": _file_digest(ROOT / REFERENCE_RUNNER_PATH),
        },
        "metrics": metrics,
        "timing": timing,
        "repair_precondition_definition": (
            "src/copper_mcp/routing/congestion.py fires ADR-0117's local-repair transaction only "
            "when repair_settings is supplied AND the whole-set physical-clearance gate returns "
            "CLEARANCE_VIOLATION AND the allocation is complete (no unrouted net, and candidates "
            "plus connections equal the submitted requests) AND at least two nets are named as "
            "violating. This census supplies no repair_settings and records how often the other "
            "three conjuncts hold, which is the frequency that decides whether enabling repair is "
            "worth a slice."
        ),
        "direction_of_error": (
            "The recorder sees the candidate tuple the gate was handed but not the connection "
            "map, so completeness is tested with the solo reference's already_connected count as "
            "an upper bound on connections. Over-counting connections can only make the "
            "completeness conjunct easier to satisfy, so a recorded zero cannot be an artefact of "
            "the bound. Under the primary configuration the bound is exact: every submitted net "
            "produced a candidate in the solo reference, so no submitted net can be already "
            "connected."
        ),
        "not_claimed": [
            "any statement about the local repair transaction's effectiveness; repair is never "
            "enabled by this runner and no repaired candidate was constructed, validated, or "
            "published",
            "a routing-quality claim about the negotiated coordinator, the reference router, or "
            "any other router; this records coordinator behaviour at the declared budgets on this "
            "corpus and nothing else",
            "that the negotiated completion count and B-088's per-net count answer the same "
            "question: B-088 routes each net independently against the unrouted snapshot, so its "
            "candidates are not mutually compatible, while a negotiated envelope must satisfy one "
            "shared allocation",
            "KiCad DRC, electrical correctness, signal integrity, thermal behaviour, or "
            "fabrication readiness for any imported board",
            "board mutation, apply authority, export, or live-editor behaviour",
            "generalisation beyond LLM-generated 2-layer tscircuit boards, or from the committed "
            "20-board subset to the full 36-board corpus",
            "that a different grid step, budget, layer selection, or net-selection rule would "
            "produce the same census; none was tried, and trying one to move a number is exactly "
            "what the predeclared stop rule forbids",
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
    parser.add_argument("--write", action="store_true", help="write the committed artifact")
    arguments = parser.parse_args()
    report = build_report(arguments.repetitions, arguments.corpus)
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if arguments.write:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
