# Migrating a deployment from CopperMCP 0.8.0 to 0.9.0

CopperMCP 0.9.0 ships the contracts already present on `main`; it does not add a new capability
wave. It does, however, move the active Board IR accepted set **twice** relative to 0.8.0 and changes
several public output and refusal literals. Treat this as a stored-data and caller-compatibility
migration, not as a package-only upgrade.

## 1. Required action: re-convert Board IR from `0.2.0` to `0.4.0`

A 0.8.0 deployment writes Board IR `0.2.0`. A 0.9.0 deployment accepts Board IR `0.4.0` only. The
end-to-end journey is therefore:

| Stage in the release delta | Active Board IR | What changes | Operator consequence |
|---|---:|---|---|
| CopperMCP 0.8.0 | `0.2.0` | Starting persisted envelope | Preserve the source `.kicad_pcb` and the conversion constraint profile. |
| First accepted-set move on `main` | `0.3.0` | Version ownership and typed version refusal; accepted model unchanged | Understand the first break, but do **not** manufacture or retain an intermediate envelope. |
| CopperMCP 0.9.0 | `0.4.0` | Optional `Pad.copper_envelope` widens the accepted model for custom pads | Run the 0.9.0 converter once, directly from the source board, and persist only `0.4.0`. |

There is **no auto-migration for either hop** and no safe supported JSON transformation from
`0.2.0` or `0.3.0` to `0.4.0`. The two hops explain the complete contract delta; they are not two
operator conversion passes. Re-run conversion once with CopperMCP 0.9.0 from the original
`.kicad_pcb` bytes and the same constraint profile. A persisted `0.2.0` or `0.3.0` envelope
presented to 0.9.0 is refused.

Do not delete the source board after conversion. Board IR is a derived representation; the source
board is the migration authority.

### Deployment sequence

1. Stop writers that can create new 0.8.0 Board IR envelopes.
2. Back up each source `.kicad_pcb`, its conversion constraints, and any candidate/cache inventory
   keyed by snapshot digest.
3. Upgrade the CopperMCP package and all workers that decode Board IR.
4. Re-convert each source board directly with 0.9.0, producing `0.4.0`.
5. Rebind or invalidate candidates and cached scenes whose base revision changed, as described in
   section 3.
6. Resume writers only after every caller accepts the output and refusal literals in sections
   2–11.

## 2. First hop: Board IR `0.2.0` to `0.3.0`

This hop is governed by
[ADR-0105](../adr/0105-a-schema-version-moves-with-its-accepted-set.md). It separates a version
mismatch from malformed Board IR without changing the modelled board content.

### What breaks

- A persisted `0.2.0` envelope no longer decodes. Re-convert from source; do not edit the
  `schema_version` string.
- The diagnostic code for a well-formed envelope at an unsupported version is now
  `schema.version`, at locator `snapshot.schema_version`. It previously arrived as
  `schema.invalid`. This also changes the refusal for old `0.1` envelopes. Callers branching on
  the old literal must add `schema.version`.
- `BoardIrSummary.ir_schema_version` reports the active version rather than `0.2.0`. In the final
  0.9.0 build that literal is `0.4.0`, because the second hop has also landed.

### What does not break at this hop

The `0.3.0` accepted model is the `0.2.0` model with a new owned version. For an ordinary board,
the snapshot digest, constraint digest, source revision, and downstream identities do not move
merely because the envelope wrapper says `0.3.0`. The first-hop encoded envelope differs only in
the version string.

### Historical `0.2.0` copies are not interchangeable

`0.2.0` was published with three different accepted sets across `v0.5.0`–`v0.8.0`. The
corresponding schema shipped with the release that produced a snapshot is the authoritative copy
for that snapshot.

| Release carrying `0.2.0` | `$defs/footprint` accepts |
|---|---|
| `v0.5.0`, `v0.6.0` | `courtyards` only |
| `v0.7.0` | `+ courtyard_circles`; nullable `net_id` on via, segment and arc |
| `v0.8.0` | `+ far_side_courtyards`, `+ far_side_courtyard_circles` |

The frozen `0.2.0` schema in 0.9.0 is the `v0.8.0` copy. It is historical evidence, not an active
decoder target and not a universal validator for every older `0.2.0` document.

