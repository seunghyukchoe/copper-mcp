"""Source-only census record builders for pin-net boundary tests."""

from __future__ import annotations

from copper_mcp.engineering.project_pin_census import (
    PlacedPinOccurrence,
    PlacedSymbolOccurrence,
    ProjectPinCensus,
)

ROOT_UUID = "10000000-0000-4000-8000-000000000001"
SOURCE_SHEET = f"/{ROOT_UUID}"


def _pin(reference: str, number: str, suffix: str, *, name: str = "A") -> PlacedPinOccurrence:
    index = ord(suffix) - ord("a")
    return PlacedPinOccurrence(
        SOURCE_SHEET,
        f"20000000-0000-4000-8000-{index + 1:012d}",
        f"30000000-0000-4000-8000-{index + 1:012d}",
        reference,
        "Test:Part",
        1,
        1,
        number,
        name,
        name,
        name,
        "passive",
        "passive",
        False,
        None,
    )


def _census(*pins: PlacedPinOccurrence) -> ProjectPinCensus:
    by_identity = {
        (pin.sheet_uuid_path, pin.source_symbol_uuid): PlacedSymbolOccurrence(
            pin.sheet_uuid_path,
            pin.source_symbol_uuid,
            pin.effective_reference,
            pin.library_id,
            pin.selected_unit,
            pin.body_style,
            True,
            True,
            False,
        )
        for pin in pins
    }
    symbols = tuple(by_identity[key] for key in sorted(by_identity))
    return ProjectPinCensus("sha256:" + "1" * 64, "sha256:" + "2" * 64, symbols, tuple(pins))
