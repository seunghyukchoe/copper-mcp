# Reproducible Benchmark Artifacts

This directory contains append-only, machine-readable benchmark evidence referenced from the
[benchmark ledger](../docs/ledgers/benchmark-ledger.md). Each result records its exact source commit,
dirty-tree state, input digest, configuration, environment, raw samples, and content-addressed run
ID. Never replace a historical result; add a corrective run and link both records.

Board IR conversion results measure parsing, normalization, semantic validation, and canonical
snapshot construction. They do not measure autorouting, KiCad DRC, electrical correctness, or
fabrication readiness.

The [audio capability catalog](audio/) is a checked functional corpus rather than a performance
result. Its executable artifacts are independently authored or explicitly open hardware; external
DIY sites remain reference-only metadata and are never fetched by the runner. Use
`make benchmark-audio` to verify current Board IR and route-preview capabilities and typed limits.
The companion original RC Circuit Intent fixture is checked with `make check-circuit-intents`, which
verifies its schema, canonical digest, and byte-deterministic in-memory KiCad schematic derivative.

The [external corpora](corpora/) directory holds third-party benchmark input whose licence was
checked and recorded before any file was committed. A corpus that may not be redistributed gets a
digest manifest and a fetch script instead of files. Route the committed MIT-licensed
SimpleRouteJson corpus with `make benchmark-external-corpus`; the run is offline and verifies every
sample against its recorded digest before importing it. That result measures the existing
single-layer router on boards this project did not author and records the refusals alongside the
successes — see [B-088](../docs/ledgers/benchmark-ledger.md) and the
[research note](../docs/research/open-baseline-benchmarks-v1.md) for what it does and does not
claim, including that the cross-router baseline comparison is recorded as `not_run`.

Reproduce the current CopperTone conversion fixture from a clean checkout with:

```bash
PYTHONPATH=src python scripts/benchmark_board_ir.py --iterations 7 --warmups 2
```
