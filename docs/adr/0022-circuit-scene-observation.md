# ADR-0022: Observe a board as a semantic scene, with its text held at arm's length

- Status: Accepted
- Date: 2026-08-04
- Owners: `@seunghyukchoe`
- Related: ADR-0005, ADR-0009, ADR-0010, ADR-0015

## Context

Everything this server can do to a board, it does through a net name and a pair of constraints. A
model cannot ask what is *near* a pad, cannot tell which copper it would be allowed to move, and has
no way to name an object except by repeating its coordinates back. `inspect_board_ir` reports
structure in aggregate — counts, layers, whether the board converts — which answers "can you work
with this?" and nothing else.

The gap is an observation surface. The obvious shape for one is a picture, and that is the shape
this ADR declines.

Two things also become load-bearing the moment a board's *contents* reach a model rather than its
counts. A board file is authored by someone else, and its silkscreen, fabrication notes and
footprint properties are free text under that author's control. And an observation is only useful if
the model can act on it afterwards, which means it has to be able to name what it saw.

## Decision

Observation is a **typed semantic scene**, region-scoped, with every author-controlled string
quarantined.

- **The scene is the authority; any render would be advisory.** `observe_board_scene` returns
  structure only and emits no image. A model given the structured scene alone must be able to work
  from it, because MCP hosts routinely drop image content and because a vision model cannot recover
  nanometre geometry from pixels — it would read a picture and then guess. Rendering is deferred to
  a later slice and, when it lands, will be an orientation aid whose disagreement with the scene is
  a bug in the render.
- **Objects are named by the Board IR identity they already carry.** A model refers to
  `segment:kicad:<uuid>`, never to a coordinate. Reusing ADR-0005's ids rather than minting scene
  ids means a reference stays meaningful across a later placement or route call.
- **Reference durability is stated, not assumed.** Each object reports `ref_stability`:
  `native` for a KiCad UUID, which survives unrelated edits; `content_derived` for a geometry hash,
  which moves when its object changes; `request_scoped` for an id belonging to the request rather
  than the board. The scene repeats the summary so a caller can check in one place whether the
  references it is about to store are durable.
- **Static and mutable are separated in the response shape.** Outline, pads, keepouts and rules are
  given; segments, arcs, vias and zones are what a proposal may change. The partition is structural
  rather than a per-object flag, so code that means to read only the givens cannot accidentally
  iterate over both.
- **A region is mandatory and takes exactly one of two forms** — a complete bounding box, or one
  `around_ref_id` with a radius. There is no "whole board" shorthand: the caller states a window,
  and full detail inside it beats degraded detail everywhere.
- **Truncation is explicit.** Object and vertex ceilings are charged as the scene is built, and
  `ceiling_hit` is non-null exactly when something was dropped. A caller never infers completeness
  from a count it cannot independently check.
- **Every author-controlled string is quarantined.** Board text is off by default. When requested it
  appears only in `annotations`, each entry carrying `trust: "untrusted_board_author"` as a
  **one-value literal**. There is no vocabulary for a trusted string, so no board can label its own
  text safe. Net names never appear at all — Board IR already hashes them.

## Consequences

- A model can ask what is within 2mm of a pad and get exact integer geometry for it, then name what
  it found in a later call. That is the prerequisite for placement and for targeted rerouting.
- The whole CopperTone board is 123 objects and 41KB of JSON in 33ms, well inside the default
  ceilings, so region scoping is an affordance rather than a workaround at this board size. It stops
  being optional on a dense board, which is why the ceilings and the explicit `ceiling_hit` exist
  now rather than after the first truncated response is misread as a complete one.
- The default object ceiling of 2,000 is **provisional**. It is roughly sixteen times CopperTone and
  was not derived from a dense board, because this repository does not have one; `max_scene_objects`
  and `max_scene_vertices` are configurable so the number can be corrected by measurement rather
  than by a release.
- Reading board text costs a second parse of the source, outside Board IR. That is deliberate: Board
  IR carries no text at all, which is the right default, and the scene should not be the reason it
  starts to.
- `origin` distinguishes `board_text`, `silkscreen` and `footprint_property`. Root-level
  `(property ...)` is *not* read, because the adapter rejects any board carrying one, so the branch
  would be unreachable. The extraction is deliberately over-inclusive within a node — a structural
  keyword such as `fp_text`'s `user` is quarantined alongside the payload — because the dangerous
  error is treating author text as structure, not the reverse.
- The tool is exposed over **both** transports, unlike `render_circuit_schematic`. That tool is
  stdio-only because it returns a capability URI naming process-local bytes, which a stateless HTTP
  deployment cannot resolve. A scene is one self-contained response holding no server-side state, so
  it follows the `preview_route` precedent; workspace confinement, not transport, is what bounds the
  disclosure.
- A conforming client gets a real `outputSchema` and `structuredContent`, because the handler's
  return type is a closed contract rather than `dict[str, Any]`. The repository's other tools return
  bare dicts and therefore advertise a vacuous `{"type": "object"}`; that is a gap in those tools,
  not in the SDK, and it is now visible in a test.

## Prior art

The static/mutable split and the "name it, don't describe it" rule are the **object-capability**
reading of an observation surface: what you can refer to is what you were handed, and authority
travels with the reference rather than with a coordinate a caller can fabricate.

The quarantine follows the same reasoning as **CaMeL** (Debenedetti et al., "Defeating Prompt
Injections by Design", arXiv:2503.18813, 2025) and the dual-LLM pattern it formalises: untrusted
content is kept in a separate channel from the instructions that act on it, rather than being
sanitised in place and re-mixed. The one-value `trust` literal is the same idea applied to the
schema — a type with no unsafe inhabitant cannot be talked into producing one. This is also why the
test for it is a whole-response grep rather than a per-field assertion: sanitisation defences fail
by *leaking into a field nobody thought to check*, so the test checks all of them.

Region-scoped full detail over whole-board summary is the **level-of-detail** trade made backwards
on purpose. Graphics degrades fidelity with distance because the viewer only needs the gist far
away; a router needs exact nanometres or nothing, so the scene degrades *extent* instead.

## Alternatives considered

- **Render an image and let the model look at it**: rejected as the primary surface. It cannot carry
  exact geometry, cannot be referenced, and invites confident wrong answers. It returns later as an
  advisory aid subordinate to the scene.
- **Whole-board scenes with summarised geometry**: rejected. A summarised obstacle is one the router
  cannot use, and a caller who needs exact geometry would have to ask again anyway.
- **Sanitise board text and inline it into object fields**: rejected. It makes every consumer's
  safety depend on an escaping rule holding everywhere, which is exactly the failure mode the
  separate channel removes.
- **Mint fresh scene-local ids**: rejected. Ids that do not outlive the response cannot be used to
  act, and a second id space would need reconciling with Board IR's on every call.
- **Make the region optional and default to the whole board**: rejected. The default would be the
  expensive path, and a caller who never thought about extent would be the one to hit the ceiling.
- **A per-object `mutable: true` flag instead of two collections**: rejected. A flag is ignorable;
  a separate collection is not.

## References

- [ADR-0005](0005-canonical-board-ir.md)
- [ADR-0010](0010-board-ir-inspection-service.md)
- [ADR-0015](0015-bounded-circuit-schematic-delivery.md)
- [Circuit Scene IR references](../research/circuit-scene-ir-references.md)
- [MCP API](../architecture/mcp-api.md)