## 3. Second hop: Board IR `0.3.0` to `0.4.0` and `Pad.copper_envelope`

The final 0.9.0 Board IR migration is described in the dedicated
[Board IR 0.4 note](board-ir-0.4.md). The `0.3.0` schema is frozen and the active decoder accepts
`0.4.0` only.

### Accepted-set and digest changes

`Pad` gains an optional `copper_envelope`, a conservative pad-local axis-aligned copper envelope
for obstacle readers. The existing shape, size and centre remain the attachment core for
connectivity and under-approximating keep-in claims; the envelope must contain that core.

- **Ordinary pads:** no envelope member is emitted. Their canonical content payload, snapshot
  digest and downstream candidate identities remain unchanged. Only the outer Board IR version
  wrapper moves.
- **Custom pads:** the envelope is part of canonical content. Their content and snapshot digests
  are new addresses. Invalidate route candidates, layered candidates, placement candidates and
  cached scenes bound to the old snapshot, then regenerate or rebind them against the new one.
- **Parse budgets:** every custom primitive vertex is still charged against the caller's per-ring
  and aggregate parse ceilings even though Board IR retains only the conservative envelope.

This is not an exact custom-primitive model and not a KiCad-parity claim. It is a bounded obstacle
envelope. The frozen 18-save measurement moved conversion from 13 to 15 boards.

### Circuit Scene output changes

Circuit Scene moves to `0.4.0` for these boards and emits the following fields together:

- `copper_envelope_nm`
- `copper_envelope_frame: "pad_local"`
- `geometry_model: "anchor_with_custom_copper_envelope"`

Scene consumers must transform the local envelope for conservative obstacle checks and must use
the attachment anchor/core for connectivity or inside claims. A client asserting the previous
scene-version or geometry-model literal must be updated.

## 4. Placement boundary verdicts preserve the proof gap

`preview_placement` no longer publishes a false `violated` verdict when the available geometry can
prove neither legality nor illegality.

- `outline_containment` now permits `inconclusive` between a core-proven edge crossing and a
  bounds-proven inside result.
- `keepout_respect` now permits `inconclusive` between a core-proven intrusion and a bounds-proven
  clear result.
- Region `keep_in` evaluates a pad's attachment core; region `keep_out` evaluates its obstacle
  envelope. Alignment and symmetry use exact object centres.

**Caller impact:** results that 0.8.0 could publish as the literal `violated` may now be
`inconclusive`. Any caller that branches on `violated`, treats every non-`proven_*` result as a
violation, or assumes candidate publication/apply authorization proves boundary legality must be
changed. Inspect every verdict independently; `inconclusive` is neither pass nor violation.

No Board IR type, schema, accepted board construct, board byte or content address changes in this
section.

## 5. Pad `thermal_bridge_angle` is accepted as a typed non-claim

The KiCad adapter now validates and accepts a pad-level `thermal_bridge_angle` instead of refusing
the board solely because that token is present.

The accepted source value must be one bare exact decimal with at most microdegree precision.
Duplicate, quoted, exponent, nested and malformed forms fail closed. The token is preserved exactly
when route and placement splices edit the source board.

This acceptance does **not** add the angle to `Pad`, the Board IR schema, the codec, canonical
content or any content address. CopperMCP continues to use the whole zone outline as the
conservative routing obstacle, while exact-fill routing consumes freshness-verified KiCad fill
polygons generated from the original board bytes. Snapshot-only reproduction of thermal spokes is
not claimed.

A converted board discloses the accepted-but-unmodelled token through
`unmodelled_counts.unmodelled_thermal_bridge_angle_pad_count`. Callers that require a fully
self-contained snapshot must treat a non-zero value as a non-claim, not as proof that the angle was
modelled.

## 6. Benchmark DRC counts carry a comparability literal

Every DRC section in a benchmark artifact now carries exactly one `comparability` literal:

- `single_invocation`
- `repeated_agreement`
- `repeated_disagreement`

Aggregates inherit the weakest literal among their inputs. `drc_differential` now refuses unless
both sides are `repeated_agreement`; a numeric tolerance is not substituted for repeated evidence.
The live `schemas/drc-summary.schema.json` payload is unchanged.

