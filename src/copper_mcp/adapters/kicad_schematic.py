"""Pure deterministic rendering from Circuit Intent IR to KiCad schematic bytes.

Format sources:
- https://dev-docs.kicad.org/en/file-formats/sexpr-schematic/
- https://dev-docs.kicad.org/en/file-formats/sexpr-intro/index.html

The adapter embeds original CopperMCP symbols and performs no library or network lookup.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass

from copper_mcp import __version__
from copper_mcp.circuit_ir import (
    CircuitIntentSnapshot,
    Component,
    ComponentKind,
    Connection,
    Net,
    Port,
    verify_snapshot,
)

KICAD_SCHEMATIC_FORMAT_VERSION = "20250114"
MAX_RENDERED_SCHEMATIC_BYTES = 1_000_000

_DIGEST = re.compile(r"^sha256:[a-f0-9]{64}$")
# KiCad's default 1.27 mm schematic grid expressed in hundredths of a millimetre.
_X_ORIGIN = 2_032
_Y_ORIGIN = 2_032
_X_SPACING = 2_540
_Y_SPACING = 1_524
_COLUMNS = 9
_PIN_OFFSET = 508


@dataclass(frozen=True, slots=True)
class KiCadSchematicArtifact:
    """One immutable, content-addressed in-memory schematic derivative."""

    content: bytes
    artifact_digest: str
    intent_digest: str
    format_version: str
    component_count: int
    net_count: int
    port_count: int

    def __post_init__(self) -> None:
        if not isinstance(self.content, bytes) or not self.content:
            raise ValueError("schematic artifact content must be non-empty bytes")
        if len(self.content) > MAX_RENDERED_SCHEMATIC_BYTES:
            raise ValueError("schematic artifact exceeds the rendered byte ceiling")
        if not _DIGEST.fullmatch(self.artifact_digest):
            raise ValueError("schematic artifact digest is malformed")
        if not _DIGEST.fullmatch(self.intent_digest):
            raise ValueError("schematic intent digest is malformed")
        actual = f"sha256:{hashlib.sha256(self.content).hexdigest()}"
        if self.artifact_digest != actual:
            raise ValueError("schematic artifact digest does not match content")
        if self.format_version != KICAD_SCHEMATIC_FORMAT_VERSION:
            raise ValueError("schematic format version is unsupported")
        counts = (self.component_count, self.net_count, self.port_count)
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in counts
        ):
            raise ValueError("schematic artifact counts are malformed")
        source_marker = (
            f'\n    (comment 2 "Circuit Intent source: {self.intent_digest}")\n'.encode()
        )
        count_marker = (
            '\n    (comment 3 "Circuit Intent counts: '
            f'components={self.component_count}, nets={self.net_count}, ports={self.port_count}")\n'
        ).encode()
        if self.content.count(source_marker) != 1 or self.content.count(count_marker) != 1:
            raise ValueError("schematic artifact provenance does not match its content")


def _quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _mm(hundredths: int) -> str:
    negative = hundredths < 0
    whole, fraction = divmod(abs(hundredths), 100)
    suffix = f"{fraction:02d}".rstrip("0")
    rendered = f"{whole}.{suffix}" if suffix else str(whole)
    return f"-{rendered}" if negative else rendered


def _stable_uuid(intent_digest: str, role: str) -> str:
    payload = hashlib.sha256(f"{intent_digest}\0{role}".encode()).digest()
    raw = bytearray(payload[:16])
    raw[6] = (raw[6] & 0x0F) | 0x40
    raw[8] = (raw[8] & 0x3F) | 0x80
    return str(uuid.UUID(bytes=bytes(raw)))


def _position(index: int) -> tuple[int, int]:
    return (
        _X_ORIGIN + (index % _COLUMNS) * _X_SPACING,
        _Y_ORIGIN + (index // _COLUMNS) * _Y_SPACING,
    )


def _font_effects(*, hidden: bool = False, justify: str | None = None) -> list[str]:
    lines = ["(effects", "  (font", "    (size 1.27 1.27)", "  )"]
    if justify is not None:
        lines.append(f"  (justify {justify})")
    if hidden:
        lines.append("  (hide yes)")
    lines.append(")")
    return lines


def _indent(lines: list[str], spaces: int) -> list[str]:
    prefix = " " * spaces
    return [prefix + line for line in lines]


def _on_board(board_eligible: bool) -> str:
    """Render the symbol flag that decides whether KiCad's board netlist sees this symbol.

    The delivered schematic is ``no``: ADR-0015 scoped a schematic-delivery artifact for a subset
    with no footprint assignments, and ADR-0056's verifier asserts the resulting
    ``exclude_from_board`` netlist property. The board-eligible projection ADR-0084 introduces is
    ``yes``, because an ``on_board no`` symbol never enters the netlist ``pcb drc
    --schematic-parity`` compares against, which makes a correct board and a wrong one produce
    identical output.
    """

    return "yes" if board_eligible else "no"


def _library_property(
    key: str,
    value: str,
    *,
    x: str,
    y: str,
    hidden: bool = False,
) -> list[str]:
    lines = [f"(property {_quote(key)} {_quote(value)}", f"  (at {x} {y} 0)"]
    lines.extend(_indent(_font_effects(hidden=hidden), 2))
    lines.append(")")
    return lines


def _library_symbol(kind: ComponentKind, *, board_eligible: bool = False) -> list[str]:
    if kind is ComponentKind.RESISTOR:
        library_id = "CopperMCP:R"
        unit_name = "R"
        reference = "R"
        value = "R"
        description = "CopperMCP original two-pin resistor primitive"
        graphic = [
            '(symbol "R_0_1"',
            "  (rectangle",
            "    (start -0.762 -1.778)",
            "    (end 0.762 1.778)",
            "    (stroke (width 0.2032) (type default))",
            "    (fill (type none))",
            "  )",
            ")",
        ]
        pin_length = "3.302"
    else:
        library_id = "CopperMCP:C"
        unit_name = "C"
        reference = "C"
        value = "C"
        description = "CopperMCP original two-pin non-polarized capacitor primitive"
        graphic = [
            '(symbol "C_0_1"',
            "  (polyline",
            "    (pts (xy -1.524 -0.508) (xy 1.524 -0.508))",
            "    (stroke (width 0.3048) (type default))",
            "    (fill (type none))",
            "  )",
            "  (polyline",
            "    (pts (xy -1.524 0.508) (xy 1.524 0.508))",
            "    (stroke (width 0.3048) (type default))",
            "    (fill (type none))",
            "  )",
            ")",
        ]
        pin_length = "4.572"

    lines = [f"(symbol {_quote(library_id)}", "  (pin_numbers (hide yes))"]
    lines.extend(["  (pin_names (offset 0.254) (hide yes))", "  (exclude_from_sim no)"])
    lines.extend(["  (in_bom yes)", f"  (on_board {_on_board(board_eligible)})"])
    for property_lines in (
        _library_property("Reference", reference, x="2.54", y="0"),
        _library_property("Value", value, x="-2.54", y="0"),
        _library_property("Footprint", "", x="0", y="0", hidden=True),
        _library_property("Datasheet", "~", x="0", y="0", hidden=True),
        _library_property("Description", description, x="0", y="0", hidden=True),
    ):
        lines.extend(_indent(property_lines, 2))
    lines.extend(_indent(graphic, 2))
    lines.extend(
        _indent(
            [
                f'(symbol "{unit_name}_1_1"',
                "  (pin passive line",
                "    (at 0 -5.08 90)",
                f"    (length {pin_length})",
                '    (name "~" (effects (font (size 1.27 1.27))))',
                '    (number "1" (effects (font (size 1.27 1.27))))',
                "  )",
                "  (pin passive line",
                "    (at 0 5.08 270)",
                f"    (length {pin_length})",
                '    (name "~" (effects (font (size 1.27 1.27))))',
                '    (number "2" (effects (font (size 1.27 1.27))))',
                "  )",
                ")",
            ],
            2,
        )
    )
    lines.extend(["  (embedded_fonts no)", ")"])
    return lines


def _instance_property(
    key: str,
    value: str,
    *,
    x: int,
    y: int,
    hidden: bool = False,
) -> list[str]:
    lines = [f"(property {_quote(key)} {_quote(value)}", f"  (at {_mm(x)} {_mm(y)} 0)"]
    lines.extend(_indent(_font_effects(hidden=hidden, justify="left"), 2))
    lines.append(")")
    return lines


def _component_instance(
    component: Component,
    *,
    x: int,
    y: int,
    root_uuid: str,
    project_name: str,
    intent_digest: str,
    board_eligible: bool = False,
) -> list[str]:
    library_id = "CopperMCP:R" if component.kind is ComponentKind.RESISTOR else "CopperMCP:C"
    description = (
        "CopperMCP original resistor primitive"
        if component.kind is ComponentKind.RESISTOR
        else "CopperMCP original non-polarized capacitor primitive"
    )
    component_uuid = _stable_uuid(intent_digest, f"component:{component.id}")
    lines = ["(symbol", f"  (lib_id {_quote(library_id)})", f"  (at {_mm(x)} {_mm(y)} 0)"]
    lines.extend(
        [
            "  (unit 1)",
            "  (exclude_from_sim no)",
            "  (in_bom yes)",
            f"  (on_board {_on_board(board_eligible)})",
            "  (dnp no)",
            f"  (uuid {_quote(component_uuid)})",
        ]
    )
    for property_lines in (
        _instance_property("Reference", component.reference, x=x + 254, y=y - 127),
        _instance_property("Value", component.value, x=x + 254, y=y + 127),
        _instance_property("Footprint", "", x=x, y=y, hidden=True),
        _instance_property("Datasheet", "~", x=x, y=y, hidden=True),
        _instance_property("Description", description, x=x, y=y, hidden=True),
    ):
        lines.extend(_indent(property_lines, 2))
    for pin in ("1", "2"):
        pin_uuid = _stable_uuid(intent_digest, f"pin:{component.id}:{pin}")
        lines.extend([f"  (pin {_quote(pin)}", f"    (uuid {_quote(pin_uuid)})", "  )"])
    lines.extend(
        [
            "  (instances",
            f"    (project {_quote(project_name)}",
            f"      (path {_quote('/' + root_uuid)}",
            f"        (reference {_quote(component.reference)})",
            "        (unit 1)",
            "      )",
            "    )",
            "  )",
            ")",
        ]
    )
    return lines


def _pin_position(
    positions: dict[str, tuple[int, int]],
    connection: Connection,
) -> tuple[int, int]:
    x, y = positions[connection.component_id]
    # KiCad symbol-library Y coordinates are mirrored into schematic-sheet coordinates.
    return x, y + _PIN_OFFSET if connection.pin == "1" else y - _PIN_OFFSET


def _local_label(
    net: Net,
    connection: Connection,
    *,
    position: tuple[int, int],
    intent_digest: str,
) -> list[str]:
    x, y = position
    label_uuid = _stable_uuid(
        intent_digest,
        f"label:{net.id}:{connection.component_id}:{connection.pin}",
    )
    lines = [f"(label {_quote(net.name)}", f"  (at {_mm(x)} {_mm(y)} 0)"]
    lines.extend(_indent(_font_effects(justify="left bottom"), 2))
    lines.extend([f"  (uuid {_quote(label_uuid)})", ")"])
    return lines


def _global_label(
    net: Net,
    port: Port,
    connection: Connection,
    *,
    position: tuple[int, int],
    intent_digest: str,
) -> list[str]:
    x, y = position
    label_uuid = _stable_uuid(
        intent_digest,
        f"global-label:{port.id}:{connection.component_id}:{connection.pin}",
    )
    lines = [
        f"(global_label {_quote(net.name)}",
        f"  (shape {port.direction.value})",
        f"  (at {_mm(x)} {_mm(y)} 0)",
    ]
    lines.extend(_indent(_font_effects(justify="left bottom"), 2))
    lines.extend([f"  (uuid {_quote(label_uuid)})", ")"])
    return lines


def render_kicad_schematic(
    snapshot: CircuitIntentSnapshot,
    *,
    board_eligible: bool = False,
) -> KiCadSchematicArtifact:
    """Render one verified logical topology into deterministic in-memory KiCad bytes.

    ``board_eligible`` selects between the two derivatives ADR-0084 distinguishes. The default
    ``False`` is the *delivered* schematic and its bytes are frozen — every ADR-0056 round-trip
    digest and golden identity depends on them. ``True`` is the *parity projection*: the same
    intent, the same connectivity, differing only in the ``on_board`` flag, which is what lets
    ``kicad-cli pcb drc --schematic-parity`` see the symbols at all. The projection is never
    delivered to a caller as a schematic; it exists to give KiCad something to check a board
    against, and its digest is reported separately so the two are never confused.
    """

    if type(board_eligible) is not bool:
        raise ValueError("schematic board eligibility must be a bool")
    verify_snapshot(snapshot)
    content = snapshot.content
    root_uuid = _stable_uuid(snapshot.snapshot_digest, "root-sheet")
    positions = {
        component.id: _position(index) for index, component in enumerate(content.components)
    }
    port_by_net = {port.net_id: port for port in content.ports}

    lines = [
        "(kicad_sch",
        f"  (version {KICAD_SCHEMATIC_FORMAT_VERSION})",
        '  (generator "copper_mcp")',
        f"  (generator_version {_quote(__version__)})",
        f"  (uuid {_quote(root_uuid)})",
        '  (paper "A4")',
        "  (title_block",
        f"    (title {_quote(content.title)})",
        '    (comment 1 "Generated from CopperMCP Circuit Intent IR 0.1.0")',
        f'    (comment 2 "Circuit Intent source: {snapshot.snapshot_digest}")',
        '    (comment 3 "Circuit Intent counts: '
        f"components={len(content.components)}, nets={len(content.nets)}, "
        f'ports={len(content.ports)}")',
        "  )",
        "  (lib_symbols",
    ]
    kinds = {component.kind for component in content.components}
    for kind in sorted(kinds, key=lambda item: item.value):
        lines.extend(_indent(_library_symbol(kind, board_eligible=board_eligible), 4))
    lines.append("  )")

    for net in content.nets:
        port = port_by_net.get(net.id)
        for connection in net.connections:
            position = _pin_position(positions, connection)
            if port is not None:
                label_lines = _global_label(
                    net,
                    port,
                    connection,
                    position=position,
                    intent_digest=snapshot.snapshot_digest,
                )
            else:
                label_lines = _local_label(
                    net,
                    connection,
                    position=position,
                    intent_digest=snapshot.snapshot_digest,
                )
            lines.extend(_indent(label_lines, 2))

    for component in content.components:
        x, y = positions[component.id]
        lines.extend(
            _indent(
                _component_instance(
                    component,
                    x=x,
                    y=y,
                    root_uuid=root_uuid,
                    project_name=content.project_name,
                    intent_digest=snapshot.snapshot_digest,
                    board_eligible=board_eligible,
                ),
                2,
            )
        )

    lines.extend(
        [
            "  (sheet_instances",
            '    (path "/"',
            '      (page "1")',
            "    )",
            "  )",
            "  (embedded_fonts no)",
            ")",
        ]
    )
    rendered = ("\n".join(lines) + "\n").encode("utf-8")
    if len(rendered) > MAX_RENDERED_SCHEMATIC_BYTES:
        raise ValueError("rendered schematic exceeds the output byte ceiling")
    return KiCadSchematicArtifact(
        content=rendered,
        artifact_digest=f"sha256:{hashlib.sha256(rendered).hexdigest()}",
        intent_digest=snapshot.snapshot_digest,
        format_version=KICAD_SCHEMATIC_FORMAT_VERSION,
        component_count=len(content.components),
        net_count=len(content.nets),
        port_count=len(content.ports),
    )
