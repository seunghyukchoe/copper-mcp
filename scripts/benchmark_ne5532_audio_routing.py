#!/usr/bin/env python3
"""Measure deterministic, non-mutating route previews on an original audio fixture.

This is a structural routing benchmark, not a circuit, DRC, layout-quality, fabrication, or
hardware-performance claim.  It deliberately invokes ``copper_mcp.tools.preview_route``, the
same public application-service surface used by the MCP gateway, against independent copies of
one fully unrouted board.  Each selected net is previewed independently; candidates are not
combined, serialized, or applied.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import shutil
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

try:  # Supports both ``python scripts/...`` and imports from the test suite.
    from check_audio_benchmarks import load_and_validate_catalog
except ModuleNotFoundError:  # pragma: no cover - depends on the Python entrypoint.
    from scripts.check_audio_benchmarks import load_and_validate_catalog

from copper_mcp.adapters import (
    KiCadConstraintProfile,
    parse_kicad_bytes,
    render_kicad_candidate_board,
)
from copper_mcp.board_ir import NetClass
from copper_mcp.config import Settings
from copper_mcp.kicad_cli import (
    _drc_object_pairs,
    _finite_json_float,
    _preflight_drc_json,
    _reject_json_constant,
    _validate_drc_json_tree,
)
from copper_mcp.route_preview import preview_route as preview_route_result
from copper_mcp.security import WorkspaceViolationError, read_workspace_file
from copper_mcp.tools import inspect_board_ir, preview_route

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "benchmarks" / "audio" / "catalog.json"
FIXTURE_ID = "ne5532-stereo-summing-routing-v1"
FIXTURE = ROOT / "benchmarks" / "audio" / "fixtures" / f"{FIXTURE_ID}.kicad_pcb"
SCRIPT_PATH = Path("scripts/benchmark_ne5532_audio_routing.py")
KICAD_CLI = Path("/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli")
_REQUIRED_UNROUTED_COUNTS = {
    "segments": 0,
    "vias": 0,
    "zones": 0,
    "nets": 11,
    "pads": 35,
    "footprints": 14,
}


class AudioRoutingBenchmarkError(RuntimeError):
    """Raised when fixture provenance or deterministic evidence drifts."""


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _git_commit() -> str:
    git = shutil.which("git")
    if git is None:
        return "unknown"
    try:
        return subprocess.run(  # noqa: S603 - fixed local Git argv
            [git, "rev-parse", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            check=True,
            text=True,
            timeout=5,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def _fixture_metadata() -> dict[str, Any]:
    catalog = load_and_validate_catalog(CATALOG, root=ROOT)
    metadata = next(
        (item.document for item in catalog.fixtures if item.document["id"] == FIXTURE_ID), None
    )
    if metadata is None:
        raise AudioRoutingBenchmarkError("fixture is absent from the reviewed audio catalog")
    if (
        metadata["origin"] != "coppermcp-original"
        or metadata["license_spdx"] != "Apache-2.0"
        or metadata["third_party_content_included"] is not False
        or metadata["artifact_path"] != str(FIXTURE.relative_to(ROOT))
    ):
        raise AudioRoutingBenchmarkError("fixture provenance metadata is not the reviewed original")
    return metadata


def _request(board: str, metadata: dict[str, Any], route: dict[str, Any]) -> dict[str, Any]:
    return {
        "board": board,
        "net": route["net"],
        "layer": route["layer"],
        "seed": route["seed"],
        "constraints": dict(metadata["constraints"]),
    }


def _route_evidence(document: dict[str, Any], declaration: dict[str, Any]) -> dict[str, Any]:
    if document.get("status") != "routed":
        raise AudioRoutingBenchmarkError(f"{declaration['net']}: expected a routed candidate")
    candidate = document.get("candidate")
    if not isinstance(candidate, dict):
        raise AudioRoutingBenchmarkError(f"{declaration['net']}: routed response lacks candidate")
    if candidate.get("pad_count") != declaration["expected_pad_count"]:
        raise AudioRoutingBenchmarkError(f"{declaration['net']}: pad-count evidence drifted")
    metrics = candidate.get("metrics")
    cost = candidate.get("cost")
    patch = candidate.get("patch")
    if not isinstance(metrics, dict) or not isinstance(cost, dict) or not isinstance(patch, dict):
        raise AudioRoutingBenchmarkError(f"{declaration['net']}: candidate evidence is malformed")
    paths = patch.get("paths")
    if not isinstance(paths, list) or not paths:
        raise AudioRoutingBenchmarkError(f"{declaration['net']}: candidate has no route paths")
    if (
        metrics.get("hard_internal_violations") != 0
        or metrics.get("unrouted_connections") != 0
        or metrics.get("vias") != 0
        or metrics.get("wire_length_nm") != cost.get("length_nm")
    ):
        raise AudioRoutingBenchmarkError(
            f"{declaration['net']}: candidate is not a clean F.Cu preview"
        )
    return {
        "net": declaration["net"],
        "candidate_id": candidate.get("candidate_id"),
        "pad_count": candidate["pad_count"],
        "path_count": len(paths),
        "ordering_policy": candidate.get("ordering_policy"),
        "wire_length_nm": metrics["wire_length_nm"],
        "via_count": metrics["vias"],
        "expanded_states": metrics.get("expanded_states"),
        "obstacle_checks": metrics.get("obstacle_checks"),
    }


def _profile(metadata: dict[str, Any]) -> KiCadConstraintProfile:
    constraints = metadata["constraints"]
    # Match the public request parser's default profile so a public candidate stays snapshot-bound.
    net_class = NetClass(id="class:request", name="Request", **constraints)
    return KiCadConstraintProfile(net_classes=(net_class,), default_net_class_id=net_class.id)


def _drc_summary(workspace: Path, report_path: Path, *, max_report_bytes: int) -> dict[str, int]:
    try:
        payload = read_workspace_file(
            workspace,
            str(report_path),
            allowed_suffixes={".json"},
            max_bytes=max_report_bytes,
        ).content
        text = payload.decode("utf-8", errors="strict")
        _preflight_drc_json(text)
        document: Any = json.loads(
            text,
            object_pairs_hook=_drc_object_pairs,
            parse_constant=_reject_json_constant,
            parse_float=_finite_json_float,
        )
        _validate_drc_json_tree(document)
    except (
        OSError,
        WorkspaceViolationError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        RecursionError,
        ValueError,
    ) as error:
        raise AudioRoutingBenchmarkError("KiCad DRC report cannot be parsed") from error
    if not isinstance(document, dict):
        raise AudioRoutingBenchmarkError("KiCad DRC report must be one JSON object")
    violations = document.get("violations")
    unconnected = document.get("unconnected_items")
    if not isinstance(violations, list) or not isinstance(unconnected, list):
        raise AudioRoutingBenchmarkError("KiCad DRC report has no bounded issue lists")
    return {"violations": len(violations), "unconnected_items": len(unconnected)}


def _run_drc(
    workspace: Path,
    source: bytes,
    metadata: dict[str, Any],
    route_documents: list[tuple[dict[str, Any], dict[str, Any], object]],
    *,
    settings: Settings,
) -> dict[str, Any]:
    """Run KiCad DRC on source and independently serialized candidates, if opted in."""

    if not KICAD_CLI.is_file():
        return {
            "attempted": False,
            "status": "unavailable",
            "reason": "KiCad 10 CLI executable is absent at the reviewed local path",
        }
    profile = _profile(metadata)
    conversion = parse_kicad_bytes(source, profile)
    if conversion.snapshot is None or conversion.diagnostics:
        raise AudioRoutingBenchmarkError("fixture cannot produce a snapshot for DRC serialization")
    source_board = workspace / "source-drc.kicad_pcb"
    source_board.write_bytes(source)
    environment = {
        "HOME": str(workspace),
        "LANG": "C",
        "LC_ALL": "C",
        "PATH": "/usr/bin:/bin",
        "TMPDIR": str(workspace),
    }

    def execute(board: Path, report: Path) -> dict[str, int]:
        try:
            completed = subprocess.run(  # noqa: S603 - fixed KiCad argv and private workspace
                [
                    str(KICAD_CLI),
                    "pcb",
                    "drc",
                    "--format",
                    "json",
                    "--output",
                    str(report),
                    str(board),
                ],
                cwd=workspace,
                env=environment,
                capture_output=True,
                check=False,
                text=True,
                timeout=45,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise AudioRoutingBenchmarkError("KiCad DRC invocation failed") from error
        if completed.returncode != 0:
            raise AudioRoutingBenchmarkError("KiCad DRC returned a nonzero status")
        return _drc_summary(
            workspace,
            report,
            max_report_bytes=settings.max_drc_report_bytes,
        )

    source_counts = execute(source_board, workspace / "source-drc.json")
    candidates: list[dict[str, Any]] = []
    for index, (route, document, candidate) in enumerate(route_documents):
        candidate_document = document.get("candidate")
        if not isinstance(candidate_document, dict) or candidate is None:
            raise AudioRoutingBenchmarkError("candidate disappeared before DRC serialization")
        derivative = workspace / f"candidate-{index}.kicad_pcb"
        derivative.write_bytes(
            render_kicad_candidate_board(source, conversion.snapshot, candidate, profile)
        )
        counts = execute(derivative, workspace / f"candidate-{index}-drc.json")
        if counts["violations"] > source_counts["violations"]:
            raise AudioRoutingBenchmarkError("candidate DRC introduced additional hard violations")
        if counts["unconnected_items"] >= source_counts["unconnected_items"]:
            raise AudioRoutingBenchmarkError(
                "candidate DRC did not reduce the expected disconnected net"
            )
        candidates.append({"net": route["net"], **counts})
    return {
        "attempted": True,
        "status": "completed-not-clean",
        "authority": "KiCad CLI JSON DRC over independent disposable single-net derivatives",
        "kicad_cli_path": str(KICAD_CLI),
        "source": source_counts,
        "candidates": candidates,
        "combined_candidate_board": False,
        "clean": False,
    }


def run_benchmark(repetitions: int, *, include_kicad_drc: bool = False) -> dict[str, Any]:
    """Run repeated public-service previews without writing or applying copper."""

    if not 1 <= repetitions <= 32:
        raise ValueError("repetitions must be between 1 and 32")
    metadata = _fixture_metadata()
    source = FIXTURE.read_bytes()
    if metadata["artifact_sha256"] != _sha256(source):
        raise AudioRoutingBenchmarkError("fixture bytes disagree with catalog provenance")
    declarations = metadata["routes"]
    if not isinstance(declarations, list) or len(declarations) < 4:
        raise AudioRoutingBenchmarkError("fixture lacks nontrivial reviewed route declarations")

    with tempfile.TemporaryDirectory(prefix="copper-mcp-ne5532-routing-") as temporary:
        workspace = Path(temporary)
        board = workspace / FIXTURE.name
        board.write_bytes(source)
        before = (board.read_bytes(), board.stat().st_ino, board.stat().st_mtime_ns)
        settings = Settings(workspace=workspace)
        inspection = inspect_board_ir(
            {"board": board.name, "constraints": dict(metadata["constraints"])}, settings
        )
        if inspection.get("supported") is not True:
            raise AudioRoutingBenchmarkError("fixture is outside the Board IR subset")
        counts = inspection.get("object_counts")
        if not isinstance(counts, dict) or any(
            counts.get(key) != value for key, value in _REQUIRED_UNROUTED_COUNTS.items()
        ):
            raise AudioRoutingBenchmarkError("fixture no longer has the reviewed unrouted topology")

        route_documents: list[tuple[dict[str, Any], dict[str, Any], object]] = []
        for declaration in declarations:
            if not isinstance(declaration, dict):
                raise AudioRoutingBenchmarkError("route declaration is malformed")
            previews = [
                preview_route(_request(board.name, metadata, declaration), settings)
                for _ in range(repetitions)
            ]
            if any(item != previews[0] for item in previews[1:]):
                raise AudioRoutingBenchmarkError(
                    f"{declaration['net']}: preview is not deterministic"
                )
            result = preview_route_result(_request(board.name, metadata, declaration), settings)
            if result.to_dict() != previews[0]:
                raise AudioRoutingBenchmarkError(
                    f"{declaration['net']}: public and shared route surfaces disagree"
                )
            route_documents.append(
                (_route_evidence(previews[0], declaration), previews[0], result.candidate)
            )

        drc = (
            _run_drc(workspace, source, metadata, route_documents, settings=settings)
            if include_kicad_drc
            else {
                "attempted": False,
                "status": "not_run",
                "reason": "enable --include-kicad-drc to run KiCad on disposable derivatives",
            }
        )

        after = (board.read_bytes(), board.stat().st_ino, board.stat().st_mtime_ns)
        if before != after:
            raise AudioRoutingBenchmarkError("benchmark mutated its private source-board copy")

    routes = [route for route, _document, _candidate in route_documents]
    routed_count = len(routes)
    multi_pin_count = sum(item["pad_count"] > 2 for item in routes)
    if routed_count != len(declarations) or multi_pin_count < 2:
        raise AudioRoutingBenchmarkError("reviewed route coverage is no longer nontrivial")
    return {
        "fixture_id": FIXTURE_ID,
        "fixture_origin": metadata["origin"],
        "fixture_license_spdx": metadata["license_spdx"],
        "fixture_source_sha256": _sha256(source),
        "fixture_license_sha256": metadata["license_sha256"],
        "fixture_derivation_statement": metadata["derivation_statement"],
        "repetitions": repetitions,
        "source_object_counts": dict(counts),
        "source_is_unrouted": True,
        "route_request_count": len(declarations),
        "routed_request_count": routed_count,
        "unrouted_request_count": len(declarations) - routed_count,
        "multi_pin_routed_request_count": multi_pin_count,
        "routes": routes,
        "candidate_applied": False,
        "source_unchanged": True,
        "kicad_cli_available": KICAD_CLI.is_file(),
        "authoritative_drc": drc,
        "not_claimed": [
            "combined-net-route-feasibility",
            "candidate-drc",
            "electrical-validation",
            "fabrication-readiness",
            "freerouting-parity",
            "hardware-measurement",
        ],
    }


def build_report(
    repetitions: int,
    *,
    include_kicad_drc: bool = False,
    timestamp: datetime | None = None,
) -> dict[str, Any]:
    """Build a content-addressed report; host metadata is deliberately out of scope."""

    report: dict[str, Any] = {
        "schema": "copper-mcp/benchmark/ne5532-audio-routing/v1",
        "date_utc": (timestamp or datetime.now(UTC)).replace(microsecond=0).isoformat(),
        "source_commit": _git_commit(),
        "environment": {"platform": platform.platform(), "python": platform.python_version()},
        "fixture": str(FIXTURE.relative_to(ROOT)),
        "script": str(SCRIPT_PATH),
        "metrics": run_benchmark(repetitions, include_kicad_drc=include_kicad_drc),
    }
    canonical = json.dumps(report, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    report["run_id"] = "sha256:" + _sha256(canonical)
    return report


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--include-kicad-drc", action="store_true")
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    try:
        report = build_report(args.repetitions, include_kicad_drc=args.include_kicad_drc)
    except (AudioRoutingBenchmarkError, OSError, ValueError, json.JSONDecodeError) as error:
        raise SystemExit(f"NE5532 audio routing benchmark failed: {error}") from error
    rendered = json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":
    main()
