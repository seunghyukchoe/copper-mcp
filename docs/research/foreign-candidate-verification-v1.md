# Foreign candidate verification: how checking layers treat geometry they did not produce

Research note for the first slice of issue #99 (M5: position CopperMCP as the verification
harness for ML autorouters). Surveyed 2026-08-06.

## The strategic moment

The tscircuit ecosystem released **Z01**, one million autorouting samples "designed for training
vision models on 2-layer printed circuit routing tasks"
([blog.autorouting.com](https://blog.autorouting.com/p/were-releasing-z01-1-million-autorouting)).
The release is explicit about what it defers: the samples contain "zero pads, keepouts or other
obstacles", and the authors state that "competing concerns with obstacle avoidance, DRC
compliance, impedance matching etc." are dependent on solving the core multi-agent pathfinding
problem first — that is, the ML proposer is being trained on the *proposal* half of the problem
while the *disposal* half (obstacle avoidance, clearance, DRC) is deliberately out of the
dataset. The stated purpose is "to enable the development of vision models that can prove the
viability of autorouting with vision models, not to build a full autorouting pipeline."

The deferred half is exactly CopperMCP's invariant: AI proposes, deterministic code disposes
(ADR-0001). A verification seam that accepts *anyone's* proposal and either verifies it or
refuses makes CopperMCP the disposer for any proposer, which is the positioning issue #99 asks
for.

The interchange format is settled: tscircuit states routing problems and solutions as
**SimpleRouteJson** — bounds, obstacles, connections, `minTraceWidth` in, and a `traces` array of
`SimplifiedPcbTraces` out, where each trace's `route` is a sequence of
`{route_type: "wire", x, y, width, layer}` and `{route_type: "via", x, y, from_layer, to_layer}`
records ([custom autorouter docs](https://docs.tscircuit.com/advanced/create-or-use-custom-autorouter),
[tscircuit/autorouting](https://github.com/tscircuit/autorouting)). PR #103 already imports the
problem side through `copper_mcp.benchmarks.simple_route_json`; this slice adds the solution
side.

## How existing verification layers treat third-party geometry

The EDA industry has several mature layers whose entire job is checking artifacts produced by
tools they do not trust. Their shared principles are the contract this slice adopts.

### DRC engines: recompute from geometry, never trust producer metadata

A design-rule checker takes the physical geometry as the sole input and recomputes every
spacing, width, and enclosure fact from scratch. KiCad's DRC
([docs](https://docs.kicad.org/8.0/en/pcbnew/pcbnew.html#design-rule-checking)), KLayout's DRC
engine ([docs](https://www.klayout.de/doc-qt5/manual/drc_basic.html)), and Magic's
interactive DRC ([magic docs](http://opencircuitdesign.com/magic/)) all share the property that
*nothing the producing tool asserts about its own output participates in the check*. A route is
whatever its geometry says it is. This slice does the same: the foreign document's only
authoritative content is coordinates, widths, layers, and net attribution; every claim about
those (continuity, clearance, containment, connectivity) is recomputed.

### LVS: verification is a comparison against an independently derived reference

Layout-versus-schematic tools such as netgen
([opencircuitdesign.com/netgen](http://opencircuitdesign.com/netgen/)) extract a netlist from
the layout geometry and compare it against the *intended* netlist from an independent source.
The layout's own annotations are never the reference. The analog here is revision binding: the
foreign solution claims to solve a specific problem, and the verifier hashes the supplied
problem bytes itself and compares against the caller's stated digest rather than believing any
identifier inside the solution document.

### Formal equivalence checking: the checker is independent of the producer

Logic equivalence checkers (Cadence Conformal, Synopsys Formality) exist because synthesis
tools are large, heuristic, and occasionally wrong; the checker is deliberately a *different*,
smaller, better-understood program. The same independence argument appears in safety standards:
DO-178C tool qualification distinguishes tools whose output is independently verified from
tools whose output is trusted, and only the latter need full qualification. The design lesson —
**a small deterministic verifier can gate the output of a large unverified producer** — is the
proof-carrying-code arrangement (Necula, 1997: the proof checker, not the compiler, is the
trusted computing base). An ML autorouter is exactly a large unverified producer; the verifier
here is small, bounded, integer-exact, and independent of every proposer.

### Refusal over repair

Production DRC/LVS layers report violations; they do not edit the layout. Repair is a separate
tool with separate authority, because a checker that silently patches its input can no longer
state what it verified. CopperMCP already holds this line internally (bounded local exact
repair is its own seam with its own record, #90), and the foreign seam holds it absolutely:
accept or refuse, never fix.

## Contract decisions this survey justifies

1. **Geometry in, verdict out.** The accepted input is the SimpleRouteJson problem/solution
   pair, byte-exact, parsed with the same literal-token discipline as the import seam
   (millimetre tokens through `decimal.Decimal`, never floats). Anything outside the documented
   subset refuses the whole submission — the DRC posture, not the linter posture.
2. **Identity is computed, never accepted.** The result binds `sha256` content addresses the
   verifier computed over both documents plus the imported snapshot digest. No CopperMCP
   candidate identity is minted, because a candidate ID means "produced under our identity
   rules from our base revision", which is false of foreign geometry by definition. Reserved
   identity keys inside the solution are refused as `forged_identity` rather than ignored,
   because a discarded forgery is indistinguishable from a laundered one in the response.
3. **Attribution is part of the claim.** Clearance between two traces is only defined if their
   nets are known, so every trace must carry the `connection_name` it claims to route
   (`SimpleRouteJson` connections carry stable names; solvers in the tscircuit pipeline carry
   the connection name through the trace). Inferring ownership from geometric contact would be
   repair by another name.
4. **Direction of error is inherited and extended.** The import seam already over-approximates
   obstacles and under-approximates routing room. The verifier extends the same discipline to
   the route itself: widths round up for clearance and down for connectivity; a via blocks on
   every declared layer; sub-nanometre coordinate residue (endemic in JavaScript-emitted SRJ)
   is rounded and then *charged for* by slackening every comparison in the refusing direction,
   so an inexact document can only be refused more often than its exact counterpart, never
   less.
5. **A pass is a bounded claim.** KiCad DRC is not run by this slice (an SRJ-imported board has
   no KiCad file to run it on), so the verdict literal and the response's one-value fields
   (`kicad_drc: "not_run"`, `apply_authority: "none"`, `repair: "not_attempted"`,
   `origin: "foreign_untrusted"`) state the boundary as data. Binding real KiCad DRC to a
   foreign route over a KiCad-backed board is the natural second slice of #99.

## Sources

- [Z01 release post](https://blog.autorouting.com/p/were-releasing-z01-1-million-autorouting) —
  tscircuit/autorouting, 1M samples, deferral of obstacle avoidance and DRC compliance.
- [tscircuit custom autorouter docs](https://docs.tscircuit.com/advanced/create-or-use-custom-autorouter)
  — SimpleRouteJson problem and `SimplifiedPcbTraces` solution shapes.
- [tscircuit/autorouting](https://github.com/tscircuit/autorouting) — dataset and format
  specification (cited for format only; the repository is unlicensed and archived, see D-150).
- [KiCad DRC documentation](https://docs.kicad.org/8.0/en/pcbnew/pcbnew.html#design-rule-checking),
  [KLayout DRC manual](https://www.klayout.de/doc-qt5/manual/drc_basic.html),
  [Magic / netgen](http://opencircuitdesign.com/netgen/) — recompute-from-geometry and
  independent-reference postures.
- George Necula, *Proof-Carrying Code*, POPL 1997 — the small-checker/large-producer trust
  arrangement.
- [Prior import-seam research](open-baseline-benchmarks-v1.md) and the
  [SimpleRouteJson import module](../../src/copper_mcp/benchmarks/simple_route_json.py) —
  the conservative mapping this verifier builds on.
