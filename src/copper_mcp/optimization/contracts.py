"""Closed, immutable draft optimization/v1 messages, not registered MCP tools.

The five command shapes reserve a transport contract. Parsing a message establishes shape,
not authorization, board fidelity, evidence authenticity, or permission to execute a backend.
Raw requests (including footprint references and confirmation capabilities) are ephemeral;
only the explicitly redacted lifecycle record is intended for a future durable repository.
"""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Any, ClassVar, Literal, TypeAlias

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    TypeAdapter,
    ValidationError,
    ValidationInfo,
    field_validator,
    model_validator,
)

VERSION = "optimization/v1"
MAX_MESSAGE_BYTES = 131_072
MAX_MESSAGE_NODES = 16_384
MAX_MESSAGE_DEPTH = 16
Digest = Annotated[
    str, StringConstraints(pattern=r"^sha256:[0-9a-f]{64}$", min_length=71, max_length=71)
]
Counter = Annotated[int, Field(ge=0, le=(1 << 53) - 1)]
BackendVersion = Annotated[
    str, StringConstraints(pattern=r"^[0-9][0-9A-Za-z.+_-]{0,63}$", max_length=64)
]
FootprintRef = Annotated[
    str, StringConstraints(pattern=r"^footprint:[A-Za-z0-9_.:-]{1,160}$", max_length=170)
]
Capability = Annotated[
    str, StringConstraints(pattern=r"^[0-9a-f]{64}$", min_length=64, max_length=64)
]
Domain: TypeAlias = Literal["DRC", "ERC", "DFM", "SI", "PI", "thermal", "EMC"]
Verdict: TypeAlias = Literal["pass", "fail", "inconclusive", "not_run"]
Backend: TypeAlias = Literal["internal-layered-v1", "freerouting-dsn-ses-v1", "simpleroutejson-v1"]
DOMAINS: tuple[Domain, ...] = ("DRC", "ERC", "DFM", "SI", "PI", "thermal", "EMC")


class OptimizationError(ValueError):
    """A fixed, non-echoing contract or lifecycle refusal."""


def digest_document(namespace: str, document: object) -> str:
    """Domain-separated canonical identity; exclude clocks and host paths at the caller."""

    encoded = json.dumps(
        document, sort_keys=True, ensure_ascii=True, allow_nan=False, separators=(",", ":")
    ).encode("ascii")
    return "sha256:" + hashlib.sha256(namespace.encode("ascii") + b"\x00" + encoded).hexdigest()


