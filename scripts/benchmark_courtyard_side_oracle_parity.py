#!/usr/bin/env python3
"""Measure whether a courtyard's *layer* or its footprint's *side* decides a collision.

This extends B-089's methodology (``benchmark_courtyard_oracle_parity.py``, ADR-0075) and
B-090's (``benchmark_courtyard_curved_oracle_parity.py``, ADR-0080) to the one question those
two never asked: what happens when a footprint draws a courtyard on the layer opposite the
copper side it is mounted on. Board IR used to refuse every such board as
``unsupported.transform`` / "courtyard layer does not match its footprint side" (issue #151).

The cases are built so that footprint side and courtyard layer vary **independently**. Two
boards carrying the identical rectangle on the identical courtyard layer, differing only in
which copper side the second footprint sits on, must receive the same verdict; two boards whose
rectangles sit at identical coordinates on *different* courtyard layers must not collide. A
model that keyed on footprint side answers the first pair differently and the second pair
wrongly, so the cases discriminate the two hypotheses rather than merely exercising the code.

Three outcomes are counted separately and must not be conflated:

* **exact parity** - the legalizer said ``violated`` where KiCad reported the overlap, or
  ``proven_clear`` where it did not;
* **conceded** - the legalizer said ``inconclusive``. A declared non-claim, reported rather than
  hidden inside an agreement percentage;
* **contradiction** - a ``violated`` KiCad calls clear, or a ``proven_clear`` KiCad calls
  overlapping. Either is a bug, and the script refuses to emit an artifact if one occurs.
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

#: Nominal penetration in nanometres between two 10 mm courtyard squares. The same eleven
#: offsets B-089 used on ``F.CrtYd``, replayed on ``B.CrtYd`` drawn by two *front-side*
#: footprints, so the 10,000 nm threshold is re-measured on the far side rather than assumed.
PENETRATIONS = (-1_000_000, -1, 0, 9_999, 10_000, 10_001, 1_000_000)


@dataclass(frozen=True, slots=True)
class CaseOutcome:
    name: str
    kicad_overlap: bool
    model_verdict: str
    agreement: str


def _git_commit() -> tuple[str, bool]:
    """Return the source commit and whether the tree it was read from was dirty.

    Bare ``HEAD`` is not provenance.  The first artifact this runner recorded named the
    `v0.7.0` release commit, whose adapter *refuses* every far-side board the runner
    generates -- so the run could not have come from that commit's tree, and the field said
    it did.  A benchmark is a claim about the code that produced it, and a working-tree edit
    is exactly the case where the commit alone is a false claim about the code.

    ``dirty`` is reported separately rather than being folded into the commit string, so a
    consumer reads a boolean instead of parsing a suffix, matching
    ``benchmark_real_board_capability.py``.  Both failure paths report **dirty**: not knowing
    whether the tree was clean is not evidence that it was.
    """

    git = shutil.which("git")
    if git is None:
        return "unknown", True
    try:
        commit = subprocess.run(  # noqa: S603 - fixed local Git argv
            [git, "rev-parse", "HEAD"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
        status = subprocess.run(  # noqa: S603 - fixed local Git argv
            [git, "status", "--porcelain"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown", True
    return commit, bool(status)


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


def _footprint(
    serial: int,
    *,
    copper_side: str,
    courtyard_layer: str,
    x0: int,
    x1: int,
    pad_x: int,
    pad_y: int,
) -> str:
    face = "F" if copper_side == "F.Cu" else "B"
    return f"""  (footprint "CopperMCP_Side{serial}"
    (layer "{copper_side}")
    (uuid "9d000000-0000-0000-0000-0000000000{serial:02d}")
    (at 0 0 0)
    (fp_rect
      (start {_nm(x0)} 10)
      (end {_nm(x1)} 20)
      (stroke (width 0.05) (type default))
      (fill none)
      (layer "{courtyard_layer}")
      (uuid "9d000000-0000-0000-0000-0000000001{serial:02d}")
    )
    (pad "1" smd rect
      (at {_nm(pad_x)} {_nm(pad_y)} 0)
      (size 0.5 0.5)
      (layers "{face}.Cu" "{face}.Mask" "{face}.Paste")
      (uuid "9d000000-0000-0000-0000-0000000002{serial:02d}")
    )
  )
