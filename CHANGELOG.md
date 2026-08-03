# Changelog

All notable changes are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Multi-pin nets are routed, not just recognised. A net with more than two pads is spanned by a
  deterministic minimum spanning tree over its connected components - edges weighted by the exact
  integer rectilinear gap between component bounding boxes, ordered by `(gap, lower index, higher
  index)` - and each MST edge is routed as one leg by the existing multi-source/multi-target search.
  A routed leg's copper joins the merged component, so later legs may attach anywhere along it, and
  same-net legs are never obstacles to one another. The ordering policy is recorded in the candidate
  as `component-mst-v1`, which makes a better topology additive behind the same contract.
  **Claimed**: every pad ends in one component, each leg is optimal for the obstacles present when
  it was routed, and the result is exactly reproducible. **Not claimed**: Steiner optimality, or
  optimality of the tree as a whole - an earlier leg is never revisited once a later one is routed.
  Any leg failing refuses the whole call; a partial tree is not a candidate. A committed four-pad
  `tree-star` fixture becomes a three-leg tree that real KiCad 10.0.5 accepts with zero errors,
  warnings and unconnected items, and that check is discriminating because removing any one leg
  makes KiCad report an unconnected item. **This slice is validated by fixtures, not by
  CopperTone**: every net on that board is already routed by its designer, so multi-pin routing
  changes nothing there, and what that board still needs is via-aware connectivity for its three
  remaining nets. See [ADR-0019](docs/adr/0019-multi-pin-component-merging.md).

### Changed

- `RoutePatch` now carries a tuple of `RoutePath`s instead of a single vertex list, so one candidate
  can describe a tree. `ROUTER_VERSION` advances to `astar-grid/0.4.0` and the preview response
  carries `patch.paths[].vertices_nm` in place of `patch.vertices_nm`, plus `pad_count` and
  `ordering_policy`. This is a breaking response change, taken deliberately while the project is
  pre-1.0 rather than maintaining two candidate shapes forever. Two-pin proposals carry exactly one
  path and the ordering policy `single-path`, and their geometry, cost and metrics are unchanged.
- Multi-pin legs seed from pad cores rather than pad centres. Requiring every pad centre to sit on
  one lattice is unworkable: on the repository's own CopperTone board the largest grid step putting
  all pads of a multi-pin net on one lattice is 5 um for six of the nine such nets, which is a
  62-million-node lattice against a 500,000 ceiling. Seeding from cores removes the constraint for
  every pad but the anchor. Two-pin nets keep centre seeding, so their candidate identities are
  unchanged apart from the version bump. Budgets are shared across the whole tree rather than per
  leg, because one candidate should honour one ceiling; merge order and budget consumption are both
  deterministic, so exhaustion fails at a reproducible leg with reproducible counts.

### Fixed

- A round pad's connectivity core was a zero-width bar through its centre. That is a legal subset of
  the copper and was harmless while pads were only contact-tested, but it covers a lattice node only
  when the pad centre happens to land on one, so a round pad could offer no attachment point at all.
  Round pads now also contribute their largest inscribed axis-aligned square, of half side
  `isqrt(r^2 // 2)`, alongside the original bar and its perpendicular twin - a strict enlargement,
  since replacing the bar would have discarded the pad's extremes. Every rectangle contains the pad
  centre, so a pad is never split into two components by its own decomposition. Measured across all
  committed fixtures and every CopperTone net this changes no outcome and no candidate identity
  today; it is what makes multi-pin pad-core seeding possible at all, taking CopperTone's multi-pin
  pads from three nets with an unreachable pad at 250 um to none.

## [0.2.0] - 2026-08-03

### Added

