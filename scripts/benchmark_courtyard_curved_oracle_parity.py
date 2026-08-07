#!/usr/bin/env python3
"""Measure chamfered- and circular-courtyard legality against a real KiCad 10.0.5 DRC oracle.

``benchmark_courtyard_oracle_parity.py`` (B-089) pinned the orthogonal subset. This benchmark
extends the same methodology to the two shapes that unblocked the #116 real-board survey:
exact 45-degree chamfer rings and exact-radius circles. The model answers with outer/inner
bracket bounds (or the exact circle-pair distance predicate), so the honest expectations are:

* **exact parity** wherever a bracket entitles the model to a claim - deep penetrations and
  real clearances;
* **conceded** ``inconclusive`` in two declared bands: the sub-threshold cache band that the
  orthogonal benchmark already concedes, and overlap confined to a bracket's disagreement
  region (a chamfer's corner triangle, a circle's bounding-box corner, the exact-threshold
  circle contact);
* **zero contradictions** in either direction. The script refuses to emit an artifact
  otherwise, exactly like its predecessor.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from copper_mcp.adapters import KiCadConstraintProfile, parse_kicad_bytes
from copper_mcp.board_ir import NetClass, ParseLimits
from copper_mcp.placement import build_placement_view, evaluate_placement, parse_placement_intent
from copper_mcp.placement.geometry import (
    COURTYARD_CACHE_INSET_NM,
    COURTYARD_COLLISION_THRESHOLD_NM,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_KICAD_CLI = Path("/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli")

#: Nominal penetration in nanometres between the two courtyard envelopes under test.
PENETRATIONS = (-1_000_000, -1, 0, 1, 5_000, 9_999, 10_000, 10_001, 20_000, 1_000_000)


@dataclass(frozen=True, slots=True)
class CaseOutcome:
    name: str
    kicad_overlap: bool
    model_verdict: str
    agreement: str


def _git_commit() -> str:
    git = shutil.which("git")
    if git is None:
        return "unknown"
    try:
        return subprocess.run(  # noqa: S603 - fixed local Git argv
            [git, "rev-parse", "HEAD"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def _profile() -> KiCadConstraintProfile:
    net_class = NetClass(
        id="class:benchmark",
        name="Benchmark",
        clearance_nm=200_000,
        track_width_nm=250_000,
        via_diameter_nm=600_000,
        via_drill_nm=300_000,
    )
    return KiCadConstraintProfile(net_classes=(net_class,), default_net_class_id=net_class.id)


def _nm(value: int) -> str:
    """Render integer nanometres as an exact millimetre literal; no float ever appears."""

    sign = "-" if value < 0 else ""
    value = abs(value)
    return f"{sign}{value // 1_000_000}.{value % 1_000_000:06d}"


def _board(first_courtyard: str, second_courtyard: str, second_at_nm: tuple[int, int]) -> bytes:
    return f"""(kicad_pcb
  (version 20260206)
  (generator "copper-mcp")
  (generator_version "0.2.0")
  (layers
    (0 "F.Cu" signal)
    (2 "B.Cu" signal)
    (25 "Edge.Cuts" user)
  )
  (footprint "CopperMCP_CurvedA"
    (layer "F.Cu")
    (uuid "98000000-0000-0000-0000-000000000001")
    (at 15 15)
    {first_courtyard}
    (pad "1" smd rect
      (at 0 0)
      (size 0.5 0.5)
      (layers "F.Cu" "F.Mask" "F.Paste")
      (uuid "98000000-0000-0000-0000-000000000002")
    )
  )
  (footprint "CopperMCP_CurvedB"
    (layer "F.Cu")
    (uuid "98000000-0000-0000-0000-000000000011")
    (at {_nm(second_at_nm[0])} {_nm(second_at_nm[1])})
    {second_courtyard}
    (pad "1" smd rect
      (at 0 0)
      (size 0.5 0.5)
      (layers "F.Cu" "F.Mask" "F.Paste")
      (uuid "98000000-0000-0000-0000-000000000012")
    )
  )
  (gr_rect
    (start 0 0)
    (end 60 40)
    (stroke (width 0.1) (type default))
    (fill no)
    (layer "Edge.Cuts")
    (uuid "98000000-0000-0000-0000-000000000099")
  )
)
""".encode()


_CHAMFER = """(fp_poly
      (pts (xy -3 -1.5) (xy -1.5 -3) (xy 3 -3) (xy 3 3) (xy -3 3))
      (stroke (width 0.05) (type default))
      (fill none)
      (layer "F.CrtYd")
      (uuid "97000000-0000-0000-0000-0000000000{serial}")
    )"""

_CIRCLE = """(fp_circle
      (center 0 0)
      (end 1.8 0)
      (stroke (width 0.05) (type default))
      (fill no)
      (layer "F.CrtYd")
      (uuid "97000000-0000-0000-0000-0000000000{serial}")
    )"""


def _model_verdict(source: bytes, board_name: str) -> str:
    conversion = parse_kicad_bytes(source, _profile(), ParseLimits())
    if conversion.snapshot is None:
        raise RuntimeError(f"{board_name} did not convert to Board IR")
    view = build_placement_view(source, conversion.snapshot)
    intent = parse_placement_intent(
        {
            "board": board_name,
            "constraints": {
                "clearance_nm": 200_000,
                "track_width_nm": 250_000,
                "via_diameter_nm": 600_000,
                "via_drill_nm": 300_000,
            },
            "subjects": sorted(view.footprints),
        }
    )
    result = evaluate_placement(intent, conversion.snapshot, view)
    legality = (
        result.candidate.evidence.legality
        if result.candidate is not None
        else result.diagnostic.legality
        if result.diagnostic is not None
        else None
    )
    if legality is None:
        raise RuntimeError(f"{board_name} produced no legality record")
    return legality.courtyard_overlap


def _kicad_courtyard_overlap(kicad_cli: Path, source: bytes, board_name: str) -> bool:
    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory)
        board = work / board_name
        board.write_bytes(source)
        report = work / "drc.json"
        environment = dict(os.environ)
        for variable, name in (
            ("KICAD_CONFIG_HOME", "config"),
            ("XDG_CONFIG_HOME", "xdg-config"),
            ("XDG_CACHE_HOME", "xdg-cache"),
            ("XDG_DATA_HOME", "xdg-data"),
            ("HOME", "home"),
            ("TMPDIR", "tmp"),
        ):
            isolated = work / name
            isolated.mkdir(parents=True, exist_ok=True)
            environment[variable] = str(isolated)
        completed = subprocess.run(  # noqa: S603 - fixed local argv, operator-supplied CLI path
            [
                str(kicad_cli),
                "pcb",
                "drc",
                "--format",
                "json",
                "--units",
                "mm",
                "--severity-all",
                "-o",
                str(report),
                str(board),
            ],
            capture_output=True,
            check=False,
            timeout=180,
            env=environment,
        )
        if completed.returncode not in (0, 5) or not report.is_file():
            raise RuntimeError(f"KiCad DRC failed for {board_name}: {completed.stderr!r}")
        payload = json.loads(report.read_text(encoding="utf-8"))
    return any(
        violation.get("type") == "courtyards_overlap" for violation in payload.get("violations", [])
    )


def _classify(kicad_overlap: bool, model_verdict: str) -> str:
    if model_verdict == "inconclusive":
        return "conceded"
    if kicad_overlap and model_verdict == "violated":
        return "exact_parity"
    if not kicad_overlap and model_verdict == "proven_clear":
        return "exact_parity"
    return "contradiction"


def _cases() -> tuple[tuple[str, bytes], ...]:
    first_chamfer = _CHAMFER.format(serial="03")
    second_chamfer = _CHAMFER.format(serial="13")
    first_circle = _CIRCLE.format(serial="03")
    second_circle = _CIRCLE.format(serial="13")
    cases: list[tuple[str, bytes]] = []

    # Chamfered rings meeting edge-on along their orthogonal sides: 6 mm wide bodies whose
    # x-extents touch when the centres are 6 mm apart.
    for penetration in PENETRATIONS:
        cases.append(
            (
                f"chamfer_penetration_{penetration}_nm",
                _board(
                    first_chamfer,
                    second_chamfer,
                    (15_000_000 + 6_000_000 - penetration, 15_000_000),
                ),
            )
        )
    # Overlap confined to A's chamfered-away corner square: KiCad sees clear shapes, the
    # model's outer bounds overlap, and its inner bounds do not - a declared concession.
    cases.append(
        (
            "chamfer_corner_triangle_only",
            _board(first_chamfer, second_chamfer, (9_600_000, 9_600_000)),
        )
    )
    # Two circles approaching along the x axis: radii sum to 3.6 mm.
    for penetration in PENETRATIONS:
        cases.append(
            (
                f"circle_penetration_{penetration}_nm",
                _board(
                    first_circle,
                    second_circle,
                    (15_000_000 + 3_600_000 - penetration, 15_000_000),
                ),
            )
        )
    # A circle deep inside a chamfered ring, and a circle clipping only the ring's chamfer
    # corner square.
    cases.append(
        (
            "circle_deep_in_chamfer_ring",
            _board(first_circle, second_chamfer, (15_500_000, 15_500_000)),
        )
    )
    cases.append(
        (
            "circle_against_chamfer_corner_square",
            _board(first_circle, second_chamfer, (19_600_000, 19_600_000)),
        )
    )
    return tuple(cases)


def _run(kicad_cli: Path) -> dict[str, Any]:
    outcomes: list[CaseOutcome] = []
    for name, source in _cases():
        board_name = f"curved-{name}.kicad_pcb"
        kicad_overlap = _kicad_courtyard_overlap(kicad_cli, source, board_name)
        verdict = _model_verdict(source, board_name)
        outcomes.append(
            CaseOutcome(name, kicad_overlap, verdict, _classify(kicad_overlap, verdict))
        )

    total = len(outcomes)
    exact = sum(1 for item in outcomes if item.agreement == "exact_parity")
    conceded = sum(1 for item in outcomes if item.agreement == "conceded")
    contradictions = [item.name for item in outcomes if item.agreement == "contradiction"]
    false_positives = [
        item.name
        for item in outcomes
        if not item.kicad_overlap and item.model_verdict == "violated"
    ]
    false_negatives = [
        item.name
        for item in outcomes
        if item.kicad_overlap and item.model_verdict == "proven_clear"
    ]
    return {
        "cases": [
            {
                "name": item.name,
                "kicad_courtyards_overlap": item.kicad_overlap,
                "model_courtyard_overlap": item.model_verdict,
                "agreement": item.agreement,
            }
            for item in outcomes
        ],
        "case_count": total,
        "exact_parity": exact,
        "conceded_inconclusive": conceded,
        "contradictions": contradictions,
        "false_positive_violations": false_positives,
        "false_negative_clears": false_negatives,
        "exact_parity_rate": f"{exact}/{total}",
        "non_contradiction_rate": f"{exact + conceded}/{total}",
        "courtyard_cache_inset_nm": COURTYARD_CACHE_INSET_NM,
        "courtyard_collision_threshold_nm": COURTYARD_COLLISION_THRESHOLD_NM,
        "kicad_invoked": True,
        "workspace_mutations": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT
        / "benchmarks/results/placement/2026-08-06-courtyard-curved-oracle-parity.json",
    )
    parser.add_argument("--kicad-cli", type=Path, default=DEFAULT_KICAD_CLI)
    args = parser.parse_args()
    if not args.kicad_cli.is_file():
        raise RuntimeError(f"KiCad CLI is not installed at {args.kicad_cli}")

    version = subprocess.run(  # noqa: S603 - fixed local argv, operator-supplied CLI path
        [str(args.kicad_cli), "version"],
        capture_output=True,
        check=False,
        text=True,
        timeout=60,
    ).stdout.strip()

    metrics = _run(args.kicad_cli)
    if metrics["contradictions"]:
        raise RuntimeError(f"curved courtyard oracle contradictions: {metrics['contradictions']}")

    payload: dict[str, Any] = {
        "schema": "copper-mcp/benchmark/courtyard-curved-oracle-parity/v1",
        "date_utc": "2026-08-06",
        "source_commit": _git_commit(),
        "environment": {
            "platform": platform.platform(),
            "python": sys.version.split()[0],
            "kicad_cli": version,
        },
        "metrics": metrics,
        "not_claimed": [
            "arc courtyard primitives, which remain a typed refusal",
            "rings mixing chamfers with nesting, which degrade to a concession",
            "footprint poses that are not quarter turns",
            "configurable nonzero courtyard clearance",
            "the sub-threshold cache band, conceded exactly as in B-089",
            "placement apply or full-board DRC",
        ],
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    payload["run_id"] = "sha256:" + hashlib.sha256(canonical).hexdigest()
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
