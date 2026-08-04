#!/usr/bin/env python3
"""Compare Placement 0.2 courtyard verdicts with a local KiCad 10.0.5 DRC oracle."""

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
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from copper_mcp.adapters import KiCadConstraintProfile, parse_kicad_bytes
from copper_mcp.board_ir import NetClass, ParseLimits
from copper_mcp.placement import (
    COURTYARD_POLICY,
    PLACEMENT_VERSION,
    build_placement_view,
    evaluate_placement,
    parse_placement_intent,
)

DEFAULT_KICAD_CLI = Path("/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli")
SCRIPT_PATH = Path(__file__).resolve()
REPOSITORY_ROOT = SCRIPT_PATH.parents[1]
BENCHMARK_NAME = "placement-kicad-courtyard-oracle-v1"
CONSTRAINTS = {
    "clearance_nm": 200_000,
    "track_width_nm": 250_000,
    "via_diameter_nm": 600_000,
    "via_drill_nm": 300_000,
}


@dataclass(frozen=True, slots=True)
class OracleCase:
    name: str
    second_origin_x_nm: int
    expected_collision: bool


@dataclass(frozen=True, slots=True)
class TinyCacheCase:
    name: str
    width_nm: int
    height_nm: int
    expected_violation_types: tuple[str, ...]


CASES = (
    OracleCase("disjoint_1mm", 31_000_000, False),
    OracleCase("gap_1nm", 30_000_001, False),
    OracleCase("edge_touch", 30_000_000, False),
    OracleCase("penetration_1nm", 29_999_999, False),
    OracleCase("penetration_9999nm", 29_990_001, False),
    OracleCase("penetration_10000nm", 29_990_000, True),
    OracleCase("penetration_10001nm", 29_989_999, True),
    OracleCase("overlap_1mm", 29_000_000, True),
    OracleCase("coincident", 20_000_000, True),
)

TINY_CACHE_REPETITIONS = 5
TINY_CACHE_CASES = (
    TinyCacheCase("tiny_square_1nm", 1, 1, ("missing_courtyard", "missing_courtyard")),
    TinyCacheCase(
        "tiny_square_10000nm",
        10_000,
        10_000,
        ("missing_courtyard", "missing_courtyard"),
    ),
    TinyCacheCase("tiny_square_10001nm", 10_001, 10_001, ()),
    TinyCacheCase("tiny_square_10050nm", 10_050, 10_050, ()),
    TinyCacheCase("tiny_square_10051nm", 10_051, 10_051, ("courtyards_overlap",)),
    TinyCacheCase("tiny_xshort_10050nm", 10_050, 1_000_000, ()),
    TinyCacheCase("tiny_xshort_10051nm", 10_051, 1_000_000, ("courtyards_overlap",)),
    TinyCacheCase(
        "tiny_yshort_10000nm",
        1_000_000,
        10_000,
        ("missing_courtyard", "missing_courtyard"),
    ),
    TinyCacheCase("tiny_yshort_10001nm", 1_000_000, 10_001, ()),
    TinyCacheCase("tiny_yshort_10031nm", 1_000_000, 10_031, ()),
    TinyCacheCase("tiny_yshort_10037nm", 1_000_000, 10_037, ("courtyards_overlap",)),
    TinyCacheCase("tiny_yshort_10051nm", 1_000_000, 10_051, ("courtyards_overlap",)),
)


def _profile() -> KiCadConstraintProfile:
    net_class = NetClass(id="class:oracle", name="Oracle", **CONSTRAINTS)
    return KiCadConstraintProfile(
        net_classes=(net_class,),
        default_net_class_id=net_class.id,
    )


def _millimetres(value_nm: int) -> str:
    whole, fraction = divmod(value_nm, 1_000_000)
    return f"{whole}.{fraction:06d}".rstrip("0").rstrip(".")


