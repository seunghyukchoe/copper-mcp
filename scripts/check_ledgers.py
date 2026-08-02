#!/usr/bin/env python3
"""Check that required project ledgers exist and remain structurally usable."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = {
    "docs/ledgers/decision-ledger.md": "# Decision Ledger",
    "docs/ledgers/risk-register.md": "# Risk Register",
    "docs/ledgers/release-ledger.md": "# Release Ledger",
    "docs/ledgers/benchmark-ledger.md": "# Benchmark Ledger",
    "docs/ledgers/security-ledger.md": "# Security Review Ledger",
}


def main() -> int:
    failures: list[str] = []
    for relative, heading in REQUIRED.items():
        path = ROOT / relative
        if not path.is_file():
            failures.append(f"missing {relative}")
            continue
        if heading not in path.read_text(encoding="utf-8"):
            failures.append(f"{relative} is missing heading {heading!r}")
    if failures:
        raise SystemExit("Ledger check failed:\n- " + "\n- ".join(failures))
    print("Ledger check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
