#!/usr/bin/env python3
"""Measure deterministic obstacle-query reduction from the routing spatial index."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import statistics
import subprocess
import sys
import time
from pathlib import Path

from copper_mcp.routing.spatial_index import ConservativeSpatialIndex, SpatialIndexEntry

_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_OUTPUT = _ROOT / "benchmarks/results/routing/2026-08-05-spatial-index.json"


def _git_commit() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],  # noqa: S607 - repository-local executable
            cwd=_ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _entries(count: int) -> tuple[SpatialIndexEntry[int], ...]:
    entries: list[SpatialIndexEntry[int]] = []
    for index in range(count):
        column = index % 32
        row = index // 32
        min_x = 1_000 + column * 3_000
        min_y = 1_000 + row * 3_000
        # The short query corridor crosses a small fraction of the board objects. The pattern
        # is fixed so CI can compare relation counts across commits without random noise.
        entries.append(
            SpatialIndexEntry(
                ordinal=index,
                bounds=(min_x, min_y, min_x + 900, min_y + 900),
                value=index,
            )
        )
    return tuple(entries)


def _queries(count: int) -> tuple[tuple[int, int, int, int], ...]:
    return tuple(
        (2_000 + index * 750, 13_000, 2_450 + index * 750, 13_500) for index in range(count)
    )


def _legacy_count(
    entries: tuple[SpatialIndexEntry[int], ...], query: tuple[int, int, int, int]
) -> tuple[int, tuple[int, ...]]:
    hits: list[int] = []
    for entry in entries:
        if (
            entry.bounds[0] <= query[2]
            and query[0] <= entry.bounds[2]
            and entry.bounds[1] <= query[3]
            and query[1] <= entry.bounds[3]
        ):
            hits.append(entry.ordinal)
    return len(entries), tuple(hits)


def _canonical_bytes(document: dict[str, object]) -> bytes:
    return json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _run(repetitions: int, entry_count: int, query_count: int) -> dict[str, object]:
    entries = _entries(entry_count)
    queries = _queries(query_count)
    index = ConservativeSpatialIndex(entries, min_index_entries=0)
    if not index.indexed:
        raise RuntimeError("benchmark fixture unexpectedly selected linear fallback")

    legacy_relations = 0
    indexed_relations = 0
    exact_matches = 0
    for query in queries:
        tested, hits = _legacy_count(entries, query)
        legacy_relations += tested
        indexed = index.query(query)
        indexed_relations += len(indexed)
        exact_matches += int(tuple(indexed) == hits)

    legacy_samples: list[int] = []
    indexed_samples: list[int] = []
    for _ in range(repetitions):
        started = time.perf_counter_ns()
        for query in queries:
            for entry in entries:
                if (
                    entry.bounds[0] <= query[2]
                    and query[0] <= entry.bounds[2]
                    and entry.bounds[1] <= query[3]
                    and query[1] <= entry.bounds[3]
                ):
                    pass
        legacy_samples.append(time.perf_counter_ns() - started)

        started = time.perf_counter_ns()
        for query in queries:
            index.query(query)
        indexed_samples.append(time.perf_counter_ns() - started)

    return {
        "benchmark": "routing-conservative-spatial-index-v1",
        "configuration": {
            "entry_count": entry_count,
            "query_count": query_count,
            "repetitions": repetitions,
            "index_policy": "uniform-grid-with-linear-fallback-v1",
        },
        "environment": {
            "machine": platform.machine(),
            "platform": platform.platform(),
            "python": sys.version.split()[0],
        },
        "metrics": {
            "deterministic_replays": exact_matches,
            "indexed": index.indexed,
            "bucket_count": index.bucket_count,
            "cell_size_nm": index.cell_size_nm,
            "legacy_relation_checks": legacy_relations,
            "indexed_relation_checks": indexed_relations,
            "relation_reduction_ratio": round(1 - indexed_relations / legacy_relations, 6),
            "median_legacy_query_ns": statistics.median(legacy_samples),
            "median_indexed_query_ns": statistics.median(indexed_samples),
            "median_speedup": round(
                statistics.median(legacy_samples) / statistics.median(indexed_samples), 3
            ),
            "exact_query_matches": exact_matches,
            "queries": len(queries),
        },
        "not_claimed": [
            "multi-net congestion optimization",
            "FreeRouting parity",
            "KiCad DRC or fabrication readiness",
            "wall-clock performance outside this fixture and machine",
        ],
        "source_commit": _git_commit(),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=_DEFAULT_OUTPUT)
    parser.add_argument("--repetitions", type=int, default=7)
    parser.add_argument("--entries", type=int, default=512)
    parser.add_argument("--queries", type=int, default=256)
    args = parser.parse_args()
    if min(args.repetitions, args.entries, args.queries) < 1:
        parser.error("repetitions, entries, and queries must be positive")
    report = _run(args.repetitions, args.entries, args.queries)
    canonical = dict(report)
    report["run_id"] = "sha256:" + hashlib.sha256(_canonical_bytes(canonical)).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