def _board_bytes(case: OracleCase) -> bytes:
    second_x = _millimetres(case.second_origin_x_nm)
    return f"""(kicad_pcb
  (version 20260206)
  (generator "copper-mcp-courtyard-oracle")
  (generator_version "0.2.0")
  (layers
    (0 "F.Cu" signal)
    (2 "B.Cu" signal)
    (25 "Edge.Cuts" user)
  )
  (footprint "CopperMCP_CourtyardOracleA"
    (layer "F.Cu")
    (uuid "94000000-0000-0000-0000-000000000001")
    (at 20 20 0)
    (fp_rect
      (start -5 -5)
      (end 5 5)
      (stroke (width 0.05) (type default))
      (fill none)
      (layer "F.CrtYd")
      (uuid "94000000-0000-0000-0000-000000000002")
    )
    (pad "1" smd rect
      (at -4 0 0)
      (size 1 1)
      (layers "F.Cu" "F.Mask" "F.Paste")
      (net "ORACLE_A")
      (uuid "94000000-0000-0000-0000-000000000003")
    )
  )
  (footprint "CopperMCP_CourtyardOracleB"
    (layer "F.Cu")
    (uuid "94000000-0000-0000-0000-000000000011")
    (at {second_x} 20 0)
    (fp_rect
      (start -5 -5)
      (end 5 5)
      (stroke (width 0.05) (type default))
      (fill none)
      (layer "F.CrtYd")
      (uuid "94000000-0000-0000-0000-000000000012")
    )
    (pad "1" smd rect
      (at 4 0 0)
      (size 1 1)
      (layers "F.Cu" "F.Mask" "F.Paste")
      (net "ORACLE_B")
      (uuid "94000000-0000-0000-0000-000000000013")
    )
  )
  (gr_rect
    (start 0 0)
    (end 60 40)
    (stroke (width 0.1) (type default))
    (fill no)
    (layer "Edge.Cuts")
    (uuid "94000000-0000-0000-0000-000000000099")
  )
)
""".encode()


def _tiny_board_bytes(case: TinyCacheCase) -> bytes:
    width = _millimetres(case.width_nm)
    height = _millimetres(case.height_nm)
    return f"""(kicad_pcb
  (version 20260206)
  (generator "copper-mcp-courtyard-tiny-oracle")
  (generator_version "0.2.0")
  (layers
    (0 "F.Cu" signal)
    (2 "B.Cu" signal)
    (25 "Edge.Cuts" user)
  )
  (footprint "CopperMCP_TinyCourtyardA"
    (layer "F.Cu")
    (uuid "95000000-0000-0000-0000-000000000001")
    (at 20 20 0)
    (fp_rect
      (start 0 0)
      (end {width} {height})
      (stroke (width 0.001) (type default))
      (fill none)
      (layer "F.CrtYd")
      (uuid "95000000-0000-0000-0000-000000000002")
    )
  )
  (footprint "CopperMCP_TinyCourtyardB"
    (layer "F.Cu")
    (uuid "95000000-0000-0000-0000-000000000011")
    (at 20 20 0)
    (fp_rect
      (start 0 0)
      (end {width} {height})
      (stroke (width 0.001) (type default))
      (fill none)
      (layer "F.CrtYd")
      (uuid "95000000-0000-0000-0000-000000000012")
    )
  )
  (gr_rect
    (start 0 0)
    (end 60 40)
    (stroke (width 0.1) (type default))
    (fill no)
    (layer "Edge.Cuts")
    (uuid "95000000-0000-0000-0000-000000000099")
  )
)
""".encode()


def _tiny_project_bytes() -> bytes:
    project = {
        "board": {
            "design_settings": {
                "meta": {"filename": "board_design_settings.json", "version": 2},
                "rule_severities": {
                    "courtyards_overlap": "error",
                    "malformed_courtyard": "error",
                    "missing_courtyard": "error",
                },
            }
        },
        "meta": {"filename": "copper_mcp_tiny_oracle.kicad_pro", "version": 1},
    }
    return json.dumps(project, sort_keys=True, separators=(",", ":")).encode()


def _copper_verdict(source: bytes, case: OracleCase) -> tuple[bool, int]:
    conversion = parse_kicad_bytes(source, _profile(), ParseLimits())
    if conversion.snapshot is None:
        codes = ", ".join(item.code for item in conversion.diagnostics)
        raise RuntimeError(f"{case.name}: Board IR conversion failed: {codes}")
    view = build_placement_view(source, conversion.snapshot)
    intent = parse_placement_intent(
        {
            "board": f"{case.name}.kicad_pcb",
            "constraints": dict(CONSTRAINTS),
            "subjects": sorted(view.footprints),
        }
    )
    result = evaluate_placement(intent, conversion.snapshot, view)
    if result.candidate is not None:
        legality = result.candidate.evidence.legality
        checks_used = result.candidate.evidence.checks_used
    else:
        if result.diagnostic is None or result.diagnostic.legality is None:
            raise RuntimeError(f"{case.name}: placement returned no legality verdict")
        legality = result.diagnostic.legality
        checks_used = result.diagnostic.checks_used
    if legality.pad_overlap != "proven_clear":
        raise RuntimeError(f"{case.name}: oracle pads did not remain provably clear")
    return legality.courtyard_overlap == "violated", checks_used


