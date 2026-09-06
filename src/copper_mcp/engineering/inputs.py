"""Private, ephemeral electrical-inputs/v1 declarations; never engineering evidence."""

from __future__ import annotations

from typing import Annotated, Literal, TypeAlias

from pydantic import Field, StringConstraints, TypeAdapter, ValidationError, model_validator

from copper_mcp.optimization.contracts import (
    ClosedModel,
    Digest,
    OptimizationError,
    bounded_json,
)

SCHEMA_VERSION = "electrical-inputs/v1"
Identifier = Annotated[
    str, StringConstraints(pattern=r"^[a-z][a-z0-9_-]{0,31}$", min_length=1, max_length=32)
]
ProfileId = Literal[
    "analog-audio-v1",
    "mcu-sensor-v1",
    "low-power-supply-v1",
]
ArtifactRole: TypeAlias = Literal["bom", "model-library", "netlist", "schematic"]
ElectricalDomain: TypeAlias = Literal["ERC", "SI", "PI", "thermal", "EMC"]
MissingReason = Literal[
    "missing_bom_binding",
    "missing_edge_rate",
    "missing_model_binding",
    "missing_netlist_artifact",
    "missing_operating_limit",
    "missing_rail_load_case",
    "missing_source_artifact",
    "missing_stackup",
    "missing_suitable_model",
    "missing_thermal_conductivity",
    "missing_thermal_boundary",
    "missing_power_dissipation",
    "missing_loss_tangent",
    "missing_schematic_artifact",
]


def _canonical(values: tuple[object, ...], attribute: str) -> bool:
    return tuple(sorted(values, key=lambda value: getattr(value, attribute))) == values and len(
        {getattr(value, attribute) for value in values}
    ) == len(values)


class SourceArtifactRef(ClosedModel):
    """An immutable source reference, deliberately without a path, URL, or executable authority."""

    artifact_id: Identifier
    role: ArtifactRole
    artifact_digest: Digest


class BomBinding(ClosedModel):
    item_id: Identifier
    artifact_id: Identifier
    quantity: Annotated[int, Field(ge=1, le=10_000)]
    power_dissipation_uw: Annotated[int, Field(ge=0, le=100_000_000)] | None = None


class ModelBinding(ClosedModel):
    model_id: Identifier
    bom_item_id: Identifier
    artifact_id: Identifier
    model_kind: Literal["ibis", "spice", "thermal"]
    model_digest: Digest


class StackupLayer(ClosedModel):
    physical_index: Annotated[int, Field(ge=0, le=31)]
    kind: Literal["copper", "dielectric"]
    material_id: Identifier
    thickness_nm: Annotated[int, Field(ge=1, le=10_000_000)]
    copper_thickness_nm: Annotated[int, Field(ge=0, le=1_000_000)]
    relative_permittivity_ppm: Annotated[int, Field(ge=1_000_000, le=30_000_000)]
    thermal_conductivity_uw_per_mk: Annotated[int, Field(ge=1, le=10_000_000_000)] | None = None
    loss_tangent_ppm: Annotated[int, Field(ge=0, le=1_000_000)] | None = None

    @model_validator(mode="after")
    def kind_matches_copper_thickness(self) -> StackupLayer:
        if (self.kind == "copper") != (self.copper_thickness_nm > 0):
            raise ValueError("stackup kind must agree with copper thickness")
        if self.kind == "copper" and self.copper_thickness_nm != self.thickness_nm:
            raise ValueError("copper layer thickness declarations disagree")
        return self


class Rail(ClosedModel):
    rail_id: Identifier
    nominal_voltage_uv: Annotated[int, Field(ge=-100_000_000, le=100_000_000)]


class LoadCase(ClosedModel):
    case_id: Identifier
    rail_id: Identifier
    current_ua: Annotated[int, Field(ge=0, le=100_000_000)]
    duration_ms: Annotated[int, Field(ge=1, le=86_400_000)]


class EdgeRate(ClosedModel):
    signal_id: Identifier
    rail_id: Identifier
    model_id: Identifier
    rise_time_ps: Annotated[int, Field(ge=1, le=1_000_000_000)]
    fall_time_ps: Annotated[int, Field(ge=1, le=1_000_000_000)]


class OperatingLimits(ClosedModel):
    min_temperature_millic: Annotated[int, Field(ge=-100_000, le=250_000)]
    max_temperature_millic: Annotated[int, Field(ge=-100_000, le=250_000)]
    max_input_voltage_uv: Annotated[int, Field(ge=1, le=100_000_000)]

    @model_validator(mode="after")
    def ordered_temperature_range(self) -> OperatingLimits:
        if self.min_temperature_millic > self.max_temperature_millic:
            raise ValueError("operating temperatures must be ordered")
        return self


