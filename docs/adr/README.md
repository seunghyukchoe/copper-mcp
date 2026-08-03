# Architecture Decision Records

ADRs record durable decisions and their tradeoffs. They are immutable after acceptance except for
status and links to superseding records. Copy `template.md`, assign the next number, and link the ADR
from the decision ledger.

- [ADR-0001: Candidate-first mutation model](0001-candidate-first.md)
- [ADR-0002: MCP is an external adapter](0002-mcp-adapter.md)
- [ADR-0003: Python reference core with Rust-ready contracts](0003-python-reference-core.md)
- [ADR-0004: Authoritative KiCad CLI DRC gate](0004-authoritative-kicad-drc.md)
- [ADR-0005: Canonical integer Board IR v0.1](0005-canonical-board-ir.md)
- [ADR-0006: Bounded deterministic A* reference](0006-bounded-deterministic-astar.md)
- [ADR-0007: Disposable KiCad candidate snapshots](0007-disposable-kicad-candidate-snapshot.md)
- [ADR-0008: Candidate-bound authoritative KiCad DRC evidence](0008-candidate-bound-kicad-drc.md)
- [ADR-0009: Bounded non-mutating route preview](0009-non-mutating-route-preview.md)
- [ADR-0010: Read-only Board IR inspection service](0010-board-ir-inspection-service.md)
- [ADR-0011: Existing copper as exact rectangular obstacles](0011-existing-copper-obstacles.md)
- [ADR-0012: Through vias as selected-layer obstacles](0012-via-obstacles.md)
- [ADR-0013: Conservative polygon zone-boundary obstacles](0013-polygon-zone-obstacles.md)
- [ADR-0014: Canonical circuit intent and deterministic schematic rendering](0014-canonical-circuit-intent.md)
- [ADR-0015: Bounded Circuit Intent schematic delivery](0015-bounded-circuit-schematic-delivery.md)
- [ADR-0016: Same-net attachment and partial-route completion](0016-same-net-attachment.md)
- [ADR-0017: Conservative integer envelopes for diagonal foreign copper](0017-diagonal-segment-envelopes.md)
- [ADR-0018: Chained integer squares as the core of diagonal attachment copper](0018-diagonal-attachment-cores.md)
- [ADR-0019: Route multi-pin nets by deterministic component merging](0019-multi-pin-component-merging.md)
- [ADR-0020: Treat same-net through vias as connectivity joints](0020-via-aware-connectivity.md)
- [ADR-0021: Trust poured copper only against a fresh KiCad refill](0021-zone-fill-authority.md)
- [ADR-0022: Observe a board as a semantic scene, with its text held at arm's length](0022-circuit-scene-observation.md)
- [ADR-0023: Render a board deterministically, and only as an advisory aid](0023-deterministic-board-render.md)