`scripts/benchmark_real_board_capability.py --drc-repetitions N` controls how the literal is earned
and defaults to `1`. Benchmark consumers must stop comparing bare counts without reading
`comparability`, and automation expecting a differential from a single or disagreeing invocation
must handle the typed refusal.

## 7. Single-layer `verified_fill` malformed evidence has typed refusals

The single-layer route `propose` and `replay` boundaries now reject malformed `verified_fill`
evidence with `unsupported_geometry` instead of accepting some list-shaped impostors or allowing
an uncaught `AttributeError`.

Valid evidence that is too large to validate is distinct:

- at most 32,768 islands;
- at most 1,000,000 vertices per island; and
- at most 10,000,000 aggregate validation-walk obstacle checks.

Crossing the aggregate validation-work ceiling returns `obstacle_check_budget_exceeded`, not
`unsupported_geometry`, because the evidence is structurally valid but too expensive to inspect.
Callers should branch on these typed codes and must not retry either refusal unchanged. Well-formed
real-board evidence below the ceilings is unchanged.

## 8. `inspect_board_ir` adds `unmodelled_counts`

`BoardIrSummary`, returned by `inspect_board_ir`, gains an additive `unmodelled_counts` map. In the
final 0.9.0 contract a supported board reports all six measured entries, including zeros:

```json
"unmodelled_counts": {
  "edge_connector_pad_count": 2,
  "max_roundrect_rounding_nm": 0,
  "unmodelled_board_property_count": 1,
  "unmodelled_group_count": 0,
  "unmodelled_pad_property_count": 0,
  "unmodelled_thermal_bridge_angle_pad_count": 1
}
```

The first five keys disclose accepted source constructs or rounding that were already measured
inside conversion but did not previously reach MCP clients; the sixth is the typed non-claim from
section 5. An unsupported board reports `{}` because no conversion measurement occurred.

This is additive: no existing field moves, no content address is involved and
`models.SCHEMA_VERSION` remains `1.0`. Strict decoders that reject unknown output keys must be
widened before deployment. Do not interpret a count as an identity set: the map does not name the
pads, groups or properties involved.

## 9. Layered routing exposes fill authority and binds candidates to it

`preview_layered_route` accepts `include_fill_authority`. When true, CopperMCP refills a disposable
private board copy with KiCad, admits cached fill only when the fresh refill reproduces it exactly,
and routes with the verified islands.

### Request, response and literal changes

- Every layered preview response now has a `fill_authority` key. It is `null` except on a routed
  proposal that requested and used verified fill.
- A successful authority record includes one closed `routing_effect` literal from the same four
  labels used by `preview_route`, selected over the signal layers the layered search reached.
- A cached fill that the fresh KiCad refill does not reproduce returns the layered `stale_fill`
  diagnostic rather than routing from either version.
- `include_drc` and `include_fill_authority` may be requested together.
- The ordered-layer adapter still refuses any one verified-fill island above 4,096 vertices with
  `invalid_request`; it refuses the request rather than silently falling back to the zone envelope.
  This limit affected 14 of 18 measured corpus boards, so callers opting in must handle that result.

### Candidate and replay changes

`LayeredRouteCandidate` gains `fill_binding`, the content address of the verified fill that produced
the route. Replay with different fill evidence refuses with
`fill_evidence_mismatch` (`LayeredRouteFailureCode.FILL_EVIDENCE_MISMATCH`). The equality check is
required in both directions: a stricter model can change the route, and a looser model can confirm
geometry the router never proved.

When `fill_binding` is `null`, the field is omitted from canonical identity and all previously
pinned layered candidate identities remain unchanged. When it is non-null, persist and replay the
matching fill evidence with the candidate.

### Surfaces that deliberately refuse the flag

- Durable routing jobs reject `include_fill_authority` and reject a fill-bound candidate, because
  the durable job record cannot carry the replay evidence required downstream.
- `preview_live_layered_route` pins the flag to `false` and refuses an explicit `true`, because
  file-cache freshness cannot prove the fill of a possibly unsaved IPC editor snapshot.

