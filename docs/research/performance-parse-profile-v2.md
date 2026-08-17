# Performance parse profile v2: measure the complete read before acceleration

## Question

B-068 profiles three small committed service fixtures, but the later fill-budget calibration
observed `20.9 s` of a `24.2 s` complete read on its largest external board inside the parse that
the original profile does not represent. What clean-checkout measurement can attribute a complete
read by stage without importing a private board or changing a public contract?

## Predeclared prediction and stop rule

The committed `hardware/coppertone-buffer/coppertone-buffer.kicad_pcb` fixture is the largest
redistributable KiCad board in this checkout at `166,070` bytes. Before recording the evidence, the
prediction is:

1. every warmup, unprofiled sample, and profiled pass returns one identical Board IR snapshot
   digest;
2. `parse_sexpr` is the largest named child stage and accounts for at least `500,000 ppm` (50%) of
   the complete read's cumulative instrumented time;
3. model conversion remains a distinct, smaller cumulative stage; and
4. no timing value enters deterministic identity.

If condition 1 or 4 fails, the artifact is invalid. If condition 2 or 3 fails, record the result and
stop: do not select a parser optimization merely because #87 was named as acceleration work.
Instrumentation perturbs timings, and cumulative rows are nested, so they must never be summed.

## Measurement contract

`scripts/performance_parse_profile_v2.py` runs two warmups, five independent unprofiled
`perf_counter_ns` samples, and one separate `cProfile` pass. It profiles the full local sequence
`Path.read_bytes()` → `parse_kicad_bytes()` → S-expression parsing/tokenization → typed Board IR
conversion and snapshot construction. The bounded stage vocabulary is:

- complete read;
- Board IR conversion entry;
- S-expression parse;
- tokenization; and
- typed model conversion.

Each stage records a cumulative duration and integer parts-per-million share of the complete read.
The report explicitly marks those values nested and non-additive. It stores at most twelve redacted
cumulative hotspot rows, never an absolute path or raw profiler dump.

The deterministic identity binds the fixture bytes, measurement counts, hotspot limit, clean Git
head, this script, and the B-068 support script. Environmental timing, run span, machine, and Python
version remain outside identity. The report is self-digested and refuses a tracked or untracked
dirty tree before reading the fixture.

## Scope

This profile can select the next experiment; it cannot establish a speedup. It carries no Rust,
SIMD, GPU, KiCad CLI, DRC, route-quality, placement-quality, fabrication, electrical, or hardware
claim. One original audio board is not a population, and its absolute timing is not comparable
across machines.

The existing B-068 primary-source basis remains applicable: Python's documented `perf_counter_ns`,
`monotonic_ns`, `cProfile`, and cumulative `pstats` semantics. This increment changes no production
module or published contract.