- Connectivity analysis now spans nets of any width, not only two-pin nets. When every pad of a net
  lands in one component the router reports the terminal `already_connected` outcome whatever the
  pad count, reusing the existing pad cores, orthogonal rectangles and diagonal chains unchanged.
  `RouteConnection` gains a `pad_count` field and its component invariant generalises to
  `attachment_segments + pad_count`; `start_pad_id` and `end_pad_id` keep their names and their
  two-pin meaning, and are documented as the lexicographically first and last pads, which bound the
  set rather than naming a route a connected net does not have. **Routing** a multi-pin net stays
  unsupported: a wider net that is not fully connected gets the unchanged `invalid_two_pin_net`
  refusal, and no new failure code is introduced. A net carrying a same-net via or zone is never
  claimed connected, because that copper is on another layer or otherwise unrepresented here; a
  two-pin net still names the via or zone directly, while a wider one is refused for its pad count.
  Two-pin behaviour is bit-identical and `ROUTER_VERSION` does not move. On the repository's own
  CopperTone board this takes recognition from five of fourteen nets to **eleven of fourteen**, the
  widest being `VREF` at seven pads joined by ten segments; the three that remain refused — `GND`,
  `VCC`, `L_OUT` — are exactly the nets carrying same-net vias. `kicad-cli pcb drc` corroborates by
  reporting zero unconnected items for the whole board. Still zero nets routed: no copper is
  proposed for that board, and none of its nets needs any.
- Diagonal copper on the *routed* net is now attachment copper rather than a refusal, completing
  the model: obstacles are over-approximated, attachment copper under-approximated. A diagonal track
  has no single axis-aligned inner rectangle, so it contributes a chain of axis-aligned squares
  centred at `start + (delta * i) // steps`. Flooring moves a centre less than a nanometre per axis
  off the exact centreline point, so it stays within `sqrt(2)` of the track; a two-nanometre
  tolerance absorbs that, and the square half side satisfies `2 * s^2 <= (radius - 2)^2`, which by
  the triangle inequality on distance-to-a-set puts every square provably inside the real copper.
  `steps` is chosen so consecutive centres differ by at most `2 * s` per axis — exactly when two
  closed squares still touch — so the chain is one connected component by construction rather than
  by inspection, and both properties are covered by a property test over many orientations and
  widths in exact integer arithmetic. The first and last squares are centred exactly on the track's
  endpoints, so a diagonal stub reaches its pad and can be picked up at its far end. Endpoints are
  canonically ordered, so a track recorded in either direction yields the identical chain; each
  square charges the shared obstacle-check budget, so an over-long track fails closed; and a track
  too thin to model at all is refused with a distinct diagnostic. Same-net vias and zones remain
  fail-closed, and foreign diagonal envelopes are unchanged. Boards carrying same-net diagonals were
  previously refused outright, so no board the router already accepted changes geometry or identity
  and `ROUTER_VERSION` does not move. The `diagonal-stub` fixture now completes a route off a
  diagonal stub, adding 18 mm where an empty board needs 20 mm, verified against real KiCad 10.0.5;
  that check is discriminating because displacing the same proposal by 0.5 mm so it misses the stub
  end makes KiCad report two `track_dangling` warnings and one unconnected item. On the repository's
  own CopperTone board this resolves the entire two-pin surface: all five two-pin `F.Cu` nets now
  report `already_connected`, five of fourteen overall, with `kicad-cli pcb drc` corroborating by
  reporting zero unconnected items. No copper is proposed for that board — the nets it can reason
  about need none — and multi-pin routing remains the contract its other nine nets require.
- Diagonal selected-layer copper on a foreign net is now a conservative obstacle instead of a
  board-level refusal. The envelope is the Minkowski sum of the track's centreline with an
  axis-aligned square of its half width — the convex hull of the two squares at its endpoints —
  which provably contains the real track because the swept disc is inscribed in the swept square,
  and whose every vertex is an exact integer with no rounding rule to argue about. It is inflated
  by the routed half width plus the stricter of the routed and obstacle net-class clearances,
  exactly as the orthogonal path is, and charges one obstacle check per vertex against the same
  budget and `max_obstacles` ceiling as zones and keepouts. The cost is over-approximating the
  perpendicular extent by at most about 41%, worst at 45°, which can only refuse a route and never
  permit a violation. Orthogonal foreign segments keep their exact swept-rectangle fast path, so no
  board the router already accepted changes geometry or identity and `ROUTER_VERSION` does not move.
  Diagonal copper on the *routed* net still fails closed with a distinct diagnostic: an obstacle may
  be over-approximated, but attachment copper must be under-approximated or the router would claim
  a connection the board does not have, and a diagonal has no exact integer inner core yet. A
  committed `diagonal-blocker` fixture is verified against real KiCad 10.0.5 DRC and checked to be
  discriminating — the straight route it replaces is reported as `tracks_crossing` — and the
  envelope's superset property is covered by a test that samples the exact integer stadium across
  seven orientations.
