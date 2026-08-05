# ADR-0056: Verify bounded Circuit Intent schematic parity

- Status: Accepted
- Date: 2026-08-05
- Owners: CopperMCP maintainers
- Related: M1 schematic round-trip roadmap item; KiCad CLI schematic export

## Context

CopperMCP can render a deterministic schematic derivative for a bounded two-pin passive Circuit
Intent subset. A successful render or KiCad parse is not enough to show that the exported component
and net connectivity still match the source intent. The parity input is also untrusted XML and must
not become an unbounded parser or a source of leaked design text.

## Decision

Add a pure, reusable `kicad_schematic_parity` verifier. It first requires byte-exact replay of the
canonical renderer, then accepts only the bounded KiCad format-E `kicadxml` shape and compares the
component references, library identities, pins, and net nodes against the immutable Circuit Intent
snapshot. The verifier rejects DTDs, entities, processing instructions, unknown structures, duplicate
connectivity, malformed values, and budget exhaustion with stable non-echoing error codes. It emits
only digests, counts, and passed literals in frozen evidence.

## Consequences

The passive fixture now has a deterministic source replay and real KiCad component/connectivity
oracle that can be reused by tests and future MCP evidence. This does not invoke ERC, prove
schematic-to-PCB parity, certify symbols/footprints, or make the schematic board-ready. Broader
symbols, hierarchy, buses, and an authoritative ERC surface require separate contracts.

## References

- [KiCad CLI schematic export](https://docs.kicad.org/10.0/en/cli/cli.html#schematic-export-netlist)
- [KiCad schematic file format](https://dev-docs.kicad.org/en/file-formats/sexpr-schematic/)
- [KiCad nets section](https://docs.kicad.org/8.0/en/eeschema/eeschema.html#the-nets-section)
