# Migrating a deployment from CopperMCP 0.9.0 to 0.10.0

This note is the deployer's delta, audited item by item against the `0.10.0` CHANGELOG section.
Every entry there is classified below as either a caller-visible change with an action, or an
explicit non-claim with the reason it needs none.

## 0. No schema version moves, and no snapshot needs re-conversion

**`BOARD_IR_SCHEMA_VERSION` does not move in this release. It is `0.4.0` at `v0.9.0` and `0.4.0`
at `0.10.0`.** This was checked against the constant itself
(`src/copper_mcp/board_ir/types.py`) at both points, not inferred from the absence of a
CHANGELOG entry.

The consequence is the whole of the previous release's required action reversed:

- **do not** re-convert persisted Board IR;
- **do not** invalidate snapshot digests, candidate caches or scene caches on account of the
  version;
- `inspect_board_ir` continues to report `ir_schema_version: "0.4.0"`;
- `schemas/board-ir/0.4.0.schema.json` is the active schema and its accepted set is unchanged.
  `0.1.0`, `0.2.0` and `0.3.0` remain frozen legacy files.

0.9.0 required a two-hop `0.2.0` → `0.4.0` re-conversion
([the 0.9.0 note](copper-mcp-0.9.0.md), [board-ir-0.4](board-ir-0.4.md)). If that migration is
still outstanding it remains outstanding — installing 0.10.0 neither performs nor excuses it.
There is no `0.4` → `0.5` hop in this release and no `0.4` → `0.5` note, because there is no
such move.

`SCENE_VERSION`, `NEGOTIATED_ROUTER_VERSION`, `ROUTER_VERSION`, the candidate versions and every
published schema file are likewise unmoved. **What moves in 0.10.0 is the preview response
envelope and the MCP tool list, not the IR.**

## 1. New public MCP tool: `verify_external_route_candidate`

This is the release's one genuinely new public surface, and the reason 0.10.0 is a minor rather
than a patch. A 0.9.0 client that enumerates tools will see one more.

**Nothing existing changes.** The tool is additive, MCP-only over both stdio and streamable HTTP,
read-only, and has no CLI, persistence, repair, apply or live-IPC peer. A deployment that does
not call it is unaffected by it.

### The contract a caller must build

The tool wrapper has exactly one key, `request`. That value is a closed envelope declaring
`schema_version: "1.0"` — a version **independent of** the v1/v2 foreign document versions
inside it, so do not derive one from the other.

The envelope carries a reference-only route selector, one existing closed v1 or v2 external route
document, and coordinator endpoint pad IDs. The selector requires all three of `net_ref_id`,
`expect_board_revision` and `expect_snapshot_digest`.

- **There is no net-*name* selector.** `preview_route`'s `net` compatibility selector has no
  counterpart here. A caller holding only a KiCad net name must resolve a `net_ref_id` first.
- The selector may optionally carry a bounded `seed` and routing `settings`.
- The document may describe only its declared segments, paths and optional vias. Candidate
  identity, net binding, metrics and policy are **reconstructed by the server** and may not be
  supplied.
- Obstacle work uses the validated coordinator setting; edge work uses its grid-node ceiling
  capped at 4,096. Neither has a standalone request field, and neither can be raised by the
  foreign document.

### The two response shapes, and the one that is easiest to read wrong

Authoritative KiCad DRC is **mandatory and has no request switch**. There is no way to ask for a
structural-only verification.

- A structural refusal executes no KiCad process and returns `status: "refused"`,
  `physical_validation: "not_run"`, a typed fixed diagnostic and bounded counts.
- An acceptance continues through a private source-preserving board copy and returns
  `status: "accepted"`, the server-recomputed `candidate_id`, `physical_validation: "completed"`,
  candidate-bound aggregate `drc_evidence`, and `drc_comparability: "single_invocation"`.

**`completed` means KiCad completed, not that the board is clean.** It records execution. A
caller must read the DRC summary fields as well; treating `physical_validation: "completed"` as a
pass is the single most likely integration error against this tool. `single_invocation` likewise
labels how the evidence was obtained and is required before any differential is computed against
it (the `comparability` discipline from 0.9.0 §6 applies here unchanged).

The result never contains geometry, paths, segments, vias, coordinates, board bytes or names,
workspace paths, tokens, capabilities or mutation claims. **An acceptance is not an apply
authority**; it mints no token and there is no apply peer for this surface.

Accepted-set changes to this envelope require a new public version under
[ADR-0115](../adr/0115-external-route-verification-is-a-versioned-read-only-mcp-boundary.md).