- Selected-layer track keepouts are no longer required to be axis-aligned rectangles. A rule area
  with any simple polygon outline — including the octagonal mounting-hole areas KiCad emits, and
  concave outlines — becomes a conservative polygon envelope obstacle reusing the exact integer
  containment, inclusive intersection, and rational squared-distance geometry already built for
  foreign-net zone envelopes, with the same per-vertex work accounting and `max_obstacles` ceiling.
  A keepout carries no net and no clearance of its own, so the routed net's class clearance is the
  only rule that applies; that margin is deliberately stricter than KiCad, which prohibits only
  tracks that intersect the area. Rectangular keepouts keep their existing exact square-cornered
  inflation rather than being folded into the polygon path, because a Euclidean offset would round
  their corners into a strictly looser obstacle — so candidate geometry and identity on every board
  the router already accepted are unchanged and `ROUTER_VERSION` does not move. A committed
  `octagon-keepout` fixture is verified against real KiCad 10.0.5 DRC, and checked to be
  discriminating: a straight track through the same rule area is reported as `items_not_allowed`.
  Board IR already refuses curved and multi-loop rule-area outlines, so no keepout reaching the
  router is now unmodeled.
- Orthogonal same-net copper on the selected layer is now attachment copper instead of a
  partial-routing veto, so a half-routed net completes from its stub rather than being refused.
  Connectivity uses a second rectangle model that deliberately errs opposite to the obstacle model:
  obstacle rectangles over-approximate copper so clearance is never understated, while connectivity
  cores under-approximate it — dropping a track's round end caps, flooring half widths, insetting a
  `roundrect` by its corner radius, and reducing a `circle` to a centre line — so an electrical
  connection can never be claimed that the board does not have. Exact integer union-find over those
  cores decides components under the existing obstacle-work budget and cancellation cadence. A net
  whose pads already share one component returns the new terminal `already_connected` preview
  status carrying a typed `RouteConnection`, not a failure code; `include_drc` is skipped there
  because no copper is proposed. Otherwise a multi-source, multi-target search seeded from the
  covered lattice nodes proposes only the missing piece, using a target-bounding-box heuristic that
  stays admissible and consistent and reduces exactly to the previous estimate for a single target.
  Diagonal same-net segments, same-net vias and zones, and endpoint pads whose shape is not modeled
  exactly still fail closed, and attachment copper counts against `max_obstacles`. Boards with no
  same-net copper produce byte-identical geometry, costs, and metrics; only `ROUTER_VERSION`
  advances, to `astar-grid/0.3.0`. A committed `partial-route` fixture proposes 10 mm where the
  equivalent empty board needs 20 mm, verified against real KiCad 10.0.5 DRC for zero errors,
  warnings, and unconnected items. Coverage on the repository's own CopperTone board did not move at
  the time: removing the veto revealed that three of the five two-pin nets carry diagonal same-net
  copper, while the other two became genuinely attachable and failed on the next unmodeled object
  instead. (Both those measurements, and the ones in the entries below, were taken before the
  footprint-rotation fix recorded under Fixed; with pads placed correctly the two attachable nets
  report `already_connected`.) Attaching mid-stub rather than at a stub endpoint leaves
  copper with an unconnected end, which KiCad reports as a `track_dangling` warning. (Corrected
  while adding polygon keepouts above: this entry originally named octagonal keepouts, then the
  `GND` zone envelope, then an off-grid pad delta as the remaining chain for those two nets. Direct
  measurement shows foreign-net diagonal segments come first, and the last two are in the opposite
  order. The architecture doc carries the evidenced chain.)
