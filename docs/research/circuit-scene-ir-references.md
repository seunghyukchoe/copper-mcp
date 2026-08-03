# Circuit Scene IR references

Literature and measurement behind [ADR-0022](../adr/0022-circuit-scene-observation.md), gathered
2026-08-04. Concepts inform CopperMCP's own implementation; no external code is copied.

> **Verification status.** The measured section below was produced in this session against the
> tools named in it, and every number in it is reproducible from this repository. The literature
> section was **not** re-fetched in this session — no network retrieval was performed — so it
> records works the author is confident of from prior reading, not sources checked today. Nothing
> in the implementation depends on any of them being retrievable. Consistent with the caveat in
> [multi-pin routing references](multi-pin-routing-references.md), arXiv identifiers after the
> `2505.*` range post-date this assistant's knowledge cutoff and are **not** cited here at all
> rather than being cited unverified; a reader adding recent work should mark it as unconfirmed.

## Measured against KiCad 10.0.5

Environment: KiCad 10.0.5 (`kicad-cli --version`), macOS 25.5.0, CPython 3.12, board
`hardware/coppertone-buffer/coppertone-buffer.kicad_pcb` (52mm x 30mm, two copper layers).

### Scene size and cost

| Region | Objects | JSON bytes | Median of 7 |
| --- | --- | --- | --- |
| Whole board | 123 | 41,016 | 38.0 ms |
| 10mm x 10mm corner | 8 | 3,694 | 39.7 ms |

Two findings, both of which shaped the decision:

- **Region scoping is a token economy, not a latency economy.** An eleven-fold reduction in
  response size produced no reduction in wall time, because parsing the whole board dominates and
  happens either way. The window is worth having for what it does to the caller's context budget
  and for the precision of what comes back, not for server cost. Claiming otherwise would have been
  easy and wrong.
- **A real two-layer board is small.** 123 objects is about 6% of the provisional 2,000-object
  ceiling. This is the honest reason the ceiling is marked provisional in ADR-0022: it was not
  derived from a board that stresses it, because this repository has no such board.

Composition of the 123: 1 outline, 55 pads, 2 keepouts, 1 rule, 53 segments, 0 arcs, 9 vias,
2 zones. All 122 board references are native KiCad UUIDs (`all_board_refs_native: true`); the single
non-native reference is the request-scoped net class, which is why it is classified separately
rather than counted as content-derived.

### Author-controlled text

Extracting every author-controlled string from the same board yields **160** strings: 4 from
board-level `gr_text` and 156 from footprint properties, on `F.SilkS` and `F.Fab`. None of them
appear anywhere in a scene response unless `include_annotations` is set, and none appear outside
`annotations` even then — asserted by whole-response grep in
`tests/test_circuit_scene.py`.

Net names are a separate case and never appear at all: Board IR hashes them into
`net:name:<digest>` at conversion, so a hostile net name cannot reach a caller through this surface
even with annotations enabled. `tests/fixtures/circuit-scene-v0.1/scene-hostile-text.kicad_pcb`
pins that with a net named `CANARY_NET_NAME`.

### Format behaviour confirmed by experiment

- A text node is `(gr_text "payload" (at ...) (layer ...) ...)`. Only the leading run of atoms is
  author payload; the repository's flat-payload helper rejects such a node outright, which is how
  the first implementation of the reader was caught failing on a real board rather than on a
  fixture.
- A footprint `(property "Name" "Value" ...)` carries **two** author strings, not one. The name is
  as attacker-controlled as the value, so both are quarantined and neither is promoted into a
  structural field.
- KiCad requires an explicit `(layer ...)` on footprint properties and `fp_text`; the Board IR
  adapter rejects boards whose graphics lack one. Root-level `(property ...)` is rejected by the
  adapter entirely, which is why the scene reader does not read it.

### MCP SDK behaviour confirmed by experiment

Against the SDK pinned in `pyproject.toml` (`mcp>=2.0.0,<3.0.0`), there is **no gap** to document:
a handler whose return type is a Pydantic model produces a real `outputSchema` — 18,207 bytes with
`$defs`, `additionalProperties: false`, and all fourteen top-level fields required — and calls
return populated `structuredContent`. The contrast is with this repository's own older tools:
`preview_route` and friends are typed `dict[str, Any]` and therefore advertise a vacuous
`{"type": "object", "additionalProperties": true}`. That is a gap in those tools' typing, not in the
SDK, and `tests/test_mcp_server.py` now asserts both halves so the difference stays visible.

### Rendering, measured 2026-08-04

All numbers from `kicad-cli pcb export svg` on KiCad 10.0.5, same environment as above.

- **Nondeterminism is exactly one line.** Two exports of an unchanged board three seconds
  apart differ only in the `<title>` element, which carries a wall-clock timestamp and the
  output filename: 1 differing line out of 5,603, with the line count unchanged. Everything
  else - path data, ordering, viewBox, style attributes - is byte-identical. This is what
  makes `title-line-v1` sufficient and anything more unjustified.
