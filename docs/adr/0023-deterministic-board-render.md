# ADR-0023: Render a board deterministically, and only as an advisory aid

- Status: Accepted
- Date: 2026-08-04
- Owners: `@seunghyukchoe`
- Related: ADR-0015, ADR-0021, ADR-0022

## Context

[ADR-0022](0022-circuit-scene-observation.md) settled that observation is a typed semantic
scene and deferred rendering, on the grounds that a picture cannot carry exact geometry and
cannot be referenced. That reasoning has not changed. What it left open is whether a render is
worth having at all as a *secondary* artifact, and if so, under what rules.

It is worth having. A model orienting itself on an unfamiliar board benefits from seeing the
shape of the copper, and a human reviewing what the model was given benefits far more. But a
render introduces three problems the semantic scene does not have: it is produced by an
external program with its own idea of determinism, it is bytes rather than structure so it
cannot be diffed meaningfully, and it can carry board-author text into a channel where nothing
is quarantining it.

## Why a separate ADR rather than a section in ADR-0022

ADR-0022 is Accepted and records a decision that was actually made: renders are subordinate,
and none is emitted. This slice makes new decisions with their own trade-offs — a
canonicalization rule, a layer set, a delivery mechanism, a transport asymmetry — and reverses
none of them. Amending an accepted ADR to contain a decision it did not make would blur what
was decided when. ADR-0022's premise survives intact and is inherited here.

## Decision

A render is **opt-in, digest-bound, canonicalized, copper-only, and advisory**.

- **`include_render` is off by default** and spawns KiCad, so it is never implicit. This is
  the same rule as `include_fill_authority` in ADR-0021.
- **Canonicalization is `title-line-v1`**, named so a digest can never be compared across
  rules. Measured against KiCad 10.0.5: two exports of an unchanged board taken three seconds
  apart differ in exactly one line — the `<title>`, which embeds a wall-clock timestamp and
  the output filename. Rewriting that one line makes the bytes content-addressable, and
  rewriting anything more would be unjustified editing of bytes KiCad is authoritative for.
- **Canonicalization fails closed.** If the expected title line is missing or duplicated, the
  assumption the digest rests on no longer holds and the render is refused rather than
  digested unnormalized. Recognising the already-rewritten line keeps the function idempotent
  without loosening that check.
- **A truncated render is a refusal.** Found by testing rather than assumed: at the file-size
  ceiling KiCad 10.0.5 does *not* die on `SIGXFSZ` — it exits 0 having written a partial file.
  The title line is near the top of the document and survives, so the exit code, the title
  check and the digest would all have been satisfied by half an SVG. The canonicalizer
  therefore requires a complete document.
- **The layer set is a security control, not a presentation choice.** Only `F.Cu`, `B.Cu` and
  `Edge.Cuts` are drawn. This corrects a belief worth stating plainly: silkscreen text is
  **not** merely stroked as unreadable paths. Measured on KiCad 10.0.5, an export including
  `F.SilkS` or `F.Fab` embeds each string **twice in literal form** — once in a `<desc>`
  beside the stroked paths and once in an invisible `<text opacity="0">`. The strings are
  fully greppable. Excluding the layers is the only control that works; filtering `<text>`
  after the fact would leave the `<desc>` copy behind.
- **`--black-and-white` is for determinism, not aesthetics.** Measured: colour output follows
  the active KiCad theme (`#4D7FC4` under the default, `#008400` under "KiCad Classic"), and
  black-and-white output is byte-identical across themes.
- **The private snapshot is read-only**, which is stronger than the zone-fill path. Verified:
  the export completes against a fully read-only input tree. Given a *writable* directory
  KiCad drops a `.kicad_prl` beside the input — a side effect the read-only snapshot removes
  rather than relocates.
- **Evidence records every input that changes the bytes**: `normalized_digest`,
  `source_revision`, `context_revision`, `kicad_version`, `layers`, `side`, `canonicalization`
  and `byte_count`. A digest alone cannot say whether two renders are comparable.
- **Delivery is a `resource_link`, annotated `audience: ["assistant"]`**, from a bounded
  process-local store separate from the schematic store.

## Consequences