"""


def _board(
    *,
    first_side: str,
    first_layer: str,
    second_side: str,
    second_layer: str,
    penetration_nm: int,
) -> bytes:
    """Two 10 mm courtyard squares whose horizontal penetration and layers are both chosen.

    The pads sit outside both squares and 8 mm apart, so no pad rule can fire and mask the
    courtyard verdict this benchmark is measuring.
    """

    left_x1 = 20_000_000
    right_x0 = left_x1 - penetration_nm
    first = _footprint(
        1,
        copper_side=first_side,
        courtyard_layer=first_layer,
        x0=10_000_000,
        x1=left_x1,
        pad_x=2_000_000,
        pad_y=2_000_000,
    )
    second = _footprint(
        2,
        copper_side=second_side,
        courtyard_layer=second_layer,
        x0=right_x0,
        x1=right_x0 + 10_000_000,
        pad_x=10_000_000,
        pad_y=2_000_000,
    )
    return f"""(kicad_pcb
  (version 20260206)
  (generator "copper-mcp")
  (generator_version "0.2.0")
  (layers
    (0 "F.Cu" signal)
    (2 "B.Cu" signal)
    (25 "Edge.Cuts" user)
  )
{first}{second}  (gr_rect
    (start 0 0)
    (end 40 30)
    (stroke (width 0.1) (type default))
    (fill no)
    (layer "Edge.Cuts")
    (uuid "9d000000-0000-0000-0000-000000000099")
  )
)
""".encode()


def _model_verdict(source: bytes, board_name: str) -> str:
    conversion = parse_kicad_bytes(source, _profile(), ParseLimits())
    if conversion.snapshot is None:
        codes = ", ".join(f"{item.code}@{item.source_locator}" for item in conversion.diagnostics)
        raise RuntimeError(f"{board_name} did not convert to Board IR: {codes}")
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


#: The four side/layer arrangements at full overlap. Read as a two-by-two: the courtyard layers
#: agree or disagree, and the copper sides agree or disagree, and only the first of those two
#: may change the verdict.
_ARRANGEMENTS = (
    ("far_far__opposite_sides", "F.Cu", "B.CrtYd", "B.Cu", "B.CrtYd"),
    ("far_near__opposite_sides", "F.Cu", "B.CrtYd", "B.Cu", "F.CrtYd"),
    ("far_far__both_front", "F.Cu", "B.CrtYd", "F.Cu", "B.CrtYd"),
    ("far_near__both_front", "F.Cu", "B.CrtYd", "F.Cu", "F.CrtYd"),
    ("near_near__both_front", "F.Cu", "F.CrtYd", "F.Cu", "F.CrtYd"),
    ("near_near__opposite_sides", "F.Cu", "F.CrtYd", "B.Cu", "F.CrtYd"),
)


def _run(kicad_cli: Path) -> dict[str, Any]:
    outcomes: list[CaseOutcome] = []

    for name, first_side, first_layer, second_side, second_layer in _ARRANGEMENTS:
        source = _board(
            first_side=first_side,
            first_layer=first_layer,
            second_side=second_side,
            second_layer=second_layer,
            penetration_nm=10_000_000,
        )
        board_name = f"side-{name}.kicad_pcb"
        kicad_overlap = _kicad_courtyard_overlap(kicad_cli, source, board_name)
        verdict = _model_verdict(source, board_name)
        outcomes.append(
            CaseOutcome(name, kicad_overlap, verdict, _classify(kicad_overlap, verdict))
        )

    for penetration in PENETRATIONS:
        source = _board(
            first_side="F.Cu",
            first_layer="B.CrtYd",
            second_side="F.Cu",
            second_layer="B.CrtYd",
            penetration_nm=penetration,
        )
        name = f"far_side_penetration_{penetration}_nm"
        board_name = f"side-{penetration}.kicad_pcb"
        kicad_overlap = _kicad_courtyard_overlap(kicad_cli, source, board_name)
        verdict = _model_verdict(source, board_name)
        outcomes.append(
            CaseOutcome(name, kicad_overlap, verdict, _classify(kicad_overlap, verdict))
        )

    total = len(outcomes)
    exact = sum(1 for item in outcomes if item.agreement == "exact_parity")
    conceded = sum(1 for item in outcomes if item.agreement == "conceded")
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
        "contradictions": [item.name for item in outcomes if item.agreement == "contradiction"],
        "false_positive_violations": [
            item.name
            for item in outcomes
            if not item.kicad_overlap and item.model_verdict == "violated"
        ],
        "false_negative_clears": [
            item.name
            for item in outcomes
            if item.kicad_overlap and item.model_verdict == "proven_clear"
        ],
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
        default=ROOT / "benchmarks/results/placement/2026-08-13-courtyard-side-oracle-parity.json",
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
        raise RuntimeError(f"courtyard side oracle contradictions: {metrics['contradictions']}")

    source_commit, source_dirty = _git_commit()
    payload: dict[str, Any] = {
        "schema": "copper-mcp/benchmark/courtyard-side-oracle-parity/v1",
        # The date this artifact was recorded, which the filename mirrors.
        "date_utc": "2026-08-13",
        "source_commit": source_commit,
        # True means the run cannot be reproduced from `source_commit` alone.
        "source_dirty": source_dirty,
        "environment": {
            "platform": platform.platform(),
            "python": sys.version.split()[0],
            "kicad_cli": version,
        },
        "fixtures": ["generated in-process; no board is written to the repository"],
        "metrics": metrics,
        "not_claimed": [
            "that any real board in any corpus is DRC-clean",
            "configurable nonzero courtyard clearance",
            "arc, curved, or non-orthogonal courtyard geometry on either courtyard layer",
            "the pth_inside_courtyard rule, which KiCad also reports and this model does not "
            "evaluate",
            "placement apply or write-back of a footprint carrying a far-side courtyard, which "
            "the source-preserving serializer still refuses",
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
