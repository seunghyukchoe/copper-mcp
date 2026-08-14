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
| [PCBench](https://github.com/PCBench/PCBench) | ~~MIT, © 2023 PCBench. Redistributable with attribution.~~ **Corrected 2026-08-14 — not redistributable.** The repository `LICENSE` is verbatim MIT, but PCBench is a scrape of 1,018 other repositories and its boards retain their own upstream licences, which PCBench itself records per board. Of its advertised 164: **36 have no licence at all**, 57 are copyleft or CERN-OHL, 53 permissive. The MIT grant covers PCBench's own code, not the board data — the same fact that already disqualified PCBWorld's real boards, above. See [ADR-0107](../../docs/adr/0107-an-aggregators-licence-does-not-govern-what-it-aggregated.md) and [D-199](../../docs/ledgers/decision-ledger.md). | **Not redistributed**, and separately unusable today: [B-110](../../docs/ledgers/benchmark-ledger.md) measured **0 of 164 boards converting**, all refusing `unsupported.version` because the corpus predates the one board format version this server accepts. Digests for all 164 boards in both stored variants are recorded in [the conversion census](../results/board-ir/2026-08-14-pcbench-conversion-census.json); no board file is copied. |

## Rules

1. **Check the licence before the first byte lands in Git.** A corpus whose licence does not permit
   redistribution gets a fetch script and a digest manifest, never files.
   **Check it at the granularity of the item, not the repository, whenever the upstream aggregated
   someone else's work** ([ADR-0107](../../docs/adr/0107-an-aggregators-licence-does-not-govern-what-it-aggregated.md)).
   An aggregator's `LICENSE` covers the aggregator's own contribution and cannot relicense what it
   collected — PCBench above is verbatim MIT and still not redistributable. Evidence of aggregation
   (scraped metadata, per-item `source` URLs, per-item licence fields) makes the repository licence
   non-dispositive, an absent per-item licence is all rights reserved rather than an oversight, and
   the aggregator's own record of an item's licence is a lead to verify upstream, never the
   determination itself.
2. **Record digests for the whole upstream set**, not only the part committed, so a fetched
   remainder is verifiable against the same manifest.
3. **State the subset rule.** A committed subset is chosen by a rule fixed in advance (a lexical
   prefix, a size cap) and never by which files produced better numbers.
4. **Keep provenance limits next to the data.** Whether a corpus is human-designed or generated,
   and whether any router was in the loop when it was built, changes what a measurement on it can
   claim. Those facts live in each corpus's `ATTRIBUTION.md`.
