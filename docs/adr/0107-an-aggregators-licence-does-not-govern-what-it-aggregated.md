# ADR-0107: An aggregator's repository licence does not govern the data it aggregated

- Status: Accepted
- Date: 2026-08-14
- Owners: `@seunghyukchoe`
- Related: [D-199](../ledgers/decision-ledger.md), [R-153](../ledgers/risk-register.md),
  [B-110](../ledgers/benchmark-ledger.md),
  [issue #110](https://github.com/seunghyukchoe/copper-mcp/issues/110),
  [`benchmarks/corpora/README.md`](../../benchmarks/corpora/README.md),
  [open-baseline benchmarks v1](../research/open-baseline-benchmarks-v1.md)

## Context

`benchmarks/corpora/README.md` rule 1 says to check a corpus's licence before the first byte lands
in Git. Until now "check the licence" meant *read the repository's `LICENSE`*, and for the one
corpus imported so far — `dwiel/tscircuit-benchmark`, whose boards its own author generated — that
was the same question.

It is not the same question for a **corpus that aggregates other people's work**, and the plan's
next intended import was exactly that. PCBench is MIT: its `LICENSE` at
`dec3be75cbdef74787625f9043c7391cd473bb64` is the verbatim MIT text, `Copyright (c) 2023 PCBench`,
and the GitHub API reports `spdx_id: MIT`. Both this repository's research note and its corpora
README recorded that as "redistributable with attribution", and the audit plan (P1.1) budgeted an
import against it.

Reading the fetched tree rather than its `LICENSE` shows what the `LICENSE` covers. PCBench is a
scrape: `github_meta/*.json` are stored GitHub *search results* over 1,018 distinct repositories,
and every board directory carries a `metadata.json` naming its `source` repository and recording
that repository's **own** licence in a `licenses` field. PCBench itself tracks the fact that the
boards are not its to license. Across its advertised 164-board dataset those recorded licences are:
34 MIT, 26 "GNU", 19 recorded as an empty string, 17 recorded as `License not found`, 14 Creative
Commons, 12 CERN OHL, 8 Apache, 5 BSD, and a long tail — so **36 of 164 boards (22 %) have no
licence at all**, 57 name a copyleft or CERN-OHL family licence, and only 53 name a permissive one.
Over the full 1,182-directory tree, 624 have no recorded licence.

So the repository licence and the data licence disagree, and the repository licence is the one that
is easy to read. MIT here grants rights over PCBench's own contribution — its scripts, its RL
environment, its PCB-RDL serialisations — and grants nothing over board files PCBench had no right
to relicense. This project already reached the correct conclusion once, for PCBWorld, whose paper
says its real boards "retain the license of their source repository"; it reached the wrong one for
PCBench because PCBench states the same fact in per-item metadata instead of in prose.

The failure is not a mistake about MIT. It is a **check performed at the wrong granularity**, and
nothing in the intake rules said which granularity was required.

## Decision

Rule 1 of `benchmarks/corpora/README.md` is sharpened: for any corpus this project did not fetch
directly from the party that authored the data, the licence determination is made **per item**, from
the item's own provenance, and never from the aggregator's repository licence alone.

Concretely, before any third-party board byte lands in Git:

1. **Establish whether the upstream is an author or an aggregator.** Evidence of aggregation —
   scraped metadata, per-item `source` URLs, per-item licence fields, a paper describing collection
   — makes the repository `LICENSE` non-dispositive for the data, whatever it says.
2. **For an aggregator, the determination is per item**, made from the item's own upstream and
   verified against that upstream, not from the aggregator's record of it. The aggregator's
   per-item metadata is a *lead*, not the finding: it may be stale, and an empty field is not
   evidence of a permissive licence.
3. **An item with no licence is not importable.** Absence of a licence is all rights reserved. It
   is not an oversight to be worked around, and an aggregator's willingness to redistribute it is
   not a determination this project may borrow.
4. **Record the negative determinations too**, with their counts, so the next slice does not
   re-litigate a corpus that was already ruled out — and so a determination that later turns out to
   be wrong is falsifiable against a stated number.

The rule is stated so it cannot be satisfied by an absence: a corpus is importable only when a
*positive* per-item permission was observed for every item imported. "We found nothing prohibiting
it" is not a determination.

## Consequences

**PCBench is not importable as a whole, and this is what closes P1.1 negatively** ([D-199](../ledgers/decision-ledger.md)).
Its 164-board dataset cannot be committed under an MIT determination, and the 128 boards that do
record some permission cannot be committed on that record alone — each would need its own upstream
verified, its own copyright notice carried, and, for the copyleft and CERN-OHL entries, a licence
compatibility question this repository has no reason to open for benchmark input data.

**The research note and the corpora README were wrong and are corrected in place with a dated
qualification**, not retracted: the MIT reading of PCBench's `LICENSE` was accurate, the
redistribution conclusion drawn from it was not.

**What this costs.** The excessive-agency evaluation still has no externally authored `.kicad_pcb`
family, so issue #110's central residual stays open ([R-153](../ledgers/risk-register.md)). That is
the honest cost of the rule and not a reason to weaken it — an evaluation is not made more credible
by third-party data this project had no right to redistribute.

**What it does not cost.** The determination is cheap once the granularity is right: PCBench
records the per-item licences itself, so the census took one pass over 1,182 metadata files. The
rule adds a step, not a project.

**Follow-up.** A genuinely redistributable `.kicad_pcb` family is still wanted. The candidates this
rule leaves standing are a single-author open-hardware repository with a clear licence covering its
own boards, or a small set of individually verified upstreams — either of which is a narrower and
better-founded import than PCBench would have been. Separately,
[B-110](../ledgers/benchmark-ledger.md) shows the licence is not currently the binding constraint:
**no PCBench board converts anyway**.

## Alternatives considered

**Import the 53 of 164 boards whose PCBench metadata records a permissive licence.**
Rejected on two independent grounds. It would take the aggregator's record as the determination,
which is precisely the granularity error one level down — the field is a 2023 scrape of a GitHub API
response, and neither the upstream repository nor its `LICENSE` was re-read. And it is moot:
[B-110](../ledgers/benchmark-ledger.md) measured 0 of 164 converting, so the subset that is both
verifiably licensed *and* usable today is empty, and the corpora README's own subset rule forbids
choosing a committed subset by anything other than a rule fixed in advance.

**Import under a fair-use or research-use theory.** Rejected. This repository is Apache-2.0 and
publishes sdists; a benchmark corpus travels with the package to every consumer. That is
redistribution, and it is not this project's call to make on 36 boards whose authors said nothing.

**Keep the MIT determination and note the risk.** Rejected as exactly the shape this project keeps
having to correct: a claim carried forward with a caveat attached, where the claim is the thing that
gets cited and the caveat is the thing that does not. The determination is wrong; it is corrected.

**Fetch at runtime instead of committing, so nothing is redistributed.** Not rejected in principle —
it is what rule 1 already prescribes for an unredistributable corpus, and it is why the digest
manifest in [B-110](../ledgers/benchmark-ledger.md) is committed while no board is. It is not
pursued further here only because the conversion rate makes the fetch worthless today.