## 2. Every preview response now says why an apply token is absent

This is the change most likely to break a strict 0.9.0 client, and it affects four surfaces.
[The dedicated note](preview-apply-token-reasons.md) is the short form; this section is what a
deployer must act on.

### Response versions that move

| Surface | Field | 0.9.0 | 0.10.0 |
|---|---|---|---|
| `preview_route` | `schema_version` | `1.0` | `1.1` |
| `preview_layered_route` | `schema_version` | `1.0` | `1.1` |
| `preview_placement` | `placement_version` | `0.1.0` | `0.2.0` |
| placement **candidates** | `placement_version` | `0.1.0` | **`0.1.0` — unchanged** |

The candidate version deliberately does not move, so canonical placement identity and the apply
binding of an existing candidate are untouched.

### What a 0.9.0 caller must do

**The route, layered-route, and placement preview responses — including their live variants —
now carry both `apply_token` and `apply_token_withheld_reason`, and set exactly one of them.**
The contract enforces the exclusive-or in both directions. This does **not** apply to
`preview_route_bundle`: its response variants stay at schema version `1.0` and carry neither
field, so a bundle-preview client must not require the reason key or attempt to read it.

1. **A closed response decoder must accept the new key.** A decoder that rejects unknown fields
   will fail on every route, layered-route, and placement preview response, including ones it
   handled in 0.9.0.
2. **`apply_token` is no longer an optional key with a `None` default — it is always present.**
   Code that *constructs* or round-trips these responses and previously omitted `apply_token`
   now fails validation. Code that only reads them is unaffected by this half.
3. **Branch on the reason, not on the null.** A null `apply_token` now carries exactly one of
   eight fixed literals: `unsupported_surface`, `not_requested`, `apply_disabled`,
   `no_candidate`, `no_move`, `board_not_appliable`, `fill_bound_candidate`, `replay_refused`.
   The set is closed; a caller may treat an unrecognized value as a protocol error.
4. **One outcome genuinely changes, not just its label.** A placement replay refusal was
   previously swallowed by a bare `except` and the caller received an unexplained `null`. It now
   returns `replay_refused`. A client that inferred "no token means the operator disabled apply"
   was wrong before and is now told so.
5. **`not_requested` is not a refusal.** It is the ordinary answer when `include_apply_token` was
   false. Do not alert on it.

The literals are deliberately bare: they carry no digit, path, net, reference designator or
coordinate. A withheld reason discloses why a capability was refused and nothing about the board
it was refused for — so it is safe to log and safe to show a caller who is already entitled to
the preview.

### Do not backfill

Do not infer or insert a reason into a stored `1.0` route/layered response or a `0.1.0` placement
preview. Re-run the preview against the original board revision and request if the reason is
needed. The version move does not migrate, refresh or reauthorize an old token; single-use token
semantics, apply flags and revision checks are unchanged.

## 3. The verified-fill island ceiling rises from 4,096 to 500,000 vertices

The 0.9.0 note (§9) told layered-preview callers that the ordered-layer adapter refuses any one
verified-fill island above **4,096** vertices with `invalid_request`, and that this affected 14 of
18 measured corpus boards. **That ceiling was uncalibrated. It is now a measured 500,000**
([ADR-0116](../adr/0116-layered-fill-islands-have-a-measured-source-boundary.md), `B-123`).

This is a **widening**, and the caller-visible consequence is the pleasant direction:

- `preview_layered_route` requests with `include_fill_authority: true` that refused in 0.9.0 on
  island size will now proceed and route on many real boards. A deployment that special-cased or
  suppressed that refusal should retire the workaround.
- 500,001 vertices still refuses, and still refuses **before** bounds computation or identity
  hashing, so an over-large island costs nothing.
- The independent aggregate meter and the rectangle-search meter are **unchanged**. Only the
  per-island boundary moved.

A one-million-vertex alternative was measured and rejected: its split-island proposal plus replay
exceeded the predeclared time gate. 500,000 is the measured boundary, not a round number.

**No behaviour changes for a caller that does not set `include_fill_authority`.**

## 4. A new aggregate refusal reaches preview and durable-job contracts

