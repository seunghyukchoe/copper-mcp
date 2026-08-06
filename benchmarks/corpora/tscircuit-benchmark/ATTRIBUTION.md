# Attribution — tscircuit-benchmark SimpleRouteJson corpus

The files under [`samples/`](samples/) are redistributed unmodified from
[dwiel/tscircuit-benchmark](https://github.com/dwiel/tscircuit-benchmark), commit
`be36518b5bf51755dae92c230061ab3cf4e3e063` (branch `master`).

- **Copyright** © 2026 Zach Dwiel
- **Licence** MIT — see [`LICENSE`](LICENSE), the upstream
  [`LICENSE`](https://github.com/dwiel/tscircuit-benchmark/blob/master/LICENSE)
- **Licensing determination** reviewed on 2026-08-06. MIT permits redistribution and modification
  provided the copyright notice and permission notice travel with the copies, which this directory
  does. No CopperMCP file is relicensed by their presence: CopperMCP itself remains Apache-2.0 and
  these inputs are data, not linked code.

## What is committed here, and what is not

Upstream ships 36 SimpleRouteJson boards. This directory commits the **first 20 in upstream lexical
order** (`ts01_led` … `ts20_esp32_wifi`) and records the SHA-256 of **all 36** in
[`manifest.json`](manifest.json). The subset rule is a stated prefix, not a selection on results.

Upstream orders its samples roughly by growing component count, so the committed prefix is the
**easier half** of the corpus. Any number measured on it is a statement about that prefix and must
not be read as a whole-corpus result. Fetch the remaining 16 with
`scripts/fetch_simple_route_json_corpus.py` and rerun the harness to measure the rest; those runs
are environment-dependent and are recorded as such.

## Provenance limits that constrain what a measurement here can claim

These are stated by upstream and matter more than the licence:

1. **The boards are LLM-generated, not human-engineered production hardware.** Upstream's README
   describes them as "36 real-world PCB designs … converted from circuit JSON exported by
   [pcbgen](https://github.com/dwiel/ai-pcb-experiment) … Generated from plain-English specs by an
   LLM, then routed with Freerouting." A result here generalises to LLM-generated tscircuit boards
   and to nothing else.
2. **The corpus was constructed with FreeRouting in the loop, so it is not a neutral yardstick for
   FreeRouting.** Upstream's own table reports FreeRouting "clean" on 35 of 36 boards. A comparison
   that uses this corpus to score CopperMCP *against FreeRouting* would be measuring a set that
   FreeRouting helped define.
3. **Narrow coverage.** Every board is 2-layer, 3–35 components. There is no multi-layer, BGA,
   differential-pair, or width-constrained case here at all.
4. **Upstream deliberately strips `externallyConnectedPointIds`** so benchmark nets are not
   pre-shorted by off-board equivalence metadata. That is a good anti-leakage measure and the
   import adapter relies on it: obstacle-to-net ownership is derived from `connectedTo` against the
   points the connections actually name.

## Not modified

No file under `samples/` is edited, reformatted, or re-serialised. The digests in
`manifest.json` are the upstream bytes, and `scripts/check_ledgers.py`-adjacent tooling reads them
rather than trusting the filenames.
