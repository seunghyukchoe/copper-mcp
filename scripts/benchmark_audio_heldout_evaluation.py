#!/usr/bin/env python3
"""Evaluate one licence-safe, held-out synthetic audio board family.

The runner deliberately reads only the fixture named in the held-out partition.  It records
deterministic inspection, legalizer-backed placement-proxy, and route-preview measurements without
training a policy, mutating source material, invoking KiCad, or making electrical/DRC claims.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from copper_mcp.adapters import KiCadConstraintProfile, parse_kicad_bytes
from copper_mcp.board_ir import NetClass
from copper_mcp.config import Settings
from copper_mcp.placement import build_placement_view, parse_placement_intent
from copper_mcp.placement.solver import PlacementSolverSettings, solve_placement
from copper_mcp.route_preview import RoutePreviewStatus, preview_route
from copper_mcp.tools import inspect_board_ir

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIRECTORY = ROOT / "tests" / "fixtures" / "benchmarks" / "heldout-audio"
FIXTURE = FIXTURE_DIRECTORY / "ac-coupled-signal-chain-v1.kicad_pcb"
PROVENANCE = FIXTURE_DIRECTORY / "provenance.json"
SPLIT = FIXTURE_DIRECTORY / "split.json"
SCRIPT_PATH = Path("scripts/benchmark_audio_heldout_evaluation.py")
ARTIFACT_PATH = Path("benchmarks/results/heldout/2026-08-05-audio-project-family-v1.json")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GIT_COMMIT = re.compile(r"^[0-9a-f]{40}$")
CONSTRAINTS = {
    "clearance_nm": 250_000,
    "track_width_nm": 250_000,
    "via_diameter_nm": 800_000,
    "via_drill_nm": 400_000,
}
PLACEMENT_SETTINGS = PlacementSolverSettings(
    max_evaluations=96,
    max_rounds=4,
    beam_width=4,
    max_ranked=8,
    step_nm=1_000_000,
    deadline_seconds=5.0,
    legalizer_max_checks=100_000,
    legalizer_deadline_seconds=1.0,
)


class HeldoutEvaluationError(RuntimeError):
    """The checked-in held-out protocol or observed deterministic result is invalid."""


@dataclass(frozen=True, slots=True)
class HeldoutProtocol:
    """Exact reviewed fixture and predeclared route targets for one evaluation run."""

    fixture_bytes: bytes
    fixture_sha256: str
    license_sha256: str
    route_nets: tuple[str, ...]


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise HeldoutEvaluationError("protocol JSON contains a duplicate key")
        result[key] = value
    return result


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_bytes(), object_pairs_hook=_strict_object)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise HeldoutEvaluationError("protocol JSON cannot be read") from error
    if not isinstance(value, dict):
        raise HeldoutEvaluationError("protocol JSON must contain one object")
    return value


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


def _git_output(arguments: list[str]) -> bytes:
    git = shutil.which("git")
    if git is None:
        raise HeldoutEvaluationError("Git is required for reproducible evidence")
    try:
        return subprocess.run(  # noqa: S603 - fixed local Git argv
            [git, *arguments],
            cwd=ROOT,
            capture_output=True,
            check=True,
            timeout=5,
        ).stdout
    except (OSError, subprocess.SubprocessError) as error:
        raise HeldoutEvaluationError("Git evidence lookup failed") from error


def _require_clean_git_tree() -> None:
    if _git_output(["status", "--porcelain=v1", "--untracked-files=all"]):
        raise HeldoutEvaluationError("reproducible artifact generation requires a clean Git tree")


def _evidence_source_commit(value: str) -> str:
    if not _GIT_COMMIT.fullmatch(value):
        raise HeldoutEvaluationError("evidence source commit must be one full lowercase Git SHA")
    resolved = _git_output(["rev-parse", "--verify", f"{value}^{{commit}}"]).decode().strip()
    if resolved != value:
        raise HeldoutEvaluationError("evidence source commit did not resolve exactly")
    return resolved


def _tracked_sha256(commit: str, path: Path) -> str:
    try:
        content = _git_output(["show", f"{commit}:{path.as_posix()}"])
    except HeldoutEvaluationError as error:
        raise HeldoutEvaluationError("evidence source commit lacks a bound input") from error
    return _sha256(content)


def _profile() -> KiCadConstraintProfile:
    net_class = NetClass(id="class:heldout-audio", name="Held-out audio", **CONSTRAINTS)
    return KiCadConstraintProfile(net_classes=(net_class,), default_net_class_id=net_class.id)


def load_protocol() -> HeldoutProtocol:
    """Bind a single held-out fixture to local licence/provenance and split evidence."""

    provenance = _load_json(PROVENANCE)
    split = _load_json(SPLIT)
    required_provenance = {
        "artifact",
        "artifact_sha256",
        "derivation_statement",
        "license_file",
        "license_sha256",
        "license_spdx",
        "origin",
        "rights_holder",
        "safety_class",
        "third_party_content_included",
    }
    if not required_provenance <= provenance.keys():
        raise HeldoutEvaluationError("provenance record is incomplete")
    if (
        provenance["artifact"] != FIXTURE.name
        or provenance["license_file"] != "LICENSE"
        or provenance["license_spdx"] != "Apache-2.0"
        or provenance["origin"] != "coppermcp-original"
        or provenance["third_party_content_included"] is not False
    ):
        raise HeldoutEvaluationError("provenance record is not an original Apache-2.0 fixture")
    if (
        not isinstance(provenance["derivation_statement"], str)
        or len(provenance["derivation_statement"]) < 100
    ):
        raise HeldoutEvaluationError("provenance derivation statement is insufficient")

    try:
        fixture_bytes = FIXTURE.read_bytes()
        license_bytes = (FIXTURE_DIRECTORY / "LICENSE").read_bytes()
    except OSError as error:
        raise HeldoutEvaluationError("held-out fixture or its licence is unavailable") from error
    fixture_sha256 = _sha256(fixture_bytes)
    license_sha256 = _sha256(license_bytes)
    if provenance["artifact_sha256"] != fixture_sha256:
        raise HeldoutEvaluationError("held-out fixture hash does not match provenance")
    if provenance["license_sha256"] != license_sha256:
        raise HeldoutEvaluationError("held-out licence hash does not match provenance")
    if b"Apache-2.0" not in license_bytes:
        raise HeldoutEvaluationError("held-out licence does not identify Apache-2.0")

    definitions = split.get("family_definitions")
    if not isinstance(definitions, dict):
        raise HeldoutEvaluationError("split lacks family definitions")
    partitions = ("training", "tuning", "heldout")
    if set(definitions) != set(partitions):
        raise HeldoutEvaluationError("split must declare only training, tuning, and heldout")
    hashes: list[str] = []
    heldout_entries = definitions["heldout"]
    if not isinstance(heldout_entries, list) or len(heldout_entries) != 1:
        raise HeldoutEvaluationError("this v1 evaluator requires exactly one held-out family")
    for partition in partitions:
        entries = definitions[partition]
        if not isinstance(entries, list):
            raise HeldoutEvaluationError("split partition is malformed")
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("family_id"), str):
                raise HeldoutEvaluationError("split family declaration is malformed")
            fixture_hash = entry.get("fixture_sha256")
            if not isinstance(fixture_hash, str) or not _SHA256.fullmatch(fixture_hash):
                raise HeldoutEvaluationError("split fixture hash is required and must be SHA-256")
            hashes.append(fixture_hash)
    if len(hashes) != len(set(hashes)):
        raise HeldoutEvaluationError("a fixture hash appears in multiple split partitions")
    heldout = heldout_entries[0]
    if (
        heldout.get("family_id") != "ac-coupled-signal-chain"
        or heldout.get("fixture") != FIXTURE.name
        or heldout.get("fixture_sha256") != fixture_sha256
    ):
        raise HeldoutEvaluationError("held-out partition does not bind the reviewed fixture")
    if definitions["tuning"] != []:
        raise HeldoutEvaluationError("v1 evaluation has no tuning partition")

    route_nets = tuple(
        sorted(("ACTIVE_LINK", "BIAS_LINK", "GND", "IN_COUPLING", "OUT_COUPLING", "OUTPUT_LINK"))
    )
    return HeldoutProtocol(
        fixture_bytes=fixture_bytes,
        fixture_sha256=fixture_sha256,
        license_sha256=license_sha256,
        route_nets=route_nets,
    )


def _placement_metrics(protocol: HeldoutProtocol) -> dict[str, Any]:
    conversion = parse_kicad_bytes(protocol.fixture_bytes, _profile())
    if conversion.snapshot is None or conversion.diagnostics:
        raise HeldoutEvaluationError("held-out fixture is outside the supported Board IR subset")
    snapshot = conversion.snapshot
    view = build_placement_view(protocol.fixture_bytes, snapshot)
    intent = parse_placement_intent(
        {
            "board": FIXTURE.name,
            "constraints": dict(CONSTRAINTS),
            "subjects": sorted(view.footprints),
            "placement_grid_nm": PLACEMENT_SETTINGS.step_nm,
        }
    )
    result = solve_placement(intent, snapshot, view, settings=PLACEMENT_SETTINGS)
    if result.initial_score is None or not result.ranked:
        raise HeldoutEvaluationError("placement baseline did not retain a legal candidate")
    best = min(result.ranked, key=lambda item: (item.score, item.candidate.candidate_id))
    return {
        "status": result.status,
        "evaluations": result.evaluations,
        "legal_candidate_count": sum(
            item.candidate.evidence.legality.legal for item in result.ranked
        ),
        "initial_connectivity_manhattan_nm": result.initial_score.connectivity_manhattan_nm,
        "best_connectivity_manhattan_nm": best.score.connectivity_manhattan_nm,
        "initial_violated_rules": result.initial_score.violated_rules,
        "best_violated_rules": best.score.violated_rules,
    }


def _route_request(board: str, net: str) -> dict[str, Any]:
    return {
        "board": board,
        "net": net,
        "layer": "F.Cu",
        "seed": 23,
        "constraints": dict(CONSTRAINTS),
    }


def _single_run(protocol: HeldoutProtocol) -> dict[str, Any]:
    """Run isolated inspection/routing/placement without reading another partition."""

    with tempfile.TemporaryDirectory(prefix="copper-mcp-heldout-audio-") as temporary:
        workspace = Path(temporary)
        board = workspace / FIXTURE.name
        board.write_bytes(protocol.fixture_bytes)
        before = (board.read_bytes(), board.stat().st_ino, board.stat().st_mtime_ns)
        settings = Settings(workspace=workspace)
        inspection = inspect_board_ir(
            {"board": board.name, "constraints": dict(CONSTRAINTS)}, settings
        )
        if inspection.get("supported") is not True:
            raise HeldoutEvaluationError("held-out fixture did not support Board IR inspection")

        routes: list[dict[str, Any]] = []
        for net in protocol.route_nets:
            preview = preview_route(_route_request(board.name, net), settings)
            if preview.status is not RoutePreviewStatus.ROUTED or preview.candidate is None:
                raise HeldoutEvaluationError(f"held-out routing target {net} was not routed")
            candidate = preview.candidate
            routes.append(
                {
                    "net": net,
                    "candidate_id": candidate.candidate_id,
                    "wire_length_nm": candidate.metrics.wire_length_nm,
                    "expanded_states": candidate.metrics.expanded_states,
                    "obstacle_checks": candidate.metrics.obstacle_checks,
                    "hard_internal_violations": candidate.metrics.hard_internal_violations,
                }
            )
        after = (board.read_bytes(), board.stat().st_ino, board.stat().st_mtime_ns)
        if before != after:
            raise HeldoutEvaluationError("evaluation mutated its private fixture copy")

    placement = _placement_metrics(protocol)
    return {
        "inspection": {
            "snapshot_digest": inspection.get("snapshot_digest"),
            "object_counts": inspection["object_counts"],
        },
        "placement": placement,
        "routing": {
            "attempted_nets": len(routes),
            "routed_nets": len(routes),
            "completion_fraction": 1.0,
            "total_wire_length_nm": sum(item["wire_length_nm"] for item in routes),
            "total_expanded_states": sum(item["expanded_states"] for item in routes),
            "total_obstacle_checks": sum(item["obstacle_checks"] for item in routes),
            "hard_internal_violations": sum(item["hard_internal_violations"] for item in routes),
            "per_net": routes,
        },
        "source_unchanged": True,
        "network_access": False,
        "kicad_invoked": False,
        "candidate_applied": False,
    }


def _signature(metrics: dict[str, Any]) -> str:
    return _sha256(json.dumps(metrics, sort_keys=True, separators=(",", ":")).encode())


def run_evaluation(repetitions: int) -> dict[str, Any]:
    """Return measured deterministic metrics over repeated held-out-only replays."""

    if not 2 <= repetitions <= 16:
        raise ValueError("repetitions must be between 2 and 16")
    protocol = load_protocol()
    runs = [_single_run(protocol) for _ in range(repetitions)]
    signatures = tuple(_signature(run) for run in runs)
    if len(set(signatures)) != 1:
        raise HeldoutEvaluationError("held-out evaluation replays diverged")
    return {
        "fixture": str(FIXTURE.relative_to(ROOT)),
        "fixture_sha256": protocol.fixture_sha256,
        "license_spdx": "Apache-2.0",
        "license_sha256": protocol.license_sha256,
        "split": {
            "training_family_ids": ["passive-rc-low-pass"],
            "tuning_family_ids": [],
            "heldout_family_ids": ["ac-coupled-signal-chain"],
            "training_or_tuning_fixture_read": False,
        },
        "repetitions": repetitions,
        "deterministic_replays": True,
        "replay_signature": signatures[0],
        "metrics": runs[0],
        "not_claimed": [
            "policy training or policy quality",
            "placement or routing quality improvement",
            "KiCad DRC, ERC, electrical, signal-integrity, thermal, or fabrication readiness",
            "source-board mutation, candidate apply, or live-editor behavior",
            "external-project coverage or audio hardware performance",
        ],
    }


def _input_hashes() -> dict[str, str]:
    paths = (
        SCRIPT_PATH,
        FIXTURE.relative_to(ROOT),
        (FIXTURE_DIRECTORY / "LICENSE").relative_to(ROOT),
        PROVENANCE.relative_to(ROOT),
        SPLIT.relative_to(ROOT),
    )
    return {path.as_posix(): _sha256((ROOT / path).read_bytes()) for path in paths}


def _evidence(repetitions: int, *, evidence_source_commit: str) -> dict[str, Any]:
    return {
        "schema": "copper-mcp/benchmark/heldout-audio-project-family/evidence-v1",
        "evidence_source_commit": evidence_source_commit,
        "inputs": _input_hashes(),
        "script": str(SCRIPT_PATH),
        "evaluation": run_evaluation(repetitions),
    }


def _evidence_run_id(evidence: dict[str, Any]) -> str:
    return "sha256:" + _sha256(
        json.dumps(evidence, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    )


def build_report(repetitions: int, *, timestamp: datetime | None = None) -> dict[str, Any]:
    """Build a local report with host observations separate from deterministic evidence."""

    evidence = _evidence(repetitions, evidence_source_commit=_git_commit())
    return {
        "schema": "copper-mcp/benchmark/heldout-audio-project-family/report-v1",
        "evidence": evidence,
        "evidence_run_id": _evidence_run_id(evidence),
        "observations": {
            "generated_at_utc": (timestamp or datetime.now(UTC)).replace(microsecond=0).isoformat(),
            "platform": platform.platform(),
            "python": platform.python_version(),
        },
    }


def build_reproducible_artifact(repetitions: int, *, evidence_source_commit: str) -> dict[str, Any]:
    """Build evidence only after every bound input matches a clean source commit."""

    _require_clean_git_tree()
    commit = _evidence_source_commit(evidence_source_commit)
    inputs = _input_hashes()
    if any(_tracked_sha256(commit, Path(path)) != digest for path, digest in inputs.items()):
        raise HeldoutEvaluationError("evidence source commit does not match current bound inputs")
    evidence = _evidence(repetitions, evidence_source_commit=commit)
    return {
        "schema": "copper-mcp/benchmark/heldout-audio-project-family/artifact-v1",
        "evidence": evidence,
        "evidence_run_id": _evidence_run_id(evidence),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--reproducible-artifact", action="store_true")
    parser.add_argument("--evidence-source-commit")
    args = parser.parse_args()
    try:
        if args.reproducible_artifact:
            if args.output is None or args.evidence_source_commit is None:
                raise HeldoutEvaluationError(
                    "reproducible artifact mode requires --output and --evidence-source-commit"
                )
            report = build_reproducible_artifact(
                args.repetitions, evidence_source_commit=args.evidence_source_commit
            )
        else:
            report = build_report(args.repetitions)
    except (HeldoutEvaluationError, OSError, ValueError) as error:
        raise SystemExit(f"Held-out audio evaluation failed: {error}") from error
    rendered = json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":
    main()
