#!/usr/bin/env python3
"""Measure courtyard-legality agreement against a real KiCad 10.0.5 DRC oracle.

Unlike ``benchmark_courtyard_legality.py``, which records ``kicad_invoked: false``, this benchmark
actually shells out to ``kicad-cli`` and compares KiCad's own ``courtyards_overlap`` verdict with
the deterministic legalizer's, case by case (ADR-0075, issues #72 and #74).

Three outcomes are counted separately and must not be conflated:

* **exact parity** - the legalizer said ``violated`` where KiCad reported the overlap, or
  ``proven_clear`` where it did not;
* **conceded** - the legalizer said ``inconclusive``. This is a declared non-claim, not an error,
  and it is reported rather than hidden inside an agreement percentage;
* **contradiction** - a ``violated`` KiCad calls clear, or a ``proven_clear`` KiCad calls
  overlapping. Either is a bug, and the script refuses to emit a passing artifact if one occurs.
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
FIXTURES = ROOT / "tests/fixtures/board-ir-v0.2"
DONUT = FIXTURES / "courtyard-donut.kicad_pcb"
INSET_BELOW = FIXTURES / "courtyard-inset-below.kicad_pcb"
INSET_AT = FIXTURES / "courtyard-inset-at.kicad_pcb"
COPPERTONE = ROOT / "hardware/coppertone-buffer/coppertone-buffer.kicad_pcb"
DEFAULT_KICAD_CLI = Path("/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli")

#: Nominal penetration in nanometres between two 10 mm front courtyard squares.
PENETRATIONS = (
    -1_000_000,
    -1,
    0,
    1,
    5_000,
    9_998,
    9_999,
    10_000,
    10_001,
    20_000,
    1_000_000,
)


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


def _penetration_board(penetration_nm: int) -> bytes:
    left_x1 = 20_000_000
    right_x0 = left_x1 - penetration_nm
    right_x1 = right_x0 + 10_000_000
    return f"""(kicad_pcb
  (version 20260206)
  (generator "copper-mcp")
  (generator_version "0.2.0")
  (layers
    (0 "F.Cu" signal)
    (2 "B.Cu" signal)
    (25 "Edge.Cuts" user)
  )
  (footprint "CopperMCP_OracleA"
    (layer "F.Cu")
    (uuid "97000000-0000-0000-0000-000000000001")
    (at 0 0 0)
    (fp_rect
      (start 10 10)
      (end 20 20)
      (stroke (width 0.05) (type default))
      (fill none)
      (layer "F.CrtYd")
      (uuid "97000000-0000-0000-0000-000000000002")
    )
    (pad "1" smd rect
      (at 10.5 10.5 0)
      (size 0.5 0.5)
      (layers "F.Cu" "F.Mask" "F.Paste")
      (uuid "97000000-0000-0000-0000-000000000003")
    )
  )
  (footprint "CopperMCP_OracleB"
    (layer "F.Cu")
    (uuid "97000000-0000-0000-0000-000000000011")
    (at 0 0 0)
    (fp_rect
      (start {_nm(right_x0)} 10)
      (end {_nm(right_x1)} 20)
      (stroke (width 0.05) (type default))
      (fill none)
      (layer "F.CrtYd")
      (uuid "97000000-0000-0000-0000-000000000012")
    )
    (pad "1" smd rect
      (at {_nm(right_x1 - 600_000)} 19.5 0)
      (size 0.5 0.5)
      (layers "F.Cu" "F.Mask" "F.Paste")
      (uuid "97000000-0000-0000-0000-000000000013")
    )
  )
  (gr_rect
    (start 0 0)
    (end 40 30)
    (stroke (width 0.1) (type default))
    (fill no)
    (layer "Edge.Cuts")
    (uuid "97000000-0000-0000-0000-000000000099")
  )
)
""".encode()


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


def _run(kicad_cli: Path) -> dict[str, Any]:
    outcomes: list[CaseOutcome] = []

    for penetration in PENETRATIONS:
        source = _penetration_board(penetration)
        name = f"penetration_{penetration}_nm"
        board_name = f"oracle-{penetration}.kicad_pcb"
        kicad_overlap = _kicad_courtyard_overlap(kicad_cli, source, board_name)
        verdict = _model_verdict(source, board_name)
        outcomes.append(
            CaseOutcome(name, kicad_overlap, verdict, _classify(kicad_overlap, verdict))
        )

    committed = [
        ("donut_ring_nesting", DONUT),
        ("inset_below", INSET_BELOW),
        ("inset_at", INSET_AT),
    ]
    if COPPERTONE.is_file():
        committed.append(("coppertone_buffer", COPPERTONE))
    for name, path in committed:
        source = path.read_bytes()
        kicad_overlap = _kicad_courtyard_overlap(kicad_cli, source, path.name)
        verdict = _model_verdict(source, path.name)
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
        default=ROOT / "benchmarks/results/placement/2026-08-06-courtyard-oracle-parity.json",
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
        raise RuntimeError(f"courtyard oracle contradictions: {metrics['contradictions']}")

    payload: dict[str, Any] = {
        "schema": "copper-mcp/benchmark/courtyard-oracle-parity/v1",
        "date_utc": "2026-08-06",
        "source_commit": _git_commit(),
        "environment": {
            "platform": platform.platform(),
            "python": sys.version.split()[0],
            "kicad_cli": version,
        },
        "fixtures": [
            str(DONUT.relative_to(ROOT)),
            str(INSET_BELOW.relative_to(ROOT)),
            str(INSET_AT.relative_to(ROOT)),
        ],
        "metrics": metrics,
        "not_claimed": [
            "configurable nonzero courtyard clearance",
            "arc, curved, or non-orthogonal courtyard geometry",
            "same-footprint rings that touch or properly intersect",
            "the tiny-shape band where a courtyard is thinner than the inset threshold",
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
