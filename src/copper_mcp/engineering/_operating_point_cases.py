"""Private operating-point case binding; it neither executes nor judges simulation."""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
from dataclasses import dataclass
from typing import Annotated, Literal, NoReturn, cast

from pydantic import Field, StringConstraints, TypeAdapter, ValidationError, model_validator

from copper_mcp.engineering.inputs import Identifier, parse_electrical_inputs
from copper_mcp.engineering.native_pin_net_map import NativePinNet
from copper_mcp.engineering.project_spice_model_binding import ProjectSpiceModelBinding
from copper_mcp.engineering.project_spice_source import _admit_binding
from copper_mcp.engineering.spice_export import SpiceExportError, _net_name, expected_spice_rows
from copper_mcp.optimization.contracts import ClosedModel, Digest, OptimizationError, bounded_json

_TEXT = Annotated[str, StringConstraints(min_length=1, max_length=4096)]
_MAX_BYTES = 128 * 1024
_MAX_PINS = 16_384
_PIN = re.compile(r"[^\s=]{1,4096}\Z")


class OperatingPointCasesError(ValueError):
    """Fixed private refusal without topology, source, or simulation authority."""


def _fail(message: str = "SPICE operating-point cases are malformed") -> NoReturn:
    raise OperatingPointCasesError(message)


def _deadline(value: object) -> float:
    if type(value) not in (int, float) or isinstance(value, bool):
        _fail()
    result = 0.0
    failed = False
    try:
        result = float(cast(int | float, value))
    except (OverflowError, ValueError):
        failed = True
    if failed or not math.isfinite(result):
        _fail()
    if time.monotonic() >= result:
        _fail("SPICE operating-point cases deadline expired")
    return result


def _check(deadline: float) -> None:
    if time.monotonic() >= deadline:
        _fail("SPICE operating-point cases deadline expired")


class _Pin(ClosedModel):
    reference: _TEXT
    pin_number: _TEXT

    @model_validator(mode="after")
    def valid_text(self) -> _Pin:
        if _PIN.fullmatch(self.pin_number) is None or any(
            ord(value) < 32 or ord(value) > 126 for value in self.reference
        ):
            raise ValueError("pin is malformed")
        return self

    def __repr__(self) -> str:
        return "<_Pin redacted>"

    __str__ = __repr__


class _RailNode(ClosedModel):
    rail_id: Identifier
    positive: _Pin
    negative: _Pin

    @model_validator(mode="after")
    def distinct_pins(self) -> _RailNode:
        if self.positive == self.negative:
            raise ValueError("rail endpoints must differ")
        return self

    def __repr__(self) -> str:
        return "<_RailNode redacted>"

    __str__ = __repr__


class _Load(ClosedModel):
    load_case_id: Identifier
    from_: _Pin = Field(alias="from")
    to: _Pin

    def __repr__(self) -> str:
        return "<_Load redacted>"

    __str__ = __repr__


class _Case(ClosedModel):
    case_id: Identifier
    temperature_millic: Annotated[int, Field(ge=-100_000, le=250_000)]
    ground: _Pin
    rail_nodes: Annotated[tuple[_RailNode, ...], Field(max_length=32)]
    energized_rails: Annotated[tuple[Identifier, ...], Field(max_length=32)]
    current_loads: Annotated[tuple[_Load, ...], Field(max_length=256)]

    @model_validator(mode="after")
    def canonical_members(self) -> _Case:
        rails = tuple(item.rail_id for item in self.rail_nodes)
        loads = tuple(item.load_case_id for item in self.current_loads)
        if (
            tuple(sorted(rails)) != rails
            or len(set(rails)) != len(rails)
            or tuple(sorted(set(self.energized_rails))) != self.energized_rails
            or tuple(sorted(loads)) != loads
            or len(set(loads)) != len(loads)
        ):
            raise ValueError("case members must be canonical")
        return self

    def __repr__(self) -> str:
        return "<_Case redacted>"

    __str__ = __repr__


class _CasesDocument(ClosedModel):
    identity_namespace = "copper-mcp/project-spice-operating-point-cases/v1"
    schema_version: Literal["project-spice-operating-point-cases/v1"]
    declaration_digest: Digest
    project_capture_digest: Digest
    cases: Annotated[tuple[_Case, ...], Field(min_length=1, max_length=8)]

    @model_validator(mode="after")
    def canonical_cases(self) -> _CasesDocument:
        values = tuple(item.case_id for item in self.cases)
        if tuple(sorted(values)) != values or len(set(values)) != len(values):
            raise ValueError("cases must be canonical")
        return self

    def __repr__(self) -> str:
        return "<_CasesDocument redacted>"

    __str__ = __repr__


@dataclass(frozen=True, slots=True, repr=False)
class OperatingPointRail:
    rail_id: str
    positive_node: str
    negative_node: str
    signed_voltage_uv: int
    energized: bool

    def __repr__(self) -> str:
        return "<OperatingPointRail redacted>"


@dataclass(frozen=True, slots=True, repr=False)
class OperatingPointLoad:
    load_case_id: str
    rail_id: str
    current_ua: int
    duration_ms: int
    from_node: str
    to_node: str

    def __repr__(self) -> str:
        return "<OperatingPointLoad redacted>"


@dataclass(frozen=True, slots=True, repr=False)
class OperatingPointCase:
    case_id: str
    temperature_millic: int
    ground_node: str
    rails: tuple[OperatingPointRail, ...]
    loads: tuple[OperatingPointLoad, ...]

    def __repr__(self) -> str:
        return "<OperatingPointCase redacted>"


