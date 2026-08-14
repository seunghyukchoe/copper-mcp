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

## Single-author open-hardware boards, examined 2026-08-14

ADR-0107's follow-up named the candidate its rule leaves standing — "a single-author open-hardware
repository with a clear licence covering its own boards" — so ten were examined per item, and the
result is recorded here **so the next slice does not re-litigate them**
([B-114](../../docs/ledgers/benchmark-ledger.md),
[the intake sweep](../results/board-ir/2026-08-14-open-hardware-intake-sweep.json)).

**The licence question came out well and it did not help.** Eight of the ten are importable on
licence alone, and two of those carry the permission *inside the board file's own `title_block`*,
which is the strongest form of per-item evidence this rule can ask for. Every one of the ten also
clears the board-format-version gate once re-saved in KiCad 10.0.5 — the gate that refused 164 of
164 PCBench boards. **None of the ten converts**, and none converts with the `Edge.Cuts` curve gate
lifted in a scratch probe either.

| Repository | Licence | Covers its own boards? | Original format | Converts after a KiCad 10.0.5 re-save |
|---|---|---|---|---|
| [antmicro/m2-pcie-adapter](https://github.com/antmicro/m2-pcie-adapter) | Apache-2.0 | **Yes, stated** — README calls the contents "open hardware design files" and licenses the project Apache-2.0; the repository holds no firmware | `20241229` | No — `Edge.Cuts` outline curves |
| [antmicro/lpddr4-testbed](https://github.com/antmicro/lpddr4-testbed) | Apache-2.0 | Repository scope (single hardware design, no firmware) | `20241229` | No — `Edge.Cuts` outline curves |
| [antmicro/ov9281-camera-board](https://github.com/antmicro/ov9281-camera-board) | Apache-2.0 | Repository scope | `20241229` | No — copper text (its outline is four straight lines) |
| [TinyTapeout/tt-demo-pcb](https://github.com/TinyTapeout/tt-demo-pcb) | Apache-2.0 | **Yes, stated** — README licenses the PCB Apache-2.0 and the documentation CC0, by name | `20241229` | No — `Edge.Cuts` outline curves |
| [greatscottgadgets/amalthea-hardware](https://github.com/greatscottgadgets/amalthea-hardware) | CERN-OHL-P-2.0 | **Yes, in the board file** — `title_block` carries the copyright and "Licensed under the CERN-OHL-P v2" | `20221018` | No — `Edge.Cuts` outline curves |
| [greatscottgadgets/cynthion-hardware](https://github.com/greatscottgadgets/cynthion-hardware) | CERN-OHL-P-2.0 | Repository scope; the board's own licence field is an unsubstituted `${LICENSE}` template variable | `20221018` | No — `Edge.Cuts` outline curves |
| [greatscottgadgets/hackrf-pro](https://github.com/greatscottgadgets/hackrf-pro) | CERN-OHL-P-2.0 | Repository scope; `${LICENSE}` template variable again | `20241229` | No — copper layer kind |
| [keebio/bfo9000-pcb](https://github.com/keebio/bfo9000-pcb) | MIT | Repository scope (`LICENSE.txt` plus a README licence section) | `20240108` | No — `Edge.Cuts` outline curves |
| [machdyne/lakritz](https://github.com/machdyne/lakritz) | Custom "Lone Dynamics Open License" (non-OSI, `NOASSERTION`) | Names physical forms explicitly, **but** the README warns the KiCad files may carry third-party symbols and footprints — an unresolved per-item unknown inside the file that would ship | `20240108` | No — `Edge.Cuts` outline curves |
| [wntrblm/Castor_and_Pollux](https://github.com/wntrblm/Castor_and_Pollux) | CERN-OHL-P v2 (`NOASSERTION` at API level) | **Yes, per directory and in the board file** — `LICENSE.md` maps CERN-OHL-P v2 to `hardware/mainboard`, and the `title_block` repeats it | `20241229` | No — `Edge.Cuts` outline curves |

**What this changes about the intake rules: nothing, and that is the point.** Rule 1 worked — it
produced eight clean per-item determinations in one pass, including two that never needed repository
metadata at all. The blocker is one rule further down the pipeline than any of these rules reach.
A future import from any of these upstreams needs no new licence work; it needs `Edge.Cuts` curve
outlines **and** [ADR-0095](../../docs/adr/0095-copper-text-has-no-derivable-envelope.md)'s copper-text
envelope, and B-114 measures that the first without the second buys nothing. **No board byte from
any of these repositories is committed**, and re-saving one would be a derivative work requiring
recorded provenance for both the original and the derived bytes — which is why the sweep records
both digests for all ten.

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