Closed request/response decoders must accept the new preview input, response key, candidate field,
`stale_fill` diagnostic and `fill_evidence_mismatch` replay code. Do not send the flag to live or
durable layered surfaces.

## 10. Evaluation and cross-router benchmark artifact outputs

### Excessive-agency evaluation records an unconvertible family

The excessive-agency evaluator no longer aborts the whole artifact at the first board family that
cannot convert. It records every scenario in that family as:

- status `not_run`; and
- reason `board_does_not_convert_to_board_ir`.

To prevent a quiet all-skipped family from looking healthy, the artifact gains a fourth coverage
control literal: `every-accepted-format-family-is-actually-exercised`. Evaluation consumers that
expect an exception, only three controls, or a closed set without the new reason must be updated.

### Cross-router comparison is a typed artifact, including `not_run` rows

`scripts/benchmark_cross_router_comparison.py` publishes one row for every declared baseline. A
baseline that was not measured remains present with a typed `not_run` result and a reason derived
from the licence or environment precondition that was actually unmet. Consumers must not treat a
missing measurement as a missing row or retain a stale reason after the environment changes.

The artifact also publishes `measured_rows: 1` and `comparison_supported: false`; those literals
mean the one measured CopperMCP row supports no comparative conclusion. Its metric set deliberately
contains no DRC field. This is a new benchmark artifact rather than a runtime MCP contract, but any
closed artifact reader must accept its typed rows and non-claim fields.

## 11. CI and release-operator behavior

The repository's release accepted state is stricter in 0.9.0. Operators carrying the upstream
workflows or running `make check` must account for these gates:

- `scripts/check_ci_budgets.py` requires every explicit job-level `timeout-minutes` to have hosted
  success-only calibration and to satisfy the half rule. Failed or cancelled jobs cannot calibrate
  a ceiling. At this release boundary the declared budgets are 120 minutes for CI, 120 minutes for
  release verification and 10 minutes for release publication.
- `scripts/check_ledgers.py` now refuses a `Ready` authorization that has neither a matching
  published-release row nor an explicit outstanding-publication marker. Record publication the
  same day rather than leaving the authorization open.
- `scripts/check_commit_message.py --range` validates pull-request commits server-side; an empty or
  unresolvable range is a failure. Do not rely only on the local `commit-msg` hook.
- `scripts/check_schema_sets.py` and `scripts/check_drc_comparability.py` are release gates, and the
  audio-benchmark and Circuit Intent checkers now run in hosted CI as well as locally. During a
  release cut, only the final tag matching `pyproject.toml` may be listed but not yet present;
  historical missing tags and any unlisted repository tag still fail. Once the final tag exists,
  it joins the historical comparison automatically.
- The release environment must install `.[dev,security]`. `pip-audit` is in the `security` extra,
  not the `dev` extra.

The refusal-message golden set is a regression detector, not a promise that the prose is a stable
public literal. Continue branching on typed codes rather than message text.

## 12. Release and deployment checklist

Before switching traffic to 0.9.0, verify all of the following:

- all persisted Board IR was regenerated from source `.kicad_pcb` to `0.4.0`;
- candidate and scene caches were invalidated where a custom-pad snapshot digest moved;
- Board IR clients handle `schema.version` and report `ir_schema_version: "0.4.0"`;
- Scene clients accept Scene `0.4.0` and the custom-envelope geometry literals;
- placement clients handle `inconclusive` without treating it as either proof or violation;
- Board IR inspection clients accept the additive six-entry `unmodelled_counts` map;
- routing clients handle `unsupported_geometry`, `obstacle_check_budget_exceeded`, `stale_fill`
  and `fill_evidence_mismatch` at the surfaces described above;
- benchmark consumers require DRC `comparability` before computing a differential;
- layered preview clients persist matching fill evidence when `fill_binding` is non-null; and
- release operators keep `.github/ci-budget-calibration.json` synchronized with successful hosted
  durations and install `.[dev,security]` so the release gate includes `pip-audit`.

The remaining Unreleased CHANGELOG entries are measurements, research records, licensing
corrections or documentation. They do not change a runtime output, public literal, accepted set or
release gate and therefore require no deployment migration.