class ThermalBoundary(ClosedModel):
    ambient_temperature_millic: Annotated[int, Field(ge=-100_000, le=250_000)]
    convection_uw_per_k: Annotated[int, Field(ge=0, le=10_000_000_000)]
    enclosure_to_ambient_uk_per_w: Annotated[int, Field(ge=0, le=10_000_000_000)]


class ElectricalInputs(ClosedModel):
    """Strict input declaration. Its digest identifies inputs, not engineering correctness."""

    identity_namespace = "copper-mcp/electrical-inputs/v1"
    schema_version: Literal["electrical-inputs/v1"]
    board_revision: Digest
    snapshot_digest: Digest
    project_context_digest: Digest
    profile_id: ProfileId
    source_artifacts: Annotated[tuple[SourceArtifactRef, ...], Field(min_length=1, max_length=16)]
    bom_bindings: Annotated[tuple[BomBinding, ...], Field(max_length=512)] = ()
    model_bindings: Annotated[tuple[ModelBinding, ...], Field(max_length=128)] = ()
    stackup: Annotated[tuple[StackupLayer, ...], Field(max_length=32)] = ()
    rails: Annotated[tuple[Rail, ...], Field(max_length=32)] = ()
    load_cases: Annotated[tuple[LoadCase, ...], Field(max_length=256)] = ()
    edge_rates: Annotated[tuple[EdgeRate, ...], Field(max_length=512)] = ()
    operating_limits: OperatingLimits | None = None
    thermal_boundary: ThermalBoundary | None = None

    @model_validator(mode="after")
    def canonical_and_bound(self) -> ElectricalInputs:
        if not _canonical(self.source_artifacts, "artifact_id"):
            raise ValueError("source artifacts must be unique and canonical")
        if not _canonical(self.bom_bindings, "item_id"):
            raise ValueError("BOM bindings must be unique and canonical")
        if not _canonical(self.model_bindings, "model_id"):
            raise ValueError("model bindings must be unique and canonical")
        if tuple(layer.physical_index for layer in self.stackup) != tuple(range(len(self.stackup))):
            raise ValueError("stackup layers must be unique and canonical")
        if not _canonical(self.rails, "rail_id"):
            raise ValueError("rails must be unique and canonical")
        if not _canonical(self.load_cases, "case_id"):
            raise ValueError("load cases must be unique and canonical")
        if not _canonical(self.edge_rates, "signal_id"):
            raise ValueError("edge rates must be unique and canonical")
        artifacts = {artifact.artifact_id: artifact for artifact in self.source_artifacts}
        items = {binding.item_id for binding in self.bom_bindings}
        models = {binding.model_id for binding in self.model_bindings}
        rail_ids = {rail.rail_id for rail in self.rails}
        if any(
            binding.artifact_id not in artifacts or artifacts[binding.artifact_id].role != "bom"
            for binding in self.bom_bindings
        ):
            raise ValueError("BOM binding source is unavailable")
        if any(
            binding.artifact_id not in artifacts
            or artifacts[binding.artifact_id].role != "model-library"
            or binding.bom_item_id not in items
            for binding in self.model_bindings
        ):
            raise ValueError("model binding reference is unavailable")
        if any(case.rail_id not in rail_ids for case in self.load_cases) or any(
            edge.rail_id not in rail_ids for edge in self.edge_rates
        ):
            raise ValueError("rail reference is unavailable")
        if any(edge.model_id not in models for edge in self.edge_rates):
            raise ValueError("edge-rate model reference is unavailable")
        if self.stackup:
            copper_count = sum(layer.kind == "copper" for layer in self.stackup)
            if (
                copper_count not in (2, 4, 6, 8)
                or self.stackup[0].kind != "copper"
                or self.stackup[-1].kind != "copper"
            ):
                raise ValueError("stackup copper layers are unsupported")
            if any(
                self.stackup[index].kind == self.stackup[index + 1].kind
                for index in range(len(self.stackup) - 1)
            ):
                raise ValueError("stackup layers must alternate physically")
        return self


class DomainCompleteness(ClosedModel):
    domain: ElectricalDomain
    complete: bool
    missing_reasons: tuple[MissingReason, ...]

    @model_validator(mode="after")
    def consistent_missing_reasons(self) -> DomainCompleteness:
        if tuple(sorted(set(self.missing_reasons))) != self.missing_reasons:
            raise ValueError("missing reasons must be unique and canonical")
        if self.complete != (not self.missing_reasons):
            raise ValueError("completeness must agree with missing reasons")
        return self


class InputCompletenessAssessment(ClosedModel):
    """Input coverage only: no pass verdict, evidence, simulation, or signoff authority."""

    identity_namespace = "copper-mcp/electrical-inputs/v1/completeness"
    package_digest: Digest
    domains: Annotated[tuple[DomainCompleteness, ...], Field(min_length=5, max_length=5)]
    assessment_state: Literal["inputs_only"] = "inputs_only"

    @model_validator(mode="after")
    def all_assessable_domains_once(self) -> InputCompletenessAssessment:
        if tuple(domain.domain for domain in self.domains) != ("ERC", "SI", "PI", "thermal", "EMC"):
            raise ValueError("assessment domains must be complete and canonical")
        return self