Ordered-layer verified-fill validation now refuses an aggregate polygon walk above the existing
**10,000,000** obstacle-check ceiling *before* inspecting vertices
([issue #189](https://github.com/seunghyukchoe/copper-mcp/issues/189), `D-208`, `SEC-149`).

The ceiling itself is not new; the preflight is. The caller-visible part is that the typed
`obstacle_check_budget_exceeded` result now propagates through **preview and durable-job
contracts** — `preview_layered_route`, and the `start_routing` / `get_routing_job` lifecycle
records.

Routing clients were already told in 0.9.0 to handle `obstacle_check_budget_exceeded`; if that
was done, nothing further is required. If it was not, a layered or durable-job client can now
receive it where in 0.9.0 it received a per-island refusal or ran on. The independent per-island
refusal is preserved and is not replaced by the aggregate one.

## 5. Authoritative DFM sign-off exists, and is not a surface you can call

Authoritative sign-off can now produce `SIGNED_OFF`, for `dfm` only
([ADR-0119](../adr/0119-a-signoff-claim-rests-on-repeated-agreement-from-a-registered-backend.md)).
A deployer needs to know this **only** to avoid two misreadings, because there is nothing to
integrate:

- **The seam is not exported through MCP or CLI.** There is no tool, no request field and no
  response key. A 0.10.0 client cannot reach it, and no existing response gains a sign-off field.
- **A sign-off is not an authorization to write copper.** The apply surfaces and their tokens are
  untouched by it. It does not create, extend or bypass an apply token.

For completeness, since the claim is easy to overstate: `si`, `pi` and `thermal` are
**unregistered** and answer with a non-claim; the backend registry is a module constant with no
`register()` function, so no deployment configuration can add one; a caller-supplied backend is
refused without being invoked; and a claim requires the real KiCad DRC to agree **exactly** across
N ≥ 2 runs over one immutable candidate — disagreement is a refusal rather than a quietly weaker
claim, and a run that skipped or excluded checks refuses even when it passed.
[Issue #91](https://github.com/seunghyukchoe/copper-mcp/issues/91) remains open for SI, PI and
thermal.

## 6. The `mcp` dependency is capped below 2.1

**`mcp` moves from `>=2.0.0,<3.0.0` to `>=2.0.0,<2.1.0`.** This is a resolver-visible constraint,
so it is a migration item even though no CopperMCP API changes: a deployment that pins or requires
`mcp >= 2.1` will now fail to resolve against CopperMCP 0.10.0, and one that floated to 2.1.x will
be held back on upgrade.

**The reason is a refusal-disclosure regression, not tidiness.** Through 2.0.1
`MCPServer.call_tool()` wrapped every escaping exception as
`ToolError(f"Error executing tool {name}: {exc}")`, message included. From 2.1.0 only `ToolError`,
`ResourceError` and `MCPError` remain anticipated; anything else becomes `UnexpectedToolError`,
whose message is replaced by a bare `Error executing tool <name>`.

CopperMCP's deliberate boundary refusals raise `copper_mcp.request_boundary.RequestError`, which is
a `ValueError`. Under 2.1 they are therefore reclassified as crashes and **the reason a request was
refused stops reaching the model** — measured, not predicted: plain `main` under `mcp==2.1.1`
reproduces eleven failures that pass under `2.0.1`, and the excessive-agency harness correctly
scores fifteen `budget_dos` cases as unrefused rather than refused.

**What a deployer must do:** nothing, if `mcp` is left to resolve. If your environment pins `mcp`
independently, move the pin inside `>=2.0.0,<2.1.0` before upgrading. Do not override the cap to
take 2.1: on that line CopperMCP's refusals are silent to the caller.

**Stated as a non-claim, because the hold conceals something:** 2.1's classification is the better
contract, and this is a hold rather than an endorsement of 2.0.x. Adopting it means auditing every
refusal path in `mcp_server.py` and deciding per exception type whether it is anticipated or a
crash — a security-surface change that earns its own review. `R-176` records the cost of waiting:
on the pinned 2.0.x line the harness cannot tell a deliberate refusal from an unhandled crash,
because 2.0.x collapses both into `ToolError` (`D-225`, `R-176`).

## 7. CI and release-operator behavior

Only operators carrying the upstream workflows or running `make check` are affected. Everything
0.9.0 §11 said still holds. Two things move:

- **`.github/ci-budget-calibration.json` is re-recorded at this boundary.** The suite grew from
  more than 2,860 tests to roughly 3,200 across the external-candidate arc and the R/S/C/M wave,
  so the ceilings are re-measured rather than inherited. **No budget is raised**: CI remains 120
  minutes, release verification 120, release publication 10, and all three still clear the half
  rule. Re-record before every wave that materially grows the suite, from `success` conclusions
  only.
- **`scripts/check_schema_sets.py` gains `v0.10.0` in `RELEASE_TAGS`.** During the cut this is
  the one listed tag that does not yet exist; every earlier listed tag must already exist, and any
  repository tag not listed still fails.

The release environment must still install `.[dev,security]` — `pip-audit` is in the `security`
extra, not `dev`.

## 8. Changes that are real but reach no caller

Each of these has a `0.10.0` CHANGELOG entry and is listed here so the audit is complete rather
than selective. None requires deployment action.

- **The external route-candidate disposer (v1) and multi-path patch (v2)**
  ([ADR-0112](../adr/0112-external-route-candidates-enter-through-a-disposer.md),
  [ADR-0113](../adr/0113-external-route-patches-preserve-multi-pin-topology.md)) are exported from
  `copper_mcp.routing` but reach a caller only *through* the public tool in §1. The document
  shapes they accept are the ones that tool's envelope wraps.
- **The file-backed KiCad DRC continuation**
  ([ADR-0114](../adr/0114-external-candidates-continue-to-private-kicad-drc.md)) is the production
  path behind §1's mandatory DRC. It has no independent surface.
- **The negotiated local-repair transaction**
  ([ADR-0117](../adr/0117-local-exact-repair-is-an-opt-in-verified-transaction.md)) is internal
  and opt-in. `negotiate_routes` is **not** an MCP tool; `repair_settings` is not a public request
  field. Legacy no-repair result shapes and candidate identities are unchanged.
- **The deferred authoritative-signoff seam**
  ([ADR-0118](../adr/0118-authoritative-signoff-stays-closed-until-a-bounded-executor-exists.md))
  is superseded within this same release by §5.
- **The tscircuit SimpleRouteJson output adapter** lives in `copper_mcp.benchmarks`, is
  re-exported from no `__init__`, and is reached by no MCP tool, CLI or script. A committed test
  pins that no module outside that package references it. Its output is an untrusted document that
  must still pass §1's tool.
- **Five measurements and profiles** — the whole-board negotiated census (`B-124`, 0 of 20 boards
  admitted), the M3 frozen apply census (`B-128`), the fixed-point masking census (`B-129`), the
  parse-inclusive performance profile (`B-122`) and the B-119 dimension refutation — add no
  production code, no acceleration and no public-contract change. They are evidence, not
  behaviour.

## 9. What this release explicitly does not change

Stated as non-claims, because an absent entry and a verified absence are not the same thing:

- **no schema version moves** — Board IR stays `0.4.0`; Scene, router and candidate versions are
  unmoved, and no published schema file's accepted set changes;
- **no persisted artifact needs migration** — no snapshot, candidate, scene or job record;
- **no apply or write authority is added, widened or relaxed** anywhere, by any of the above;
- **placement candidate identity does not move** — `placement_version` stays `0.1.0` for
  candidates even though the *preview* moves to `0.2.0`;
- **no refusal message text is promised** — the golden set is a regression detector. Continue
  branching on typed codes, not prose;
- **no routing-quality, electrical, SI, PI, EMC, thermal, fabrication or hardware claim** is made
  by any entry in this release, including the DFM sign-off of §5 and the DRC evidence of §1;
- **no whole-board or whole-output validity claim** follows from §1. Two foreign candidates that
  each verify cleanly are **not** thereby clean against each other: neither exists in the base
  snapshot the other was verified against.

## 10. Deployment checklist

Before switching traffic to 0.10.0:

- preview clients accept `apply_token_withheld_reason` on route, layered-route and placement
  responses, and branch on the eight closed literals rather than on `apply_token == null`;
- any code constructing or round-tripping a preview response supplies `apply_token` explicitly,
  since it no longer defaults;
- clients pin route/layered `schema_version` `1.1` and placement `placement_version` `0.2.0`,
  while leaving placement **candidate** `0.1.0` alone;
- stored `1.0` / `0.1.0` previews are re-run rather than backfilled with a reason;
- layered-preview clients using `include_fill_authority` retire any workaround for the old 4,096
  island refusal, and still handle `obstacle_check_budget_exceeded`;
- durable-job clients handle `obstacle_check_budget_exceeded` in the lifecycle record;
- any client integrating `verify_external_route_candidate` resolves a `net_ref_id` (there is no
  name selector), sends `schema_version: "1.0"`, and reads the DRC summary rather than treating
  `physical_validation: "completed"` as a pass;
- **no Board IR re-conversion is scheduled for this release** — if one is pending it is 0.9.0's,
  not this one's; and
- any independent `mcp` pin is moved inside `>=2.0.0,<2.1.0`, and the cap is not overridden to
  take 2.1, where CopperMCP's boundary refusals stop explaining themselves; and
- release operators keep `.github/ci-budget-calibration.json` synchronized with successful hosted
  durations and install `.[dev,security]`.
