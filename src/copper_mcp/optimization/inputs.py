"""Resolve private workspace inputs into the immutable optimization request contract."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass
from types import MappingProxyType
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, StringConstraints, ValidationError

from copper_mcp.adapters import KiCadConstraintProfile, parse_kicad_bytes
from copper_mcp.board_ir import BoardIRSnapshot
from copper_mcp.circuit_ir import decode_snapshot_json as decode_circuit_intent
from copper_mcp.config import Settings
from copper_mcp.kicad_cli import _context_revision, _drc_context
from copper_mcp.optimization.contracts import (
    DOMAINS,
    AnyOptimizationRequest,
    Backend,
    ClosedModel,
    Digest,
    Domain,
    FootprintRef,
    ObjectiveWeights,
    OptimizationError,
    OptimizationRequest,
    OptimizationRequestV2,
    PlacementScope,
    ResourceLimits,
    ResourceLimitsV2,
    bounded_json,
    digest_document,
)
from copper_mcp.optimization.drc_profile import DrcProfileBinding, prepare_drc_profile
from copper_mcp.optimization.provenance import native_implementation_digest
from copper_mcp.parse_budgets import parse_limits_for
from copper_mcp.placement.contracts import PlacementIntent, parse_placement_intent
from copper_mcp.request_boundary import net_class_constraints
from copper_mcp.routing.contracts import AStarSettings
from copper_mcp.security import read_workspace_file

WorkspacePath = Annotated[str, StringConstraints(min_length=1, max_length=4096)]
NetRef = Annotated[str, StringConstraints(pattern=r"^net:[A-Za-z0-9_.:-]{1,160}$")]


def default_limits() -> ResourceLimits:
    return ResourceLimits(
        max_runtime_ms=300_000,
        max_candidates=8,
        max_placement_evaluations=64,
        max_route_attempts=24,
        max_repair_rounds=2,
        max_expansions=1_000_000,
        max_obstacle_checks=10_000_000,
        max_external_output_bytes=8_388_608,
    )


class OptimizationLaunch(ClosedModel):
    """Ephemeral launch arguments; never persist these paths, references, or intent bytes."""

    board: WorkspacePath
    expect_board_revision: Digest
    expect_snapshot_digest: Digest
    constraints: dict[str, int]
    target_net_refs: Annotated[tuple[NetRef, ...], Field(min_length=1, max_length=4096)]
    movable_footprint_refs: Annotated[tuple[FootprintRef, ...], Field(max_length=128)] = ()
    placement_intent_path: WorkspacePath | None = None
    electrical_intent_path: WorkspacePath | None = None
    placement_grid_nm: Annotated[int, Field(ge=1, le=1_000_000_000)] = 1000
    routing_settings: dict[str, int] = Field(default_factory=dict)
    seed: Annotated[int, Field(ge=0, le=(1 << 53) - 4097)] = 0
    allowed_backends: Annotated[tuple[Backend, ...], Field(min_length=1, max_length=3)] = (
        "internal-layered-v1",
    )
    limits: ResourceLimits = Field(default_factory=default_limits)


class ProjectFileDeclaration(BaseModel):
    model_config = ClosedModel.model_config
    path: WorkspacePath
    digest: Digest


class ProjectLibraryDeclaration(ProjectFileDeclaration):
    name: Annotated[str, StringConstraints(min_length=1, max_length=128)]


class ProjectDeclaration(ClosedModel):
    root_path: WorkspacePath
    files: Annotated[tuple[ProjectFileDeclaration, ...], Field(min_length=1, max_length=129)]
    libraries: Annotated[tuple[ProjectLibraryDeclaration, ...], Field(max_length=128)]


def default_limits_v2() -> ResourceLimitsV2:
    return ResourceLimitsV2(
        **{**default_limits().model_dump(), "max_candidates": 4, "max_route_attempts": 256}
    )


class OptimizationLaunchV2(OptimizationLaunch):
    identity_namespace = "copper-mcp/optimization/v2/launch"
    schema_version: Literal["optimization/v2"]
    limits: ResourceLimitsV2 = Field(default_factory=default_limits_v2)
    required_domains: Annotated[tuple[Domain, ...], Field(max_length=7)] = ()
    project: ProjectDeclaration | None = None


@dataclass(frozen=True)
class PreparedOptimization:
    """Private input capture held only for this execution; metadata uses request digests."""

    request: AnyOptimizationRequest
    board_path: str
    source: bytes
    snapshot: BoardIRSnapshot
    profile: KiCadConstraintProfile
    context: MappingProxyType[str, bytes]
    original_context_digest: str
    target_net_refs: tuple[str, ...]
    placement_intent: PlacementIntent | None
    routing_settings: AStarSettings
    started_at: float
    implementation_digest: str
    electrical_source: bytes | None = None
    input_artifact_bindings: tuple[tuple[str, str], ...] = ()
    project: ProjectDeclaration | None = None
    drc_profile: DrcProfileBinding | None = None


def parse_launch(payload: object) -> OptimizationLaunch:
    try:
        raw = json.dumps(payload, allow_nan=False, ensure_ascii=True).encode("ascii")
        bounded_json(raw)
        model = (
            OptimizationLaunchV2
            if type(payload) is dict and payload.get("schema_version") == "optimization/v2"
            else OptimizationLaunch
        )
        return model.model_validate_json(raw)
    except (ValueError, TypeError, RecursionError, UnicodeError):
        raise OptimizationError("optimization launch is malformed") from None


def prepare_optimization(payload: object, settings: Settings) -> PreparedOptimization:
    """Capture exact source/rules and validate explicit scopes before a job is queued."""

    started_at = time.monotonic()
    launch = parse_launch(payload)
    try:
        net_class = net_class_constraints(launch.constraints)
        routing = AStarSettings(**launch.routing_settings)
        profile = KiCadConstraintProfile(
            net_classes=(net_class,), default_net_class_id=net_class.id
        )
        board = read_workspace_file(
            settings.workspace,
            launch.board,
            allowed_suffixes={".kicad_pcb"},
            max_bytes=settings.max_board_bytes,
        )
        relative = board.path.relative_to(settings.workspace.resolve(strict=True)).as_posix()
        revision = "sha256:" + hashlib.sha256(board.content).hexdigest()
        if revision != launch.expect_board_revision:
            raise OptimizationError("optimization source revision is stale")
        converted = parse_kicad_bytes(board.content, profile, parse_limits_for(settings))
        if converted.snapshot is None or converted.diagnostics:
            raise OptimizationError("optimization board geometry is unsupported")
        snapshot = converted.snapshot
        if snapshot.snapshot_digest != launch.expect_snapshot_digest:
            raise OptimizationError("optimization snapshot revision is stale")
        if not 2 <= len(snapshot.content.copper_layers) <= 8:
            raise OptimizationError("optimization layer stack is unsupported")
        targets = tuple(sorted(set(launch.target_net_refs)))
        if len(targets) != len(launch.target_net_refs):
            raise OptimizationError("optimization targets must be distinct")
        pads_per_net: dict[str, int] = {}
        for pad in snapshot.content.pads:
            if pad.net_id is not None:
                pads_per_net[pad.net_id] = pads_per_net.get(pad.net_id, 0) + 1
        if any(pads_per_net.get(net, 0) < 2 for net in targets):
            raise OptimizationError("optimization target is not a routable net")
        movable = tuple(sorted(set(launch.movable_footprint_refs)))
        if len(movable) != len(launch.movable_footprint_refs):
            raise OptimizationError("optimization movable scope must be distinct")
        footprints = {item.id: item for item in snapshot.content.footprints}
        if any(ref not in footprints or footprints[ref].locked for ref in movable):
            raise OptimizationError("optimization movable scope is unavailable or locked")
        placement_document: dict[str, Any] = {}
        artifact_bindings: list[tuple[str, str]] = []
        if launch.placement_intent_path is not None:
            artifact = read_workspace_file(
                settings.workspace,
                launch.placement_intent_path,
                allowed_suffixes={".json"},
                max_bytes=96_000,
            )
            document = bounded_json(artifact.content)
            if type(document) is not dict or set(document) - {"rules", "proposals"}:
                raise OptimizationError("optimization placement intent is malformed")
            placement_document = document
            artifact_bindings.append(
                (
                    launch.placement_intent_path,
                    "sha256:" + hashlib.sha256(artifact.content).hexdigest(),
                )
            )
        if not movable and placement_document:
            raise OptimizationError("optimization placement intent requires movable scope")
        intent = None
        if movable:
            intent = parse_placement_intent(
                {
                    **placement_document,
                    "board": relative,
                    "constraints": launch.constraints,
                    "subjects": list(movable),
                    "placement_grid_nm": launch.placement_grid_nm,
                    "expect_board_revision": revision,
                    "expect_snapshot_digest": snapshot.snapshot_digest,
                },
                max_subjects=min(128, settings.max_placement_subjects),
                max_rules=settings.max_placement_rules,
                require_revisions=True,
            )
            if any(proposal.side is not None for proposal in intent.proposals):
                # Side is inherited from each footprint; it cannot be set through a proposal.
                raise OptimizationError("optimization placement must preserve existing side")
        electrical_source = None
        electrical_digest = None
        if launch.electrical_intent_path is not None:
            electrical = read_workspace_file(
                settings.workspace,
                launch.electrical_intent_path,
                allowed_suffixes={".json"},
                max_bytes=96_000,
            )
            electrical_source = electrical.content
            electrical_digest = decode_circuit_intent(electrical_source).snapshot_digest
            artifact_bindings.append(
                (
                    launch.electrical_intent_path,
                    "sha256:" + hashlib.sha256(electrical_source).hexdigest(),
                )
            )
        context = _drc_context(board.path, settings, board)
        limits = type(launch.limits).model_validate(
            {
                **launch.limits.model_dump(),
                "max_runtime_ms": min(
                    launch.limits.max_runtime_ms, settings.max_route_preview_seconds * 1000
                ),
            }
        )
        implementation_digest = native_implementation_digest()
        fields: dict[str, Any] = {
            "schema_version": "optimization/v1",
            "board_revision": revision,
            "snapshot_digest": snapshot.snapshot_digest,
            "placement_scope": PlacementScope(
                movable_footprint_refs=movable,
                intent_digest=digest_document(
                    "optimization-placement-input/v1", placement_document
                ),
                grid_nm=launch.placement_grid_nm,
                cardinal_rotations=(0, 90, 180, 270),
                preserve_existing_side=True,
            ),
            "target_net_scope_digest": digest_document("optimization-targets/v1", targets),
            "target_net_count": len(targets),
            "routing_profile_digest": digest_document(
                "optimization-routing-profile/v1",
                {
                    "constraints": launch.constraints,
                    "astar": asdict(routing),
                    "implementation": implementation_digest,
                },
            ),
            "judge_profile_digest": digest_document(
                "optimization-judge-profile/v1",
                {
                    "drc": "kicad-drc-v1",
                    "dfm": "kicad-drc-dfm-v1",
                    "repetitions": 2,
                    "source_context_digest": _context_revision(context),
                },
            ),
            "electrical_inputs_digest": electrical_digest,
            "required_domains": ("DRC", "ERC", "DFM")
            if electrical_digest is not None
            else ("DRC", "DFM"),
            "allowed_backends": tuple(sorted(launch.allowed_backends)),
            "objective_weights": ObjectiveWeights.model_validate(
                dict.fromkeys(ObjectiveWeights.model_fields, 1)
            ),
            "seed": launch.seed,
            "limits": limits,
            "human_approval_required": True,
            "policy_profile": "deterministic-v1",
        }
        effective_request: AnyOptimizationRequest
        drc_profile = None
        if isinstance(launch, OptimizationLaunchV2):
            from copper_mcp.optimization.project_evidence import capture_project_inputs

            if launch.project is not None and electrical_source is not None:
                raise OptimizationError("optimization electrical inputs are ambiguous")
            _effective_context, drc_profile = prepare_drc_profile(
                context, relative, settings, started_at + limits.max_runtime_ms / 1000
            )
            capture_digest = library_digest = None
            if launch.project is not None:
                captured, _libraries, library_digest = capture_project_inputs(
                    launch.project, settings, started_at + limits.max_runtime_ms / 1000
                )
                capture_digest = captured.digest
            required = set(fields["required_domains"]) | set(launch.required_domains)
            if capture_digest is not None:
                required.add("ERC")
            fields.update(
                schema_version="optimization/v2",
                policy_profile="deterministic-v2",
                limits=limits.model_dump(),
                project_capture_digest=capture_digest,
                project_libraries_digest=library_digest,
                drc_profile_digest=drc_profile.digest,
                electrical_inputs_digest=capture_digest or electrical_digest,
                required_domains=tuple(domain for domain in DOMAINS if domain in required),
                routing_profile_digest=digest_document(
                    "optimization-routing-profile/v2",
                    {
                        "v1": fields["routing_profile_digest"],
                        "allocation": "equal-slots-half-checks/v2",
                    },
                ),
                judge_profile_digest=digest_document(
                    "optimization-judge-profile/v2",
                    {
                        "v1": fields["judge_profile_digest"],
                        "project": capture_digest,
                        "libraries": library_digest,
                        "drc_profile": drc_profile.digest,
                    },
                ),
            )
            effective_request = OptimizationRequestV2.model_validate(fields)
        else:
            effective_request = OptimizationRequest.model_validate(fields)
        if (time.monotonic() - started_at) * 1000 >= limits.max_runtime_ms:
            raise OptimizationError("optimization preparation exhausted its time budget")
        return PreparedOptimization(
            effective_request,
            relative,
            board.content,
            snapshot,
            profile,
            MappingProxyType(context),
            _context_revision(context),
            targets,
            intent,
            routing,
            started_at,
            implementation_digest,
            electrical_source,
            tuple(artifact_bindings),
            launch.project if isinstance(launch, OptimizationLaunchV2) else None,
            drc_profile,
        )
    except OptimizationError:
        raise
    except (OSError, ValueError, TypeError, ValidationError):
        raise OptimizationError("optimization inputs are unavailable or unsupported") from None