class RedactedInputsProjection(ClosedModel):
    package_digest: Digest
    source_artifact_count: Annotated[int, Field(ge=0, le=16)]
    bom_binding_count: Annotated[int, Field(ge=0, le=512)]
    model_binding_count: Annotated[int, Field(ge=0, le=128)]
    stackup_layer_count: Annotated[int, Field(ge=0, le=32)]
    missing_requirements: tuple[DomainCompleteness, ...]


def parse_electrical_inputs(payload: bytes) -> ElectricalInputs:
    """Decode one bounded JSON declaration. The raw payload remains caller-owned and ephemeral."""

    bounded_json(payload)
    try:
        return TypeAdapter(ElectricalInputs).validate_json(payload)
    except (ValidationError, ValueError, TypeError, RecursionError):
        raise OptimizationError("electrical inputs are malformed") from None


def assess_completeness(inputs: ElectricalInputs) -> InputCompletenessAssessment:
    """State which declarations are absent without evaluating their engineering sufficiency."""

    inputs = ElectricalInputs.model_validate(inputs)
    base: list[MissingReason] = []
    if not inputs.source_artifacts:
        base.append("missing_source_artifact")
    roles = {artifact.role for artifact in inputs.source_artifacts}
    model_kinds = {binding.model_kind for binding in inputs.model_bindings}
    models = {binding.model_id: binding for binding in inputs.model_bindings}
    all_edges_have_signal_models = bool(inputs.edge_rates) and all(
        models[edge.model_id].model_kind in {"ibis", "spice"} for edge in inputs.edge_rates
    )
    erc = list(base)
    if "schematic" not in roles:
        erc.append("missing_schematic_artifact")
    if "netlist" not in roles:
        erc.append("missing_netlist_artifact")
    if not inputs.bom_bindings:
        erc.append("missing_bom_binding")
    si = list(base)
    if not inputs.stackup:
        si.append("missing_stackup")
    if not all_edges_have_signal_models:
        si.append("missing_suitable_model")
    if not inputs.edge_rates:
        si.append("missing_edge_rate")
    if inputs.operating_limits is None:
        si.append("missing_operating_limit")
    pi = list(base)
    if not inputs.stackup:
        pi.append("missing_stackup")
    if "spice" not in model_kinds:
        pi.append("missing_suitable_model")
    if not inputs.rails or {rail.rail_id for rail in inputs.rails} - {
        case.rail_id for case in inputs.load_cases
    }:
        pi.append("missing_rail_load_case")
    if inputs.operating_limits is None:
        pi.append("missing_operating_limit")
    thermal = list(base)
    if not inputs.stackup:
        thermal.append("missing_stackup")
    if "thermal" not in model_kinds:
        thermal.append("missing_suitable_model")
    if any(binding.power_dissipation_uw is None for binding in inputs.bom_bindings):
        thermal.append("missing_power_dissipation")
    if inputs.thermal_boundary is None:
        thermal.append("missing_thermal_boundary")
    if inputs.operating_limits is None:
        thermal.append("missing_operating_limit")
    emc = list(base)
    if not inputs.stackup:
        emc.append("missing_stackup")
    if not all_edges_have_signal_models:
        emc.append("missing_suitable_model")
    if not inputs.edge_rates:
        emc.append("missing_edge_rate")
    if inputs.operating_limits is None:
        emc.append("missing_operating_limit")
    if any(layer.loss_tangent_ppm is None for layer in inputs.stackup):
        emc.append("missing_loss_tangent")
    if any(layer.thermal_conductivity_uw_per_mk is None for layer in inputs.stackup):
        thermal.append("missing_thermal_conductivity")
    rows: tuple[tuple[ElectricalDomain, list[MissingReason]], ...] = (
        ("ERC", erc),
        ("SI", si),
        ("PI", pi),
        ("thermal", thermal),
        ("EMC", emc),
    )
    return InputCompletenessAssessment(
        package_digest=inputs.digest,
        domains=tuple(
            DomainCompleteness(
                domain=domain,
                complete=not reasons,
                missing_reasons=tuple(sorted(set(reasons))),
            )
            for domain, reasons in rows
        ),
    )


def redacted_projection(inputs: ElectricalInputs) -> RedactedInputsProjection:
    """The only durable-safe view: digest, counts, and absent input categories."""

    assessment = assess_completeness(inputs)
    return RedactedInputsProjection(
        package_digest=inputs.digest,
        source_artifact_count=len(inputs.source_artifacts),
        bom_binding_count=len(inputs.bom_bindings),
        model_binding_count=len(inputs.model_bindings),
        stackup_layer_count=len(inputs.stackup),
        missing_requirements=assessment.domains,
    )
