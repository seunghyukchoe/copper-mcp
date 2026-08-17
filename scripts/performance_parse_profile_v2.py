#!/usr/bin/env python3
"""Profile one committed parse-heavy board through the complete Board IR read path.

This is a measurement harness, not an acceleration. Unprofiled wall-clock samples and the
instrumented cumulative profile are deliberately separate. The report binds the exact fixture,
both scripts, configuration, and clean Git source while excluding environmental timing values from
its deterministic identity.
"""

from __future__ import annotations

import argparse
import cProfile
import json
import platform
import pstats
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from performance_profile_v1 import (
    _canonical_bytes,
    _clean_source_provenance,
    _count,
    _profile,
    _redacted_label,
    _sha256,
    _timing_summary,
)

from copper_mcp.adapters import parse_kicad_bytes

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = _ROOT / "scripts" / "performance_parse_profile_v2.py"
_SUPPORT_SCRIPT = _ROOT / "scripts" / "performance_profile_v1.py"
_DEFAULT_OUTPUT = (
    _ROOT
    / "benchmarks"
    / "results"
    / "performance"
    / "2026-08-17-performance-parse-profile-v2.json"
)
_FIXTURE = _ROOT / "hardware" / "coppertone-buffer" / "coppertone-buffer.kicad_pcb"
_SCHEMA = "copper-mcp/performance-parse-profile/v2"
_TOP_FUNCTIONS = 12
_STAGE_LABELS = {
    "complete_read": "external:performance_parse_profile_v2.py:_parse_pipeline",
    "board_ir_conversion": ("copper_mcp:copper_mcp.adapters.kicad_board_ir:parse_kicad_bytes"),
    "sexpr_parse": "copper_mcp:copper_mcp.adapters.sexpr:parse_sexpr",
    "tokenization": "copper_mcp:copper_mcp.adapters.sexpr:_tokens",
    "model_conversion": "copper_mcp:copper_mcp.adapters.kicad_board_ir:convert",
}


def _parse_pipeline() -> str:
    conversion = parse_kicad_bytes(_FIXTURE.read_bytes(), _profile())
    if conversion.snapshot is None or conversion.diagnostics:
        raise RuntimeError("fixed parse fixture is outside the supported Board IR subset")
    return conversion.snapshot.snapshot_digest


def _profile_rows(profile: cProfile.Profile) -> list[dict[str, int | str]]:
    rows: list[dict[str, int | str]] = []
    for function, values in pstats.Stats(profile).stats.items():
        primitive_calls, total_calls, total_seconds, cumulative_seconds, _callers = values
        rows.append(
            {
                "cumulative_time_ns": round(cumulative_seconds * 1_000_000_000),
                "function": _redacted_label(function),
                "primitive_calls": primitive_calls,
                "self_time_ns": round(total_seconds * 1_000_000_000),
                "total_calls": total_calls,
            }
        )
    return sorted(
        rows,
        key=lambda item: (
            -int(item["cumulative_time_ns"]),
            -int(item["self_time_ns"]),
            str(item["function"]),
        ),
    )


def _stage_attribution(rows: list[dict[str, int | str]]) -> dict[str, Any]:
    by_label = {str(row["function"]): row for row in rows}
    missing = sorted(set(_STAGE_LABELS.values()) - set(by_label))
    if missing:
        raise RuntimeError("parse profile is missing a required cumulative stage")
    complete_ns = int(by_label[_STAGE_LABELS["complete_read"]]["cumulative_time_ns"])
    if complete_ns <= 0:
        raise RuntimeError("parse profile complete-read duration is invalid")
    stages: dict[str, dict[str, int | str]] = {}
    for stage, label in _STAGE_LABELS.items():
        row = by_label[label]
        cumulative_ns = int(row["cumulative_time_ns"])
        if cumulative_ns < 0 or cumulative_ns > complete_ns:
            raise RuntimeError("parse profile cumulative stage is outside the complete read")
        stages[stage] = {
            "cumulative_time_ns": cumulative_ns,
            "function": label,
            "share_of_complete_read_ppm": cumulative_ns * 1_000_000 // complete_ns,
        }
    return {
        "stages": stages,
        "values_are_nested_not_additive": True,
    }