@dataclass(frozen=True, slots=True, repr=False)
class OperatingPointCases:
    declaration_digest: str
    project_capture_digest: str
    binding_digest: str
    case_document_digest: str
    cases: tuple[OperatingPointCase, ...]

    def __repr__(self) -> str:
        return "<OperatingPointCases redacted>"


def _document_digest(document: _CasesDocument, deadline: float) -> str:
    digest = hashlib.sha256(b"copper-mcp/project-spice-operating-point-cases/v1\x00")
    encoder = json.JSONEncoder(
        sort_keys=True, ensure_ascii=True, allow_nan=False, separators=(",", ":")
    )
    for token in encoder.iterencode(document.document()):
        _check(deadline)
        digest.update(token.encode("ascii"))
    _check(deadline)
    return "sha256:" + digest.hexdigest()


def _parse(payload: bytes, deadline: float) -> _CasesDocument:
    result: _CasesDocument | None = None
    try:
        if type(payload) is not bytes or len(payload) > _MAX_BYTES:
            _fail()
        bounded_json(payload)
        result = TypeAdapter(_CasesDocument).validate_json(payload)
    except (OperatingPointCasesError, OptimizationError, ValidationError, ValueError, TypeError):
        pass
    if result is None:
        _fail()
    _check(deadline)
    return result


def _node_name(node: NativePinNet, deadline: float) -> str:
    _check(deadline)
    try:
        value = _net_name(node.net_name)
    except (AttributeError, SpiceExportError, TypeError, ValueError):
        _fail()
    return "0" if value.casefold() == "gnd" else value.casefold()


def resolve_operating_point_cases(
    case_json: bytes,
    declaration_json: bytes,
    binding: ProjectSpiceModelBinding,
    *,
    deadline: float,
) -> OperatingPointCases:
    """Bind explicit DC cases to complete native model pins without creating simulator commands."""

    active = _deadline(deadline)
    result: OperatingPointCases | None = None
    try:
        document = _parse(case_json, active)
        declaration = parse_electrical_inputs(declaration_json)
        _check(active)
        _admit_binding(binding, active)
        expected_spice_rows(binding, deadline=active)
        if (
            document.declaration_digest != declaration.digest
            or document.project_capture_digest != binding.project_capture_digest
            or binding.declaration_digest != declaration.digest
            or declaration.operating_limits is None
        ):
            _fail()
        nodes: dict[tuple[str, str], str] = {}
        count = 0
        for reference in binding.references:
            for pin in reference.pins:
                _check(active)
                count += 1
                if count > _MAX_PINS:
                    _fail()
                if pin.port is None or pin.node.no_connect:
                    continue
                key = pin.node.reference, pin.node.pin_number
                if key in nodes:
                    _fail()
                nodes[key] = _node_name(pin.node, active)
        rails = {rail.rail_id: rail for rail in declaration.rails}
        loads = {load.case_id: load for load in declaration.load_cases}
        completed: list[OperatingPointCase] = []
        for item in document.cases:
            _check(active)
            if (
                not declaration.operating_limits.min_temperature_millic
                <= item.temperature_millic
                <= declaration.operating_limits.max_temperature_millic
            ):
                _fail()
            ground = nodes.get((item.ground.reference, item.ground.pin_number))
            if ground != "0" or tuple(rail.rail_id for rail in item.rail_nodes) != tuple(rails):
                _fail()
            if any(rail_id not in rails for rail_id in item.energized_rails):
                _fail()
            pairs: dict[str, tuple[str, str]] = {}
            resolved_rails: list[OperatingPointRail] = []
            for rail_node in item.rail_nodes:
                _check(active)
                positive = nodes.get((rail_node.positive.reference, rail_node.positive.pin_number))
                negative = nodes.get((rail_node.negative.reference, rail_node.negative.pin_number))
                if positive is None or negative is None or positive == negative:
                    _fail()
                pairs[rail_node.rail_id] = positive, negative
                rail = rails[rail_node.rail_id]
                resolved_rails.append(
                    OperatingPointRail(
                        rail.rail_id,
                        positive,
                        negative,
                        rail.nominal_voltage_uv,
                        rail.rail_id in item.energized_rails,
                    )
                )
            used_rails: set[str] = set()
            resolved_loads: list[OperatingPointLoad] = []
            for load_node in item.current_loads:
                _check(active)
                load = loads.get(load_node.load_case_id)
                if load is None or load.rail_id in used_rails:
                    _fail()
                source = nodes.get((load_node.from_.reference, load_node.from_.pin_number))
                sink = nodes.get((load_node.to.reference, load_node.to.pin_number))
                pair = pairs[load.rail_id]
                if source is None or sink is None or (source, sink) not in (pair, pair[::-1]):
                    _fail()
                used_rails.add(load.rail_id)
                resolved_loads.append(
                    OperatingPointLoad(
                        load.case_id, load.rail_id, load.current_ua, load.duration_ms, source, sink
                    )
                )
            completed.append(
                OperatingPointCase(
                    item.case_id,
                    item.temperature_millic,
                    ground,
                    tuple(resolved_rails),
                    tuple(resolved_loads),
                )
            )
        output = OperatingPointCases(
            declaration.digest,
            binding.project_capture_digest,
            binding._digest(active),
            _document_digest(document, active),
            tuple(completed),
        )
        _check(active)
        result = output
    except (OperatingPointCasesError, OptimizationError, SpiceExportError, ValueError, TypeError):
        pass
    if result is None:
        _fail()
    return result
