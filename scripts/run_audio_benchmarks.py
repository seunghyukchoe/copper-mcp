#!/usr/bin/env python3
"""Run the local, non-mutating audio circuit capability checks."""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path
from typing import Any

from check_audio_benchmarks import (
    DEFAULT_CATALOG,
    MAX_ARTIFACT_BYTES,
    ROOT,
    CatalogError,
    ValidatedFixture,
    load_and_validate_catalog,
)

from copper_mcp.config import Settings
from copper_mcp.tools import inspect_board_ir, preview_route


class BenchmarkError(RuntimeError):
    """Observed capability evidence did not match the reviewed catalog."""


def _inspection_request(board: str, fixture: dict[str, Any]) -> dict[str, Any]:
    return {
        "board": board,
        "constraints": dict(fixture["constraints"]),
    }


def _route_request(
    board: str,
    fixture: dict[str, Any],
    route: dict[str, Any],
) -> dict[str, Any]:
    return {
        "board": board,
        "net": route["net"],
        "layer": route["layer"],
        "seed": route["seed"],
        "constraints": dict(fixture["constraints"]),
    }


def _check_inspection(
    fixture: dict[str, Any],
    document: dict[str, Any],
) -> None:
    expected = fixture["inspection"]
    if document["supported"] is not expected["expected_supported"]:
        raise BenchmarkError(f"{fixture['id']}: Board IR support status changed")
    observed_counts = document["object_counts"]
    for key, value in expected["expected_object_counts"].items():
        if observed_counts.get(key) != value:
            raise BenchmarkError(f"{fixture['id']}: Board IR object count changed")


def _check_route(
    fixture: dict[str, Any],
    route: dict[str, Any],
    document: dict[str, Any],
) -> None:
    if document["status"] != route["expected_status"]:
        raise BenchmarkError(f"{fixture['id']}: route status changed")
    diagnostic = document.get("diagnostic")
    observed_code = diagnostic.get("code") if isinstance(diagnostic, dict) else None
    if observed_code != route["expected_diagnostic"]:
        raise BenchmarkError(f"{fixture['id']}: route diagnostic changed")
    candidate = document.get("candidate")
    if document["status"] == "routed":
        if not isinstance(candidate, dict):
            raise BenchmarkError(f"{fixture['id']}: routed result has no candidate")
        candidate_id = candidate.get("candidate_id")
        if not isinstance(candidate_id, str) or not candidate_id:
            raise BenchmarkError(f"{fixture['id']}: routed result has no candidate_id")
    elif candidate is not None:
        raise BenchmarkError(f"{fixture['id']}: refused route unexpectedly has a candidate")


def _observed_claims(
    inspection: dict[str, Any],
    routes: list[dict[str, Any]],
) -> list[str]:
    claims: list[str] = []
    if inspection["supported"] is True:
        claims.append("board-ir-inspection")
    if any(route["status"] == "routed" and route["candidate_id"] for route in routes):
        claims.append("two-pin-route-preview")
    if any(route["status"] != "routed" and route["diagnostic_code"] for route in routes):
        claims.append("typed-route-refusal")
    return claims


def _run_fixture(validated: ValidatedFixture) -> dict[str, Any]:
    fixture = validated.document
    source = validated.artifact_bytes
    if len(source) > MAX_ARTIFACT_BYTES:
        raise BenchmarkError(f"{fixture['id']}: source exceeds the artifact byte limit")
    source_digest = validated.artifact_sha256

    with tempfile.TemporaryDirectory(prefix="copper-mcp-audio-") as temporary:
        workspace = Path(temporary)
        board_name = Path(fixture["artifact_path"]).name
        board = workspace / board_name
        board.write_bytes(source)
        settings = Settings(workspace=workspace)

        inspection_request = _inspection_request(board_name, fixture)
        first_inspection = inspect_board_ir(inspection_request, settings)
        second_inspection = inspect_board_ir(inspection_request, settings)
        if first_inspection != second_inspection:
            raise BenchmarkError(f"{fixture['id']}: Board IR inspection is not deterministic")
        _check_inspection(fixture, first_inspection)

        route_results: list[dict[str, Any]] = []
        for index, route in enumerate(fixture["routes"]):
            request = _route_request(board_name, fixture, route)
            first_route = preview_route(request, settings)
            second_route = preview_route(request, settings)
            if first_route != second_route:
                raise BenchmarkError(f"{fixture['id']}: route preview is not deterministic")
            _check_route(fixture, route, first_route)
            diagnostic = first_route.get("diagnostic")
            route_results.append(
                {
                    "route_index": index,
                    "status": first_route["status"],
                    "diagnostic_code": (
                        diagnostic.get("code") if isinstance(diagnostic, dict) else None
                    ),
                    "candidate_id": (
                        first_route["candidate"]["candidate_id"]
                        if isinstance(first_route.get("candidate"), dict)
                        else None
                    ),
                }
            )

        if board.read_bytes() != source:
            raise BenchmarkError(f"{fixture['id']}: benchmark mutated its private board copy")

    observed_claims = _observed_claims(first_inspection, route_results)
    if set(observed_claims) != set(fixture["claims"]):
        raise BenchmarkError(f"{fixture['id']}: observed evidence does not match claims")

    return {
        "fixture_id": fixture["id"],
        "artifact_sha256": source_digest,
        "license_spdx": fixture["license_spdx"],
        "license_sha256": validated.license_sha256,
        "safety_class": fixture["safety_class"],
        "inspection": {
            "supported": first_inspection["supported"],
            "snapshot_digest": first_inspection.get("snapshot_digest"),
            "object_counts": first_inspection["object_counts"],
        },
        "routes": route_results,
        "claims": observed_claims,
        "not_claimed": list(fixture["not_claimed"]),
    }


def run_catalog(catalog_path: Path, *, root: Path = ROOT) -> dict[str, Any]:
    """Validate and run every committed fixture without network or source mutation."""

    resolved_root = root.resolve(strict=True)
    catalog = load_and_validate_catalog(catalog_path, root=resolved_root)
    fixtures = [_run_fixture(fixture) for fixture in catalog.fixtures]
    return {
        "schema_version": "audio-circuit-capability-run/0.1.0",
        "catalog_sha256": catalog.catalog_sha256,
        "catalog_schema_sha256": catalog.schema_sha256,
        "network_access": False,
        "source_mutation": False,
        "external_references": [
            {
                "id": reference["id"],
                "executed": False,
                "reason": "reference-only-no-network",
            }
            for reference in catalog.document["external_references"]
        ],
        "fixtures": fixtures,
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=ROOT / DEFAULT_CATALOG)
    parser.add_argument("--root", type=Path, default=ROOT)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        report = run_catalog(args.catalog, root=args.root)
    except (CatalogError, BenchmarkError) as error:
        raise SystemExit(f"Audio benchmark failed: {error}") from error
    print(json.dumps(report, indent=2, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
