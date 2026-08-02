# Reproducible Benchmark Artifacts

This directory contains append-only, machine-readable benchmark evidence referenced from the
[benchmark ledger](../docs/ledgers/benchmark-ledger.md). Each result records its exact source commit,
dirty-tree state, input digest, configuration, environment, raw samples, and content-addressed run
ID. Never replace a historical result; add a corrective run and link both records.

Board IR conversion results measure parsing, normalization, semantic validation, and canonical
snapshot construction. They do not measure autorouting, KiCad DRC, electrical correctness, or
fabrication readiness.

Reproduce the current CopperTone conversion fixture from a clean checkout with:

```bash
PYTHONPATH=src python scripts/benchmark_board_ir.py --iterations 7 --warmups 2
```
