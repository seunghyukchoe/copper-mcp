#!/usr/bin/env python3
"""Generate the CopperTone board-first KiCad engineering preview.

SPDX-License-Identifier: CERN-OHL-S-2.0
Copyright 2026 CopperMCP Contributors

This generator intentionally emits only a PCB and project settings. It does not
pretend to provide a source schematic, electrical simulation, or measured audio
performance. Run ``./validate.sh`` after generation to refill zones and execute
the authoritative KiCad checks and exports.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BOARD_PATH = ROOT / "coppertone-buffer.kicad_pcb"
PROJECT_PATH = ROOT / "coppertone-buffer.kicad_pro"
METRICS_PATH = ROOT / "metrics.json"


NETS: dict[str, int] = {
    "GND": 1,
    "9V_RAW": 2,
    "VCC": 3,
    "VREF": 4,
    "R_IN_RAW": 5,
    "R_IN_BIASED": 6,
    "R_BUF": 7,
    "R_ISO": 8,
    "R_OUT": 9,
    "L_IN_RAW": 10,
    "L_IN_BIASED": 11,
    "L_BUF": 12,
    "L_ISO": 13,
    "L_OUT": 14,
}


def f(value: float) -> str:
    """Return compact, deterministic KiCad numeric text."""

    return f"{value:.4f}".rstrip("0").rstrip(".")


def effects(size: float = 1.0, thickness: float = 0.15) -> str:
    return f"(effects (font (size {f(size)} {f(size)}) (thickness {f(thickness)})))"


def property_text(name: str, value: str, y: float) -> str:
    return (
        f'    (property "{name}" "{value}" (at 0 {f(y)} 0) (layer "F.Fab") (hide yes) {effects()})'
    )


def model(path: str) -> str:
    return (
        f'    (model "${{KICAD10_3DMODEL_DIR}}/{path}" '
        "(offset (xyz 0 0 0)) (scale (xyz 1 1 1)) "
        "(rotate (xyz 0 0 0)))"
    )


def pad_net(name: str) -> str:
    return f'(net {NETS[name]} "{name}")'


def smd_pad(
    number: str,
    x: float,
    y: float,
    sx: float,
    sy: float,
    net: str,
    *,
    shape: str = "roundrect",
) -> str:
    ratio = " (roundrect_rratio 0.2)" if shape == "roundrect" else ""
    return (
        f'    (pad "{number}" smd {shape} (at {f(x)} {f(y)}) '
        f'(size {f(sx)} {f(sy)}) (layers "F.Cu" "F.Paste" "F.Mask") '
        f'{ratio} {pad_net(net)} (pintype "passive"))'
    )


def tht_pad(
    number: str,
    x: float,
    y: float,
    sx: float,
    sy: float,
    drill: str,
    net: str,
    *,
    shape: str = "oval",
    at_rotation: float | None = None,
) -> str:
    rotation = "" if at_rotation is None else f" {f(at_rotation)}"
    return (
        f'    (pad "{number}" thru_hole {shape} '
        f"(at {f(x)} {f(y)}{rotation}) (size {f(sx)} {f(sy)}) "
        f'(drill {drill}) (layers "*.Cu" "*.Mask") {pad_net(net)} '
        '(pintype "passive"))'
    )


def footprint_start(name: str, ref: str, value: str, x: float, y: float, rot: float) -> list[str]:
    return [
        f'  (footprint "CopperTone_{name}"',
        '    (layer "F.Cu")',
        f"    (at {f(x)} {f(y)} {f(rot)})",
        property_text("Reference", ref, -2.0),
        property_text("Value", value, 2.0),
    ]


def resistor(ref: str, value: str, x: float, y: float, rot: float, n1: str, n2: str) -> str:
    lines = footprint_start("R_0805", ref, value, x, y, rot)
    lines.extend(
        [
            "    (attr smd)",
            "    (fp_rect (start -1.5 -0.9) (end 1.5 0.9) "
            '(stroke (width 0.1) (type default)) (fill none) (layer "F.Fab"))',
            smd_pad("1", -1.2, 0, 1.2, 1.4, n1),
            smd_pad("2", 1.2, 0, 1.2, 1.4, n2),
            model("Resistor_SMD.3dshapes/R_0805_2012Metric.step"),
            "  )",
        ]
    )
    return "\n".join(lines)


def ceramic_cap(ref: str, value: str, x: float, y: float, rot: float, n1: str, n2: str) -> str:
    lines = footprint_start("C_0805", ref, value, x, y, rot)
    lines.extend(
        [
            "    (attr smd)",
            "    (fp_rect (start -1.5 -0.9) (end 1.5 0.9) "
            '(stroke (width 0.1) (type default)) (fill none) (layer "F.Fab"))',
            smd_pad("1", -1.2, 0, 1.2, 1.4, n1),
            smd_pad("2", 1.2, 0, 1.2, 1.4, n2),
            model("Capacitor_SMD.3dshapes/C_0805_2012Metric.step"),
            "  )",
        ]
    )
    return "\n".join(lines)


def electrolytic(
    ref: str,
    value: str,
    x: float,
    y: float,
    rot: float,
    n_positive: str,
    n_negative: str,
    *,
    diameter: float,
) -> str:
    if diameter == 4.0:
        pitch, pad_x, model_name = 1.8, 2.6, "CP_Elec_4x5.8"
    elif diameter == 6.3:
        pitch, pad_x, model_name = 2.7, 3.5, "CP_Elec_6.3x5.8"
    else:  # pragma: no cover - generator invariant
        raise ValueError("unsupported electrolytic diameter")
    lines = footprint_start(model_name, ref, value, x, y, rot)
    lines.extend(
        [
            "    (attr smd)",
            f"    (fp_circle (center 0 0) (end {f(diameter / 2)} 0) "
            '(stroke (width 0.1) (type default)) (fill none) (layer "F.Fab"))',
            smd_pad("1", -pitch, 0, pad_x, 1.6, n_positive),
            smd_pad("2", pitch, 0, pad_x, 1.6, n_negative),
            model(f"Capacitor_SMD.3dshapes/{model_name}.step"),
            "  )",
        ]
    )
    return "\n".join(lines)


def film_cap(ref: str, x: float, y: float, n1: str, n2: str) -> str:
    lines = footprint_start("MKS2_470n_P5.00", ref, "470nF PET film", x, y, 0)
    lines.extend(
        [
            "    (attr through_hole)",
            "    (fp_rect (start -1.1 -1.75) (end 6.1 1.75) "
            '(stroke (width 0.1) (type default)) (fill none) (layer "F.Fab"))',
            tht_pad("1", 0, 0, 1.6, 1.6, "0.8", n1, shape="circle"),
            tht_pad("2", 5, 0, 1.6, 1.6, "0.8", n2, shape="circle"),
            model("Capacitor_THT.3dshapes/C_Rect_L7.2mm_W3.5mm_P5.00mm_FKS2_FKP2_MKS2_MKP2.step"),
            "  )",
        ]
    )
    return "\n".join(lines)


def diode(ref: str, x: float, y: float, rot: float) -> str:
    lines = footprint_start("D_SMA", ref, "SS14", x, y, rot)
    lines.extend(
        [
            "    (attr smd)",
            "    (fp_rect (start -2.6 -1.3) (end 2.6 1.3) "
            '(stroke (width 0.1) (type default)) (fill none) (layer "F.Fab"))',
            smd_pad("1", -2, 0, 2.5, 1.8, "VCC"),
            smd_pad("2", 2, 0, 2.5, 1.8, "9V_RAW"),
            model("Diode_SMD.3dshapes/D_SMA.step"),
            "  )",
        ]
    )
    return "\n".join(lines)


def opamp(x: float, y: float, rot: float) -> str:
    pad_nets = {
        "1": "R_BUF",
        "2": "R_BUF",
        "3": "R_IN_BIASED",
        "4": "GND",
        "5": "L_IN_BIASED",
        "6": "L_BUF",
        "7": "L_BUF",
        "8": "VCC",
    }
    positions = {
        "1": (-2.475, -1.905),
        "2": (-2.475, -0.635),
        "3": (-2.475, 0.635),
        "4": (-2.475, 1.905),
        "5": (2.475, 1.905),
        "6": (2.475, 0.635),
        "7": (2.475, -0.635),
        "8": (2.475, -1.905),
    }
    lines = footprint_start("SOIC-8_3.9x4.9mm_P1.27mm", "U1", "OPA1656IDR", x, y, rot)
    lines.extend(
        [
            "    (attr smd)",
            "    (fp_rect (start -1.95 -2.45) (end 1.95 2.45) "
            '(stroke (width 0.1) (type default)) (fill none) (layer "F.Fab"))',
        ]
    )
    for number, (px, py) in positions.items():
        lines.append(smd_pad(number, px, py, 0.6, 1.95, pad_nets[number]))
    lines.extend(
        [
            model("Package_SO.3dshapes/SOIC-8_3.9x4.9mm_P1.27mm.step"),
            "  )",
        ]
    )
    return "\n".join(lines)


def audio_jack(ref: str, x: float, y: float, rot: float, ring: str, tip: str) -> str:
    lines = footprint_start("SJ1-3513N", ref, "SJ1-3513N stereo TRS", x, y, rot)
    lines.extend(
        [
            "    (attr through_hole)",
            "    (fp_rect (start -1.8 -5.75) (end 12.2 5.75) "
            '(stroke (width 0.1) (type default)) (fill none) (layer "F.Fab"))',
            tht_pad("R", 8.3, -5, 3, 1.5, "oval 2 1", ring),
            tht_pad("S", 0, 0, 3.5, 2.5, "oval 2 1", "GND", at_rotation=270),
            tht_pad("T", 3.5, 4.5, 3.5, 2.5, "oval 2 1", tip),
            "  )",
        ]
    )
    return "\n".join(lines)


def power_header() -> str:
    lines = footprint_start("PinHeader_1x02_P2.54mm", "J3", "9V POWER", 20, 2, 270)
    lines.extend(
        [
            "    (attr through_hole)",
            "    (fp_rect (start -1.27 -1.27) (end 1.27 3.81) "
            '(stroke (width 0.1) (type default)) (fill none) (layer "F.Fab"))',
            tht_pad("1", 0, 0, 1.7, 1.7, "1", "9V_RAW", shape="rect"),
            tht_pad("2", 0, 2.54, 1.7, 1.7, "1", "GND", shape="circle"),
            model("Connector_PinHeader_2.54mm.3dshapes/PinHeader_1x02_P2.54mm_Vertical.step"),
            "  )",
        ]
    )
    return "\n".join(lines)


def testpoint(ref: str, x: float, y: float, net: str) -> str:
    lines = footprint_start("TestPoint_Plated_Hole_D2.0", ref, net, x, y, 0)
    lines.extend(
        [
            "    (attr through_hole)",
            tht_pad("1", 0, 0, 2, 2, "1", net, shape="circle"),
            "  )",
        ]
    )
    return "\n".join(lines)


def mounting_hole(ref: str, x: float, y: float) -> str:
    lines = footprint_start("MountingHole_2.7mm_M2.5", ref, "M2.5", x, y, 0)
    lines.extend(
        [
            "    (attr exclude_from_pos_files exclude_from_bom)",
            "    (fp_circle (center 0 0) (end 2.85 0) "
            '(stroke (width 0.1) (type default)) (fill none) (layer "F.Fab"))',
            '    (pad "" np_thru_hole circle (at 0 0) (size 2.7 2.7) '
            '(drill 2.7) (layers "*.Cu" "*.Mask"))',
            "  )",
        ]
    )
    return "\n".join(lines)


@dataclass(frozen=True)
class Route:
    net: str
    points: tuple[tuple[float, float], ...]
    width: float = 0.25
    layer: str = "F.Cu"

    def segments(self) -> Iterable[str]:
        for start, end in zip(self.points, self.points[1:], strict=False):
            yield (
                f"  (segment (start {f(start[0])} {f(start[1])}) "
                f"(end {f(end[0])} {f(end[1])}) (width {f(self.width)}) "
                f'(layer "{self.layer}") (net {NETS[self.net]}))'
            )

    def length(self) -> float:
        return sum(math.dist(a, b) for a, b in zip(self.points, self.points[1:], strict=False))


ROUTES: tuple[Route, ...] = (
    Route("9V_RAW", ((20, 2), (26, 2), (29, 3)), 0.5),
    Route("VCC", ((29, 7), (29, 5), (11.8, 5), (11.8, 4.5)), 0.5),
    Route("VCC", ((29, 7), (32, 7), (32, 14.5), (31.5, 15.5), (30.3, 17.5)), 0.5),
    Route("VCC", ((30.3, 17.5), (29.405, 17.475)), 0.5),
    Route("VCC", ((32, 7), (32, 5)), 0.5),
    Route("VCC", ((30, 7), (23.5, 11.5)), 0.5, layer="B.Cu"),
    Route("VCC", ((23.5, 11.5), (22.8, 12.6)), 0.5),
    Route("VREF", ((22.8, 15), (22.8, 16))),
    Route("VREF", ((18, 12.8), (20, 14.5), (22.8, 15))),
    Route("VREF", ((16.3, 10.5), (18, 12.8))),
    Route("VREF", ((24.5, 10.2), (21, 10.5), (20.5, 13), (20, 14.5))),
    Route("VREF", ((24.5, 19.8), (21, 20), (20.5, 17), (22.8, 16))),
    Route("R_IN_RAW", ((10.6, 10), (13, 10), (15.8, 7.5))),
    Route(
        "R_IN_BIASED",
        ((20.8, 7.5), (24.5, 7.8), (26.865, 9.5), (26.865, 12.525)),
    ),
    Route("L_IN_RAW", ((5.8, 19.5), (9, 21.5), (13, 23.5), (15.8, 23.5))),
    Route(
        "L_IN_BIASED",
        ((20.8, 23.5), (24.5, 22.2), (25.595, 20.5), (25.595, 17.475)),
    ),
    Route("R_BUF", ((28.135, 12.525), (29.405, 12.525))),
    Route("R_BUF", ((29.405, 12.525), (29.4, 10.5), (30.8, 9.5))),
    Route("R_ISO", ((33.2, 9.5), (35.3, 9.5))),
    Route("R_OUT", ((35.3, 13.1), (37, 13.5), (38, 17), (41.4, 20))),
    Route("R_OUT", ((41.4, 20), (40, 21.8))),
    Route("L_BUF", ((26.865, 17.475), (28.135, 17.475))),
    Route("L_BUF", ((28.135, 17.475), (28, 19.5), (30.8, 19.5))),
    Route("L_ISO", ((33.2, 19.5), (35.3, 19.5))),
    Route("L_OUT", ((35.3, 23.1), (36.5, 23.5))),
    Route("L_OUT", ((36.5, 23.5), (39, 25.5), (44, 25.5), (44, 14), (46.2, 10.5)), layer="B.Cu"),
    Route("L_OUT", ((46.2, 10.5), (46.2, 7.5), (44, 6.3))),
)


def via(x: float, y: float, net: str, *, size: float = 0.8, drill: float = 0.4) -> str:
    return (
        f"  (via (at {f(x)} {f(y)}) (size {f(size)}) (drill {f(drill)}) "
        f'(layers "F.Cu" "B.Cu") (net {NETS[net]}))'
    )


def keepout(cx: float, cy: float) -> str:
    r = 2.85
    points = []
    for index in range(8):
        angle = math.radians(22.5 + index * 45)
        points.append((cx + r * math.cos(angle), cy + r * math.sin(angle)))
    pts = " ".join(f"(xy {f(x)} {f(y)})" for x, y in points)
    return (
        '  (zone (net 0) (net_name "") (layers "F.Cu" "B.Cu") '
        "(hatch full 0.5) (connect_pads (clearance 0)) (min_thickness 0.25) "
        "(keepout (tracks not_allowed) (vias not_allowed) (pads allowed) "
        "(copperpour not_allowed) (footprints allowed)) "
        f"(fill (thermal_gap 0.3) (thermal_bridge_width 0.3)) (polygon (pts {pts})))"
    )


def copper_zone(layer: str) -> str:
    return (
        f'  (zone (net {NETS["GND"]}) (net_name "GND") (layer "{layer}") '
        "(hatch edge 0.5) (connect_pads (clearance 0.25)) "
        "(min_thickness 0.25) (fill yes (thermal_gap 0.3) "
        "(island_removal_mode 0) "
        "(thermal_bridge_width 0.3)) (polygon (pts "
        "(xy 0.5 0.5) (xy 51.5 0.5) (xy 51.5 29.5) (xy 0.5 29.5))))"
    )


def board_text() -> str:
    footprints = [
        audio_jack("J1", 2.3, 15, 0, "R_IN_RAW", "L_IN_RAW"),
        audio_jack("J2", 49.7, 15, 180, "R_OUT", "L_OUT"),
        power_header(),
        diode("D1", 29, 5, 90),
        electrolytic("C8", "10uF 25V", 10, 4.5, 180, "VCC", "GND", diameter=4.0),
        film_cap("C2", 15.8, 7.5, "R_IN_RAW", "R_IN_BIASED"),
        film_cap("C1", 15.8, 23.5, "L_IN_RAW", "L_IN_BIASED"),
        resistor("R3", "100k 1%", 24.5, 9, 270, "R_IN_BIASED", "VREF"),
        resistor("R4", "100k 1%", 24.5, 21, 270, "VREF", "L_IN_BIASED"),
        resistor("R1", "47k 1%", 22.8, 13.8, 270, "VCC", "VREF"),
        resistor("R2", "47k 1%", 22.8, 17.2, 270, "VREF", "GND"),
        electrolytic("C5", "47uF 25V", 18, 15.5, 270, "VREF", "GND", diameter=6.3),
        ceramic_cap("C6", "100nF X7R", 17.5, 10.5, 0, "VREF", "GND"),
        opamp(27.5, 15, 270),
        ceramic_cap("C7", "100nF X7R", 31.5, 17.5, 0, "VCC", "GND"),
        resistor("R6", "47R 1%", 32, 19.5, 0, "L_BUF", "L_ISO"),
        electrolytic("C3", "10uF 25V", 35.3, 21.3, 270, "L_ISO", "L_OUT", diameter=4.0),
        resistor("R8", "100k 1%", 44, 7.5, 270, "L_OUT", "GND"),
        resistor("R5", "47R 1%", 32, 9.5, 0, "R_BUF", "R_ISO"),
        electrolytic("C4", "10uF 25V", 35.3, 11.3, 270, "R_ISO", "R_OUT", diameter=4.0),
        resistor("R7", "100k 1%", 40, 23, 270, "R_OUT", "GND"),
        testpoint("TP1", 32, 5, "VCC"),
        testpoint("TP2", 20, 14.5, "VREF"),
        testpoint("TP3", 38, 5, "GND"),
        mounting_hole("H1", 3.5, 3.5),
        mounting_hole("H2", 48.5, 26.5),
    ]
    lines = [
        "(kicad_pcb",
        "  (version 20240108)",
        '  (generator "coppertone_generator")',
        "  (general (thickness 1.6))",
        '  (paper "A4")',
        '  (title_block (title "CopperTone Stereo Line Buffer") '
        '(date "2026-08-03") (rev "0.1.0-preview") (company "CopperMCP") '
        '(comment 1 "CERN-OHL-S-2.0") '
        '(comment 2 "UNVERIFIED LOW-VOLTAGE PROTOTYPE"))',
        "  (layers",
        '    (0 "F.Cu" signal)',
        '    (31 "B.Cu" signal)',
        '    (36 "B.SilkS" user "b.silkscreen")',
        '    (37 "F.SilkS" user "f.silkscreen")',
        '    (38 "B.Mask" user "b.soldermask")',
        '    (39 "F.Mask" user "f.soldermask")',
        '    (44 "Edge.Cuts" user)',
        "  )",
        "  (setup (pad_to_mask_clearance 0))",
        '  (net 0 "")',
    ]
    lines.extend(f'  (net {number} "{name}")' for name, number in NETS.items())
    lines.extend(footprints)
    lines.extend(
        [
            "  (gr_rect (start 0 0) (end 52 30) "
            '(stroke (width 0.1) (type default)) (fill none) (layer "Edge.Cuts"))',
            '  (gr_text "CopperTone v0.1 — BOARD-FIRST PREVIEW" (at 26 28) '
            '(layer "F.SilkS") (effects (font (size 1.2 1.2) (thickness 0.2))))',
            '  (gr_text "9V ONLY" (at 36 2) (layer "F.SilkS") '
            "(effects (font (size 1 1) (thickness 0.18))))",
            '  (gr_text "IN" (at 4 23) (layer "F.SilkS") '
            "(effects (font (size 1 1) (thickness 0.18))))",
            '  (gr_text "OUT" (at 48 23) (layer "F.SilkS") '
            "(effects (font (size 1 1) (thickness 0.18))))",
        ]
    )
    for route in ROUTES:
        lines.extend(route.segments())
    lines.append(via(36.5, 23.5, "L_OUT"))
    lines.append(via(30, 7, "VCC"))
    lines.append(via(23.5, 11.5, "VCC"))
    for x, y in ((12, 2), (40, 2), (12, 28), (40, 28), (27, 26), (42, 16)):
        lines.append(via(x, y, "GND"))
    lines.extend(
        [
            keepout(3.5, 3.5),
            keepout(48.5, 26.5),
            copper_zone("F.Cu"),
            copper_zone("B.Cu"),
            ")",
        ]
    )
    return "\n".join(lines) + "\n"


def project_data() -> dict[str, object]:
    return {
        "board": {
            "design_settings": {
                "defaults": {
                    "board_outline_line_width": 0.1,
                    "copper_line_width": 0.25,
                    "courtyard_line_width": 0.05,
                    "fab_line_width": 0.1,
                    "silk_line_width": 0.15,
                    "zones": {"45_degree_only": False, "min_clearance": 0.25},
                },
                "drc_exclusions": [],
                "meta": {"filename": "board_design_settings.json", "version": 2},
                "rules": {
                    "allow_blind_buried_vias": False,
                    "allow_microvias": False,
                    "max_error": 0.005,
                    "min_clearance": 0.25,
                    "min_connection": 0.0,
                    "min_copper_edge_clearance": 0.5,
                    "min_groove_width": 0.0,
                    "min_hole_clearance": 0.25,
                    "min_hole_to_hole": 0.25,
                    "min_microvia_diameter": 0.2,
                    "min_microvia_drill": 0.1,
                    "min_resolved_spokes": 2,
                    "min_silk_clearance": 0.0,
                    "min_text_height": 0.8,
                    "min_text_thickness": 0.08,
                    "min_through_hole_diameter": 0.3,
                    "min_track_width": 0.25,
                    "min_via_annular_width": 0.15,
                    "min_via_diameter": 0.6,
                    "solder_mask_to_copper_clearance": 0.0,
                    "use_height_for_length_calcs": True,
                },
                "track_widths": [0.0, 0.25, 0.5],
                "via_dimensions": [
                    {"diameter": 0.0, "drill": 0.0},
                    {"diameter": 0.8, "drill": 0.4},
                ],
            }
        },
        "boards": [],
        "cvpcb": {"equivalence_files": []},
        "erc": {},
        "libraries": {"pinned_footprint_libs": [], "pinned_symbol_libs": []},
        "meta": {"filename": PROJECT_PATH.name, "version": 3},
        "net_settings": {
            "classes": [
                {
                    "bus_width": 12,
                    "clearance": 0.25,
                    "diff_pair_gap": 0.25,
                    "diff_pair_via_gap": 0.25,
                    "diff_pair_width": 0.25,
                    "line_style": 0,
                    "microvia_diameter": 0.3,
                    "microvia_drill": 0.1,
                    "name": "Default",
                    "pcb_color": "rgba(0, 0, 0, 0.000)",
                    "priority": 2147483647,
                    "schematic_color": "rgba(0, 0, 0, 0.000)",
                    "track_width": 0.25,
                    "via_diameter": 0.8,
                    "via_drill": 0.4,
                    "wire_width": 6,
                },
                {
                    "bus_width": 12,
                    "clearance": 0.25,
                    "diff_pair_gap": 0.25,
                    "diff_pair_via_gap": 0.25,
                    "diff_pair_width": 0.5,
                    "line_style": 0,
                    "microvia_diameter": 0.3,
                    "microvia_drill": 0.1,
                    "name": "POWER",
                    "pcb_color": "rgba(0, 0, 0, 0.000)",
                    "priority": 0,
                    "schematic_color": "rgba(0, 0, 0, 0.000)",
                    "track_width": 0.5,
                    "via_diameter": 0.8,
                    "via_drill": 0.4,
                    "wire_width": 6,
                },
            ],
            "meta": {"version": 4},
            "net_colors": None,
            "netclass_assignments": {"9V_RAW": "POWER", "VCC": "POWER"},
            "netclass_patterns": [],
        },
        "pcbnew": {"last_paths": {}, "page_layout_descr_file": ""},
        "schematic": {},
        "text_variables": {},
    }


def metrics_data() -> dict[str, object]:
    per_net: dict[str, float] = {}
    for route in ROUTES:
        per_net[route.net] = per_net.get(route.net, 0.0) + route.length()
    return {
        "schema_version": 1,
        "source": "generate_board.py",
        "board_mm": {"width": 52.0, "height": 30.0, "copper_layers": 2},
        "named_nets": len(NETS),
        "generated_non_ground_track_length_mm": round(sum(per_net.values()), 3),
        "per_net_track_length_mm": {
            net: round(length, 3) for net, length in sorted(per_net.items())
        },
        "non_ground_vias": 3,
        "ground_stitching_vias": 6,
        "high_impedance_input_vias": 0,
        "notes": [
            "Lengths are generator-centerline geometry, not an audio performance claim.",
            "KiCad DRC and board statistics are recorded separately by validate.sh.",
            "The layout is manually authored; CopperMCP did not route this board.",
        ],
    }


def main() -> None:
    BOARD_PATH.write_text(board_text(), encoding="utf-8")
    PROJECT_PATH.write_text(
        json.dumps(project_data(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    METRICS_PATH.write_text(
        json.dumps(metrics_data(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
