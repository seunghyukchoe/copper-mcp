"""Reusable synthetic captured-source builders for pin-census tests."""

from __future__ import annotations

ROOT_UUID = "10000000-0000-4000-8000-000000000001"
SYMBOL_1_UUID = "20000000-0000-4000-8000-000000000001"
SYMBOL_2_UUID = "20000000-0000-4000-8000-000000000002"
PIN_UUIDS = tuple(f"30000000-0000-4000-8000-{index:012d}" for index in range(1, 16))


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