- Canonical Circuit Intent IR `0.1.0` as a strict, immutable, content-addressed logical topology
  contract for two-pin resistors and non-polarized capacitors. A pure bounded adapter renders
  verified snapshots into byte-deterministic in-memory KiCad `20250114` schematics with original
  embedded symbols, empty footprints, tighten-only parser budgets, content-verified source/count
  provenance, source/artifact digests, exact 1.27 mm grid placement, global labels at every
  connection of port-backed nets, and no file, library, or network access. A shared service accepts
  strict snapshot JSON or structured content, normalizes it, and requires byte-identical double
  rendering. The CLI explicitly creates one new workspace `.kicad_sch` without overwrite; the
  stdio-only MCP tool returns redacted metadata plus a non-enumerable opaque resource whose access
  expires after 15 minutes in a 16-entry, 16 MiB process-local store. Expired objects are reclaimed
  lazily on later store activity or process exit; this is not a secure memory-erasure claim. An
  independently authored RC low-pass fixture passes
  schema/canonical checks and a real KiCad 10.0.5 SVG plus `kicadxml` connectivity round trip; the
  reviewed run preserved exact nets and reduced ERC warnings from seven to four, with two isolated
  external-port labels and two missing private-library-configuration warnings remaining. This is
  not an ERC-clean, electrical, board-parity, manufacturability, or fabrication-readiness claim.
- ADR-0015 defines a future Circuit Scene IR for bounded semantic and visual observation, typed
  placement intent, immutable previews/candidates, deterministic validation, and separately
  authorized apply. It does not add placement or permit direct AI mutation of KiCad.
- A licence-aware, network-free audio capability catalog and runner. Elliott Sound Products and
  diyAudioProjects.com are recorded only as non-redistributable reference sources; no
  project/article content, schematics, or downloads are copied or fetched. An independently
  authored low-voltage RC connectivity fixture and the existing open-hardware CopperTone board are
  bound with their exact licence bytes into one bounded validation snapshot, then exercised twice
  through the MCP-shared Board IR and route-preview services. The result demonstrates one routed
  two-pad audio net and typed multi-pad
  refusals, with claims derived from observed outcomes and kept disjoint from explicit non-claims;
  a local KiCad 10.0.5 parse/plot smoke test is kept distinct from DRC. This board-routing corpus
  does not itself claim circuit derivation, schematic-to-board parity, ERC, electrical validation,
  autorouted boards, or fabrication readiness.
- Foreign-net solid zones on the selected layer are now conservative polygon boundary-envelope
  obstacles instead of a blanket rejection. Concave and diagonal outlines use exact integer
  containment, intersection, and rational squared-distance checks under the strictest routed class,
  zone-net class, and zone clearance; bounds construction and every polygon relation consume the
  existing obstacle-work budget. Same-net zones remain unsupported, cached KiCad fill is not
  trusted, and CopperTone previewed zero of fourteen `F.Cu` nets at the time because nine are
  multi-pin and all five two-pin nets already carry same-net copper. (Corrected twice since: that
  last clause was true but misleading, because the partial-routing veto fired early enough to mask
  several further blockers; and the board's per-net measurements were themselves distorted by the
  footprint-rotation defect recorded under Fixed. The routing baseline carries the current
  numbers.) A committed `blocked-zone` fixture verifies deterministic read-only
  adapter-to-preview routing without claiming fill-aware KiCad DRC.
- Through vias outside the routed net are now selected-layer obstacles built from their outer
  diameter, rather than a board-level rejection. A via on the routed net still fails closed as
  partial routing. On the repository's own CopperTone board this moved the failure from "nine vias
  reject everything" to per-net diagnostics. Later re-measurement, after the footprint-rotation fix
  recorded under Fixed, shows two of fourteen nets reaching a terminal `already_connected` outcome
  and none routed; the remaining two-pin nets are blocked by diagonal copper on the routed net.
