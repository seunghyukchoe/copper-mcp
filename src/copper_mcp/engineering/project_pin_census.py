"""Private bounded placed-pin occurrence census over captured KiCad source bytes.

The census is an immutable source interpretation within the explicitly supported flat-symbol
profile.  It performs no filesystem access, native execution, connectivity inference, model
validation, engineering validation, or mutation.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from dataclasses import asdict, dataclass
from typing import cast

from copper_mcp.engineering import _pin_census_source as _source
from copper_mcp.engineering.capture import CaptureLimits
from copper_mcp.engineering.project_erc_inputs import (
    SymbolLibraryInput,
    prepare_project_erc,
)
from copper_mcp.engineering.schematic_project_capture import SchematicProjectCapture

ProjectPinCensusError = _source.ProjectPinCensusError
_check_deadline = _source._check_deadline
_fail = _source._fail


@dataclass(frozen=True, slots=True, repr=False)
class PlacedSymbolOccurrence:
    sheet_uuid_path: str
    source_symbol_uuid: str
    effective_reference: str
    library_id: str
    selected_unit: int
    body_style: int
    in_bom: bool
    on_board: bool
    dnp: bool

    def __repr__(self) -> str:
        return "<PlacedSymbolOccurrence redacted>"


@dataclass(frozen=True, slots=True, repr=False)
class PlacedPinOccurrence:
    sheet_uuid_path: str
    source_symbol_uuid: str
    source_pin_uuid: str
    effective_reference: str
    library_id: str
    selected_unit: int
    body_style: int
    pin_number: str
    raw_declared_name: str
    declared_name: str
    effective_name: str
    declared_electrical_type: str
    effective_electrical_type: str
    hidden: bool
    selected_alternate: str | None

    def __repr__(self) -> str:
        return "<PlacedPinOccurrence redacted>"


@dataclass(frozen=True, slots=True, repr=False)
class ProjectPinCensus:
    """Frozen private records with a dynamically self-bound, redacted disclosure."""

    capture_digest: str
    symbol_context_digest: str
    symbols: tuple[PlacedSymbolOccurrence, ...]
    pins: tuple[PlacedPinOccurrence, ...]

    def __repr__(self) -> str:
        return "<ProjectPinCensus redacted>"

    @property
    def digest(self) -> str:
        return self._digest(math.inf)

    def _digest(self, deadline: float) -> str:
        digest = hashlib.sha256(b"copper-mcp/project-pin-census/v1\x00")
        encoder = json.JSONEncoder(
            sort_keys=True, ensure_ascii=True, allow_nan=False, separators=(",", ":")
        )

        def write(payload: str) -> None:
            _check_deadline(deadline)
            digest.update(payload.encode("ascii"))

        write('{"capture_digest":')
        for token in encoder.iterencode(self.capture_digest):
            write(token)
        write(',"pins":[')
        for index, pin in enumerate(self.pins):
            _check_deadline(deadline)
            if index:
                write(",")
            for token in encoder.iterencode(asdict(pin)):
                write(token)
        write('],"symbol_context_digest":')
        for token in encoder.iterencode(self.symbol_context_digest):
            write(token)
        write(',"symbols":[')
        for index, symbol in enumerate(self.symbols):
            _check_deadline(deadline)
            if index:
                write(",")
            for token in encoder.iterencode(asdict(symbol)):
                write(token)
        write("]}")
        _check_deadline(deadline)
        return "sha256:" + digest.hexdigest()

    def document(self) -> dict[str, object]:
        return {
            "capture_digest": self.capture_digest,
            "symbol_context_digest": self.symbol_context_digest,
            "census_digest": self.digest,
            "symbol_count": len(self.symbols),
            "pin_count": len(self.pins),
            "native_validation": "not_run",
            "model_validation": "not_run",
            "engineering_validation": "not_run",
            "apply_authority": "none",
        }


def _copy_controls(limits: object, deadline: object) -> tuple[CaptureLimits, float]:
    if type(deadline) not in (int, float):
        _fail("project pin census controls are malformed")
    normalized: float | None = None
    try:
        normalized = float(cast("int | float", deadline))
    except OverflowError:
        pass
    if normalized is None or not math.isfinite(normalized):
        _fail("project pin census controls are malformed")

    copied: CaptureLimits | None = None
    try:
        if limits is None:
            copied = CaptureLimits()
        elif type(limits) is CaptureLimits:
            copied = CaptureLimits(
                limits.max_file_bytes,
                limits.max_total_bytes,
                limits.max_capture_seconds,
            )
    except (AttributeError, ValueError):
        pass
    if copied is None:
        _fail("project pin census controls are malformed")
    active_deadline = min(normalized, time.monotonic() + copied.max_capture_seconds)
    _check_deadline(active_deadline)
    return copied, active_deadline


def _derive(
    capture: SchematicProjectCapture,
    libraries: tuple[SymbolLibraryInput, ...],
    limits: CaptureLimits,
    deadline: float,
) -> ProjectPinCensus:
    prepared = prepare_project_erc(capture, libraries, limits=limits, deadline=deadline)
    _check_deadline(deadline)
    budget = _source._Budget()
    source_bytes = {
        item.path: item.content for item in capture._files if item.path.endswith(".kicad_sch")
    }
    parsed: dict[str, tuple[_source._PlacedTemplate, ...]] = {}
    for source_path in sorted(source_bytes):
        budget.step(deadline)
        parsed[source_path] = _source._parse_source(
            source_bytes[source_path], limits, budget, deadline
        )

    symbols: list[PlacedSymbolOccurrence] = []
    pins: list[PlacedPinOccurrence] = []
    for hierarchy_instance in capture.hierarchy.instance_paths:
        budget.step(deadline)
        source = parsed.get(hierarchy_instance.source_path)
        if source is None:
            _fail("project pin census hierarchy source is missing")
        path = _source._canonical_sheet_path(hierarchy_instance.uuid_path)
        for template in source:
            budget.symbol(deadline)
            instance = template.instances.get(path)
            if instance is None:
                _fail("project pin census current instance is missing or ambiguous")
            if instance.unit not in template.body.units:
                _fail("project pin census selected unit is unknown")
            if (instance.unit, template.body_style) not in template.body.unit_body_styles and (
                instance.unit,
                0,
            ) not in template.body.unit_body_styles:
                _fail("project pin census selected unit/body style combination is unknown")
            occurrence = PlacedSymbolOccurrence(
                path,
                template.source_symbol_uuid,
                instance.reference,
                template.library_id,
                instance.unit,
                template.body_style,
                template.in_bom,
                template.on_board,
                template.dnp,
            )
            symbols.append(occurrence)
            raw_by_number = {pin.number: pin for pin in template.raw_pins}
            for library_pin in template.body.pins:
                budget.step(deadline)
                if library_pin.body_style not in {
                    0,
                    template.body_style,
                } or library_pin.unit not in {
                    0,
                    instance.unit,
                }:
                    continue
                raw_pin = raw_by_number[library_pin.number]
                alternate: _source._Alternate | None = None
                if raw_pin.selected_alternate is not None:
                    for item in library_pin.alternates:
                        budget.step(deadline)
                        if item.name == raw_pin.selected_alternate:
                            alternate = item
                            break
                    if alternate is None:
                        _fail("project pin census selected alternate is unresolved")
                budget.pin(deadline)
                pins.append(
                    PlacedPinOccurrence(
                        path,
                        template.source_symbol_uuid,
                        raw_pin.source_uuid,
                        instance.reference,
                        template.library_id,
                        instance.unit,
                        template.body_style,
                        library_pin.number,
                        library_pin.raw_name,
                        library_pin.declared_name,
                        alternate.name if alternate is not None else library_pin.declared_name,
                        library_pin.electrical_type,
                        (
                            alternate.electrical_type
                            if alternate is not None
                            else library_pin.electrical_type
                        ),
                        library_pin.hidden,
                        raw_pin.selected_alternate,
                    )
                )

    _check_deadline(deadline)

    def symbol_key(item: PlacedSymbolOccurrence) -> tuple[object, ...]:
        _check_deadline(deadline)
        return (
            item.sheet_uuid_path,
            item.source_symbol_uuid,
            item.effective_reference,
            item.library_id,
            item.selected_unit,
            item.body_style,
        )

    def pin_key(item: PlacedPinOccurrence) -> tuple[object, ...]:
        _check_deadline(deadline)
        return (
            item.sheet_uuid_path,
            item.source_symbol_uuid,
            item.pin_number,
            item.source_pin_uuid,
        )

    ordered_symbols = tuple(sorted(symbols, key=symbol_key))
    _check_deadline(deadline)
    ordered_pins = tuple(sorted(pins, key=pin_key))
    _check_deadline(deadline)
    result = ProjectPinCensus(
        prepared.capture_digest,
        prepared.execution_digest,
        ordered_symbols,
        ordered_pins,
    )
    result._digest(deadline)
    _check_deadline(deadline)
    return result


def derive_project_pin_census(
    capture: SchematicProjectCapture,
    libraries: tuple[SymbolLibraryInput, ...],
    *,
    deadline: float,
    limits: CaptureLimits | None = None,
) -> ProjectPinCensus:
    """Derive complete placed-pin occurrences for the captured flat-symbol profile."""

    copied_limits, active_deadline = _copy_controls(limits, deadline)
    result: ProjectPinCensus | None = None
    try:
        result = _derive(capture, libraries, copied_limits, active_deadline)
    except ProjectPinCensusError:
        raise
    except (AttributeError, TypeError, ValueError, OverflowError, RecursionError):
        pass
    if result is None:
        if time.monotonic() >= active_deadline:
            _fail("project pin census deadline expired")
        _fail("project pin census refused")
    return result
