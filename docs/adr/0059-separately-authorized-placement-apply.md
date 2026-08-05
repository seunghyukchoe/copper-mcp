# ADR-0059: Separately authorize bounded placement application

- Status: Accepted
- Date: 2026-08-05
- Owners: `@seunghyukchoe`
- Related: ADR-0024, ADR-0025, ADR-0026, ADR-0034, ADR-0057, ADR-0058

## Context

CopperMCP can already observe footprint poses and produce an immutable, legality-checked
placement candidate. The source-preserving serializer also replays a narrow front-side,
orthogonal footprint subset while retaining the rest of the KiCad file byte-for-byte. Leaving
the candidate permanently advisory, however, prevents a measurable AI-to-KiCad placing loop.

KiCad's board format is an s-expression document with footprint pose, layer, pads, graphics,
properties, and other constructs in the same source file. The official file-format reference
documents those sections rather than promising that a partial Board IR round trip is lossless.
KiCad's Python API likewise exposes footprint position/layer as editable board state, which is
useful evidence for a future IPC action but does not make a file-level edit safe by itself.

## Decision

### A separate tool and token domain

`preview_placement` accepts an explicit `include_apply_token: true` only for file-backed previews.
When the operator has enabled `COPPER_MCP_ALLOW_APPLY=1`, and the pure placement replay accepts
the candidate, the preview issues a short-lived HMAC token with operation domain `placement`.
`apply_placement_candidate` verifies that domain, candidate identity, Board IR revision, source
byte revision, and workspace-relative path before reading the board. Route tokens therefore
cannot authorize placement, and no token is minted by default or for a live IPC proposal.

### The mutation boundary is the route boundary, with different pure geometry

The placement apply service reuses the route apply safety sequence: operator opt-in, token
verification, lockfile refusal, first compare-and-swap, fail-closed Board IR conversion, pure
replay, local-filesystem check, timestamped content-addressed pre-apply copy, an under-lock
second compare-and-swap, atomic replacement, and truthful post-publication reporting. The manifest
decoder bounds pose, grid, legality, and evidence fields before the board is parsed, and the MCP
request exposes the same closed shape to clients. After publication the service re-renders the
authorized bytes, reparses them, spends the capability exactly once even on an unverified write,
and re-reads the final digest after guarded recovery. Before a successful response it takes one
last best-effort digest observation, catching a visible concurrent rewrite after verification; the
capability is still spent if that observation reports `applied_but_unverified`. A refusal before
publication never consumes its token; any operation that has published bytes consumes it once.

The pure engine calls the existing replay-verified placement serializer and returns only bytes
that it proved reparsed and matched the source Board IR plus the transformed footprint poses,
owned pad positions/angles, and supported rectangular courtyards. It requires at least one
changed footprint pose, so an apply capability cannot be spent on a no-op.

### Bounded support is a safety property, not a hidden partial edit

The initial apply surface remains intentionally narrower than observation and preview:

- front-side orthogonal footprints only;
- native footprint identities;
- supported unfilled rectangular `F.CrtYd` centerlines;
- no side flip, property/text/fabrication-graphic/library/3D-model rewrite, or IPC mutation;
- locked and unsupported footprints refuse before any file replacement.

A candidate outside this subset may still be previewed and inspected, but receives no placement
apply token. This keeps AI policy separate from the deterministic file writer and preserves the
project invariant that model output never becomes copper or board mutation without bounded
validation and explicit operator authorization.

## Consequences

- The M3/M4 placement-apply gate is closed for the measured subset, with a public MCP tool and
  strict structured response.
- Placement application has the same stale-candidate and recoverable-backup semantics as route
  application, but its result reports `footprints_moved` and `bytes_changed` rather than route
  segment counts.
- Post-publication verification now checks the actual published bytes against a fresh authorized
  replay and Board IR parse. A concurrent writer or recovery-sync failure returns
  `applied_but_unverified` with the digest observed on disk and cannot replay the same capability;
  that digest may equal the original when guarded rollback succeeded. A final best-effort
  observation before success catches a visible rewrite after verification, while a longer editor
  transaction would still be required to close the last nanosecond race.
- A real KiCad DRC run, post-apply scene observation, side-aware flip serialization, arbitrary
  courtyard topology, and a genuine KiCad undo transaction remain open. The response reports
  KiCad-open and DRC stages as `not_run` until those checks are actually executed.
- The capability is still file-level. Live IPC mutation is deferred because an in-memory editor
  document cannot be bound to the same file digest/CAS contract without a separate transaction
  design.

## Research basis

- [KiCad Board File Format](https://dev-docs.kicad.org/en/file-formats/sexpr-pcb/): the board is a
  structured s-expression containing footprint and other independently significant sections.
- [KiCad kicad-python Board API](https://docs.kicad.org/kicad-python-main/board.html): exposes
  footprint instance layer and board editing primitives, informing the future IPC path while
  not replacing the file-level CAS contract.
- [KiCad placement guidance](https://docs.kicad.org/8.0/en/getting_started_in_kicad/getting_started_in_kicad.html):
  courtyard separation is an explicit placement consideration and supports keeping the preview
  legalizer and apply surface conservative.
