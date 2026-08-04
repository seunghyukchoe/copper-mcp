# ADR-0030: Bind a bounded KiCad IPC snapshot to Circuit Scene

- Status: Accepted
- Date: 2026-08-04
- Owners: `@seunghyukchoe`
- Related: ADR-0022, ADR-0028, ADR-0029, SEC-024, B-009

## Context

The redacted IPC observer can prove that a PCB Editor is reachable and can hash its current
serialization, but that summary is not useful for high-fidelity AI placement or routing. The
semantic Circuit Scene already converts a byte-backed KiCad board into exact integer geometry,
revision-bound Board IR references, and quarantined author text. The missing edge is a single
snapshot hand-off that proves the bytes observed through IPC are the bytes converted into that
scene.

KiCad's 9/10 IPC API is synchronous and GUI-bound. This workstation has the server disabled, so
the implementation needs a fake official-client oracle and must not claim a live editor result.
The first binding must remain read-only: it cannot expose socket tokens, raw serializations, or
create an authority for placement, routing, DRC, or apply.

## Decision

Add `kicad_ipc.capture_live_board`, an internal bounded hand-off containing the redacted
`LiveBoardObservation` and the exact UTF-8 bytes that produced its digest. Its invariant checks
size and SHA-256 equality before a caller can consume the source. Keep `inspect_live_board` as
the public redacted summary.

Add `observe_live_board_scene` as a read-only MCP tool and shared service. Its request uses the
literal board label `live`, the same constraints/region shape as file-backed scene observation,
and optional `expect_board_revision` / `expect_snapshot_digest` compare values. It captures one
IPC snapshot, refuses a stale expected board digest before conversion, converts those exact bytes
through the existing Board IR and Scene pipeline, and refuses a stale expected snapshot digest
before returning. The output uses the existing Circuit Scene `0.2.0` contract with `board_path:
live`; no raw source is added to the response. Live rendering is refused until a private
snapshot-to-render binding exists.

This is an observation bridge, not action authority. `preview_route`, `preview_placement`, DRC,
and `apply_candidate` remain file-backed and must gain their own live compare-and-swap/session
contracts before they can consume a live scene reference.

## Consequences

- AI clients can ask for exact semantic geometry from the active editor without a file-export
  step, while retaining the existing ref-stability and author-text quarantine rules.
- A captured board digest and Board IR snapshot digest now refer to one immutable source hand-off;
  stale live sessions fail closed when callers provide both expected digests.
- The optional dependency and local IPC transport remain bounded and dependency-light in CI.
- A real KiCad session, live placement/routing, DRC, ERC, electrical, and fabrication behavior
  remain unverified because the local API server is disabled; B-009 is a fake-client contract
  benchmark, not a live-editor claim.

## References

- [KiCad IPC API for add-on developers](https://dev-docs.kicad.org/en/apis-and-binding/ipc-api/for-addon-developers/)
- [Official `kicad-python` bindings](https://gitlab.com/kicad/code/kicad-python)
- [ADR-0022: Circuit Scene observation](0022-circuit-scene-observation.md)
- [ADR-0029: Read-only KiCad IPC observer](0029-read-only-kicad-ipc-observer.md)
- [KiCad IPC research](../research/kicad-ipc-references.md)