- Selected-layer pads and orthogonal segments outside the routed net are now exact rectangular
  routing obstacles instead of a hard rejection, so preview works on boards that already carry
  copper. Obstacles are inflated by the routed half-width plus the stricter of the routed and
  obstacle net-class clearances, round pad shapes over-approximate via their bounding box, and
  arcs, off-axis rotations, diagonal segments, and partially routed nets still fail closed. A
  committed blocked-pad fixture verifies the detour against real KiCad 10.0.5 DRC.
- Read-only Board IR inspection as the `inspect_board_ir` MCP tool and the `copper-mcp board-ir`
  command. It reports whether a board converts to the supported Board IR subset and describes its
  revision, snapshot and constraint digests, schema, units, copper layer identities, and object
  counts, or bounded conversion diagnostic-code counts. Coordinates, net names, pad and net
  identities, UUIDs, and source bytes are never returned.
- A bounded, non-mutating route preview exposed as the `preview_route` MCP tool and the
  `copper-mcp preview-route` command. It strictly validates an untrusted request, takes routing
  constraints only from typed caller values, reads one workspace board read-only, and reports
  `routed`, `not_routed`, or `unsupported_board` with the candidate geometry, exact cost
  decomposition, and deterministic search metrics, one typed non-echoing diagnostic, or bounded
  conversion diagnostic-code counts. A configurable wall-clock deadline starts at the operation
  boundary and bounds the whole call — conversion, search, and the clamped KiCad timeout for
  optional DRC — above the existing integer ceilings, and `include_drc` binds the proposal to
  aggregate authoritative KiCad DRC evidence or fails the call. Rejected requests report an
  unsupported-field count rather than echoing caller-supplied names. Durable jobs, persistence,
  export, and apply stay deferred.
- A bounded, integer-only, single-layer A* reference that produces content-addressed immutable
  two-pin candidates for a narrow rectangular Board IR subset, with exact boundary semantics,
  deterministic tie-breaking, preparation/search cancellation, independent grid/expansion/obstacle
  work ceilings, typed diagnostics with deterministic counters, and fail-closed geometry and API
  handling. Durable KiCad export, MCP exposure, preview, and apply are deferred.
- A bounded benchmark-only Dijkstra oracle plus a reproducible synthetic harness that verifies A*
  completion and exact optimal-cost agreement while retaining the expected no-path fixture and raw
  deterministic, runtime, and incremental-memory evidence. This is not a KiCad DRC or throughput
  claim.
- A pure, bounded KiCad route-patch bridge that accepts only an exact replayed A* candidate, appends
  deterministic native segments to new disposable board bytes, records CopperMCP writer provenance,
  precomputes native identities for collision checks, enforces total output-object limits, and
  requires native source-geometry identities plus a full Board IR round-trip match. An optional
  KiCad 10 integration test validates the synthetic fixture without mutating source or candidate
  files; durable export, preview, MCP, and apply remain deferred.
- Internal candidate-bound KiCad DRC orchestration that captures one bounded source/rule/library
  context, parses and exact-replay serializes only its captured board bytes, replaces the board only
  in memory, rechecks all context budgets, and returns frozen evidence binding candidate, Board IR,
  source, patched-board, patched-context, and strict aggregate DRC revisions. The derivative exists
  only in a private temporary directory; public ingestion, persistence, preview, and apply remain
  deferred.
- Canonical Board IR `0.1.0` with integer nanometre/microdegree geometry, typed routing constraints,
  strict canonical JSON, semantic and snapshot digests, bounded decoding, and a versioned JSON Schema.
- A bounded, read-only, fail-closed KiCad converter for the documented rectangular-outline subset,
  plus golden valid/invalid JSON and synthetic source fixtures and explicit architecture/ADR
  documentation. This converter does not route, mutate, preview, or apply board changes.
- Explicit solid-zone priority, pad-connection, and island-removal intent in Board IR, plus a
  version-pinned KiCad semantic preflight for copper/`Edge.Cuts` graphics and supported object fields.

### Changed

- Project metadata and `server_info` now identify the source as the `0.2.0` MVP-alpha; the latest
  public GitHub release remains `0.1.0` until the separate tag-and-release gate succeeds.
