"""Bounded admission of supplied source-pin census records, without native authority."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import NoReturn

from copper_mcp.adapters.sexpr import QuotedAtom
from copper_mcp.engineering import _pin_census_source as _source
from copper_mcp.engineering import component_netlist as _xml
from copper_mcp.engineering.project_pin_census import (
    PlacedPinOccurrence,
    PlacedSymbolOccurrence,
    ProjectPinCensus,
)

_MAX_SOURCE_PINS = 250_000
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_MALFORMED = "KiCad native pin-net map is malformed"
_BOUNDS = "KiCad native pin-net map exceeds its bounds"
_DEADLINE = "KiCad native pin-net map deadline expired"


class NativePinNetMapError(ValueError):
    """A fixed, context-free refusal from the private native pin-net boundary."""


def _fail(message: str = _MALFORMED) -> NoReturn:
    raise NativePinNetMapError(message)


def _check_deadline(deadline: float) -> None:
    if time.monotonic() >= deadline:
        _fail(_DEADLINE)


@dataclass(frozen=True, slots=True, repr=False)
class SourcePinAlias:
    sheet_uuid_path: str
    source_symbol_uuid: str
    source_pin_uuid: str

    def __repr__(self) -> str:
        return "<SourcePinAlias redacted>"


def _text(value: object, *, allow_empty: bool = False) -> str:
    text: str | None = None
    refusal = _MALFORMED
    try:
        text = _xml._bounded_text(value, allow_empty=allow_empty)
    except _xml.ComponentNetlistError as error:
        refusal = _component_refusal(error)
    if text is None:
        _fail(refusal)
    return text


def _component_refusal(error: _xml.ComponentNetlistError) -> str:
    if error.args == (_xml._DEADLINE,):
        return _DEADLINE
    if error.args == (_xml._BOUNDS,):
        return _BOUNDS
    return _MALFORMED


def _alias_key(alias: SourcePinAlias, deadline: float) -> tuple[str, str, str]:
    _check_deadline(deadline)
    return alias.sheet_uuid_path, alias.source_symbol_uuid, alias.source_pin_uuid


@dataclass(frozen=True, slots=True)
class _SourcePinGroup:
    aliases: tuple[SourcePinAlias, ...]
    library_id: str
    effective_name: str
    electrical_type: str


@dataclass(frozen=True, slots=True)
class _SourceSymbolContext:
    reference: str
    library_id: str
    selected_unit: int
    body_style: int


def _source_text(value: object, *, allow_empty: bool = False) -> str:
    if type(value) is not str and type(value) is not QuotedAtom:
        _fail()
    # Character count is O(1), and UTF-8 needs at least one byte per character.
    # Refuse before copying a quoted atom or scanning/encoding either string type.
    if len(value) > _xml._MAX_VALUE_BYTES:
        _fail(_BOUNDS)
    if type(value) is QuotedAtom:
        value = str(value)
    return _text(value, allow_empty=allow_empty)


def _source_symbol_identity(
    item: PlacedPinOccurrence | PlacedSymbolOccurrence,
) -> tuple[str, str] | None:
    identity: tuple[str, str] | None = None
    try:
        identity = (
            _source._canonical_sheet_path(_source_text(item.sheet_uuid_path)),
            _source._canonical_uuid(_source_text(item.source_symbol_uuid)),
        )
    except NativePinNetMapError:
        raise
    except (_source.ProjectPinCensusError, AttributeError, TypeError, ValueError, OverflowError):
        pass
    return identity


def _positive_selector(value: object) -> int | None:
    if type(value) is not int or not 1 <= value <= _source._MAX_SELECTOR:
        return None
    return value


def _validate_census(
    census: object, deadline: float
) -> tuple[dict[tuple[str, str], _SourcePinGroup], set[tuple[str, str]], int, str]:
    _check_deadline(deadline)
    if type(census) is not ProjectPinCensus:
        _fail()
    if type(census.symbols) is not tuple or type(census.pins) is not tuple:
        _fail()
    if len(census.pins) > _MAX_SOURCE_PINS:
        _fail(_BOUNDS)
    for binding in (census.capture_digest, census.symbol_context_digest):
        _check_deadline(deadline)
        if type(binding) is not str:
            _fail()
        if len(binding) > _xml._MAX_VALUE_BYTES:
            _fail(_BOUNDS)
        if len(binding) != 71 or _DIGEST.fullmatch(binding) is None:
            _fail()
    aliases_by_key: dict[tuple[str, str], list[tuple[SourcePinAlias, tuple[str, str, str]]]] = {}
    nonvirtual_components: set[tuple[str, str]] = set()
    symbols_by_identity: dict[tuple[str, str], _SourceSymbolContext] = {}
    identities: set[tuple[str, str, str]] = set()
    virtual_pin_count = 0
    if len(census.symbols) > _xml._MAX_COMPONENTS:
        _fail(_BOUNDS)
    for symbol in census.symbols:
        _check_deadline(deadline)
        try:
            if type(symbol) is not PlacedSymbolOccurrence:
                _fail()
            symbol_identity = _source_symbol_identity(symbol)
            reference = _source_text(symbol.effective_reference)
            library_id = _source_text(symbol.library_id)
            selected_unit = _positive_selector(symbol.selected_unit)
            body_style = _positive_selector(symbol.body_style)
            if any(type(flag) is not bool for flag in (symbol.in_bom, symbol.on_board, symbol.dnp)):
                _fail()
        except NativePinNetMapError:
            raise
        except (
            _source.ProjectPinCensusError,
            AttributeError,
            TypeError,
            ValueError,
            OverflowError,
        ):
            symbol_identity = None
            reference = None
            library_id = None
            selected_unit = None
            body_style = None
        if (
            symbol_identity is None
            or reference is None
            or library_id is None
            or selected_unit is None
            or body_style is None
            or symbol_identity in symbols_by_identity
        ):
            _fail()
        symbols_by_identity[symbol_identity] = _SourceSymbolContext(
            reference, library_id, selected_unit, body_style
        )
        if not reference.startswith("#"):
            nonvirtual_components.add((reference, library_id))
    for pin in census.pins:
        _check_deadline(deadline)
        pin_identity: tuple[str, str, str] | None = None
        try:
            if type(pin) is not PlacedPinOccurrence:
                _fail()
            symbol_identity = _source_symbol_identity(pin)
            source_pin_uuid = _source._canonical_uuid(_source_text(pin.source_pin_uuid))
            pin_identity = (
                (*symbol_identity, source_pin_uuid) if symbol_identity is not None else None
            )
            reference = _source_text(pin.effective_reference)
            number = _source_text(pin.pin_number)
            library_id = _source_text(pin.library_id)
            _source_text(pin.raw_declared_name, allow_empty=True)
            _source_text(pin.declared_name, allow_empty=True)
            name = _source_text(pin.effective_name, allow_empty=True)
            declared_type = _source_text(pin.declared_electrical_type)
            electrical_type = _source_text(pin.effective_electrical_type)
            selected_unit = _positive_selector(pin.selected_unit)
            body_style = _positive_selector(pin.body_style)
            if type(pin.hidden) is not bool:
                _fail()
            if pin.selected_alternate is not None:
                _source_text(pin.selected_alternate)
            alias = SourcePinAlias(*pin_identity) if pin_identity is not None else None
        except NativePinNetMapError:
            raise
        except (
            _source.ProjectPinCensusError,
            AttributeError,
            TypeError,
            ValueError,
            OverflowError,
        ):
            alias = None
        if alias is None:
            _fail()
        if (
            pin_identity is None
            or reference is None
            or number is None
            or library_id is None
            or selected_unit is None
            or body_style is None
            or declared_type not in _source._ELECTRICAL_TYPES
            or electrical_type not in _source._ELECTRICAL_TYPES
        ):
            _fail()
        source_symbol = symbols_by_identity.get(pin_identity[:2])
        if source_symbol is None or (
            reference,
            library_id,
            selected_unit,
            body_style,
        ) != (
            source_symbol.reference,
            source_symbol.library_id,
            source_symbol.selected_unit,
            source_symbol.body_style,
        ):
            _fail()
        if pin_identity in identities:
            _fail()
        identities.add(pin_identity)
        if reference.startswith("#"):
            virtual_pin_count += 1
            continue
        if (reference, library_id) not in nonvirtual_components:
            _fail()
        aliases_by_key.setdefault((reference, number), []).append(
            (alias, (library_id, name, electrical_type))
        )
    grouped: dict[tuple[str, str], _SourcePinGroup] = {}
    for key, rows in aliases_by_key.items():
        _check_deadline(deadline)
        first_metadata = rows[0][1]
        for _, metadata in rows:
            _check_deadline(deadline)
            if metadata != first_metadata:
                _fail()
        grouped[key] = _SourcePinGroup(
            tuple(
                sorted((alias for alias, _ in rows), key=lambda alias: _alias_key(alias, deadline))
            ),
            first_metadata[0],
            first_metadata[1],
            first_metadata[2],
        )
    digest: str | None = None
    try:
        digest = census._digest(deadline)
    except (AttributeError, TypeError, ValueError, OverflowError, RecursionError):
        pass
    _check_deadline(deadline)
    if digest is None:
        _fail()
    return grouped, nonvirtual_components, virtual_pin_count, digest


__all__ = ["NativePinNetMapError", "SourcePinAlias"]
