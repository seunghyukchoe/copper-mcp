#!/usr/bin/env python3
"""Measure whether Circuit Scene references are directly actionable through MCP."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import platform
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any
from unittest.mock import patch

from check_audio_benchmarks import DEFAULT_CATALOG, ROOT, load_and_validate_catalog

from copper_mcp.adapters import net_id_for_name
from copper_mcp.config import Settings

if __package__:
    from .offline_mcp_harness import load_offline_mcp_server
else:
    from offline_mcp_harness import load_offline_mcp_server

mcp_server = load_offline_mcp_server()

BENCHMARK_NAME = "scene-route-referential-closure-v1"
FIXTURE_ID = "rc-low-pass-routing-v1"
SCRIPT_PATH = Path("scripts/benchmark_scene_action_closure.py")


class BenchmarkError(RuntimeError):
    """The observed MCP behavior did not satisfy the declared oracle."""


def _bounded_count(value: str, *, minimum: int) -> int:
    count = int(value)
    if not minimum <= count <= 20:
        raise argparse.ArgumentTypeError(f"count must be between {minimum} and 20")
    return count


def _repetitions(value: str) -> int:
    return _bounded_count(value, minimum=2)


def _warmups(value: str) -> int:
    return _bounded_count(value, minimum=0)


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _digest(value: Any) -> str:
    return f"sha256:{hashlib.sha256(_canonical_bytes(value)).hexdigest()}"


def _git_metadata() -> tuple[str, bool | None, int | None]:
    git = shutil.which("git")
    if git is None:
        return "unknown", None, None
    try:
        commit = subprocess.run(  # noqa: S603 - resolved executable, fixed local argv
            [git, "rev-parse", "HEAD"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
        tracked_dirty = bool(
            subprocess.run(  # noqa: S603 - resolved executable, fixed local argv
                [git, "status", "--porcelain", "--untracked-files=no"],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
                timeout=5,
            ).stdout
        )
        untracked = subprocess.run(  # noqa: S603 - resolved executable, fixed local argv
            [git, "ls-files", "--others", "--exclude-standard"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.splitlines()
    except (OSError, subprocess.SubprocessError):
        return "unknown", None, None
    return commit, tracked_dirty, len(untracked)


def _physical_memory_bytes() -> int | None:
    try:
        return os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
    except (OSError, TypeError, ValueError):
        return None


def _package_version(distribution: str) -> str | None:
    try:
        return version(distribution)
    except PackageNotFoundError:
        return None


def _tree_state(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


async def _call_tool(name: str, arguments: dict[str, Any]) -> tuple[dict[str, Any], int]:
    started = time.perf_counter_ns()
    result = await mcp_server.mcp.call_tool(name, arguments)
    elapsed = time.perf_counter_ns() - started
    if result.is_error or not isinstance(result.structured_content, dict):
        raise BenchmarkError(f"{name} did not return successful structured content")
    return result.structured_content, elapsed


async def _replay(
    name: str,
    arguments: dict[str, Any],
    *,
    warmups: int,
    repetitions: int,
) -> tuple[list[dict[str, Any]], list[int]]:
    for _ in range(warmups):
        await _call_tool(name, arguments)
    documents: list[dict[str, Any]] = []
    latencies: list[int] = []
    for _ in range(repetitions):
        document, elapsed = await _call_tool(name, arguments)
        documents.append(document)
        latencies.append(elapsed)
    return documents, latencies


def _route_request(
    *,
    board: str,
    constraints: dict[str, int],
    seed: int,
    layer: str,
    selector: dict[str, Any],
) -> dict[str, Any]:
    return {
        "request": {
            "board": board,
            "constraints": constraints,
            "seed": seed,
            "layer": layer,
            **selector,
        }
    }


def _assert_deterministic(documents: list[dict[str, Any]], *, label: str) -> str:
    digests = {_digest(document) for document in documents}
    if len(digests) != 1:
        raise BenchmarkError(f"{label} was not deterministic")
    return next(iter(digests))


def _schema_evidence(tools: list[Any]) -> dict[str, Any]:
    route = next(tool for tool in tools if tool.name == "preview_route")
    request = route.input_schema["properties"]["request"]
    variants = request.get("anyOf", [])
    output = route.output_schema
    if not isinstance(output, dict):
        raise BenchmarkError("preview_route has no structured output schema")
    definitions = output.get("$defs", {})
    output_variants = output.get("anyOf", [])

    def resolve_definition(value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        reference = value.get("$ref")
        if isinstance(reference, str) and reference.startswith("#/$defs/"):
            resolved = definitions.get(reference.removeprefix("#/$defs/"))
            return resolved if isinstance(resolved, dict) else None
        return value

    resolved_output_variants = [resolve_definition(value) for value in output_variants]
    closed_record_objects: list[str] = []
    open_record_objects: list[str] = []

    def inspect_records(value: Any, path: str) -> None:
        if isinstance(value, dict):
            if value.get("type") == "object" and isinstance(value.get("properties"), dict):
                target = (
                    closed_record_objects
                    if value.get("additionalProperties") is False
                    else open_record_objects
                )
                target.append(path)
            for key, item in value.items():
                inspect_records(item, f"{path}/{key}")
        elif isinstance(value, list):
            for index, item in enumerate(value):
                inspect_records(item, f"{path}/{index}")

    inspect_records(output, "output")
    evidence = {
        "input_wrapper_closed": route.input_schema.get("additionalProperties") is False,
        "selector_variants": len(variants),
        "selector_variants_closed": all(
            variant.get("additionalProperties") is False for variant in variants
        ),
        "reference_selector_revision_bound": any(
            {
                "net_ref_id",
                "expect_board_revision",
                "expect_snapshot_digest",
            }.issubset(set(variant.get("required", [])))
            for variant in variants
        ),
        "output_status_variants": len(resolved_output_variants),
        "output_closed": bool(resolved_output_variants)
        and all(
            isinstance(variant, dict) and variant.get("additionalProperties") is False
            for variant in resolved_output_variants
        ),
        "candidate_closed": (
            isinstance(definitions, dict)
            and definitions.get("RouteCandidateContract", {}).get("additionalProperties") is False
        ),
        "all_record_objects_closed": not open_record_objects,
        "closed_record_object_count": len(closed_record_objects),
        "output_schema_digest": _digest(output),
    }
    if (
        not all(
            evidence[key]
            for key in (
                "input_wrapper_closed",
                "selector_variants_closed",
                "reference_selector_revision_bound",
                "output_closed",
                "candidate_closed",
                "all_record_objects_closed",
            )
        )
        or evidence["selector_variants"] != 2
        or evidence["output_status_variants"] != 5
        or evidence["closed_record_object_count"] < 12
    ):
        raise BenchmarkError("preview_route did not advertise the closed reference contract")
    return evidence


async def _run(
    *,
    repetitions: int,
    warmups: int,
    catalog_path: Path,
) -> dict[str, Any]:
    catalog = load_and_validate_catalog(catalog_path)
    validated = next(
        (fixture for fixture in catalog.fixtures if fixture.document["id"] == FIXTURE_ID),
        None,
    )
    if validated is None:
        raise BenchmarkError(f"catalog does not contain {FIXTURE_ID}")
    fixture = validated.document
    constraints = dict(fixture["constraints"])
    route_specs = list(fixture["routes"])
    oracle_by_ref = {net_id_for_name(route["net"]): route for route in route_specs}
    if len(oracle_by_ref) != len(route_specs):
        raise BenchmarkError("route oracle names do not map to unique Board IR references")

    with tempfile.TemporaryDirectory(prefix="copper-mcp-scene-action-") as temporary:
        workspace = Path(temporary)
        board_name = Path(fixture["artifact_path"]).name
        board = workspace / board_name
        board.write_bytes(validated.artifact_bytes)
        settings = Settings(workspace=workspace)
        before = _tree_state(workspace)

        with patch.object(mcp_server, "_SETTINGS", settings):
            tools = await mcp_server.mcp.list_tools()
            schemas = _schema_evidence(tools)
            scene_arguments = {
                "request": {
                    "board": board_name,
                    "constraints": constraints,
                    "region": {
                        "min_x_nm": 0,
                        "min_y_nm": 0,
                        "max_x_nm": 100_000_000,
                        "max_y_nm": 100_000_000,
                    },
                }
            }
            scenes, scene_latencies = await _replay(
                "observe_board_scene",
                scene_arguments,
                warmups=warmups,
                repetitions=repetitions,
            )
            scene_digest = _assert_deterministic(scenes, label="Circuit Scene observation")
            scene = scenes[0]
            if scene["supported"] is not True or scene["truncation"]["objects_omitted"] != 0:
                raise BenchmarkError("scene is unsupported or incomplete")
            observed_refs = sorted(
                {
                    pad["geometry"]["net_id"]
                    for pad in scene["static"]["pads"]
                    if pad["geometry"]["net_id"] is not None
                }
            )
            if set(observed_refs) != set(oracle_by_ref):
                raise BenchmarkError(
                    "observed net references do not match the reviewed route oracle"
                )

            route_records: list[dict[str, Any]] = []
            all_latencies: dict[str, list[int]] = {
                "legacy_miswire": [],
                "reference": [],
                "oracle": [],
            }
            deterministic_replays = 0
            candidate_equivalence = 0
            reference_actionable = 0
            legacy_actionable = 0
            oracle_actionable = 0
            stale_refusals = {"board": 0, "snapshot": 0}
            reference_payload_bytes = 0

            for net_ref_id in observed_refs:
                route = oracle_by_ref[net_ref_id]
                common = {
                    "board": board_name,
                    "constraints": constraints,
                    "seed": route["seed"],
                    "layer": route["layer"],
                }
                selectors = {
                    "legacy_miswire": {"net": net_ref_id},
                    "reference": {
                        "net_ref_id": net_ref_id,
                        "expect_board_revision": scene["board_revision"],
                        "expect_snapshot_digest": scene["snapshot_digest"],
                    },
                    "oracle": {"net": route["net"]},
                }
                documents: dict[str, dict[str, Any]] = {}
                response_digests: dict[str, str] = {}
                for mode, selector in selectors.items():
                    calls, latencies = await _replay(
                        "preview_route",
                        _route_request(**common, selector=selector),
                        warmups=warmups,
                        repetitions=repetitions,
                    )
                    response_digests[mode] = _assert_deterministic(
                        calls, label=f"{net_ref_id} {mode}"
                    )
                    deterministic_replays += len(calls)
                    all_latencies[mode].extend(latencies)
                    documents[mode] = calls[0]

                legacy = documents["legacy_miswire"]
                referenced = documents["reference"]
                oracle = documents["oracle"]
                legacy_actionable += int(legacy["status"] in {"routed", "already_connected"})
                reference_actionable += int(referenced["status"] in {"routed", "already_connected"})
                reference_payload_bytes += len(_canonical_bytes(referenced))
                oracle_actionable += int(oracle["status"] in {"routed", "already_connected"})
                if legacy["status"] != "not_routed" or legacy["diagnostic"]["code"] != (
                    "invalid_two_pin_net"
                ):
                    raise BenchmarkError(
                        "legacy scene-ID-as-name counterfactual unexpectedly worked"
                    )
                if referenced["status"] != route["expected_status"]:
                    raise BenchmarkError("scene reference route did not match its expected status")
                if _canonical_bytes(referenced["candidate"]) != _canonical_bytes(
                    oracle["candidate"]
                ):
                    raise BenchmarkError(
                        "scene reference candidate differs from hidden-name oracle"
                    )
                candidate_equivalence += 1

                for stale_kind, stale_selector in (
                    (
                        "board",
                        {
                            "net_ref_id": net_ref_id,
                            "expect_board_revision": f"sha256:{'0' * 64}",
                            "expect_snapshot_digest": scene["snapshot_digest"],
                        },
                    ),
                    (
                        "snapshot",
                        {
                            "net_ref_id": net_ref_id,
                            "expect_board_revision": scene["board_revision"],
                            "expect_snapshot_digest": f"sha256:{'0' * 64}",
                        },
                    ),
                ):
                    stale, _ = await _call_tool(
                        "preview_route",
                        _route_request(**common, selector=stale_selector),
                    )
                    if stale["status"] != "not_routed" or stale["diagnostic"]["code"] != (
                        "stale_revision"
                    ):
                        raise BenchmarkError(
                            f"a stale {stale_kind} scene reference was not refused"
                        )
                    stale_refusals[stale_kind] += 1

                route_records.append(
                    {
                        "net_ref_id": net_ref_id,
                        "oracle_net": route["net"],
                        "expected_pad_count": route["expected_pad_count"],
                        "legacy_status": legacy["status"],
                        "reference_status": referenced["status"],
                        "oracle_status": oracle["status"],
                        "candidate_id": referenced["candidate"]["candidate_id"],
                        "candidate_equivalent": True,
                        "response_digests": response_digests,
                    }
                )

        after = _tree_state(workspace)
        if after != before:
            raise BenchmarkError("the MCP benchmark changed its final workspace file tree")

    commit, tracked_dirty, untracked_count = _git_metadata()
    script_bytes = (ROOT / SCRIPT_PATH).read_bytes()
    route_count = len(route_records)
    if reference_actionable != route_count or oracle_actionable != route_count:
        raise BenchmarkError("not every supported observed net was actionable")
    if legacy_actionable != 0 or candidate_equivalence != route_count:
        raise BenchmarkError("referential closure improvement did not meet its oracle")
    dependency_versions = {
        "jsonschema": _package_version("jsonschema"),
        "mcp": _package_version("mcp"),
        "pydantic": _package_version("pydantic"),
    }
    if not all(isinstance(value, str) and value for value in dependency_versions.values()):
        raise BenchmarkError("required benchmark dependency version is unavailable")

    document: dict[str, Any] = {
        "benchmark": BENCHMARK_NAME,
        "created_at_utc": datetime.now(UTC).isoformat(),
        "source_commit": commit,
        "tracked_worktree_dirty": tracked_dirty,
        "untracked_file_count": untracked_count,
        "environment": {
            "platform": platform.platform(),
            "machine": platform.machine(),
            "processor": platform.processor() or None,
            "python": platform.python_version(),
            "dependencies": dependency_versions,
            "physical_memory_bytes": _physical_memory_bytes(),
            "accelerator": None,
            "kicad_invoked": False,
        },
        "dataset": {
            "fixture_id": fixture["id"],
            "artifact_path": fixture["artifact_path"],
            "artifact_sha256": validated.artifact_sha256,
            "license_spdx": fixture["license_spdx"],
            "license_sha256": validated.license_sha256,
            "catalog_sha256": catalog.catalog_sha256,
            "third_party_content_included": fixture["third_party_content_included"],
        },
        "configuration": {
            "script_sha256": hashlib.sha256(script_bytes).hexdigest(),
            "harness_helper": {
                "path": "scripts/offline_mcp_harness.py",
                "sha256": hashlib.sha256(
                    (ROOT / "scripts/offline_mcp_harness.py").read_bytes()
                ).hexdigest(),
            },
            "repetitions": repetitions,
            "warmups": warmups,
            "seed": route_specs[0]["seed"],
            "constraints": constraints,
            "region_nm": [0, 0, 100_000_000, 100_000_000],
            "transport_path": "in_process_mcp_call_tool",
        },
        "schema_evidence": schemas,
        "scene": {
            "board_revision": scene["board_revision"],
            "snapshot_digest": scene["snapshot_digest"],
            "normalized_response_digest": scene_digest,
            "net_ref_count": len(observed_refs),
            "objects_returned": scene["truncation"]["objects_returned"],
            "objects_omitted": scene["truncation"]["objects_omitted"],
        },
        "metrics": {
            "legacy_scene_ref_actionable": legacy_actionable,
            "reference_actionable": reference_actionable,
            "hidden_name_oracle_actionable": oracle_actionable,
            "candidate_equivalence": candidate_equivalence,
            "stale_board_revision_refusals": stale_refusals["board"],
            "stale_snapshot_digest_refusals": stale_refusals["snapshot"],
            "stale_reference_refusals": sum(stale_refusals.values()),
            "deterministic_route_replays": deterministic_replays,
            "expected_deterministic_route_replays": route_count * 3 * repetitions,
            "deterministic_scene_replays": repetitions,
            "persistent_workspace_changes": 0,
            "median_latency_ns": {
                "scene": int(statistics.median(scene_latencies)),
                **{
                    mode: int(statistics.median(latencies))
                    for mode, latencies in all_latencies.items()
                },
            },
            "structured_payload_bytes": {
                "scene": len(_canonical_bytes(scene)),
                "reference_routes_total": reference_payload_bytes,
            },
        },
        "routes": route_records,
        "claims": [
            "scene-route-referential-closure",
            "revision-bound-net-selection",
            "closed-preview-route-mcp-schema",
            "deterministic-mcp-replay",
            "unchanged-final-workspace-tree",
        ],
        "not_claimed": [
            "kicad-drc",
            "whole-board-routing",
            "multilayer-routing",
            "placement",
            "live-kicad-ipc",
            "electrical-validation",
            "fabrication-readiness",
            "performance-generalization",
        ],
    }
    document["run_id"] = _digest(document)
    return document


def _write_result(document: dict[str, Any], output: Path | None, *, pretty: bool) -> None:
    rendered = json.dumps(
        document,
        indent=2 if pretty else None,
        sort_keys=True,
        ensure_ascii=False,
        allow_nan=False,
    )
    if pretty:
        rendered += "\n"
    if output is None:
        sys.stdout.write(rendered)
        if not rendered.endswith("\n"):
            sys.stdout.write("\n")
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as handle:
        handle.write(rendered)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=ROOT / DEFAULT_CATALOG)
    parser.add_argument("--repetitions", type=_repetitions, default=10)
    parser.add_argument("--warmups", type=_warmups, default=1)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args(argv)
    try:
        document = asyncio.run(
            _run(
                repetitions=args.repetitions,
                warmups=args.warmups,
                catalog_path=args.catalog,
            )
        )
        _write_result(document, args.output, pretty=args.pretty)
    except (BenchmarkError, OSError, ValueError) as error:
        parser.error(str(error))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
