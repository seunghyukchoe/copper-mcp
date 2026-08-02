# Decision Ledger

| ID | Date | Status | Decision | Record |
|---|---|---|---|---|
| D-001 | 2026-08-03 | Accepted | All generated work remains an immutable candidate until explicit application. | [ADR-0001](../adr/0001-candidate-first.md) |
| D-002 | 2026-08-03 | Accepted | MCP is an adapter over protocol-independent application services. | [ADR-0002](../adr/0002-mcp-adapter.md) |
| D-003 | 2026-08-03 | Accepted | Begin with a Python reference implementation and a Rust-ready backend contract. | [ADR-0003](../adr/0003-python-reference-core.md) |
| D-004 | 2026-08-03 | Accepted | Use Apache-2.0 for broad adoption and an explicit patent grant. | `LICENSE` |
| D-005 | 2026-08-03 | Accepted | Use fixed-argument KiCad JSON DRC as the authoritative validation gate. | [ADR-0004](../adr/0004-authoritative-kicad-drc.md) |
| D-006 | 2026-08-03 | Accepted | Permit manual release verification, but restrict attestation and publication to version-tag pushes. | `.github/workflows/release.yml` |
| D-007 | 2026-08-03 | Accepted | Publish audio hardware labs under a separate open-hardware license with explicit evidence limits; DRC and artifact checks never imply fabrication approval. | [`hardware/coppertone-buffer/README.md`](../../hardware/coppertone-buffer/README.md) |
| D-008 | 2026-08-03 | Accepted | Make hardware snapshot validation read-only by default; replacement of tracked manufacturing evidence requires an explicit refresh operation. | [`hardware/coppertone-buffer/validate.sh`](../../hardware/coppertone-buffer/validate.sh) |
| D-009 | 2026-08-03 | Accepted | Use a strict integer, content-addressed Board IR v0.1 and fail closed when a source adapter cannot represent geometry or constraints exactly. | [ADR-0005](../adr/0005-canonical-board-ir.md) |
| D-010 | 2026-08-03 | Accepted | Establish the first routing oracle as a bounded, integer, candidate-only two-pin A* backend with a narrow fail-closed geometry surface. | [ADR-0006](../adr/0006-bounded-deterministic-astar.md) |