- **Silkscreen text is not safely "drawn as paths".** An export including `F.SilkS` and
  `F.Fab` contains each author string **twice in literal form**: in a `<desc>` beside the
  stroked paths and in an invisible `<text opacity="0">`. A hostile fixture with six text
  items produced 6 `<text>` nodes and 12 literal marker occurrences. The copper-only export of
  the same board contains zero. Excluding the layers is the only control that works; filtering
  `<text>` would leave the `<desc>` copy. This corrects a plausible-sounding assumption, and
  it corrects it in the more dangerous direction.
- **Colour output is theme-dependent.** The same board renders `#4D7FC4` under the default
  theme and `#008400` under "KiCad Classic". With `--black-and-white` the two are
  byte-identical, so the flag is a determinism control.
- **The exporter does not need a writable input directory.** It completes against a fully
  read-only tree. Given a writable one it drops a `.kicad_prl` beside the input, which is the
  side effect the read-only snapshot removes.
- **Hitting the file-size ceiling does not fail loudly.** With `RLIMIT_FSIZE` set below the
  output size, KiCad exits 0 having written a truncated file rather than dying on `SIGXFSZ`.
  Because the title line sits near the top of the document it survives truncation, so the exit
  code, the title check and the digest all accept half an SVG. Requiring a complete document
  is what turns this into a refusal.
- **Page sizing.** The default is an A4 page with the board in a corner; `--page-size-mode 2`
  produces a board-sized viewBox (51.9938mm x 29.9974mm for this board) at essentially the
  same byte count.

## Reproducible builds

- The canonicalization problem here is the standard reproducible-builds one: an otherwise
  deterministic tool stamps its output with a build time and a path. The standard remedy is
  `SOURCE_DATE_EPOCH` (Debian/`reproducible-builds.org`), which KiCad's SVG exporter does not
  honour, so the normalisation happens after the fact instead. Naming and versioning the rule
  (`title-line-v1`) follows the same discipline as recording a toolchain version: a digest
  should never be ambiguous about the rule that produced it.

## Prompt injection and untrusted content

- Debenedetti, Shumailov, Fabian, Tramèr et al., **"Defeating Prompt Injections by Design"** (CaMeL,
  arXiv:2503.18813, 2025). Untrusted data is kept in a separate channel from the control flow that
  acts on it, with capabilities attached, rather than being sanitised and re-mixed into one prompt.
  The `annotations` collection is the same move at a much smaller scale: a separate typed channel,
  a fixed trust label, and no path by which board text becomes a structural value.
- Willison's **dual-LLM / "lethal trifecta"** framing (2023-2025). The trifecta is private data,
  untrusted content, and exfiltration. This surface deliberately holds only the first two: it is
  read-only, has no outbound channel, and is workspace-confined. Naming that explicitly is what
  makes the quarantine sufficient rather than merely helpful.
- Greshake et al., **"Not what you've signed up for"** (indirect prompt injection, AISec 2023). The
  original demonstration that content retrieved by a model is an attack surface. A `.kicad_pcb` from
  a forum, a vendor, or a colleague is exactly such content.

The design consequence taken from all three is negative rather than positive: **do not attempt to
sanitise**. Escaping defences fail by leaking through the one field nobody audited, which is why the
test is a whole-response grep for a marker planted in every author-controlled slot of the fixture,
not a per-field assertion.

## Naming, references and capabilities

- **Object-capability discipline** (Dennis & Van Horn 1966; Miller, *Robust Composition*, 2006).
  Authority travels with an unforgeable reference rather than with a name the caller can construct.
  Scene objects are named by Board IR ids, and `around_ref_id` accepts only ids the board actually
  contains — a coordinate a caller invents cannot become a handle on something it was not given.
- **Stable identity under transformation.** The metamorphic relation in `tests/test_metamorphic.py`
  turns the board a quarter turn and requires geometry to move while every `ref_id` holds still. A
  reference that tracked geometry would be useless for acting on what was observed, and the test's
  companion guard confirms the fixture actually contains geometry the turn changes.

## Level of detail

- Clark, **"Hierarchical geometric models for visible surface algorithms"** (CACM 1976), and the
  LOD literature after it. Graphics degrades *fidelity* with distance because a viewer far away
  needs only the gist.
- CopperMCP inverts this deliberately. A router needs exact nanometres or nothing — a summarised
  obstacle is one it cannot use — so the scene degrades **extent** instead of fidelity: full detail
  inside a stated window, nothing outside it. The trade is recorded here because the graphics
  intuition points the other way and the inversion is the point.

## Deferred

Deterministic SVG rendering, with normalisation so that two observations of an unchanged board
produce identical bytes, is designed but not implemented. It is subordinate by construction: any
disagreement between a future render and the scene is a bug in the render. The reason it is not the
primary surface is recorded in ADR-0022 and is not a matter of effort — MCP hosts routinely drop
image content, and a vision model cannot recover nanometre geometry from pixels.
