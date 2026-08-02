# Project Charter

## Mission

Build a trustworthy, local-first, open platform for PCB automation in which deterministic geometry
and validation are accessible through conventional APIs, KiCad, MCP clients, and optional learned
policies.

## Primary users

- PCB designers who want reviewable automation rather than an opaque one-click result.
- EDA and optimization researchers who need reproducible, engine-checked experiments.
- AI-tool developers who need a safe PCB action surface.
- Open hardware teams that need local processing and auditable provenance.

## In scope

- KiCad-native inspection, candidate preview, validation, and explicit application.
- Deterministic placement/routing baselines and incremental rerouting.
- MCP tools and resources over stable application services.
- Optional policy plugins for net ordering, corridors, placement, repair, and objective weights.
- Reproducible benchmarks and documented dataset provenance.
- CPU-first optimization with optional heterogeneous acceleration after profiling.

## Non-goals

- Allowing an LLM to write unchecked copper or overwrite a board directly.
- Claiming that DRC-clean automatically means SI-, PI-, EMC-, thermal-, or manufacturing-safe.
- Uploading designs to a hosted model by default.
- Reimplementing the entire KiCad editor or schematic environment.
- Optimizing benchmark scores by weakening rules or excluding failures.

## Success measures

Correctness comes first: clean connectivity and zero hard DRC errors. Secondary measures include
human cleanup time, completion, vias, length, skew, layer use, runtime, memory, determinism, and
eventually SI/PI/thermal/DFM proxies. Results must be reported on held-out project families.
