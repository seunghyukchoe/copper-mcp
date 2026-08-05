# Migrating a deployment to CopperMCP 0.6.0

CopperMCP 0.6.0 changes two behaviors that a working 0.5.0 deployment can depend on without having
declared it. Neither is auto-migrated: one is an operator consent decision that only the operator
can make, and the other is a diagnostic code that callers match on. Everything else in 0.6.0 is
additive or internal.

## 1. Live KiCad IPC observation is off by default

Through 0.5.0, `capture_live_board` read `KICAD_API_SOCKET` from the ambient environment and, with
nothing set, connected to whatever socket the official binding defaults to. The MCP tools
`inspect_live_board` and `observe_live_board_scene` were registered unconditionally. Reaching a
running editor is an outbound action against the operator's machine, so 0.6.0 gates it on
`COPPER_MCP_ALLOW_LIVE_IPC` ([ADR-0069](../adr/0069-operator-gated-live-ipc-observation.md), #77).

**Any deployment that relied on ambient socket discovery stops reaching the editor on upgrade.**

To migrate:

1. Decide whether this deployment should be able to talk to a running KiCad editor at all. If it
   should not, do nothing: default-off is the new correct state, and no socket is discovered or
   opened.
2. If it should, set `COPPER_MCP_ALLOW_LIVE_IPC=1` in the server process environment, alongside
   `COPPER_MCP_WORKSPACE`. The value is matched by exact membership in `{"0", "1"}`, the same rule
   `COPPER_MCP_ALLOW_APPLY` already uses: `true`, `yes`, `TRUE`, and an empty string are each a
   `ConfigurationError` at startup rather than a silent enable or a silent disable.
3. Leave `KICAD_API_TOKEN` handling unchanged. It is still never passed to the binding and never
   serialized, with the flag on or off.

Expected behavior after the change:

- With the flag unset or `0`, `capture_live_board` and `capture_live_editor_context` raise
  `KicadIpcDisabledError` *before* the endpoint is read. Every live surface — scene, route, layered
  route, placement, and editor context — is downstream of those two chokepoints and is therefore
  gated by that one check.
- Both live MCP tools remain **listed**, and answer with a refusal that names the flag. A hidden
  tool is indistinguishable from an unimplemented one, so clients that probe the tool list see no
  change in shape.
- With the flag on, the boundary is otherwise what 0.5.0 had: local IPC only, TCP endpoints still
  refused, redaction unchanged, and no mutation, DRC, fill, apply, electrical, or fabrication
  authority added.

## 2. `unsupported.document` replaces `syntax.invalid` for a foreign document root

Board IR conversion now reports a serialization whose root is not `kicad_pcb` under its own
diagnostic code, `unsupported.document`, instead of the generic `syntax.invalid`
([ADR-0069](../adr/0069-operator-gated-live-ipc-observation.md), #75). "This is not a board" and
"this is a board we cannot convert" are different answers, and a caller has to act on them
differently.

To migrate:

1. Find every caller that matches the Board IR conversion diagnostic code `syntax.invalid` in order
   to detect a wrong document type, and match `unsupported.document` instead.
2. Leave callers that match `syntax.invalid` to detect genuinely malformed S-expression syntax
   alone. That code still means exactly what it meant.
3. Re-check any caller that treated a foreign document as an ordinary unsupported board. Both
   Circuit Scene observer paths — the live one and the file-backed `observe_board_scene` — now
   refuse a foreign root with a typed `CircuitSceneError` rather than returning it as a
   `supported: false` conversion result.

Expected behavior after the change:

- A KiCad board carrying an unsupported version or construct is unaffected and still returns its
  truthful unsupported result with its diagnostic counts.
- A foreign document can no longer obtain a board digest, a snapshot digest, or a topology summary
  from either observer.
- The IPC object counter refuses a foreign root before classifying anything, so a payload such as
  `(evil_root …)` is no longer published as a live board observation with plausible topology counts.

## 3. A digest taken over rendered bytes does not survive the upgrade

This is not new in 0.6.0 — it has been true of every release — but 0.6.0 is the first release in
which it is pinned and therefore the first in which it is worth stating plainly.

CopperMCP writes `(generator_version "<package version>")` into every board and schematic it
renders, as provenance. Any digest taken over those bytes is therefore version-scoped:

- A rendered **schematic artifact digest** issued by 0.5.0 will not be reproduced by 0.6.0. The
  intent digest it carries is unaffected, so a caller can still prove which Circuit Intent produced
  an artifact; only the artifact's own content address moves.
- A **patched-board revision** — the `base_revision` a candidate DRC statement binds, and the
  combined-derivative revision recorded in route-bundle evidence — is likewise reproducible only by
  the version that produced it. The route-bundle benchmark artifact is regenerated under 0.6.0 for
  exactly this reason; `B-085` records that its measured quantities are all unchanged and that the
  0.5.0 revisions remain correct for 0.5.0.

To migrate: re-derive any stored rendered-artifact digest under 0.6.0 rather than comparing it
against a 0.5.0 value, and treat a mismatch across a version boundary as expected rather than as
tampering. Candidate IDs, bundle IDs, placement candidate IDs, Board IR snapshot and constraint
digests, Circuit Scene revisions, policy digests, and net reference IDs are **not** version-coupled
and are unchanged.

## What does not require migration

- **Golden identity pins.** `tests/test_golden_identities.py` pins the exact digest of every
  content-addressed surface as it behaves in 0.6.0 (#84). Exactly one pin moved in this release —
  the version-coupled schematic artifact digest described above — so no persisted candidate,
  bundle, snapshot, export, or scene revision is invalidated. The pins exist so that a future
  change to any of those identities cannot pass a green suite unnoticed; changing one of the
  version-independent pins is a breaking change that will require its own version bump and its own
  migration note.
- **Byte-accurate confirmation budgets.** The compare-and-swap confirmation read is now charged
  against `COPPER_MCP_MAX_BOARD_BYTES` before any encode (#76). An oversized second read is now a
  `KicadIpcPayloadError` budget refusal instead of a `KicadIpcConnectionError` reported as a
  concurrent board edit. An in-budget mid-observation edit is still the connection-class refusal it
  always was. A deployment whose budget was already large enough for its boards sees no change.
- **Ledger and ADR identifier validation.** `scripts/check_ledgers.py` and the new
  `scripts/check_adr_numbers.py` validate identifier allocation in `make lint` and CI (#85). This
  affects contributors, not deployments; see
  [the allocation rules](../ledgers/README.md#allocating-ids).

No board, snapshot, candidate, or persisted artifact is rewritten by this upgrade.
