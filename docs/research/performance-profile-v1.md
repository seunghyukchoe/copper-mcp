# Performance profile v1: evidence before acceleration

## Question

Before considering Rust, SIMD, GPU execution, or another acceleration mechanism, what bounded,
repeatable measurement can identify the Python-side hotspots in CopperMCP's routing, placement,
and Circuit Scene pipelines without changing their public contracts?

## Decision

`scripts/performance_profile_v1.py` profiles three committed local fixtures:

- file-backed two-pad route preview (input capture, Board IR conversion, and deterministic routing);
- footprint-rotation placement pipeline (conversion, placement view, intent validation, and bounded
  placement solver);
- file-backed Circuit Scene observation (confined read, conversion, and scene projection).

Before reading a fixture, the baseline runner requires Git and a fully clean repository status,
including untracked files. This is intentionally stricter than a tracked-only check: an untracked
helper, fixture, or importable source file cannot silently affect a supposedly committed baseline.
The verified `git_head` and `clean_worktree: true` are copied into both source provenance and the
canonical `identity` object. `identity_digest` binds that source provenance, fixture bytes, fixed
seed, source-script digest, warmup/sample counts, and hotspot limit. Timing samples, profiler
costs, operational span, machine, and Python version remain outside identity because they are
environmental observations rather than deterministic inputs.

Each scenario first performs warmups, then records multiple **unprofiled** `time.perf_counter_ns`
samples, checking that the result digest is identical for every sample. It then runs exactly one
separate `cProfile` pass and records only eight or fewer cumulative-time rows. Absolute source
paths are converted to stable `copper_mcp:module:function`, `builtins:function`, or
`external:basename:function` labels; raw profiler files and tracebacks are never persisted. The
profile run is not used as a timing sample because instrumentation changes execution cost.

`time.monotonic_ns` records only total operational run span. It is explicitly separate from the
`perf_counter_ns` timing samples and from deterministic identity. Python documents that monotonic
clock differences cannot go backwards and are unaffected by system-clock updates; it documents
`perf_counter` as the highest-resolution short-duration clock and the `_ns` forms as avoiding
float precision loss. The Python profiler documentation describes `cProfile` as the deterministic
profiler and `pstats` cumulative sort as including time spent in sub-functions.

Primary sources:

- Python `time` clock semantics: https://docs.python.org/3/library/time.html#time.monotonic_ns
- Python `perf_counter_ns`: https://docs.python.org/3/library/time.html#time.perf_counter_ns
- Python `cProfile`: https://docs.python.org/3/library/profile.html#module-cProfile
- Python `pstats` cumulative ordering: https://docs.python.org/3/library/profile.html#pstats.Stats.sort_stats
- KiCad command-line interface scope (kept out of this in-process profile): https://docs.kicad.org/master/en/cli/cli.html
- KiCad PCB Editor workflow reference: https://docs.kicad.org/master/en/pcbnew/pcbnew.html

## Acceleration seam and limits

The profile is intentionally a measurement seam only. A future acceleration candidate must preserve
the existing immutable Board IR, `RouteRequest`/candidate verification, placement legalizer, and
Circuit Scene output contracts. It must first reproduce the fixed output digests in this profile,
then show a separately recorded improvement against a clean baseline on the same fixture manifest.
This increment adds no Rust/GPU code, no public contract, no KiCad CLI invocation, no DRC result,
and no routing, placement, fabrication, or hardware performance claim.