- **The render is whole-board even when the scene is a window.** Region scoping applies to
  semantics only. This is stated in the tool description and pinned by a test rather than left
  for a caller to discover.
- **`include_render` is stdio-only although `observe_board_scene` is both-transport.** Bytes
  are delivered through the process-local capability store, which a stateless HTTP deployment
  cannot resolve — the same reason `render_circuit_schematic` is stdio-only. Only the flag is
  withdrawn off stdio, not the whole tool, so an HTTP caller keeps the semantic scene.
- **The render store is separate from the schematic store, with its own budget** of 8 entries
  and 32 MiB — sized so it can hold its full complement at the 4 MiB per-render ceiling rather
  than promising entries it cannot keep. Sharing one store would let a 4 MiB render evict
  schematic capabilities a caller is still holding. The 15-minute TTL is deliberately the same
  number: a render is cheaper to recreate, so a shorter life would be defensible, but "a
  capability expires 15 minutes after it is issued" is easier to audit as one number than two.
- **No render is produced for a board outside Board IR.** KiCad could probably still draw it,
  but handing back a picture of a board whose semantics we could not produce is precisely the
  inversion ADR-0022 forbids: it invites trust in the render exactly where nothing checks it.
- The CLI writes the render to a **create-only** workspace path with an exact lowercase
  `.svg` suffix, reusing the schematic export discipline. Observation never overwrites a file.
- A bottom-side render (`side="bottom"`, which adds `--mirror`) exists and is tested, but is
  reachable from the Python API only: neither the MCP request nor the CLI exposes a side
  selector yet. It is recorded in evidence so the two renders can never be mistaken for the
  same artifact.
- A PNG thumbnail for human viewing is **deferred**. It would be a separate artifact annotated
  `audience: ["user"]`; the audience field is already carried so adding one later does not
  change the delivery contract.

## Prior art

This is **reproducible builds** applied to a rendering pipeline. The canonical problem in that
literature is the same one here — an otherwise deterministic tool stamps its output with a
timestamp and a path — and the canonical remedy is the same: normalise the known-volatile
fields rather than trying to make the producer deterministic. `SOURCE_DATE_EPOCH` is the usual
mechanism; KiCad's SVG exporter offers no equivalent, so the normalisation happens after the
fact and is named and versioned so a digest is never ambiguous about the rule that produced it.

The refusal-on-truncation rule is the same **fail-closed** discipline as ADR-0021's stale
fill: an artifact that might be partial is not silently preferred to no artifact at all.

## Alternatives considered

- **Strip the whole `<title>` element instead of rewriting it**: rejected. Removing a line
  changes the line count, and an anchored substitution makes the diff between raw and
  canonical exactly one line, which a test can assert.
- **Normalise aggressively (whitespace, path rounding, attribute order)**: rejected. Every
  extra rule is another chance to erase a real difference between two boards, and none was
  needed: one line was the entire measured delta.
- **Render silkscreen for a nicer picture, and strip `<text>` nodes**: rejected on measurement.
  The literal strings also live in `<desc>`, so the filter would not work, and a prettier
  render is not worth reintroducing an unquarantined text channel.
- **Refill zones during the render** (`--check-zones`): rejected. It would depict copper the
  board file does not contain. The file is what gets fabricated — the same argument ADR-0021
  used to compare against the cache rather than substitute a fresh pour.
- **Embed the SVG in the JSON response**: rejected. It would put megabytes into model context
  unconditionally and make the scene response size depend on board complexity.
- **Share the schematic artifact store**: rejected. Two unrelated features competing for one
  eviction budget makes each one's availability depend on the other's traffic.
- **Make the render region-scoped to match the scene**: deferred, not rejected. It needs a
  viewBox transform whose correctness would have to be established against KiCad's own
  coordinate handling, and claiming a cropped render matched the region without that evidence
  would be exactly the kind of unverified claim this repository avoids.

## References

- [ADR-0015](0015-bounded-circuit-schematic-delivery.md)
- [ADR-0021](0021-zone-fill-authority.md)
- [ADR-0022](0022-circuit-scene-observation.md)
- [Circuit Scene IR references](../research/circuit-scene-ir-references.md)
- [MCP API](../architecture/mcp-api.md)