def _isolated_kicad_env(root: Path) -> dict[str, str]:
    home = root / "home"
    config = root / "config"
    documents = root / "documents"
    runtime = root / "runtime"
    temp = root / "tmp"
    for directory in (home, config, documents, runtime, temp):
        directory.mkdir(mode=0o700, parents=True)
    return {
        "HOME": str(home),
        "KICAD_CONFIG_HOME": str(config),
        "KICAD_DOCUMENTS_HOME": str(documents),
        "LANG": "C",
        "LC_ALL": "C",
        "PATH": os.defpath,
        "TMPDIR": str(temp),
        "XDG_CONFIG_HOME": str(config / "xdg"),
        "XDG_RUNTIME_DIR": str(runtime),
    }


def _kicad_verdict(
    executable: Path,
    source: bytes,
    case: OracleCase,
    work: Path,
    environment: dict[str, str],
) -> tuple[bool, int, int]:
    board = work / f"{case.name}.kicad_pcb"
    report = work / f"{case.name}.json"
    board.write_bytes(source)
    started = time.perf_counter_ns()
    completed = subprocess.run(  # noqa: S603 - explicit operator-selected KiCad binary, fixed argv
        [
            str(executable),
            "pcb",
            "drc",
            "--format",
            "json",
            "--units",
            "mm",
            "--severity-all",
            "--exit-code-violations",
            "--output",
            str(report),
            str(board),
        ],
        capture_output=True,
        check=False,
        env=environment,
        timeout=180,
    )
    elapsed = time.perf_counter_ns() - started
    if completed.returncode not in (0, 5):
        raise RuntimeError(
            f"{case.name}: KiCad exited {completed.returncode}: "
            f"{completed.stderr.decode(errors='replace')[:500]}"
        )
    payload = json.loads(report.read_text(encoding="utf-8"))
    count = sum(
        1 for item in payload.get("violations", []) if item.get("type") == "courtyards_overlap"
    )
    return count > 0, count, elapsed


def _tiny_kicad_observation(
    executable: Path,
    board: Path,
    report: Path,
    environment: dict[str, str],
) -> tuple[tuple[str, ...], int, int]:
    started = time.perf_counter_ns()
    completed = subprocess.run(  # noqa: S603 - explicit operator-selected binary, fixed argv
        [
            str(executable),
            "pcb",
            "drc",
            "--format",
            "json",
            "--units",
            "mm",
            "--severity-all",
            "--exit-code-violations",
            "--output",
            str(report),
            str(board),
        ],
        capture_output=True,
        check=False,
        env=environment,
        timeout=180,
    )
    elapsed = time.perf_counter_ns() - started
    if completed.returncode not in (0, 5):
        raise RuntimeError(
            f"{board.stem}: KiCad exited {completed.returncode}: "
            f"{completed.stderr.decode(errors='replace')[:500]}"
        )
    payload = json.loads(report.read_text(encoding="utf-8"))
    violation_types = tuple(sorted(item.get("type", "") for item in payload.get("violations", [])))
    return violation_types, len(payload.get("unconnected_items", [])), elapsed