- Release-tag validation now requires both a dated changelog section for the version and an
  append-only `Ready` release-ledger authorization naming the exact fully checked source commit.
  The later tag commit may differ only in `CHANGELOG.md` and the release ledger. Authorization
  permits tagging but does not claim publication.
- Untrusted JSON request validation now lives in one shared `request_boundary` module, so field,
  type, range, boolean, and character rules cannot drift between public services.
- Ledger validation now rejects oversized, non-strict, non-finite, or content-address mismatched
  benchmark JSON artifacts.
- CodeQL `init`, `analyze`, and SARIF upload now move as one pinned v4 suite, and Dependabot groups
  future CodeQL suite updates so incompatible action generations cannot be proposed separately.
- Workspace path validation compares a caller-supplied absolute path against the resolved workspace
  root without resolving the caller's own path first. An absolute path spelled through a symlinked
  prefix, such as `/tmp/...` where the resolved root is `/private/tmp/...` on macOS, is therefore
  rejected fail-closed and must be spelled through the resolved path. Workspace-relative paths are
  unaffected.

### Fixed

- The KiCad adapter placed pads on rotated footprints at their mirror image. KiCad stores board
  coordinates with y increasing downward while its `(at x y angle)` angle is counter-clockwise on
  screen, so a quarter turn maps a footprint-local point `(x, y)` to `(y, -x)`; the adapter used the
  `(-y, x)` that a y-up reading gives. The 0° and 180° cases are identical either way, so the defect
  was invisible except at 90° and 270°, where it silently swapped the two pads of every rotated
  two-pad footprint. The convention is now pinned by a committed `footprint-rotation.kicad_pcb`
  fixture whose expected positions come from KiCad itself: each rotated footprint has a track drawn
  to where the corrected placement predicts its first pad, and a real KiCad 10.0.5 test asserts zero
  violations and zero unconnected items — which a mirrored turn could not produce, because the track
  would land on the neighbouring pad's net. `_transform` is used only for pad centres; footprint-local
  zones and graphics are separately refused and all other geometry is stored absolutely, so no other
  object class was affected, and pad `rotation_udeg` composition was already correct because it is a
  rotation sum rather than a coordinate map. **Board IR snapshot digests change for any board with a
  rotated footprint**, and with them route-candidate `base_revision` and candidate IDs; the committed
  golden `schema-valid.json` is regenerated, and its diff is exactly the two pad coordinates plus the
  digest. The Board IR `0.1.0` schema is unchanged — no field changed shape — so no version bump is
  warranted; this restores conformance rather than altering the format. Historical benchmark records
  under `benchmarks/results/board-ir/` retain digests computed before the fix and are no longer
  reproducible against current code, which is correct for dated evidence. On the repository's own
  CopperTone board the correction produces its first coverage: `L_ISO` and `R_ISO` are each two pads
  joined by one segment running between their centres and now report `already_connected`, which
  `kicad-cli pcb drc` corroborates by reporting zero unconnected items for the whole board.
- The deterministic passive-schematic layout now uses longer symbol leads, wider A4-aware
  component spacing, and grid-aligned reference/value offsets. The RC fixture's pin labels,
  component bodies, and visible properties no longer collide in the reviewed KiCad SVG. This is a
  readability baseline, not general or AI-driven placement.
- The tag-only publish job now passes its repository explicitly when creating a GitHub release, so
  it does not depend on a checkout in the isolated publish job.
- Board IR construction now normalizes direct content before hashing, aligns runtime/schema limits,
  restricts v0.1 to one hole-free outline and full-stack through vias, and keeps public writer output
  readable by default decoder budgets.

### Security

- KiCad subprocesses now receive a minimal allowlisted environment and private per-run HOME, KiCad,
  XDG, runtime, and temporary roots instead of inherited credentials or user-global KiCad settings.
  They run from a private working directory, accept only snapshot-confined file-table dependencies,
  and reject environment-expanded, absolute, remote, and plugin-backed URIs. The private state tree
  rejects symlinks and special files and is covered by the same per-file, file-count,
  cumulative-byte, and scan-time ceilings as captured design context.
