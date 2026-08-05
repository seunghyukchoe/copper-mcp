# Held-out audio project-family evaluation

**Review date:** 2026-08-05

This note defines the first project-family separation for CopperMCP's placement, routing, and
inspection evaluation. It is a conservative, reproducible protocol rather than an audio-quality
claim.

## Licence and source boundary

Elliott Sound Products and diyAudioProjects are useful to identify broad public project categories,
but neither is treated as an open benchmark corpus. CopperMCP's existing intake review classifies
both as `reference-only`. The diyAudioProjects terms explicitly limit any copied content to one
offline personal/non-commercial copy and prohibit reproduction or distribution without written
permission ([terms, proprietary rights](https://www.diyaudioprojects.com/tos.htm#proprietary-rights)).
The Elliott catalog and disclaimer remain a reference-only source because no independently verified
compatible licence for its project material has been recorded ([catalog](https://sound-au.com/p-cat.htm),
[disclaimer](https://sound-au.com/disclaimer.htm)).

Accordingly, this evaluation contains an independently authored Apache-2.0 fixture only. Its
generic coupled-signal-chain category is a test taxonomy, not a reconstruction of any external
project. No third-party layout, schematic, artwork, values, BOM, prose, image, or download is
included. See the existing [audio intake review](audio-circuit-benchmarks.md) for the same policy.

## Split protocol

`tests/fixtures/benchmarks/heldout-audio/split.json` predeclares three mutually exclusive
partitions:

- training: the existing original `passive-rc-low-pass` family;
- tuning: empty, because this reference run has no learned model or tunable policy; and
- held-out: a new original `ac-coupled-signal-chain` family.

The evaluator rejects duplicate fixture hashes across partitions, requires the held-out hash to
match its provenance record, and reads only the held-out board. It does not open the training
fixture, choose settings from observed held-out results, train a model, invoke a network, or write
the source board. Before a learned policy is admitted, it must use a separately frozen train/tune
protocol; this fixture cannot become a tuning sample retroactively.

## Metrics and interpretation

The evaluator runs the unmodified deterministic services and records:

| Surface | Metrics | Meaning / limit |
|---|---|---|
| Inspection | supported flag, Board IR object counts, snapshot digest | parser coverage only |
| Placement | status, legal candidate count, initial/best same-net Manhattan proxy, evaluations | legalizer-backed proxy only; not DRC or placement quality |
| Routing | attempted/routed nets, completion fraction, candidate wire length, router expansions and obstacle checks | candidate-preview work/completion only; no global routing or quality claim |
| Replay | exact per-run signatures over repeated runs | deterministic behavior under fixed inputs, not correctness |

The predeclared primary comparison metrics for a future policy are routed-net completion fraction
and total router expansions. Any quality claim needs a frozen baseline and policy evaluated on a
larger held-out corpus, no regression in completion or deterministic validation, and separate
KiCad DRC evidence. This v1 run makes **no** placement/routing improvement, electrical,
manufacturing, DRC, ERC, schematic, fabrication, or hardware-performance claim.