def _git_metadata() -> tuple[str, bool | None, int | None]:
    git = shutil.which("git")
    if git is None:
        return "unknown", None, None
    try:
        commit = subprocess.run(  # noqa: S603 - fixed local Git argv
            [git, "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            cwd=REPOSITORY_ROOT,
            text=True,
            timeout=5,
        ).stdout.strip()
        tracked_dirty = any(
            subprocess.run(  # noqa: S603 - fixed local Git argv
                command,
                check=False,
                capture_output=True,
                cwd=REPOSITORY_ROOT,
                timeout=5,
            ).returncode
            != 0
            for command in ([git, "diff", "--quiet"], [git, "diff", "--cached", "--quiet"])
        )
        untracked = subprocess.run(  # noqa: S603 - fixed local Git argv
            [git, "ls-files", "--others", "--exclude-standard"],
            check=True,
            capture_output=True,
            cwd=REPOSITORY_ROOT,
            text=True,
            timeout=5,
        ).stdout.splitlines()
    except (OSError, subprocess.SubprocessError):
        return "unknown", None, None
    return commit, tracked_dirty, len(untracked)


def _kicad_version(executable: Path) -> str:
    completed = subprocess.run(  # noqa: S603 - explicit operator-selected KiCad binary
        [str(executable), "--version"],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return completed.stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kicad-cli", type=Path, default=DEFAULT_KICAD_CLI)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    executable = args.kicad_cli.resolve()
    if not executable.is_file():
        parser.error(f"KiCad CLI is not a file: {executable}")
    version = _kicad_version(executable)
    if version != "10.0.5":
        parser.error(f"this benchmark is pinned to KiCad 10.0.5, found {version}")

    rows: list[dict[str, Any]] = []
    tiny_rows: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="copper-mcp-courtyard-benchmark-") as directory:
        root = Path(directory)
        environment = _isolated_kicad_env(root / "state")
        work = root / "cases"
        work.mkdir(mode=0o700)
        for oracle_case in CASES:
            source = _board_bytes(oracle_case)
            copper_collision, checks_used = _copper_verdict(source, oracle_case)
            kicad_collision, violation_count, elapsed_ns = _kicad_verdict(
                executable,
                source,
                oracle_case,
                work,
                environment,
            )
            rows.append(
                {
                    "case": oracle_case.name,
                    "checks_used": checks_used,
                    "copper_collision": copper_collision,
                    "expected_collision": oracle_case.expected_collision,
                    "kicad_collision": kicad_collision,
                    "kicad_courtyard_violations": violation_count,
                    "kicad_elapsed_ns": elapsed_ns,
                    "second_origin_x_nm": oracle_case.second_origin_x_nm,
                    "source_sha256": hashlib.sha256(source).hexdigest(),
                }
            )

        tiny_project = _tiny_project_bytes()
        for tiny_case in TINY_CACHE_CASES:
            source = _tiny_board_bytes(tiny_case)
            board = work / f"{tiny_case.name}.kicad_pcb"
            project = work / f"{tiny_case.name}.kicad_pro"
            board.write_bytes(source)
            project.write_bytes(tiny_project)
            observed_runs: list[tuple[str, ...]] = []
            elapsed_runs: list[int] = []
            unconnected_runs: list[int] = []
            for repeat in range(TINY_CACHE_REPETITIONS):
                report_path = work / f"{tiny_case.name}-{repeat}.json"
                observed, unconnected, elapsed = _tiny_kicad_observation(
                    executable,
                    board,
                    report_path,
                    environment,
                )
                observed_runs.append(observed)
                unconnected_runs.append(unconnected)
                elapsed_runs.append(elapsed)
            tiny_rows.append(
                {
                    "case": tiny_case.name,
                    "expected_violation_types": tiny_case.expected_violation_types,
                    "height_nm": tiny_case.height_nm,
                    "kicad_elapsed_ns": elapsed_runs,
                    "observed_violation_types": observed_runs,
                    "repetitions": TINY_CACHE_REPETITIONS,
                    "source_sha256": hashlib.sha256(source).hexdigest(),
                    "unconnected_items": unconnected_runs,
                    "width_nm": tiny_case.width_nm,
                }
            )

    expected_matches = sum(row["copper_collision"] == row["expected_collision"] for row in rows)
    kicad_matches = sum(row["copper_collision"] == row["kicad_collision"] for row in rows)
    tiny_matches = sum(
        all(
            tuple(observed) == tuple(row["expected_violation_types"])
            for observed in row["observed_violation_types"]
        )
        and all(unconnected == 0 for unconnected in row["unconnected_items"])
        for row in tiny_rows
    )
    if (
        expected_matches != len(rows)
        or kicad_matches != len(rows)
        or tiny_matches != len(tiny_rows)
    ):
        raise RuntimeError("courtyard oracle disagreement; refusing to emit a passing artifact")
    commit, tracked_dirty, untracked_count = _git_metadata()
    report: dict[str, Any] = {
        "benchmark": BENCHMARK_NAME,
        "cases": rows,
        "commit": commit,
        "courtyard_policy": COURTYARD_POLICY,
        "environment": {
            "accelerator": "none (CPU-only)",
            "kicad_cli": str(executable),
            "kicad_version": version,
            "machine": platform.machine(),
            "platform": platform.platform(),
            "python": platform.python_version(),
        },
        "git": {
            "tracked_dirty": tracked_dirty,
            "untracked_file_count_before_output": untracked_count,
        },
        "metrics": {
            "baseline_determinate_cases_placement_0_1": 0,
            "current_determinate_cases": len(rows),
            "expected_matches": expected_matches,
            "false_negatives_vs_kicad": sum(
                row["kicad_collision"] and not row["copper_collision"] for row in rows
            ),
            "false_positives_vs_kicad": sum(
                row["copper_collision"] and not row["kicad_collision"] for row in rows
            ),
            "kicad_matches": kicad_matches,
            "tiny_cache_case_matches": tiny_matches,
            "tiny_cache_repetitions_per_case": TINY_CACHE_REPETITIONS,
            "tiny_cache_total_cases": len(tiny_rows),
            "total_cases": len(rows),
        },
        "placement_version": PLACEMENT_VERSION,
        "recorded_at_utc": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "script_sha256": hashlib.sha256(SCRIPT_PATH.read_bytes()).hexdigest(),
        "tiny_cache_cases": tiny_rows,
    }
    report["run_id"] = (
        "sha256:"
        + hashlib.sha256(
            json.dumps(report, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    )
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output is None:
        print(rendered, end="")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
