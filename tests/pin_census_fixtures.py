"""Reusable synthetic captured-source builders for pin-census tests."""

from __future__ import annotations

import hashlib
from pathlib import Path

from copper_mcp.engineering.project_erc_inputs import SymbolLibraryInput
from copper_mcp.engineering.schematic_project_capture import (
    ProjectFileBinding,
    SchematicProjectCapture,
    capture_schematic_project,
)

ROOT_UUID = "10000000-0000-4000-8000-000000000001"
CHILD_UUID = "10000000-0000-4000-8000-000000000002"
SHEET_A_UUID = "10000000-0000-4000-8000-00000000000a"
SHEET_B_UUID = "10000000-0000-4000-8000-00000000000b"
SYMBOL_1_UUID = "20000000-0000-4000-8000-000000000001"
SYMBOL_2_UUID = "20000000-0000-4000-8000-000000000002"
SYMBOL_3_UUID = "20000000-0000-4000-8000-000000000003"
PIN_UUIDS = tuple(f"30000000-0000-4000-8000-{index:012d}" for index in range(1, 16))


def _sha(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _pin(number: str, *, name: str = "~", kind: str = "passive", tail: str = "") -> str:
    return f'''(pin {kind} line {tail}
      (at 0 0 0) (length 2.54)
      (name "{name}" (effects (font (size 1.27 1.27))))
      (number "{number}" (effects (font (size 1.27 1.27)))))'''


def _resistor_body(root_name: str, *, duplicate_numbers: bool = False) -> str:
    second = "1" if duplicate_numbers else "2"
    return f'''(symbol "{root_name}"
      (symbol "R_0_1")
      (symbol "R_1_1" {_pin("1")} {_pin(second)}))'''


def _multi_body(root_name: str) -> str:
    return f'''(symbol "{root_name}"
      (symbol "M_0_0" {_pin("C", name="COM", kind="no_connect", tail="(hide yes)")})
      (symbol "M_1_1" {_pin("1", name="A", tail='(alternate "ALT" output line)')})
      (symbol "M_2_1" {_pin("2", name="B")})
      (symbol "M_1_2" {_pin("3", name="C")})
      (symbol "M_2_2" {_pin("4", name="D")}))'''


def _raw_pin(number: str, pin_uuid: str, *, alternate: str | None = None) -> str:
    selected = "" if alternate is None else f'(alternate "{alternate}")'
    return f'(pin "{number}" (uuid "{pin_uuid}"){selected})'


def _path(path: str, reference: str, unit: str = "1") -> str:
    return f'(path "{path}" (reference "{reference}") (unit {unit}))'


def _placed(
    symbol_uuid: str,
    reference: str,
    raw_pins: tuple[str, ...],
    *,
    library_id: str = "Test:R",
    paths: tuple[str, ...] | None = None,
    global_unit: str = "1",
    body_style: str | None = None,
    in_bom: str = "yes",
    on_board: str = "yes",
    dnp: str = "no",
) -> str:
    instance_rows = paths or (_path(f"/{ROOT_UUID}", reference),)
    body = "" if body_style is None else f"(body_style {body_style})"
    return f'''(symbol (lib_id "{library_id}") (unit {global_unit}) {body}
      (in_bom {in_bom}) (on_board {on_board}) (dnp {dnp})
      (uuid "{symbol_uuid}")
      (property "Reference" "STALE-{reference}")
      {"".join(raw_pins)}
      (instances (project "P" {"".join(instance_rows)})))'''


def _source(cache_body: str, *symbols: str, root_uuid: str = ROOT_UUID) -> bytes:
    return (
        f'''(kicad_sch (version 20250114) (generator "test")
          (uuid "{root_uuid}") (lib_symbols {cache_body}) {"".join(symbols)})'''
    ).encode()


def _library(body: str, *, digest: str | None = None) -> SymbolLibraryInput:
    payload = f'(kicad_symbol_lib (version 20241209) (generator "test") {body})'.encode()
    return SymbolLibraryInput("Test", payload, _sha(payload) if digest is None else digest)


def _capture(
    tmp_path: Path,
    root: bytes,
    library: SymbolLibraryInput,
    *,
    child: bytes | None = None,
) -> tuple[SchematicProjectCapture, tuple[SymbolLibraryInput, ...]]:
    files: dict[str, bytes] = {"root.kicad_sch": root, "root.kicad_pro": b"{}"}
    if child is not None:
        files["child.kicad_sch"] = child
    for name, payload in files.items():
        (tmp_path / name).write_bytes(payload)
    capture = capture_schematic_project(
        tmp_path,
        "root.kicad_sch",
        tuple(ProjectFileBinding(name, _sha(payload)) for name, payload in files.items()),
    )
    return capture, (library,)


def _basic_inputs(
    tmp_path: Path,
) -> tuple[SchematicProjectCapture, tuple[SymbolLibraryInput, ...], bytes]:
    cache = _resistor_body("Test:R")
    library = _library(_resistor_body("R"))
    first = _placed(
        SYMBOL_1_UUID,
        "R1",
        (_raw_pin("2", PIN_UUIDS[1]), _raw_pin("1", PIN_UUIDS[0])),
    )
    second = _placed(
        SYMBOL_2_UUID,
        "R2",
        (_raw_pin("1", PIN_UUIDS[2]), _raw_pin("2", PIN_UUIDS[3])),
    )
    source = _source(cache, second, first)
    capture, libraries = _capture(tmp_path, source, library)
    return capture, libraries, source