class ClosedModel(BaseModel):
    """Frozen models contain only scalars, tuples, and other frozen models."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        hide_input_in_errors=True,
        revalidate_instances="always",
        allow_inf_nan=False,
    )
    identity_namespace: ClassVar[str] = "copper-mcp/optimization/v1/value"

    def document(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    @property
    def digest(self) -> str:
        return digest_document(self.identity_namespace, self.document())


class ResourceLimits(ClosedModel):
    max_runtime_ms: Annotated[int, Field(ge=1, le=3_600_000)]
    max_candidates: Annotated[int, Field(ge=1, le=32)]
    max_placement_evaluations: Annotated[int, Field(ge=1, le=256)]
    max_route_attempts: Annotated[int, Field(ge=1, le=32)]
    max_repair_rounds: Annotated[int, Field(ge=0, le=8)]
    max_expansions: Annotated[int, Field(ge=1, le=1_000_000)]
    max_obstacle_checks: Annotated[int, Field(ge=1, le=10_000_000)]
    max_external_output_bytes: Annotated[int, Field(ge=1, le=16_777_216)]


class PlacementScope(ClosedModel):
    movable_footprint_refs: Annotated[tuple[FootprintRef, ...], Field(max_length=128)]
    intent_digest: Digest
    grid_nm: Annotated[int, Field(ge=1, le=1_000_000_000)]
    cardinal_rotations: Annotated[
        tuple[Literal[0, 90, 180, 270], ...], Field(min_length=1, max_length=4)
    ]
    preserve_existing_side: Literal[True]

    @field_validator("preserve_existing_side", mode="before")
    @classmethod
    def exact_side_preservation(cls, value: object) -> object:
        if type(value) is not bool or value is not True:
            raise ValueError("placement side must be preserved")
        return value

    @field_validator("cardinal_rotations", mode="before")
    @classmethod
    def exact_cardinal_rotations(cls, value: object, info: ValidationInfo) -> object:
        if not isinstance(value, (tuple, list)) or any(type(angle) is not int for angle in value):
            raise ValueError("cardinal rotations must be integers")
        return tuple(value) if info.mode == "json" else value

    @model_validator(mode="after")
    def canonical_scope(self) -> PlacementScope:
        if tuple(sorted(set(self.movable_footprint_refs))) != self.movable_footprint_refs:
            raise ValueError("movable references must be unique and sorted")
        if any(type(angle) is not int for angle in self.cardinal_rotations):
            raise ValueError("cardinal rotations must be integers")
        if tuple(sorted(set(self.cardinal_rotations))) != self.cardinal_rotations:
            raise ValueError("cardinal rotations must be unique and sorted")
        if self.preserve_existing_side is not True:
            raise ValueError("placement side must be preserved")
        return self


class ObjectiveWeights(ClosedModel):
    """Integer soft-objective weights cannot trade away legality or connectivity.

    The coordinator must retain the documented lexicographic tier order; these weights operate
    inside a tier only. This slice supplies no substitute for measured route-probe evidence.
    """

    congestion: Annotated[int, Field(ge=1, le=1_000_000)]
    clearance_margin: Annotated[int, Field(ge=1, le=1_000_000)]
    vias: Annotated[int, Field(ge=1, le=1_000_000)]
    copper_length: Annotated[int, Field(ge=1, le=1_000_000)]
    displacement: Annotated[int, Field(ge=1, le=1_000_000)]
    intent_residual: Annotated[int, Field(ge=1, le=1_000_000)]


class OptimizationRequest(ClosedModel):
    identity_namespace = "copper-mcp/optimization/v1/request"
    schema_version: Literal["optimization/v1"]
    board_revision: Digest
    snapshot_digest: Digest
    placement_scope: PlacementScope
    target_net_scope_digest: Digest
    target_net_count: Annotated[int, Field(ge=1, le=4096)]
    routing_profile_digest: Digest
    judge_profile_digest: Digest
    electrical_inputs_digest: Digest | None
    required_domains: Annotated[tuple[Domain, ...], Field(min_length=1, max_length=7)]
    allowed_backends: Annotated[tuple[Backend, ...], Field(min_length=1, max_length=3)]
    objective_weights: ObjectiveWeights
    seed: Counter
    limits: ResourceLimits
    human_approval_required: Literal[True]
    policy_profile: Literal["deterministic-v1"]

    @field_validator("human_approval_required", mode="before")
    @classmethod
    def exact_human_approval(cls, value: object) -> object:
        if type(value) is not bool or value is not True:
            raise ValueError("human approval is mandatory")
        return value

    @model_validator(mode="after")
    def closed_policy(self) -> OptimizationRequest:
        if self.human_approval_required is not True:
            raise ValueError("human approval is mandatory")
        if "DRC" not in self.required_domains:
            raise ValueError("DRC is mandatory")
        if (
            tuple(domain for domain in DOMAINS if domain in self.required_domains)
            != self.required_domains
        ):
            raise ValueError("required domains must be unique and in canonical order")
        if tuple(sorted(set(self.allowed_backends))) != self.allowed_backends:
            raise ValueError("backends must be unique and sorted")
        if self.electrical_inputs_digest is not None and "ERC" not in self.required_domains:
            raise ValueError("electrical inputs require ERC")
        return self


class StartOptimization(ClosedModel):
    method: Literal["start_optimization"]
    request: OptimizationRequest


class GetOptimizationJob(ClosedModel):
    method: Literal["get_optimization_job"]
    job_id: Digest


class CancelOptimizationJob(ClosedModel):
    method: Literal["cancel_optimization_job"]
    job_id: Digest
    expected_record_revision: Counter


class ExportOptimizationPackage(ClosedModel):
    method: Literal["export_optimization_package"]
    job_id: Digest
    expected_record_revision: Counter
    expected_package_digest: Digest
    disclosure_capability: Capability


class ApproveOptimizationJob(ClosedModel):
    method: Literal["approve_optimization_job"]
    job_id: Digest
    expected_record_revision: Counter
    expected_package_digest: Digest
    expected_judge_digest: Digest
    human_confirmation_capability: Capability


OptimizationCommand = Annotated[
    StartOptimization
    | GetOptimizationJob
    | CancelOptimizationJob
    | ExportOptimizationPackage
    | ApproveOptimizationJob,
    Field(discriminator="method"),
]
_COMMAND_ADAPTER: TypeAdapter[OptimizationCommand] = TypeAdapter(OptimizationCommand)


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    if len(pairs) > 64:
        raise OptimizationError("optimization message is malformed")
    for key, value in pairs:
        if key in result:
            raise OptimizationError("optimization message is malformed")
        result[key] = value
    return result


def _no_constant(_value: str) -> None:
    raise OptimizationError("optimization message is malformed")


def bounded_json(payload: bytes) -> object:
    """Check byte, duplicate-key, depth and node bounds before model validation."""

    if type(payload) is not bytes or not 1 <= len(payload) <= MAX_MESSAGE_BYTES:
        raise OptimizationError("optimization message is malformed")
    try:
        document = json.loads(
            payload, object_pairs_hook=_unique_object, parse_constant=_no_constant
        )
        stack = [(document, 0)]
        nodes = 0
        while stack:
            value, depth = stack.pop()
            nodes += 1
            if depth > MAX_MESSAGE_DEPTH or nodes > MAX_MESSAGE_NODES:
                raise OptimizationError("optimization message is malformed")
            if isinstance(value, dict):
                stack.extend((item, depth + 1) for item in value.values())
            elif isinstance(value, list):
                stack.extend((item, depth + 1) for item in value)
        return document
    except (ValueError, UnicodeError, RecursionError):
        raise OptimizationError("optimization message is malformed") from None


def parse_command(payload: bytes) -> OptimizationCommand:
    """Non-echoing intake; capabilities and rejected caller values never enter errors."""

    bounded_json(payload)
    try:
        return _COMMAND_ADAPTER.validate_json(payload)
    except (ValidationError, ValueError, RecursionError):
        raise OptimizationError("optimization message is malformed") from None


def command_schema() -> dict[str, Any]:
    schema = _COMMAND_ADAPTER.json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = "urn:copper-mcp:optimization:v1"
    schema["description"] = "Draft internal contract. None of these five MCP methods is registered."
    return schema
