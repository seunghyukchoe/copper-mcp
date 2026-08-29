#!/usr/bin/env python3
"""Census the current negotiated coordinator across the committed SimpleRouteJson corpus.

B-124 measured the former two-pin, shared-world-origin contract and remains an immutable historical
artifact.  The coordinator now admits two to thirty-two selected-layer pads per net and keeps each
request's local lattice origin.  Replaying B-124 as if it described that successor contract would
rewrite history, so this runner emits a new schema and output path while retaining an explicit
pointer to the old artifact.

Before any negotiated measurement, the successor freezes and checks one admission-only prediction:
of the 20 boards in the primary B-088-routable population, 16 can enter the coordinator and four
cannot form the required two-request envelope.  No routing status, completion count, physical-gate
count, complete-allocation physical-clearance trigger frequency, or two-pin repair-target
frequency is predicted.  Those remain measurements.

Four things about it are deliberate.

1. **Repair is never enabled.**  The census measures two separate signals under the no-repair
   coordinator: whether a complete allocation reached the physical-clearance trigger, and whether
   its violating candidate tuple contained a two-pin target that ``_attempt_local_repair`` could
   select.  Neither signal says that repair was enabled, usable, or successful.
2. **Both signals are observed, not inferred from the terminal status.**  The published
   ``NegotiatedRoutingResult`` does not carry the gate's per-iteration failure, violating-net
   attribution, or candidate arity, so a status alone cannot distinguish "the gate rejected a
   complete allocation" from "some net never routed", or a multi-pin violation from a selectable
   two-pin target.  The runner therefore installs a pure pass-through recorder on the coordinator's
   physical-gate symbol for the duration of one call and reads the gate's own result plus the exact
   candidate tuple it classified.  Because a recorder that changed behaviour would silently invent
   the census, every board is additionally run *uninstrumented* and the complete immutable results
   must be identical or the run fails.
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
import re
import shutil
import subprocess
import sys
import time
from collections import Counter
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
REFERENCE_RUN_ID = "sha256:facf95ee9770ffab8c1bc403a32a403e55ca79f2c56d1eabc6679eb6ec4dfca3"
LEGACY_ARTIFACT = ROOT / "benchmarks/results/routing/2026-08-20-negotiated-corpus-census-v1.json"
DEFAULT_OUTPUT = (
    ROOT / "benchmarks/results/routing/2026-08-29-negotiated-multipin-corpus-census-v1.json"
)
REPORT_SCHEMA = "copper-mcp/benchmark/negotiated-multipin-corpus-census/v1"
_GIT_COMMIT = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")

#: Frozen before any successor measurement.  This predicts admission only; routing outcomes are
#: intentionally absent because the point of the runner is to measure them under the new contract.
PREDECLARED_PRIMARY_ADMISSION: dict[str, Any] = {
    "configuration": "b088-routable",
    "population": "the per-board net sets routed by B-088's fixed-policy configuration",
    "boards_offered": 20,
    "boards_admitted_by_the_coordinator": 16,
    "boards_unable_to_form_a_two_request_envelope": 4,
    "routing_outcomes": "not_predicted",
}

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

#: The coordinator's current admission conjuncts, in the order it evaluates them.  The first two
#: are enforced by ``NegotiatedRoutingRequest.__post_init__`` and therefore refuse before
#: ``negotiate_routes`` is reachable; the last is independently checked here and then by the
#: coordinator.  Request-local origins are deliberately not an admission conjunct.
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
        "selected_layer_pad_count_between_2_and_32",
        "coordinator_admission",
        "each negotiated net must expose between 2 and 32 pads on the selected layer",
    ),
)
#: The exact coordinator diagnostic the coordinator-side conjunct must produce.  Pinning the
#: string turns the independent predicate into a cross-check rather than a restatement.
COORDINATOR_DIAGNOSTICS: dict[str, str] = {
    "selected_layer_pad_count_between_2_and_32": (
        "each negotiated net must expose 2 to 32 pads on the selected layer"
    ),
}

#: Where a board's census run stopped, ordered from earliest to latest.  The last two values split
#: the complete-allocation physical-clearance trigger by whether the request-bound candidate tuple
#: contains a violating two-pin target.  Neither value says that repair ran or that its later
#: budget, provenance, policy, solver, and validation gates would accept an attempt.
PHYSICAL_TRIGGER_WITHOUT_TWO_PIN_TARGET = (
    "complete_allocation_physical_clearance_trigger_without_two_pin_repair_eligible_target"
)
PHYSICAL_TRIGGER_WITH_TWO_PIN_TARGET = (
    "complete_allocation_physical_clearance_trigger_with_two_pin_repair_eligible_target"
)
PHYSICAL_TRIGGER_STAGES = frozenset(
    (PHYSICAL_TRIGGER_WITHOUT_TWO_PIN_TARGET, PHYSICAL_TRIGGER_WITH_TWO_PIN_TARGET)
)
BLOCKING_STAGES: tuple[str, ...] = (
    "envelope_construction",
    "coordinator_admission",
    "no_physical_gate_call",
    "no_clearance_violation",
    "clearance_violation_on_incomplete_allocation",
    "complete_allocation_clearance_violation_with_fewer_than_two_violating_nets",
    PHYSICAL_TRIGGER_WITHOUT_TWO_PIN_TARGET,
    PHYSICAL_TRIGGER_WITH_TWO_PIN_TARGET,
)


class NegotiatedCensusError(RuntimeError):
    """Raised when the harness itself is broken, or when a recorded number cannot be trusted."""


@dataclass(frozen=True, slots=True)
class GateObservation:
    """One whole-set physical-clearance gate call, as the coordinator itself saw it."""

    candidates: int
    failure: str | None
    violating_nets: int
    two_pin_repair_eligible_violating_targets: int
    pair_checks: int

    def payload(self) -> dict[str, Any]:
        return {
            "candidates": self.candidates,
            "failure": self.failure,
            "pair_checks": self.pair_checks,
            "two_pin_repair_eligible_violating_targets": (
                self.two_pin_repair_eligible_violating_targets
            ),
            "violating_nets": self.violating_nets,
        }


@contextmanager
def observed_physical_gate() -> Iterator[list[GateObservation]]:
    """Record every whole-set physical-clearance call the coordinator makes, changing nothing.

    The recorder forwards the identical arguments to the identical function and returns the
    identical object, so it cannot alter an allocation, a budget, or a published field.  The runner
    does not take that on trust: :func:`census_board` runs each board with and without this
    recorder and refuses to publish unless the complete immutable result objects are equal.
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
        candidate_tuple = candidates if isinstance(candidates, tuple) else ()
        violating_net_ids = frozenset(result.violating_nets)
        observations.append(
            GateObservation(
                candidates=len(candidates) if isinstance(candidates, tuple) else -1,
                failure=None if result.failure is None else str(result.failure.value),
                violating_nets=len(result.violating_nets),
                two_pin_repair_eligible_violating_targets=sum(
                    1
                    for candidate in candidate_tuple
                    if candidate.patch.net_id in violating_net_ids and candidate.pad_count == 2
                ),
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


def _git_state() -> tuple[str, bool]:
    """Return the exact source revision and whether any tracked or untracked path is dirty."""

    git = shutil.which("git")
    if git is None:
        return "unknown", True
    try:
        commit = subprocess.run(  # noqa: S603 - fixed local Git executable and argv
            [git, "rev-parse", "HEAD"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
        dirty = bool(
            subprocess.run(  # noqa: S603 - fixed local Git executable and argv
                [git, "status", "--porcelain", "--untracked-files=normal"],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
                timeout=5,
            ).stdout
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown", True
    if _GIT_COMMIT.fullmatch(commit) is None:
        return "unknown", True
    return commit, dirty


def _require_publishable_source(expected_commit: str | None = None) -> str:
    """Fail closed unless publication is bound to one unchanged, clean Git revision."""

    commit, dirty = _git_state()
    if commit == "unknown":
        raise NegotiatedCensusError("artifact publication requires a known Git revision")
    if dirty:
        raise NegotiatedCensusError("artifact publication requires a clean Git worktree")
    if expected_commit is not None and commit != expected_commit:
        raise NegotiatedCensusError("the Git revision changed during benchmark measurement")
    return commit


def _file_digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def load_reference_artifact(path: Path = REFERENCE_ARTIFACT) -> dict[str, Any]:
    """Return B-088 only when its self-digest resolves to the pinned historical root.

    The census pins its submitted set to B-088's routed set, so a drifted or edited B-088 artifact
    would silently redefine what this census measured.  The exact run ID also self-binds B-088's
    source commit, runner digest, and adapter digest; a newly self-consistent rewrite is not B-088.
    """

    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise NegotiatedCensusError("the recorded reference artifact is not one JSON object")
    recorded = document.get("run_id")
    body = {key: value for key, value in document.items() if key != "run_id"}
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    if recorded != "sha256:" + hashlib.sha256(canonical).hexdigest():
        raise NegotiatedCensusError("the recorded reference artifact fails its own self-digest")
    if recorded != REFERENCE_RUN_ID:
        raise NegotiatedCensusError("the recorded reference artifact is not the pinned B-088 root")
    if document.get("schema") != reference.REPORT_SCHEMA:
        raise NegotiatedCensusError("the recorded reference artifact carries an unexpected schema")
    return document


@dataclass(frozen=True, slots=True)
class ReferenceBoardAuthority:
    """B-088's stable fixed-policy identity for one imported corpus document."""

    document_sha256: str
    board_revision: str
    outcomes: tuple[tuple[str, int], ...]
    candidate_digest: str

    @property
    def routed(self) -> int:
        return dict(self.outcomes).get("routed", 0)


def _reference_authority_by_board(
    document: dict[str, Any],
) -> dict[str, ReferenceBoardAuthority]:
    """Project B-088's complete stable per-board authority, refusing malformed evidence."""

    try:
        boards = document["metrics"]["configurations"]["fixed"]["boards"]
        if not isinstance(boards, list):
            raise TypeError
        authority: dict[str, ReferenceBoardAuthority] = {}
        for board in boards:
            if not isinstance(board, dict) or "outcomes" not in board:
                raise TypeError
            name = board["board"]
            document_sha256 = board["document_sha256"]
            board_revision = board["board_revision"]
            candidate_digest = board["candidate_digest"]
            outcomes = board["outcomes"]
            if (
                not isinstance(name, str)
                or not isinstance(document_sha256, str)
                or re.fullmatch(r"[0-9a-f]{64}", document_sha256) is None
                or not isinstance(board_revision, str)
                or re.fullmatch(r"sha256:[0-9a-f]{64}", board_revision) is None
                or not isinstance(candidate_digest, str)
                or re.fullmatch(r"[0-9a-f]{64}", candidate_digest) is None
                or not isinstance(outcomes, dict)
                or not all(
                    isinstance(code, str) and type(count) is int and count >= 0
                    for code, count in outcomes.items()
                )
                or name in authority
            ):
                raise TypeError
            authority[name] = ReferenceBoardAuthority(
                document_sha256=document_sha256,
                board_revision=board_revision,
                outcomes=tuple(sorted(outcomes.items())),
                candidate_digest=candidate_digest,
            )
    except (KeyError, TypeError, ValueError) as error:
        raise NegotiatedCensusError(
            "the recorded B-088 fixed-policy authority is malformed"
        ) from error
    return authority


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
    candidate_id: str | None = None


def _solo_reference(problem: ImportedProblem, router: AStarRouter) -> tuple[SubmittedNet, ...]:
    """Replay every net of one board on its own, exactly as the B-088 runner does.

    This is the reference baseline the negotiated numbers are read against, and it is also how the
    census learns which nets are ``already_connected`` — a net the coordinator counts as allocated
    without producing a candidate, and therefore part of the completeness test below.
    """

    outcomes: list[SubmittedNet] = []
    for net in problem.nets:
        if net.pad_count < 2:
            outcomes.append(
                SubmittedNet(
                    net_id=net.net_id,
                    layer_id=net.layer_id,
                    reference_outcome="no_routing_work",
                    selected_layer_pads=len(
                        _selected_layer_pads(problem, net.net_id, net.layer_id)
                    ),
                )
            )
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
                candidate_id=(None if result.candidate is None else result.candidate.candidate_id),
            )
        )
    return tuple(sorted(outcomes, key=lambda item: item.net_id))


def _current_reference_authority(
    problem: ImportedProblem, submitted: tuple[SubmittedNet, ...]
) -> ReferenceBoardAuthority:
    """Return the same stable fixed-policy projection recorded by B-088."""

    outcomes = Counter(item.reference_outcome for item in submitted)
    candidates_by_net = {
        item.net_id: item.candidate_id for item in submitted if item.candidate_id is not None
    }
    candidate_ids = [
        candidates_by_net[net.net_id] for net in problem.nets if net.net_id in candidates_by_net
    ]
    return ReferenceBoardAuthority(
        document_sha256=problem.document_sha256,
        board_revision=problem.snapshot.snapshot_digest,
        outcomes=tuple(sorted(outcomes.items())),
        candidate_digest=hashlib.sha256("\n".join(candidate_ids).encode("utf-8")).hexdigest(),
    )


def _assert_reference_authority(
    problem: ImportedProblem,
    submitted: tuple[SubmittedNet, ...],
    expected: ReferenceBoardAuthority | None,
) -> int:
    """Bind the submitted population to B-088 identity, membership, and every fixed outcome."""

    if expected is None or _current_reference_authority(problem, submitted) != expected:
        raise NegotiatedCensusError(
            "the re-derived fixed-policy board projection does not match committed B-088 authority"
        )
    return expected.routed


def _admission(_problem: ImportedProblem, submitted: tuple[SubmittedNet, ...]) -> dict[str, bool]:
    """Decide each declared admission conjunct from public snapshot data alone.

    Nothing private to the coordinator is imported.  The point of computing this independently is
    that :func:`census_board` then requires the coordinator's own refusal to agree with it.
    """

    layers = {item.layer_id for item in submitted}
    return {
        "at_least_two_requests": 2 <= len(submitted) <= 32,
        "one_selected_layer_and_grid_step": len(layers) == 1,
        "selected_layer_pad_count_between_2_and_32": bool(submitted)
        and all(2 <= item.selected_layer_pads <= 32 for item in submitted),
    }


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
    """Return the latest physical-trigger/target stage reached by any gate call.

    ``connectable`` is the number of submitted nets the solo reference reported as
    ``already_connected``.  It is used as an **upper bound** on the connections the coordinator can
    hold, because the recorder sees the candidate tuple but not the connection map.  Over-counting
    connections can only make the completeness conjunct easier to satisfy, so it can never turn a
    reached physical trigger into an unreached one — a recorded zero is therefore not an artefact
    of this bound.  Two-pin target presence is exact because it is computed directly from the
    request-bound candidate tuple and the gate's own ``violating_nets`` result.
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
            stage = _later(
                stage,
                "complete_allocation_clearance_violation_with_fewer_than_two_violating_nets",
            )
        elif observation.two_pin_repair_eligible_violating_targets < 1:
            stage = _later(stage, PHYSICAL_TRIGGER_WITHOUT_TWO_PIN_TARGET)
        else:
            stage = _later(stage, PHYSICAL_TRIGGER_WITH_TWO_PIN_TARGET)
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
        "_negotiated_result": None,
    }

    if unmet is not None and unmet[1] == "envelope_construction":
        record["terminal_status"] = None
        record["negotiated"] = None
        record["physical_gate_calls"] = 0
        record["physical_gate_observations"] = []
        record["blocking_stage"] = "envelope_construction"
        record["complete_allocation_physical_clearance_trigger_reached"] = False
        record["two_pin_repair_eligible_violating_target_present"] = False
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
    control_result = negotiate_routes(problem.snapshot, envelope, router=router)
    with observed_physical_gate() as observations:
        instrumented_result = negotiate_routes(problem.snapshot, envelope, router=router)
    if instrumented_result != control_result:
        raise NegotiatedCensusError(
            "the physical-gate recorder changed the complete immutable negotiated result"
        )
    instrumented = _projection(instrumented_result)

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
    record["_negotiated_result"] = instrumented_result
    record["physical_gate_calls"] = len(seen)
    record["physical_gate_observations"] = [item.payload() for item in seen]
    record["blocking_stage"] = stage
    record["complete_allocation_physical_clearance_trigger_reached"] = (
        stage in PHYSICAL_TRIGGER_STAGES
    )
    record["two_pin_repair_eligible_violating_target_present"] = (
        stage == PHYSICAL_TRIGGER_WITH_TWO_PIN_TARGET
    )
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
        "reference routed it. This preserves B-124's separately declared comparison population, "
        "but it is not part of the successor's predeclared primary admission prediction"
    ),
)
CONFIGURATIONS: tuple[Configuration, ...] = (PRIMARY, TWO_PAD_CONTROL)


@dataclass(frozen=True, slots=True)
class ConfigurationMeasurement:
    """One aggregate publication plus private per-board evidence used only for replay parity."""

    aggregate: dict[str, Any]
    replay_evidence: tuple[dict[str, Any], ...]


def _preflight_primary_admission(
    samples: tuple[tuple[str, bytes], ...],
    reference_authority: dict[str, ReferenceBoardAuthority],
) -> dict[str, int]:
    """Verify the frozen primary admission partition before any negotiated measurement.

    The pass re-derives B-088's routed population under the same fixed reference policy and checks
    its per-board counts against the committed artifact.  It then evaluates only the public
    admission predicates.  ``negotiate_routes`` and the physical-gate recorder are intentionally
    unreachable from this function.
    """

    router = AStarRouter()
    admitted = 0
    unable_to_form_envelope = 0
    other_refusals = 0
    for name, payload in samples:
        board_name = Path(name).stem
        try:
            problem = import_simple_route_json(board_name, payload)
        except SimpleRouteJsonImportError as refusal:
            raise NegotiatedCensusError(
                "the predeclared primary population no longer imports cleanly"
            ) from refusal
        solo = _solo_reference(problem, router)
        _assert_reference_authority(problem, solo, reference_authority.get(board_name))
        unmet = _first_unmet(_admission(problem, PRIMARY.select(solo)))
        if unmet is None:
            admitted += 1
        elif unmet == ("at_least_two_requests", "envelope_construction"):
            unable_to_form_envelope += 1
        else:
            other_refusals += 1

    actual = {
        "boards_offered": len(samples),
        "boards_admitted_by_the_coordinator": admitted,
        "boards_unable_to_form_a_two_request_envelope": unable_to_form_envelope,
    }
    expected_partition = {
        name: int(PREDECLARED_PRIMARY_ADMISSION[name])
        for name in (
            "boards_offered",
            "boards_admitted_by_the_coordinator",
            "boards_unable_to_form_a_two_request_envelope",
        )
    }
    if actual != expected_partition or other_refusals:
        raise NegotiatedCensusError(
            "the current-contract primary admission partition diverged from its predeclared "
            "prediction"
        )
    return actual


def run_configuration(
    samples: tuple[tuple[str, bytes], ...],
    configuration: Configuration,
    reference_authority: dict[str, ReferenceBoardAuthority],
) -> ConfigurationMeasurement:
    """Run once, returning closed aggregates plus private replay evidence."""

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
        routed_here = _assert_reference_authority(
            problem, solo, reference_authority.get(board_name)
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

    return ConfigurationMeasurement(
        aggregate={
            "configuration": {
                "name": configuration.name,
                "description": configuration.description,
            },
            "boards_offered": len(samples),
            "boards_imported": imported,
            "boards_with_a_constructible_envelope": envelopes,
            "boards_admitted_by_the_coordinator": admitted,
            "boards_reaching_complete_allocation_physical_clearance_trigger": sum(
                stages[name] for name in PHYSICAL_TRIGGER_STAGES
            ),
            "boards_with_a_two_pin_repair_eligible_violating_target": stages[
                PHYSICAL_TRIGGER_WITH_TWO_PIN_TARGET
            ],
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
        },
        replay_evidence=tuple(boards),
    )


def run_census(
    repetitions: int = 2, corpus: Path = CORPUS
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run every declared configuration ``repetitions`` times and confirm the replays agree."""

    if not 1 <= repetitions <= 8:
        raise ValueError("repetitions must be between 1 and 8")
    manifest, samples = reference.load_corpus(corpus)
    recorded = load_reference_artifact()
    reference_authority = _reference_authority_by_board(recorded)
    admission_preflight = _preflight_primary_admission(samples, reference_authority)
    configurations: dict[str, Any] = {}
    wall_times: dict[str, float] = {}
    for configuration in CONFIGURATIONS:
        first: ConfigurationMeasurement | None = None
        elapsed = 0.0
        for _ in range(repetitions):
            started = time.perf_counter()
            metrics = run_configuration(samples, configuration, reference_authority)
            elapsed += time.perf_counter() - started
            if first is None:
                first = metrics
            elif metrics != first:
                raise NegotiatedCensusError(
                    f"deterministic replay diverged for configuration {configuration.name}"
                )
        assert first is not None
        configurations[configuration.name] = first.aggregate
        wall_times[configuration.name] = elapsed / repetitions

    primary = configurations[PRIMARY.name]
    control = configurations[TWO_PAD_CONTROL.name]
    measured_primary_partition = {
        "boards_offered": primary["boards_offered"],
        "boards_admitted_by_the_coordinator": primary["boards_admitted_by_the_coordinator"],
        "boards_unable_to_form_a_two_request_envelope": primary["first_unmet_conjunct_breakdown"][
            "at_least_two_requests"
        ],
    }
    if measured_primary_partition != admission_preflight:
        raise NegotiatedCensusError(
            "the measured primary admission partition diverged from its pre-measurement check"
        )
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
            "boards_admitted_by_the_coordinator": primary["boards_admitted_by_the_coordinator"],
            "boards_unable_to_form_a_two_request_envelope": primary[
                "first_unmet_conjunct_breakdown"
            ]["at_least_two_requests"],
            "boards_reaching_complete_allocation_physical_clearance_trigger": primary[
                "boards_reaching_complete_allocation_physical_clearance_trigger"
            ],
            "boards_with_a_two_pin_repair_eligible_violating_target": primary[
                "boards_with_a_two_pin_repair_eligible_violating_target"
            ],
            "negotiated_nets_completed": primary["negotiated_nets_completed"],
            "reference_per_net_nets_routed": primary["reference_per_net_nets_routed"],
            "physical_gate_calls": primary["physical_gate_calls"],
            # Preserve B-124's two-pad comparison population without treating it as the current
            # coordinator's representable population or part of the predeclared primary result.
            "two_pad_nets_offered": control["nets_submitted"],
            "two_pad_nets_the_reference_routed": control["submitted_nets_the_reference_routed"],
        },
        "premeasurement_admission_check": admission_preflight,
        "deterministic_replays": True,
        "repair_settings_enabled": False,
        "configurations": configurations,
    }, timing


def build_report(
    repetitions: int = 2,
    corpus: Path = CORPUS,
    *,
    source_commit: str | None = None,
) -> dict[str, Any]:
    """Build the canonical, self-digesting census report without writing it."""

    captured_commit = _git_commit() if source_commit is None else source_commit
    if captured_commit != "unknown" and _GIT_COMMIT.fullmatch(captured_commit) is None:
        raise NegotiatedCensusError("the captured source commit is malformed")
    metrics, timing = run_census(repetitions, corpus)
    report: dict[str, Any] = {
        "schema": REPORT_SCHEMA,
        "date_utc": "2026-08-29",
        "source_commit": captured_commit,
        "environment": {"platform": platform.platform(), "python": sys.version.split()[0]},
        "historical_predecessor": {
            "benchmark": "B-124",
            "artifact": LEGACY_ARTIFACT.relative_to(ROOT).as_posix(),
            "relationship": (
                "immutable evidence for the former exactly-two-pad, shared-world-origin contract; "
                "never replayed as current behavior"
            ),
        },
        "predeclared_prediction": dict(PREDECLARED_PRIMARY_ADMISSION),
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
            "request_local_grid_origins": True,
            "selected_layer_pad_count": {"minimum": 2, "maximum": 32},
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
        "complete_allocation_physical_clearance_trigger_definition": (
            "the whole-set physical-clearance gate returned CLEARANCE_VIOLATION, the allocation "
            "was complete (no unrouted net, and candidates plus connections equalled the "
            "submitted requests), and at least two nets were named as violating. This is the "
            "coordinator condition that can lead to _attempt_local_repair only when "
            "repair_settings is also supplied; this census never supplies it."
        ),
        "two_pin_repair_eligible_target_definition": (
            "among complete-allocation physical-clearance triggers, at least one candidate in the "
            "gate's exact request-bound tuple had its patch net named in violating_nets and "
            "pad_count equal to 2, matching _attempt_local_repair's target-arity selection. This "
            "does not claim that repair budgets, provenance, policy, the local solver, or final "
            "validation would admit or accept an attempt."
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
            "any predeclared routing outcome: the 16-of-20 prediction covers coordinator admission "
            "only; terminal status, completion, physical-gate trigger, and two-pin target fields "
            "are measured after that prediction is checked",
            "that request-local lattice origins make cross-grid copper physically compatible; "
            "the whole-set physical-clearance gate remains the acceptance authority",
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


def _new_output_target(output: Path) -> Path:
    """Resolve one create-only artifact target while protecting prior evidence authorities."""

    candidate_input = output.expanduser()
    if candidate_input.is_symlink():
        raise NegotiatedCensusError("artifact output must be a new regular path")
    try:
        parent = candidate_input.parent.resolve(strict=True)
    except OSError as error:
        raise NegotiatedCensusError("artifact output parent must already exist") from error
    if not parent.is_dir():
        raise NegotiatedCensusError("artifact output parent must be a directory")
    candidate = parent / candidate_input.name
    protected = {LEGACY_ARTIFACT.resolve(), REFERENCE_ARTIFACT.resolve()}
    if candidate.resolve(strict=False) in protected:
        raise NegotiatedCensusError("artifact output is a protected historical authority")
    if candidate.exists() or candidate.is_symlink():
        raise NegotiatedCensusError("artifact output must be a new path")
    return candidate


def _write_exclusive(output: Path, rendered: str) -> None:
    """Create one complete artifact path without following or overwriting an existing entry."""

    try:
        with output.open("x", encoding="utf-8") as stream:
            stream.write(rendered)
    except FileExistsError as error:
        raise NegotiatedCensusError("artifact output must remain a new path") from error


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--repetitions", type=int, default=2)
    parser.add_argument("--corpus", type=Path, default=CORPUS)
    parser.add_argument("--write", action="store_true", help="write the committed artifact")
    arguments = parser.parse_args()
    output: Path | None = None
    captured_commit: str | None = None
    if arguments.write:
        output = _new_output_target(arguments.output)
        captured_commit = _require_publishable_source()
    report = build_report(
        arguments.repetitions,
        arguments.corpus,
        source_commit=captured_commit,
    )
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if arguments.write:
        assert output is not None and captured_commit is not None
        _require_publishable_source(expected_commit=captured_commit)
        _write_exclusive(output, rendered)
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
