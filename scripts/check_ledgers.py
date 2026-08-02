#!/usr/bin/env python3
"""Check that required project ledgers exist and remain structurally usable."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = {
    "docs/ledgers/decision-ledger.md": "# Decision Ledger",
    "docs/ledgers/risk-register.md": "# Risk Register",
    "docs/ledgers/release-ledger.md": "# Release Ledger",
    "docs/ledgers/benchmark-ledger.md": "# Benchmark Ledger",
    "docs/ledgers/security-ledger.md": "# Security Review Ledger",
}
MAX_BENCHMARK_BYTES = 2_000_000


def _reject_json_constant(value: str) -> Any:
    raise ValueError(f"unsupported JSON constant: {value}")


def _parse_finite_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError(f"non-finite JSON number: {value}")
    return parsed


def _check_benchmark_artifacts(failures: list[str]) -> None:
    results = ROOT / "benchmarks" / "results"
    for path in sorted(results.rglob("*.json")):
        relative = path.relative_to(ROOT).as_posix()
        try:
            payload = path.read_bytes()
        except OSError as error:
            failures.append(f"{relative} cannot be read: {error}")
            continue
        if len(payload) > MAX_BENCHMARK_BYTES:
            failures.append(f"{relative} exceeds the benchmark artifact size limit")
            continue
        try:
            report = json.loads(
                payload,
                parse_constant=_reject_json_constant,
                parse_float=_parse_finite_float,
            )
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
            failures.append(f"{relative} is not strict JSON: {error}")
            continue
        if not isinstance(report, dict):
            failures.append(f"{relative} must contain one JSON object")
            continue
        run_id = report.pop("run_id", None)
        expected = (
            "sha256:"
            + hashlib.sha256(
                json.dumps(
                    report,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode()
            ).hexdigest()
        )
        if run_id != expected:
            failures.append(f"{relative} run_id does not match its canonical report content")


def main() -> int:
    failures: list[str] = []
    for relative, heading in REQUIRED.items():
        path = ROOT / relative
        if not path.is_file():
            failures.append(f"missing {relative}")
            continue
        if heading not in path.read_text(encoding="utf-8"):
            failures.append(f"{relative} is missing heading {heading!r}")
    _check_benchmark_artifacts(failures)
    if failures:
        raise SystemExit("Ledger check failed:\n- " + "\n- ".join(failures))
    print("Ledger check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