- Schematic delivery separates redacted build metadata from exact bytes using an independent
  256-bit capability, a stdio-only bounded process-local store, uniform unavailable responses, and
  digest verification on every read. Workspace inputs are captured through descriptor-anchored,
  no-follow reads. CLI export requires the exact lowercase `.kicad_sch` suffix, is explicit,
  workspace-confined and create-exclusive, and cannot overwrite an existing path.
- MCP schematic wrapper, nested content, and structured output schemas are closed. Scalar, list, and
  extra-field failures are rejected without echoing attacker-controlled names or values.
- KiCad DRC reports are captured as no-follow, nonblocking regular files and decoded with duplicate,
  non-finite, depth, and value-count rejection. Evidence is accepted only after read-only validation
  of the complete private snapshot tree, including unrecognized side effects.
- Schematic artifact-store entries detach the exact content, digest, and size at insertion, so later
  alias mutation cannot change identity or evade aggregate byte accounting.
- KiCad and Board IR parsing now use quote-aware streaming S-expression tokens, a pre-DOM JSON
  lexical/structural budget pass, exact context-independent decimal conversion, bounded non-echoing
  diagnostics, and explicit rejection of unmodeled routing or non-default fabrication semantics.
- Candidate and ordinary DRC now share one fixed KiCad subprocess/report path; candidate evidence
  rejects stale source/rule/library context and any private input-context mutation, accepts
  documented finding exit code `5` as valid evidence only when it agrees with the strict report,
  requires violation-type totals to equal aggregate counts, and freezes copied counts against
  post-validation mutation.

## [0.1.0] - 2026-08-03

### Added

- Initial Apache-2.0 project foundation and governance.
- Secure, bounded inspection for `.kicad_pcb` files.
- Fixed-argument, read-only KiCad JSON DRC with bounded severity, connectivity, ignored-check, and
  violation-type summaries plus stale-context checks.
- Versioned board-manifest, DRC-summary, and candidate JSON schemas.
- MCP tools for server information, board inspection, KiCad DRC, candidate validation, and comparison.
- Correctness-first candidate ranking and routing backend contracts.
- GitHub issue forms, CI, CodeQL, dependency auditing, release automation, and project ledgers.
- A non-publishing release dry run that verifies the requested version, complete quality gate, and
  distribution artifacts before a version tag is created.
- A source-linked survey of open PCB autorouters and a modern CPU, multicore, exact-repair, GPU, and
  typed-ML research roadmap.
- Audio Board Lab #001, CopperTone: a separately licensed, board-first KiCad 10 stereo line-buffer
  preview with BOM, manufacturing exports, STEP model, renders, constraints, provenance, and a
  one-command read-only DRC and artifact-hash validation workflow plus explicit snapshot refresh.
- Public social-preview artwork and a factual KiCad development screenshot with provenance records.

### Fixed

- KiCad 10 named-net inspection now counts deduplicated item-level `(net "NAME")` declarations when
  legacy numeric top-level net declarations are absent.
- CopperTone uses stable semantic UUIDv5 identities and temporary default validation so unchanged
  board replays no longer replace native object identities or modify tracked files.

### Security

- GitHub Actions are pinned to reviewed immutable commits; the release workflow uses the current
  official attestation action and explicitly scoped artifact-metadata permission.
- Workspace confinement protects against parent-path and symlink escapes.
- Secret-bearing files, private boards, job stores, and generated artifacts are ignored by default.
- MCP network transport binds to loopback unless explicitly reconfigured.
- KiCad execution uses a validated executable, fixed arguments, discarded logs, a POSIX child-process
  file ceiling, cumulative byte/file-count and discovery-time bounds, non-overlapping snapshot
  lifetimes, timeouts, strict contract parsing, and before/after DRC-context revision checks.
- The development dependency floor excludes pytest versions affected by `PYSEC-2026-1845`.

[Unreleased]: https://github.com/seunghyukchoe/copper-mcp/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/seunghyukchoe/copper-mcp/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/seunghyukchoe/copper-mcp/releases/tag/v0.1.0