def _scenario(*, warmups: int, samples: int) -> dict[str, Any]:
    for _ in range(warmups):
        _parse_pipeline()
    outputs: list[str] = []
    timings_ns: list[int] = []
    for _ in range(samples):
        started = time.perf_counter_ns()
        outputs.append(_parse_pipeline())
        timings_ns.append(time.perf_counter_ns() - started)
    if len(set(outputs)) != 1:
        raise RuntimeError("parse-heavy fixture did not replay deterministically")

    profile = cProfile.Profile()
    profile.enable()
    try:
        profiled_output = _parse_pipeline()
    finally:
        profile.disable()
    if profiled_output != outputs[0]:
        raise RuntimeError("profiled parse diverged from the unprofiled replay")
    rows = _profile_rows(profile)
    return {
        "fixture": {
            "bytes": _FIXTURE.stat().st_size,
            "id": "coppertone-complete-board-read",
            "sha256": _sha256(_FIXTURE.read_bytes()),
        },
        "hotspots_cumulative": rows[:_TOP_FUNCTIONS],
        "output_digest": _sha256(outputs[0].encode("ascii")),
        "stage_attribution_cumulative": _stage_attribution(rows),
        "timing_perf_counter_ns": _timing_summary(timings_ns),
    }


def _identity(
    *,
    warmups: int,
    samples: int,
    source_provenance: Mapping[str, bool | str],
) -> dict[str, Any]:
    return {
        "fixture_manifest": {
            "coppertone_complete_board_read": _sha256(_FIXTURE.read_bytes()),
        },
        "measurement_configuration": {
            "hotspot_limit": _TOP_FUNCTIONS,
            "samples": samples,
            "warmups": warmups,
        },
        "schema": _SCHEMA,
        "script_sha256": _sha256(_SCRIPT.read_bytes()),
        "source_provenance": dict(source_provenance),
        "support_script_sha256": _sha256(_SUPPORT_SCRIPT.read_bytes()),
    }


def _validate_report(document: Mapping[str, Any]) -> None:
    if document.get("schema") != _SCHEMA or not isinstance(document.get("identity"), dict):
        raise ValueError("performance parse profile schema is malformed")
    if document.get("identity_digest") != _sha256(_canonical_bytes(document["identity"])):
        raise ValueError("performance parse profile identity digest is malformed")
    source = document["identity"].get("source_provenance")
    if not isinstance(source, dict) or source.get("clean_worktree") is not True:
        raise ValueError("performance parse profile clean provenance is malformed")
    if source.get("git_head") != document.get("provenance", {}).get("git_head"):
        raise ValueError("performance parse profile source provenance is not bound")
    without_run_id = dict(document)
    run_id = without_run_id.pop("run_id", None)
    if run_id != _sha256(_canonical_bytes(without_run_id)):
        raise ValueError("performance parse profile run identifier is malformed")
    scenario = document.get("scenario")
    if not isinstance(scenario, dict) or set(scenario) != {
        "fixture",
        "hotspots_cumulative",
        "output_digest",
        "stage_attribution_cumulative",
        "timing_perf_counter_ns",
    }:
        raise ValueError("performance parse profile scenario is malformed")
    hotspots = scenario["hotspots_cumulative"]
    if not isinstance(hotspots, list) or not 1 <= len(hotspots) <= _TOP_FUNCTIONS:
        raise ValueError("performance parse profile hotspots are malformed")
    if any("/" in item["function"] or "\\" in item["function"] for item in hotspots):
        raise ValueError("performance parse profile leaks a path")
    attribution = scenario["stage_attribution_cumulative"]
    if (
        not isinstance(attribution, dict)
        or attribution.get("values_are_nested_not_additive") is not True
        or set(attribution.get("stages", {})) != set(_STAGE_LABELS)
    ):
        raise ValueError("performance parse profile attribution is malformed")


def build_report(*, warmups: int, samples: int) -> dict[str, Any]:
    source_provenance = _clean_source_provenance()
    identity = _identity(
        warmups=warmups,
        samples=samples,
        source_provenance=source_provenance,
    )
    run_started = time.monotonic_ns()
    document: dict[str, Any] = {
        "identity": identity,
        "identity_digest": _sha256(_canonical_bytes(identity)),
        "instrumentation": {
            "hotspots": "cProfile cumulative time; bounded redacted summaries only",
            "run_span": "time.monotonic_ns; operational span only, excluded from identity",
            "timings": "time.perf_counter_ns; unprofiled samples, excluded from identity",
        },
        "not_claimed": [
            "an acceleration, speedup, Rust, SIMD, GPU, DRC, fabrication, or hardware result",
            "a cross-machine timing comparison or stable nanosecond precision",
            "a general real-board population result or a public-contract change",
        ],
        "provenance": {
            **source_provenance,
            "machine": platform.machine() or "unknown",
            "python": platform.python_version(),
        },
        "scenario": _scenario(warmups=warmups, samples=samples),
        "schema": _SCHEMA,
    }
    document["monotonic_run_span_ns"] = time.monotonic_ns() - run_started
    document["run_id"] = _sha256(_canonical_bytes(document))
    _validate_report(document)
    return document


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=_DEFAULT_OUTPUT)
    parser.add_argument("--samples", type=_count, default=5)
    parser.add_argument("--warmups", type=_count, default=2)
    args = parser.parse_args(argv)

    report = build_report(warmups=args.warmups, samples=args.samples)
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
