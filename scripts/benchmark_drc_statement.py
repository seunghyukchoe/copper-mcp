#!/usr/bin/env python3
"""Measure deterministic, redacted in-toto Statement construction.

This benchmark exercises the unsigned payload boundary only.  It does not spawn
KiCad, sign an envelope, or make a provenance or DRC-quality claim.
"""

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
from typing import Any

from copper_mcp.attestation import build_candidate_drc_statement, canonical_statement_bytes
from copper_mcp.mcp_contracts import InTotoDrcStatementContract
from copper_mcp.models import DrcSummary

_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_OUTPUT = _ROOT / "benchmarks/results/routing/2026-08-05-drc-statement.json"


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


def _digest(fill: str) -> str:
    return f"sha256:{fill * 64}"


def _statement() -> dict[str, Any]:
    patched = _digest("c")
    context = _digest("d")
    return build_candidate_drc_statement(
        candidate_id=_digest("a"),
        candidate_base_revision=_digest("b"),
        source_revision=_digest("e"),
        patched_board_revision=patched,
        patched_drc_context_revision=context,
        summary=DrcSummary(
            base_revision=patched,
            drc_context_revision=context,
            kicad_version="10.0.5",
            drc_schema="https://schemas.kicad.org/drc.v1.json",
            coordinate_units="mm",
            error_count=0,
            warning_count=0,
            exclusion_count=0,
            ignored_check_count=0,
            unconnected_count=0,
            violation_type_counts={},
            passed=True,
        ),
    )


def _run(repetitions: int) -> dict[str, Any]:
    if repetitions < 1 or repetitions > 10_000:
        raise ValueError("repetitions must be between 1 and 10000")
    expected = _statement()
    expected_bytes = canonical_statement_bytes(expected)
    serialization_samples: list[int] = []
    schema_valid = 0
    deterministic = True
    subject_binding = True
    material_binding = True
    redacted = True
    for _ in range(repetitions):
        started = time.perf_counter_ns()
        statement = _statement()
        rendered = canonical_statement_bytes(statement)
        serialization_samples.append(time.perf_counter_ns() - started)
        InTotoDrcStatementContract.model_validate(statement)
        schema_valid += 1
        deterministic = deterministic and rendered == expected_bytes
        subject_binding = subject_binding and (
            statement["subject"][0]["digest"]["sha256"] == "a" * 64
        )
        names = [item["name"] for item in statement["predicate"]["materials"]]
        material_binding = material_binding and names == [
            "board-ir-base",
            "board-source",
            "patched-board",
            "patched-drc-context",
        ]
        encoded = rendered.decode("utf-8")
        redacted = redacted and all(
            value not in encoded
            for value in (".kicad_pcb", "Net-AUDIO-OUT", "UUID-PRIVATE-FINDING")
        )
    return {
        "repetitions": repetitions,
        "schema_valid": schema_valid == repetitions,
        "deterministic_bytes": deterministic,
        "subject_candidate_binding": subject_binding,
        "material_revision_binding": material_binding,
        "redacted_payload": redacted,
        "statement_bytes": len(expected_bytes),
        "median_build_and_serialize_ns": statistics.median(serialization_samples),
        "signature_count": 0,
        "dsse_envelope_emitted": False,
        "kicad_invoked": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=_DEFAULT_OUTPUT)
    parser.add_argument("--repetitions", type=int, default=128)
    args = parser.parse_args()
    payload: dict[str, Any] = {
        "schema": "copper-mcp/benchmark/drc-statement/v1",
        "date_utc": "2026-08-05",
        "source_commit": _git_commit(),
        "environment": {"platform": platform.platform(), "python": sys.version.split()[0]},
        "configuration": {"predicate_type": "https://in-toto.io/attestation/link/v0.3"},
        "metrics": _run(args.repetitions),
        "not_claimed": [
            "DSSE signature or verifier coverage",
            "whole-board KiCad DRC quality",
            "provenance or fabrication readiness",
            "remote transport or persistence",
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
