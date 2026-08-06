# External benchmark corpora

Every directory here is third-party input data with an explicit, reviewed licence. Nothing is
committed before its licence is checked, and the determination — including the URLs it was read
from — is recorded in
[`docs/research/open-baseline-benchmarks-v1.md`](../../docs/research/open-baseline-benchmarks-v1.md).

| Corpus | Licence | Committed here | Runner |
|---|---|---|---|
| [`tscircuit-benchmark`](tscircuit-benchmark/) | MIT, © 2026 Zach Dwiel | 20 of 36 boards, plus digests for all 36 | `scripts/benchmark_simple_route_json_corpus.py` |

Corpora that were reviewed and **not** committed, with the reason:

| Corpus | Determination | Consequence |
|---|---|---|
| [tscircuit/autorouting](https://github.com/tscircuit/autorouting) | **No licence at all** — no `LICENSE` file, no `license` key in `package.json`, no licence statement in `README.md` or `BENCHMARKS.md`. All rights reserved by default. Repository archived 2025-08-15. | Not redistributed. Its format specification is cited; none of its files are copied. |
| PCBWorld (arXiv:2607.05915) | Synthetic sets are CC-BY 4.0; the 679 real boards **retain their own upstream repository licences** and are heterogeneous. The datasets are supplementary material and have no public host yet. | Cannot be fetched at all today; recorded as announced, not released. |
| [PCBench](https://github.com/PCBench/PCBench) | MIT, © 2023 PCBench. Redistributable with attribution. | Not yet imported: it ships `.kicad_pcb` boards rather than SimpleRouteJson, so it belongs to the KiCad intake path, not this adapter. |

## Rules

1. **Check the licence before the first byte lands in Git.** A corpus whose licence does not permit
   redistribution gets a fetch script and a digest manifest, never files.
2. **Record digests for the whole upstream set**, not only the part committed, so a fetched
   remainder is verifiable against the same manifest.
3. **State the subset rule.** A committed subset is chosen by a rule fixed in advance (a lexical
   prefix, a size cap) and never by which files produced better numbers.
4. **Keep provenance limits next to the data.** Whether a corpus is human-designed or generated,
   and whether any router was in the loop when it was built, changes what a measurement on it can
   claim. Those facts live in each corpus's `ATTRIBUTION.md`.
