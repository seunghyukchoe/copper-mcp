# Changelog

All notable changes are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- Rebound the held-out audio benchmark artifact to the reachable merged-main source commit after
  PR #51's squash merge. Its strict detached replay now proves the locally available source is in
  checkout ancestry before checkout; all bound inputs and recorded benchmark metrics are unchanged.

- Bound the optional NE5532 KiCad DRC child process using the core POSIX file-size wrapper and
  discard untrusted stdout/stderr instead of buffering it. The timeout, private child environment,
  aggregate evidence, provenance, and benchmark scope are unchanged.

- Hardened the optional NE5532 KiCad DRC benchmark observation against untrusted report output.
  Reports are now descriptor-anchored and byte-bounded before UTF-8/JSON decoding, then reuse the
  adapter's duplicate-key, non-finite, nesting, and structural budgets. Normal aggregate counts,
  provenance, and benchmark scope are unchanged.

- Corrected the exact-local-repair gate evidence. The preserved B-071 historical artifact used a
  related KiCad-derived fixture with 512 grid nodes, 20,000 expansions, 128 obstacles, and
  200,000 obstacle checks, so it is not the predeclared semantic experiment. B-072 independently
  reconstructs and equivalence-checks the pinned 256/5,000/64/100,000 source-`965d8fc` builder.
  Routing behavior and public surfaces remain unchanged.

- Restored deterministic render and apply replay for valid low-degree multi-pin candidates made
  before `batched-1-steiner-v1`. Replay now selects the candidate's recorded
  `astar-grid/0.4.0` component-MST behavior instead of reinterpreting it with the current
  one-Steiner default. Candidate bytes and identities are never refreshed; unknown or impossible
  historical router/order combinations refuse before rendering or applying copper.

### Added

- Added an opt-in, private route-aware placement ranking policy. It scores only candidates first
  issued by the deterministic legalizer, verifies their identities and exact snapshot/view bindings
  before rebuilding an immutable in-memory Board IR pose projection, and meters all independent A*
  probes against one operation-wide cap. The default same-net Manhattan ranking and public placement
  candidate shape remain unchanged. Three deterministic replays on the CopperMCP-original
  Apache-2.0 NE5532 fixture selected a legal candidate with a 23.8095% lower one-probe routed length
  (`42,000,000 → 32,000,000 nm`); this is not whole-board routing, congestion, KiCad DRC, or apply
  authority.

- Hardened the optional harness-owned KiCad/FreeRouting transaction behind an internal
  provider-created aggregate-quota workspace capability. The harness validates canonical
  owner-private, non-symlink roots; keeps temporary directories and child `cwd`/`HOME`/`TMPDIR`
  inside that boundary; and refuses before Java/KiCad probes, DRC, export, routing, import, or the
  optional runner when no provider is available. No provider is enabled on current macOS or Linux,
  so this adds no sandbox, parity, performance, or comparison-closure claim.
- Board IR and placement legality now accept a bounded exact courtyard topology: unfilled
  orthogonal `fp_poly` shapes and unordered closed orthogonal `fp_line` chains on the matching
  KiCad courtyard layer, alongside rectangles. The legalizer uses integer positive-area overlap
  rather than bounding boxes, charges its existing work budget, and refuses diagonal, curved,
  filled, open, branching, duplicate-edge, or otherwise unsupported shapes. The source fixture is
  resaved by KiCad 10.0.5 and has a zero-violation/zero-unconnected DRC oracle. Placement apply
  remains rectangular-only and does not rewrite this new observation topology.
- Added an Apache-2.0 CopperMCP-original NE5532-class stereo audio routing fixture with 14
  synthetic footprints, 35 pads, 11 nets, and no source copper. Its bounded benchmark invokes the
  public route-preview application service for eight independently replayed two- and multi-pin
  candidates, binds fixture/licence provenance, and records exact candidate IDs, path counts,
  lengths, vias, and search work. Optional KiCad 10.0.5 JSON DRC runs only on disposable,
  independent candidate derivatives; the recorded run reduced unconnected-item counts without
  claiming a clean or combined-board DRC result, electrical correctness, fabrication readiness, or
  FreeRouting parity.

- Added a read-only live KiCad IPC fidelity oracle. When launched by a KiCad plugin with the
  instance socket and token, it binds one confirmed live serialization to Board IR and Circuit
  Scene through redacted digest equality evidence. Missing plugin credentials produce a canonical
  non-failing capability skip before process settings are resolved; endpoint, token, timeout,
  session, version, and generic configuration failures remain distinct redacted results. One
  cooperative deadline spans capture and both conversion stages. This adds no MCP action, editor
  mutation, DRC, routing, placement, apply, or live-editor success claim.

- Added the first content-addressed held-out audio project-family evaluation. Its independently
  authored Apache-2.0 fixture is isolated from the predeclared training family by a hash-bound
  train/tune/held-out split, and the evaluator reads only the held-out board. Three exact replays
  recorded Board IR support, eight legal placement candidates after 96 bounded evaluations, and
  candidate-preview completion for 6/6 nets with no source mutation. No model, network, KiCad,
  DRC/ERC, policy-quality, routing-quality, external-project, fabrication, or hardware claim is
  inferred from this one-family baseline.

- Added a clean-worktree performance-profile baseline for file-backed routing, bounded placement,
  and Circuit Scene observation. Each scenario uses two warmups, five unprofiled timing samples
  with an invariant output digest, and one separate bounded `cProfile` pass with stable redacted
  function labels. The artifact identifies placement containment/intersection work as the largest
  measured seam on its single arm64/Python 3.14.2 environment; it adds no Rust, SIMD, GPU,
  acceleration, cross-machine, KiCad, DRC, or hardware-performance claim.

- Added a packaged standalone deterministic exact local-repair operator for a conventionally
  coordinator-supplied bounded lattice window. It emits only request-bound immutable local
  proposals with fixed failure/cancellation states. The predeclared 5 × 5 detour fixture replays
  identically 10/10 times at eight unit steps, two bends, and 50 expanded states; a one-expansion
  budget and cancellation publish no route. It remains outside negotiated routing, MCP, Board IR,
  KiCad, physical clearance, DRC, candidate application, and board mutation.

- Added a bounded, in-memory routing-task handle broker and a runtime MCP Tasks compatibility
  probe. The reference environment observed `mcp 2.0.0`, but the supported dependency range
  remains `<3`; the observed runtime lacks the current Tasks wire/dispatcher contract and
  CopperMCP lacks owner-bound durable task-handle lookup, so wire Tasks stay disabled and the
  ordinary routing-job tools remain the fallback.

- Negotiated multi-net routing now rejects a lattice-clean candidate set when its same-layer,
  cross-net orthogonal copper violates the stricter assigned net-class clearance. Generic routing
  backends are independently replayed through the deterministic reference core under a shared
  half-budget allocation before they can be accounted for or published. This is a bounded
  acceptance gate, not KiCad DRC or board-wide physical clearance.

- Added an internal, bounded deterministic placement-search baseline. It evaluates only
  legalizer-issued immutable candidates, scores the same-net Manhattan connectivity proxy in
  `O(n log n)`, and propagates one deadline plus cancellation through scoring and legalization.
  It is advisory and makes no KiCad mutation, routing-quality, DRC, or fabrication claim.

- Added a closed, advisory AI routing-policy seam for deterministic net ordering and
  coordinator-supplied corridor/repair-window selection. Its bounded, redacted trace omits board
  geometry and raw identifiers; policy output cannot emit copper or bypass deterministic routing,
  validation, DRC, or explicit apply authorization. Content digests remain linkable and are not
  secret redactions. The exact internal `deterministic-reference-v1` profile now influences only
  the initial negotiated net order; no-profile v2 result shape and candidate identity are
  unchanged. This adds no MCP, model, corridor, repair, or apply authority.

- Added an internal one-shot isolated worker protocol and admitted only its fixed
  `deterministic-reference-worker-v1` backend to negotiated routing's initial net order. It accepts
  the same neutral scalar, no-window input as the in-process reference; uses nonce- and
  digest-bound canonical responses with bounded timeout/cancellation and sanitized child process
  state; and rechecks the fixed policy identity, input digest, complete known-net permutation,
  empty selections, and composite candidate binding before router construction. Any worker failure
  refuses with no fallback or router call. Retry order, geometry, validation, and every routing
  budget remain coordinator-owned. This adds no model, plugin, MCP, corridor, repair, KiCad,
  copper, or apply authority, and it is not an OS sandbox.

- Added the first real FreeRouting smoke record through the bounded GPL-isolated process boundary.
  The official v2.2.2 JAR produced a valid SES for one CopperMCP-original two-pad fixture, and
  KiCad GUI 10.0.5 DRC observations recorded zero hard violations and zero unconnected items on
  both the imported FreeRouting result and CopperMCP's pure-kernel result. Source/report,
  source/DSN-export, import, and runner relationships remain self-attested rather than causal;
  the CopperMCP runner does not exercise MCP or the authorized apply service. The artifact
  therefore retains `comparison_closed=false` and `unavailable_or_incomplete`, with no parity,
  performance, whole-board, or sandbox-containment claim.

- Added an OpenSSF-informed sustainability and supply-chain roadmap. It separates the Criticality
  Score activity proxy from Scorecard controls, records the dated `0.23`/`5.8` baseline as an
  estimate and a distinct API snapshot, and defines engineering-backed gates for a defensible
  `0.4` target without synthetic project activity.

- Live placement proposals now preserve one operation-wide deadline after IPC capture: Board IR
  conversion, placement-view construction, legalization, optional evidence, and token preparation
  receive only the remaining budget. An expired post-capture budget returns a typed refusal instead
  of silently granting a second full placement window.

- Post-placement observation now validates the complete scene request before any workspace read,
  rejects stale board revisions before scanning DRC sidecars, and refuses padless footprint rule
  references before syntactic infeasibility analysis. These boundaries keep malformed/stale work
  fail-closed and preserve the supported placement contract.

- Negotiated-congestion routing now treats cancellation-callback failures as cancellation and
  never publishes a partial candidate from that iteration, including when a later net cancels an
  otherwise productive pass or when cancellation arrives before the next retry. Layered candidate verification also
  binds track width, via diameter, and via drill to the Board IR net-class assignment, rejecting
  re-stamped dimensions before topology acceptance.

- The post-placement observation benchmark now fingerprints workspace entry inode and mtime
  metadata in addition to bytes, mode, and symlink targets. Its replay can therefore detect
  metadata-only observer mutations instead of treating them as a clean workspace.

- Live route and placement proposals now create their operation deadlines before IPC capture and
  pass both the absolute deadline and remaining millisecond timeout into the bounded KiCad
  adapter. A slow snapshot cannot silently consume the adapter default beyond the proposal budget.

- Apply replay protection now retains every consumed nonce until its own expiry instead of
  evicting live entries under count pressure. The compatibility capacity hint is validated but
  cannot weaken single-use authorization after a pre-apply backup is restored.

- Durable routing-job lookup now commits TTL purges even for malformed or unavailable IDs, and a
  stale `CANCEL_REQUESTED` lease is terminally acknowledged instead of remaining stranded until
  retention expiry. The lifecycle remains redacted and compare-and-swap bound.

- Live KiCad IPC board counting now carries its cooperative operation deadline into bounded
  S-expression decoding, with pre/post-decode and 4 KiB scan checkpoints. Expired large or
  malformed snapshots fail with the typed deadline refusal instead of spending unbounded parse
  time first (ADR-0063, B-051).

- Placement legality now keeps supported rectangular courtyards from padless/graphics-only
  footprints as stationary collision envelopes. Padless objects remain unplaceable and absent from
  candidate manifests, but movable footprints can no longer be reported clear through them
  (ADR-0062, B-050).

- Added read-only `observe_post_placement`: a required-revision, single-capture Circuit Scene and
  aggregate KiCad DRC observation. It is fail-closed on stale or changed context and never exposes
  raw DRC output, issues apply authority, or changes a board (ADR-0061, B-045).

- Added a separately authorized `apply_placement_candidate` MCP capability for the bounded
  front-side, orthogonal, source-preserving footprint subset. File-backed previews issue a
  placement-scoped single-use token only when the exact replay accepts the candidate; the apply
  path uses operator opt-in, lockfile refusal, double CAS, a recoverable pre-apply copy, atomic
  replacement, and a typed `footprints_moved`/`bytes_changed` result. Side flips, unsupported
  footprint properties/graphics/library/3D-model syntax, no-op candidates, live IPC mutation, and
  post-placement DRC remain fail-closed under ADR-0059.

- Added a deterministic, CopperMCP-original Apache-2.0 synthetic RC audio-routing microcase.
  Repeated previews produce one reproducible candidate and one new copper segment in a disposable
  derivative; the source board is unchanged and the fixture makes no DRC, apply, production,
  fabrication, or hardware claim. Evidence is recorded in B-043.

- Added deterministic same-side courtyard legality to placement previews for the Board IR v0.2
  rectangular subset. The legalizer transforms proposed poses, checks exact integer rectangle
  overlap, treats front/back courtyards independently, and refuses overlapping candidates; custom
  courtyard clearance, general topology, placement connectivity, and apply remain open under
  ADR-0058.

- Added a bounded `kicad_schematic_parity` verifier for the passive Circuit Intent subset. It
  requires exact renderer replay and checks real KiCad format-E `kicadxml` component, pin, and
  net-node parity with bounded hostile-input handling; authoritative ERC and schematic-to-PCB
  parity remain open. The fixture and evidence are recorded under ADR-0056 and B-039.

- Added front/back (`F.Cu`/`B.Cu`) observation for orthogonal footprints with matching rectangular
  courtyard centerlines. The adapter preserves KiCad's authored board-frame child coordinates and
  does not apply a second mirror; GUI flip-save, general courtyard topology, placement legality,
  and apply remain open. The source/CLI oracle is recorded under ADR-0057 and B-040.

- Added a bounded, deterministic negotiated-congestion coordinator for two-pin nets on one
  signal-layer lattice. It uses present and historical edge/vertex pressure to reroute conflicted
  candidates, binds each accepted candidate to the policy digest, and records structural overflow
  evidence in B-036. It remains candidate-only: exact physical clearance, multilayer vias, KiCad
  DRC, apply, and FreeRouting parity are not claimed.

- Added an internal, candidate-bound KiCad DRC gate for the supported placement serializer. It runs
  against a disposable private context, rechecks source/rule/library CAS, and returns only a
  redacted aggregate summary; public/live placement and apply remain unchanged.

- Added opt-in, file-backed placement DRC evidence to `preview_placement`. `include_drc: true`
  replays the immutable candidate through the same private KiCad context gate and returns only
  candidate/source/patched-board/context digests plus aggregate findings. `passed` remains the
  hard error/connectivity signal while `clean` is stricter about warnings, exclusions, and ignored
  checks; live placement, apply authority, raw reports, and fabrication claims remain excluded.
  Evidence is recorded in B-044.

- Added a redacted, deterministic unsigned in-toto Statement payload to candidate-bound DRC
  evidence. The Link v0.3 payload binds the candidate and board revisions by digest, carries only
  aggregate DRC byproducts, and is validated at the MCP boundary; DSSE signing and verification
  remain intentionally deferred.

- Added a deterministic conservative spatial index to the A* and benchmark Dijkstra obstacle
  hot path. Exact integer legality predicates remain authoritative, small/pathological boards
  fall back to linear scans, and candidate identity advances to `astar-grid/0.6.0` with policy
  `orthogonal-a-star-spatial-index-v1`. B-033 records differential route equivalence and the
  fixture-bounded reduction in exact obstacle relations; no congestion, FreeRouting, DRC, or
  fabrication claim is made.

- Added opt-in candidate-bound authoritative KiCad DRC evidence to file-backed
  `preview_layered_route`. The closed response binds candidate, base, source, patched-board, and
  DRC-context revisions while returning only aggregate findings; live layered preview and durable
  routing jobs reject the flag instead of silently ignoring it. This remains a narrow two-signal-
  layer proposal signal, not whole-board, refill, fabrication, or FreeRouting authority.

- Hardened layered DRC evidence with a strict `clean` signal distinct from the hard-gate
  compatibility field `passed`: warning, exclusion, unconnected, or ignored-check findings can no
  longer be presented as a clean report. The public boundary now rejects malformed or
  candidate-unbound authority, with warning-only and malformed-authority regressions recorded in
  B-038.

- Added a bounded `batched-1-steiner-v1` ordering policy for low-degree multi-pin nets. It keeps
  the deterministic A* core and all geometry validation authoritative while reducing the recorded
  four-pad fixture's wire length from 48 mm to 42 mm; no Steiner-optimality or FreeRouting parity
  claim is made.

- Added a bounded, restart-safe routing-job repository and ordinary MCP lifecycle surface. The
  file-backed two-signal-layer queue persists deep-frozen normalized requests, redacted manifests,
  and separately authorized content-addressed candidate geometry behind TTL/capacity limits;
  `start_routing`, `get_routing_job`, `cancel_routing_job`, and `export_routing_candidate` never
  apply copper, accept board bytes, or claim MCP Tasks compatibility.

- Added a bounded SQLite `CandidateManifestStore` for restart-safe, content-addressed candidate
  summaries. It persists only redacted identity, endpoint, cost, and metric metadata; route
  geometry, board bytes, DRC findings, durable export, and MCP Tasks remain separate capabilities.

- Added a protocol-independent `RoutingJobWorker` with one active CAS-backed lease, cooperative
  cancellation, stale-lease recovery, and fail-closed invalid-candidate publication. The worker
  stores only the existing redacted job record; candidate persistence/export and MCP Tasks remain
  deferred until their request/result and authorization contracts are pinned.

- Added a pure, bounded `verify_layered_candidate` gate for layered route topology. It binds
  candidate identity, Board IR revision, endpoints, layer transitions, path/via continuity, and
  duplicate/crossing geometry before the disposable serializer; physical validation remains an
  explicit `not_modelled` result.

- Added a transport-independent, revision-safe routing-job ledger. `RoutingJobStore` persists only
  bounded redacted JSON records in SQLite, supports idempotent content-addressed creation,
  compare-and-swap transitions, cooperative cancellation, restart rehydration, TTL/capacity
  limits, and candidate ID/base-revision binding. It does not run background routing, persist
  candidate geometry, expose MCP Tasks, export boards, or grant apply authority.

- Added `preview_live_layered_route`, a read-only MCP proposal for bounded two-signal-layer routes
  against the exact active KiCad IPC snapshot. It requires pad references plus source, Board IR,
  and redacted KiCad-session compare-and-swap digests, reuses the file-backed candidate oracle,
  and remains candidate-only with no DRC, refill, serializer, persistence, or apply authority.

### Fixed

- `inspect_live_board` now returns the opaque, fixed-format PBKDF2 session CAS as required
  structured output when KiCad supplied a plugin token, or explicit `null` when it did not. This
  makes the public inspection → live-scene → layered-preview flow composable while keeping the
  token and process salt private; changed tokens and fresh CopperMCP processes still refuse stale
  live-route requests.

- Placement validation now preflights every declared subject in request order before syntactic
  infeasibility analysis. A known padless subject paired with an unrelated front/back contradiction
  now returns the established fixed `unsupported_geometry` refusal with no candidate.

- Placement validation now preflights every explicit proposal anchor that names a known padless
  footprint after rule references and before syntactic contradiction analysis. For each supported
  anchor point (`center`, `north`, `south`, `east`, and `west`), that mixed request returns the
  established `unsupported_geometry` refusal with no candidate instead of allowing unrelated
  contradictory side rules to mask it as `infeasible_constraints`. Self-anchored proposals and
  pure contradictions retain their existing behavior. This changes validation ordering only: it
  adds no anchor geometry, padless placement, non-padless placement behavior, DRC, apply, or board
  mutation capability.

- Replaced the public unkeyed `sha256(KICAD_API_TOKEN)` live-session fingerprint with the fixed
  `hmac-sha256:<64 lowercase hex>` wire type. A fresh 256-bit process-local key and
  domain-separated HMAC-SHA256 make this precondition opaque, use constant-time comparison at
  the session CAS checks, and deliberately refuse preconditions from a fresh process/restart.
  The token and process key remain absent from outputs, errors, logs, candidates, and ledgers.

- Superseded the prior HMAC session-revision derivation with fixed-work
  `pbkdf2-hmac-sha256:<64 lowercase hex>`. The process-local 256-bit salt is domain-separated
  and non-persistent; the fixed 200,000-iteration PBKDF2-HMAC-SHA256 derivation keeps
  limited-input token guesses computationally expensive and remains bounded for local CAS.
  The HMAC history remains recorded in D-127/SEC-103/R-102; legacy HMAC and unkeyed SHA-256 wire
  values are refused.

- Durable routing jobs now validate every immutable candidate-to-job completion binding and the
  exact `RUNNING` lifecycle revision before writing bounded, owner-bound candidate artifacts, then
  revalidate at the final lifecycle CAS. Invalid candidates, and direct queued, terminal,
  cancel-requested, or stale-revision calls, leave no export or manifest; a worker returns a fixed
  invalid-request failure while the direct publisher leaves its revision-bound job retryable.
  Valid artifacts still publish
  before completion, so capacity/serialization failure cannot create a completed job without an
  export. A concurrent completion race can leave only an unreadable TTL-bounded orphan. Request
  expiry and invalid cancellation text now also reach both request and lifecycle retention cleanup
  before their fixed refusal.

- Corrected the public policy benchmark provenance contract to name the artifact's
  `evidence_harness_commit` field: the stable source/harness commit used by the embedded replay
  command. The separately recorded artifact materialization commit remains distinct; no replay
  artifact, measurement, policy authority, or routing behavior changed.

- Hosted CI now checks out full Git history before running tests, so benchmark regressions that
  replay an immutable `evidence_harness_commit` with `git show` do not fail only because the source
  commit was omitted by a shallow clone. A workflow regression pins `fetch-depth: 0`; this proves
  repository configuration, not hosted-run success or the correctness of a benchmark artifact.

- Expired routing-candidate geometry exports now commit their TTL purge before returning the
  deliberately uniform unavailable response. This preserves the access-retention boundary for
  stored `candidate_json`; TTL is not a secure-erasure guarantee for SQLite, backups, or copies a
  caller already received.

- The advisory placement solver now checks cooperative cancellation again at its publication
  boundary. When cancellation is observed, it returns an empty ranked-candidate set rather than
  exposing a partially explored ranking; this does not hard-interrupt already-running work.

- Unavailable routing-request lookups and unauthorized live candidate-export lookups now commit
  any prior TTL cleanup before returning their uniform unavailable response. An unauthorized
  live export remains intact, while unrelated expired private records are removed; decode and
  integrity failures still roll back, and TTL is not secure erasure.

- Malformed routing request and candidate-export handles now also begin and purge their stores
  before the uniform unavailable response, committing expired private-record cleanup. Lookup
  timestamps remain validated before any transaction, and TTL remains an access-retention policy,
  not secure erasure.

- Public routing-job/export lookups now defer store-owned handle validation until after retention
  cleanup, and candidate-manifest lookup purges before malformed-ID not-found handling. Fixed
  diagnostics and no-disclosure behavior are unchanged; timestamps remain validated first and
  TTL is not secure erasure.

- MCP routing get, cancel, and export entrypoints now accept handles broadly enough to reach the
  purge-first service/store boundary. Their payloads remain closed, diagnostics remain fixed and
  non-disclosing, valid requests are unchanged, and TTL is not secure erasure.

### Changed

- Corrected the spatial-index benchmark to count bucket candidates examined by the exact bounds
  predicate rather than only final hits. The regenerated B-037 artifact reports `636` indexed
  candidates versus `131,072` linear checks (`99.5148%` reduction); the older `31` metric is kept
  only as historical evidence and is no longer used for performance claims.

### Security

- Added a deterministic offline MCP excessive-agency regression evaluation. With apply enabled it
  calls the public route and placement apply handlers using syntactically valid but unauthorized
  tokens, requiring structured `invalid_token` before source access; it also covers closed
  request schemas, stale revision, quotas, and output/report disclosure. The disposable workspace
  comparison includes content, permissions, and metadata but the artifact records only stable
  unchanged assertions. It invokes no model, network, KiCad process, or board mutation, and does
  not evaluate application logging because the current source has no application logger sink.

- `make security` and the hosted security workflow now pass `.` to `pip-audit`, resolving the
  default production dependency graph instead of reporting unrelated packages installed in the
  ambient interpreter. Project-path mode excludes every optional group (`dev`, `security`, and
  `kicad`) even though CI installs `.[security]` to run the auditor; those groups need separate
  explicit audits or lock evidence. Secret scanning is unchanged.

- Hardened placement application after review: bounded manifest pose, grid, legality, and evidence
  fields are rejected before board parsing; the destructive MCP request is closed at its nested
  boundary; post-rename and post-publication paths spend the capability exactly once; and the
  final board revision is re-read after guarded recovery so an observed rollback cannot report a
  stale published digest. Published placement bytes are re-rendered and reparsed before a success
  response, followed by a final best-effort digest observation that catches a visible rewrite after
  verification. `applied_but_unverified` now explicitly permits a restored original revision or
  concurrent bytes, and uses a null after-revision when the board cannot be observed; clients use
  the reported digest and diagnostic rather than assuming that the authorized revision remains on
  disk.

- Route and placement apply now distinguish an unreadable or missing board at the final
  publication observation from the expected digest: the capability is consumed and the result is
  `applied_but_unverified` with a null after-revision instead of a false `applied` response.

- Placement DRC evidence is now a public, read-only opt-in only for the documented file-backed
  serializer subset. KiCad runs with fixed JSON DRC arguments and no refill/save flags against a
  disposable context; source bytes, inode, and mtime remain unchanged, and malformed, stale, or
  unbound evidence fails closed at the MCP contract boundary.

- Routing-job and candidate-manifest TTL misses now commit their expiry purge before returning
  the uniform unavailable diagnostic, so expired board-derived metadata cannot reappear after a
  read-only miss. The boundary remains redacted and bounded; no routing or mutation authority is
  added.

- Routing workers now clear their in-memory lease even when an expired job disappears during
  cancellation acknowledgement or publish-race resolution. This prevents a worker from being
  stranded after a bounded store miss and adds no retry or mutation authority.

- Live KiCad capture now checks the shared deadline while traversing the bounded serialized
  S-expression and preserves the typed deadline refusal during confirmation. The official
  synchronous IPC wrapper remains cooperative: a blocking third-party call cannot be forcibly
  pre-empted by this Python process.

- Closed the latest routing review-bot boundary gaps: public scene references now carry only
  content-derived net identifiers; live IPC capture carries one cooperative operation deadline;
  job failures persist fixed typed diagnostics; candidate completion and manifests bind request
  identity, router policy, seed, and work budgets; expired manifest rows are purged transactionally;
  and layered obstacle envelopes remain conservative before budget exhaustion. These changes add no
  mutation, remote transport, or general multilayer authority.

- Tightened the in-toto MCP contract so every resource descriptor has a required, closed nested
  `sha256` digest object; `{}` and unknown digest algorithms now fail validation. B-034/B-035 were
  replayed from the implementation commit and recorded as append-only current-contract evidence.

- Layered serialization now refuses structurally disconnected, crossing, duplicate, stale, or
  endpoint-via candidates before rendering. The layered router reserves endpoint pad envelopes for
  tracks and blocks via transitions there, avoiding unsupported via-in-pad geometry without
  claiming fabrication legality.

- Added `preview_layered_route`, a loopback/file-backed, read-only MCP boundary that requires
  both source and Board IR compare-and-swap digests, infers net identity from two pad references,
  validates bounded settings, verifies candidate digests, and redacts board text/net names. It
  cannot request DRC, refill, serialization, export, persistence, or apply authority.

- Layered routing now validates every obstacle and search-budget field before reporting resource
  exhaustion, and its physical envelopes include the candidate track half-width/via radius plus
  explicit zone clearances. Malformed requests cannot be reclassified as stale revisions or escape
  the non-throwing diagnostic contract. Fresh fill obstacles apply the same governing zone-clearance
  rule as conservative zone envelopes.

- `preview_live_placement` is revision-bound and read-only: malformed requests fail before IPC,
  stale board/snapshot digests stop before candidate work, and no KiCad write, DRC, fill,
  apply-token, or raw source can be requested through the contract.

- Live IPC-to-scene conversion now keeps the exact UTF-8 snapshot paired with its redacted digest
  and refuses a caller-supplied board or Board IR snapshot revision mismatch before returning a
  scene. The live tool uses the literal `board: "live"`, refuses render delivery, and does not
  grant routing, placement, DRC, or apply authority.
- Live-scene requests are now fully preflighted before any IPC connection or board serialization:
  malformed constraints, regions, layers, unknown fields, and the unsupported render flag fail at
  the application boundary, keeping invalid MCP traffic from driving expensive KiCad reads.
- The optional KiCad IPC observer is constrained to a local IPC socket, bounded by a connection
  timeout and board-size/object-count ceilings, and refuses a future KiCad API version unless an
  explicit development-only opt-in is supplied. It returns only numeric versions, a board digest,
  byte count, object counts, and the socket kind; socket paths, tokens, board text, names, UUIDs,
  and geometry never cross the MCP boundary. The bundled KiCad plugin exposes the same read-only
  surface and does not mutate an open document.
- KiCad IPC version validation now fails closed when the official binding returns a false result.
  Observer counts come from the captured serialization rather than mutable per-object getters,
  count only direct board-level net declarations, include circular graphics, and require a second
  serialization to match before the revision is accepted. The wrapper still
  allocates its complete response before Python can enforce the size ceiling; this residual API
  limitation is documented, while bounded parsing and removal of extra collection materialization
  reduce avoidable memory exposure.
- Live IPC clients are now closed on every observation success and failure path, and live layered
  proposals pass the remaining bounded route deadline into the official client. A hashed
  `KICAD_API_TOKEN` session precondition prevents identical board bytes from silently crossing a
  KiCad instance restart; the raw token remains outside all outputs and logs.
- Scene-selected routes now require both the observed board revision and Board IR snapshot digest.
  Stale source bytes are refused before Board IR conversion, while a stale snapshot is refused
  immediately after it; neither can reach route search, fill authority, DRC, or apply-token
  issuance. Shared request text now also rejects invalid Unicode surrogates before path handling or
  net hashing, and malformed MCP requests remain behind the fixed non-echoing application boundary.
- Board IR 0.2 makes footprint ownership revision-bound and budgeted: footprints count against the
  object ceiling, courtyard vertices and intersection work use polygon ceilings, one footprint is
  capped at 64 courtyard rings before geometry allocation, and Circuit Scene charges serialized
  pad relationships against its detail budget. Placement projection re-verifies canonical snapshot
  ordering and digest binding, applies caller-tightened Board IR limits before projection, and
  rejects forged footprint content instead of issuing a view under a stale digest. Locked
  footprints now refuse movement proposals.
- Candidate apply now holds an **exclusive `flock` across the compare-and-swap and the rename**,
  closing a confirmed concurrency hole: two applies from the same base both passed the checks and
  one silently destroyed the other. The board's digest is re-verified under the held lock
  immediately before the rename, so the second apply sees the first's bytes and refuses. The
  earlier docstring claimed a lock that did not exist; the lock is now real and the claim true.
- The post-publish rollback is now **guarded**: it restores the pre-apply bytes only if the file
  still holds exactly what the apply wrote. The likeliest cause of a verification failure is a
  concurrent writer, so the previous unconditional restore was itself the data loss it was meant
  to prevent - a KiCad save landing after publication would have been overwritten by the "safety
  net". A third party's newer write is now left intact.
- A failure *after* the rename is reported truthfully as a new `applied_but_unverified` status
  with the real post-apply revision, never as a refusal claiming "nothing changed". Previously the
  four post-rename failure sites (directory fsync, re-read, parent-identity checks) mapped to a
  refusal with a null after-revision while the board was already mutated, making
  `board_revision_before` a stale lie.
- The KiCad lockfile is **re-checked under the lock immediately before the write**, not only
  seconds earlier. A GUI opened between the up-front check and the write would otherwise have its
  next save silently overwrite the applied board.
- An uncaught `KiCadRoutePatchError` no longer escapes the destructive tool on a legal board.
  A board whose outline carries a derived rather than native identity is rejected as a typed
  `splice_assertion_failed` refusal, and `preview_route` no longer mints an apply token for a
  board the append-only engine could never apply to - or when apply is disabled.
- The apply token is verified **before** the board is read and parsed, and the candidate geometry
  in a manifest is bounded before any of it is materialised, so an unauthorized or oversized
  request cannot drive the expensive pre-authorisation work.
- Consumed apply-token nonces are swept by **expiry rather than a count cap**. A FIFO cap could
  evict a still-valid nonce and re-enable a replay, and the documented undo restores the exact
  revision that nonce was bound to.

### Changed

- Freshness-verified foreign KiCad fill islands now replace the conservative whole-zone routing
  envelope in the deterministic A* core. Matching zone/source revisions are required, stale or
  unmatched fill fails closed, explicit zone clearance is retained, and `preview_route` now returns
  the freshness-bound authority on routed candidates with a typed `routing_effect` when
  `include_fill_authority` is requested.
- Layered two-signal-layer candidates now have an internal, replay-bound authoritative KiCad DRC
  gate. The gate serializes only a disposable derivative, preserves source bytes, binds the
  complete private DRC context, and returns redacted aggregate evidence; it is not exposed through
  MCP/CLI and does not grant apply authority.
- Placement candidate rendering now preserves arbitrary exact Board IR pad angles while retaining
  the orthogonal-only restriction on parent footprint poses. Layered A* now reports a distinct
  obstacle-count budget diagnostic when a structurally valid request exceeds its configured ceiling.

- Added an internal, Board IR-bound two-layer routing proposal adapter. It resolves exact
  nanometre grid geometry, net-class width/clearance/via dimensions, foreign physical envelopes,
  track versus via-only keepouts, immutable candidate identity, and fail-closed stale/off-grid/
  unsupported diagnostics. This remains proposal-only: the public MCP wrapper exposes only the
  bounded candidate contract; it does not serialize KiCad segments or vias, invoke DRC, mutate a
  board, or claim production routing through vias.

- Added a request-replayed, source-preserving serializer for the layered proposal seam. It emits
  deterministic segment and full-stack through-via expressions, canonicalizes reversed via
  transitions to KiCad copper-stack order, rejects native-identity collisions, and requires a
  Board IR round trip equal to the source plus the candidate geometry. It remains disposable and
  candidate-only: no file write, KiCad invocation, DRC, MCP exposure, or apply authority.

- Added an internal, non-public two-layer A* search oracle with explicit via transitions,
  deterministic tie-breaking, per-layer cell obstacles, cancellation, and bounded resource
  accounting. It is algorithmic evidence toward via-capable routing only; it does not produce
  Board IR/KiCad candidates, change existing single-layer candidate IDs, or claim DRC validity.

- The KiCad IPC plugin README now documents the required copy into KiCad's configured PCB plugin
  discovery directory. Installing `copper-mcp[kicad]` alone does not register the hardware-side
  manifest or action, and the plugin remains intentionally outside the Python wheel.

- `preview_route` accepts exactly one net selector: the existing private KiCad `net` name or a
  Circuit Scene `net_ref_id`. The MCP tool now advertises a closed two-variant input schema and a
  complete closed, status-specific structured-output union instead of an open object; impossible
  candidate/connection/diagnostic combinations are no longer advertised. Its annotation is
  conservatively non-idempotent because `include_apply_token` can mint a fresh capability even when
  the candidate geometry is deterministic.
- The active Board IR writer and decoder now target exact `copper.board-ir` `0.2.0`. Historical 0.1
  schema and golden data stay immutable, while migration requires re-converting the original board
  because flattened 0.1 pads cannot recover trustworthy parent identity or pose. Snapshot digests
  change; constraint digests do not change from footprint-only data.
- Placement grouping is now projected from the same Board IR snapshot that carries the pads and
  rejects mismatched source bytes, replacing the second out-of-band KiCad identity parse. Route
  serialization also requires native footprint identities before producing output.
- File-backed placement previews now honor optional board and Board IR snapshot compare-and-swap
  digests before placement-view/legalizer work; stale requests return a typed refusal instead of
  echoing unverified preconditions.
- Applied and backup files now keep the board's own **permission bits** instead of collapsing to
  `0600`, so group and CI readability and hard links survive an apply.
- Pre-apply copies are written into a `.copper-mcp-backups/` subdirectory, not beside the board
  where a `pre-apply.kicad_pcb` would itself be a valid apply target and cascade, and are pruned
  to a bounded count per board so a preview→apply loop cannot exhaust the disk.
- The `apply_disabled` refusal now reports the canonical workspace-relative path and no longer
  synthesises a `sha256("")` digest for a board it never read; the refusal's revision is null.
- `replace_workspace_file` gained the confinement-preserving lock, compare-and-swap, mode
  preservation, and pre/post-rename failure split described above; `resolve_workspace_relative_path`
  resolves a confined path without reading the file, for the pre-authorisation checks.


### Added

- Added the public, candidate-only `preview_layered_route` MCP tool for the narrow two-signal-layer
  Board IR router. It returns closed structured output with per-layer paths, full-stack vias,
  deterministic metrics, and typed stale/unsupported/no-path diagnostics. B-024 records ten
  schema-valid deterministic replays, stale CAS refusals, and unchanged source bytes; it does not
  claim general multilayer routing, KiCad DRC, fabrication readiness, or FreeRouting parity.

- `inspect_live_editor_context`, a read-only MCP surface for the active KiCad layer and bounded
  native selection refs, bound to the raw board serialization and editor-context digests. It now
  avoids treating a constraint-profile-dependent Circuit Scene snapshot as an IPC serialization
  precondition. The FreeRouting comparison note documents its heuristic maze/rip-up architecture
  and a common-board benchmark protocol; no general routing-quality superiority claim is made.

- An internal source-preserving KiCad placement projection for the supported front-side,
  orthogonal, unfilled-courtyard footprint subset. It is candidate-only, revision-bound, rejects
  forged or incomplete placeable sets, preserves padless mechanical footprints and unrelated
  source bytes, and reparses the disposable result against the expected Board IR transform.
  Placement DRC, live editor mutation, undo, and post-action observation remain separate gates.

- `preview_live_placement`, a deterministic placement proposal over one byte-confirmed active
  KiCad IPC snapshot. It reuses the file-backed legalizer and requires both Circuit Scene digests;
  B-014 records fake-client equality, replay, stale-precondition, and zero-mutation evidence.

- `preview_live_route`, a read-only MCP route-proposal tool that consumes a Circuit Scene
  `net_ref_id` plus both board/snapshot revisions, converts the exact active KiCad IPC snapshot,
  and returns the existing deterministic candidate contract. DRC, zone refill, apply-token
  issuance, live editor mutation, and real-session success remain explicitly unclaimed; B-013
  records the fake-client oracle, stale-session refusals, and zero-call action preflight.
- `observe_live_board_scene`, a read-only bridge from the active KiCad IPC document to Circuit
  Scene `0.2.0`. It reuses exact Board IR geometry and author-text quarantine, with optional
  compare-and-swap digests for stale-session refusal; live action gates remain separate.
- An optional official `kicad-python` integration: the read-only `inspect_live_board` MCP tool and
  `hardware/kicad-ipc-plugin` action provide a redacted live-board observation contract while
  keeping placement, routing, DRC, and candidate application behind separate validated gates.
- A reproducible MCP observation-to-action benchmark over the licensed RC low-pass audio fixture.
  It pins the former 0/3 actionable Scene references against 3/3 revision-bound references, exact
  candidate equality with the hidden-name oracle, stale-reference refusal, deterministic replay,
  closed schemas, and an identical final private-workspace file tree.
- First-class immutable Board IR footprints with exact origin, normalized rotation, side, lock
  state, total pad ownership, and canonical board-frame rectangular courtyard rings. A compact
  KiCad fixture pins all four orthogonal transforms and passes KiCad 10.0.5 DRC with zero violations
  and zero unconnected items.
- Circuit Scene IR `0.2.0` footprint objects. Region queries can anchor on a footprint and return
  revision-bound pose, side, pad IDs, supported courtyard rings, lock state, and reference
  durability without exposing footprint names, values, properties, or other author text.
- Board IR 0.2 JSON Schema, golden/invalid fixtures, migration guidance, and ADR-0026. The schema
  requires `items.footprints`, closes every nested object, and preserves the 0.1 schema unchanged.
- `apply_candidate`: the first and only operation in this project that changes a user's board.
  It applies **route patches only**, and three independent things must hold before a single byte
  is written. The operator must have set `COPPER_MCP_ALLOW_APPLY=1` — matched as exactly `"0"` or
  `"1"`, because `bool("false")` is `True` and a flag that enables board mutation must not be
  switched on by an ambiguous spelling. Over MCP the caller must present a single-use token that
  `preview_route` issued for exactly this candidate, board revision and path, verified with
  `compare_digest` against an HMAC key that exists only inside the running process — so a model
  cannot mint one, and outstanding tokens do not survive a restart. And KiCad must be closed: a
  `~name.lck` sibling is a hard refusal that names the file and is **never removed**, because
  pcbnew has no external-change watcher and would silently overwrite the applied board on its
  next save.
- Revision-race protection. The whole-file digest and the Board IR snapshot digest are compared
  before the splice, and the file digest again immediately before publication; a mismatch returns
  `stale_candidate` and is **never auto-refreshed**, because re-routing against copper the caller
  has not seen would apply a proposal nobody approved.
- A timestamped, content-addressed pre-apply copy written beside the board before anything is
  replaced, with its path returned. **That copy is the undo, and restoring it is manual** — there
  is no `undo_apply` tool and no journal, and it never appears in KiCad's undo stack. KiCad's own
  `-bak` files are never read, written, or removed.
- `replace_workspace_file`, the project's only clobbering primitive, placed beside
  `create_workspace_file` so it inherits the same descriptor-anchored no-follow walk, symlink
  refusal, and post-write read-back verification. It writes an `O_EXCL` temporary in the target's
  own directory, `fsync`s it, renames over the name through a held directory descriptor, and
  `fsync`s the directory. `os.rename` rather than `os.replace`: on POSIX both are the same
  `renameat` syscall and both replace atomically, while `os.replace` does not accept `dir_fd` on
  macOS and would have forfeited the descriptor anchoring.
- Unsafe-filesystem refusal where it is cheaply detectable. `statvfs` names the filesystem on
  macOS and the BSDs but not on Linux, so a negative result means *not detected*, never *known
  safe* — which is why detection refuses rather than reassures.
- `apply_candidate` stays **listed even when applying is disabled**, refusing with
  `apply_disabled`. Hiding it would make the capability undiscoverable and invite retry loops; a
  tool that vanishes when a flag is off looks like a broken server rather than a locked door. Its
  annotations declare `destructiveHint: true` and `readOnlyHint: false` truthfully, but they are
  advisory client hints and enforce nothing — authorization is the flag and the token.
- A `copper-mcp apply-candidate` CLI command. It deliberately takes **no token**: the signing key
  lives only in the issuing process, so a token from an earlier `preview-route` run could never
  verify in a later `apply-candidate` run, and requiring one would be a flag satisfiable only by
  a value the same process just invented. The CLI's authorization is the operator flag plus the
  `--expect-board-revision` compare-and-swap the operator states explicitly.

### Fixed

- `inspect_live_editor_context` no longer treats a Circuit Scene's constraint-profile-dependent
  `snapshot_digest` as the raw IPC serialization precondition. The context read now binds to the
  observed `board_revision`; its response alias remains explicit, and `context_digest` still
  protects follow-up selection/layer reads.

- Added a revision-bound `inspect_live_editor_context` MCP surface that reports only KiCad's
  active layer and bounded native selection references. Unknown/empty selections, raw selection
  strings, and editor mutations are refused or never read.

- A failure while writing the pre-apply copy now returns a typed `backup_failed` refusal instead
  of escaping as an uncaught `OSError`. Found by crash injection: no copy means no way back, so
  the apply must stop rather than proceed without one.

### Added

- A byte-preserving span-splice layer over the KiCad S-expression parser (`adapters/cst.py`):
  expression spans, an overlap-rejecting `Splice`, and a source-level splice that decodes once and
  encodes once. It extends the existing parser rather than adding a second tokenizer, which would
  have duplicated the budget ceilings that keep parsing bounded. **Offsets are character indices,
  not byte offsets** - the parser decodes strictly before tokenizing, and both reference boards
  contain multi-byte characters (an em-dash in CopperTone, a `µ` in the Board IR subset fixture),
  so conflating the two would corrupt exactly the boards under test. Splicing in the character
  domain is nonetheless byte-exact because strict UTF-8 decoding round-trips, which is asserted on
  all 26 committed boards. Overlapping splices are refused outright rather than resolved: every
  resolution rule - last wins, longest wins, merge - is a silent guess about intent.
  `_rewrite_writer_metadata` was refactored onto the new module, with the existing candidate-DRC
  suite as the regression proof.
- A pure route-candidate apply engine (`copper_mcp.apply`): given board bytes and a verified
  candidate it returns the bytes an apply *would* write, proven by a three-part assertion - every
  untouched byte bit-identical (checked in bytes, not characters), the result reparsing through the
  fail-closed adapter with no diagnostics, and the resulting Board IR equalling the source IR plus
  the candidate exactly. Route patches are inserted at the root's closing delimiter, which
  measurement selected rather than convention: after a real KiCad save this repository's own board
  interleaves segments and vias across four runs, so there is no "segment section" to append to,
  and the root close is the only position that modifies no existing span - leaving 99.999% of the
  file untouched before the splice and 2 bytes after it.
- Verified against real KiCad rather than asserted: the applied board opens, the net KiCad
  previously reported as unconnected becomes connected, no DRC error is introduced, and KiCad keeps
  the added segments when it later rewrites the board itself.
- A candidate is never trusted from its manifest. The engine recomputes the candidate identity and
  replays the geometry against the board before splicing, so a tampered candidate is refused even
  when its digest has been recomputed to match its own altered contents.
- An applied board is deliberately **not** stamped with CopperMCP writer metadata. The disposable
  board rendered for candidate DRC is our derivative and claims authorship honestly; an applied
  board is the user's file with tracks added, and rewriting its `generator` would both misattribute
  it and break the untouched-bytes assertion. A test pins both halves of that distinction.

  **Nothing writes to disk.** There is no mutating path, no authorization token, no lockfile
  handling, no compare-and-swap, no pre-apply copy, and no `apply_candidate` tool or CLI command;
  all of that is designed in ADR-0025 and explicitly unshipped. Merge, lock override, IPC apply,
  placement apply and batch apply are stated non-goals rather than omissions.

## [0.4.0] - 2026-08-04

### Added

- Circuit Scene IR 0.1.0 and the `observe_board_scene` tool: a bounded, region-scoped semantic
  observation of one board, with a matching `copper-mcp observe-scene` CLI command. A caller states
  a window - either an exact nanometre bounding box or one `around_ref_id` with a radius, never a
  whole-board shorthand - and receives full-precision integer geometry for the objects that overlap
  it. Objects arrive in two collections rather than behind a flag: `static` (outline, pads, keepouts,
  rules) is what a proposal must take as given, and `mutable` (segments, arcs, vias, zones) is what
  it may change, so code meaning to read only the givens cannot iterate over both by accident. Every
  object is named by the Board IR identity it already carries, so a model can refer to what it saw
  in a later call instead of repeating coordinates back, and each reference declares its own
  durability as `native`, `content_derived` or `request_scoped` - with a scene-level summary - so a
  caller knows in one place whether the references it is about to store will outlive an edit.
  Object and vertex ceilings are charged as the scene is built and reported as an explicit
  `ceiling_hit`, so a truncated scene can never be mistaken for a complete one. The whole of this
  repository's own board is 123 objects and 41KB in 38ms, roughly 6% of the provisional 2,000-object
  ceiling; region scoping cut that response eleven-fold with no change in wall time, because parsing
  dominates, so the window is a context-budget economy rather than a server-cost one.
- Quarantine for board-author-controlled text. Silkscreen, fabrication text and footprint properties
  are off by default and, when explicitly requested, appear only in a separately typed `annotations`
  collection whose `trust` field is a one-value literal - there is no vocabulary for a trusted
  string, so no board can label its own text safe. Both the name and the value of each footprint
  property are quarantined, because the name is as attacker-controlled as the value. Net names never
  appear at any setting, since Board IR hashes them at conversion. The test for this is a
  whole-response grep against a hostile fixture carrying prompt-injection strings in every
  author-controlled slot, rather than a per-field assertion, because sanitisation defences fail by
  leaking into the one field nobody audited.
- A metamorphic relation over the scene: turn the board a quarter turn and every coordinate must be
  the image of the original under that turn while every `ref_id` holds still. A companion guard
  confirms the fixture actually contains geometry the turn changes, so the invariance of the
  references proves something.
- `observe_board_scene` advertises a real `outputSchema` and returns populated `structuredContent`,
  because its handler returns a closed contract rather than a bare dictionary. A test pins the
  contrast with the older `dict`-typed tools, which advertise a vacuous object schema - a gap in
  those tools' typing rather than in the SDK.
- An opt-in deterministic board render on `observe_board_scene` (`include_render`), with a matching
  `copper-mcp observe-scene --render` CLI flag that writes the SVG to a create-only workspace path.
  Two exports of an unchanged board are byte-identical after canonicalization: KiCad stamps the file
  with a wall-clock timestamp and the output filename in a single `<title>` line, and the named
  `title-line-v1` rule rewrites exactly that line and nothing else - measured as the entire delta
  between two real exports taken three seconds apart, one line out of 5,603. Canonicalization is
  idempotent and fails closed, so an export whose title line is missing or duplicated is refused
  rather than digested unnormalized. Evidence records `normalized_digest`, `source_revision`,
  `context_revision`, `kicad_version`, `layers`, `side`, `canonicalization` and `byte_count`,
  because a digest alone cannot tell a caller whether two renders are comparable.
- Copper-only rendering as a security control. The export draws `F.Cu`, `B.Cu` and `Edge.Cuts`
  only, and this is not a presentation choice: measured against KiCad 10.0.5, an export including
  silkscreen or fabrication layers embeds each board string **twice in literal, greppable form** -
  once in a `<desc>` beside the stroked paths and once in an invisible `<text opacity="0">`. Text is
  therefore not safely "drawn as paths", and filtering `<text>` after the fact would leave the
  `<desc>` copy behind; excluding the layers is the only control that works. A hostile fixture whose
  every author-controlled slot carries a marker is asserted absent from the render bytes, with a
  companion test proving those same markers *do* leak when the layers are included.
- Refusal on a truncated render. At the `max_render_bytes` ceiling (4 MiB default) KiCad does not
  die on `SIGXFSZ` - it exits 0 having written a partial file, and the title line is near the top of
  the document so it survives. The exit code, the title check and the digest would all have been
  satisfied by half an SVG, so the canonicalizer now requires a complete document.
- Delivery as an MCP `resource_link` annotated `audience: ["assistant"]`, from a bounded
  process-local store holding at most 8 renders and 32 MiB, deliberately separate from the schematic
  store so the two cannot evict each other. `include_render` is stdio-only because those bytes need
  the process-local store, even though the semantic scene remains available over both transports;
  only the flag is withdrawn off stdio, never the whole tool.
- A typed placement-intent contract and a deterministic legalizer (`copper_mcp.placement`), the
  first half of the M4 placement surface. The intent language has seven rule kinds - proximity,
  alignment, symmetry, board edge, region keep-in/keep-out, discrete orientation and side - and is
  deliberately **unable to express an illegal result**: every rule names objects by the references
  a scene already handed out, every parameter is an exact integer, and there is no way to state a
  coordinate or to permit an overlap. Proposals are ref-anchored for the same reason ("2.5mm right
  of that object's east edge", never a raw position), so the absolute coordinates in a candidate
  are always derived here and then snapped to an explicit `placement_grid_nm`. A test asserts that
  an absolute position cannot be smuggled in through any field.
- Footprint identity recovered out of band and joined to Board IR pads, which have no parent
  reference of their own. Adding footprints to Board IR would cost a schema version bump and change
  the digest of every board ever converted, so the grouping is read from the same source bytes and
  the candidate binds to **both** digests. The join is total rather than best-effort: pads with a
  native UUID join directly, pads without one are matched by reproducing the adapter's documented
  derived-id hash, and a view that cannot account for every pad refuses.
- Three-valued pad overlap. Bounds over-approximate a pad and cores under-approximate it, so
  disjoint bounds *prove* clearance and overlapping cores *prove* collision, while everything
  between is reported as `inconclusive` rather than guessed. Measured on this repository's own
  board, bounding boxes settle **1,359 of 1,360** different-net pad pairs and exactly one is
  inconclusive - 0.07%, an oval against a roundrect whose boxes clip at a corner both shapes round
  away. That measurement is why exact pad-shape geometry is not in v0.1, and a test pins the rate
  so the conclusion is revisited if it moves.
- Outline containment and keepout respect via exact integer ray casting, with `courtyard_overlap`
  reported as a one-value `not_modelled` literal: Board IR carries no courtyard geometry, and this
  repository's own board draws none at all while its project sets `missing_courtyard: ignore`, so
  KiCad's own courtyard check is equally blind on it. There is no vocabulary for a courtyard that
  was checked, so a candidate can never imply one.
- An honest failure taxonomy in which `infeasible_constraints` and `budget_exhausted` never
  collapse into each other - the first is a proof that no placement satisfies the rules, the second
  an admission that the work ran out - alongside `unresolved_ref`, `unsupported_geometry`,
  `illegal_placement` and `stale_revision`. Only syntactic contradictions are claimed as infeasible,
  because anything needing search would be reporting ignorance as certainty.
- Rule results carry exact residuals, and `satisfied_within_tolerance` is reported **only** when the
  caller supplied a `tolerance_nm`. An unstated tolerance means exact, so a one-nanometre residual
  is a violation and says so.
- Immutable `PlacementCandidate` with two-phase identity derivation over its own canonical content,
  placements recorded in reference order under `ordering_policy` `validate-snap-v1`, and evidence
  carrying per-rule residuals, the legality record and the checks consumed. An illegal placement is
  refused *with* its legality record, so a caller never has to guess which of three independent
  checks failed.

  Placement is **preview only**: nothing applies a candidate, and moving a footprint invalidates
  every route bound to the same base revision. There is no MCP or CLI surface yet - the contract
  and legalizer land first so the rule vocabulary is exercised before it is published. A side
  change is refused as `unsupported_geometry` rather than mirrored approximately.
- `preview_placement`, the public surface for the placement legalizer, as an MCP tool over both
  transports and a `copper-mcp preview-placement` CLI command. The response is a closed contract,
  so the tool advertises a real `outputSchema` and returns populated `structuredContent`. Requests
  are validated at the boundary before any file is read, refusals are typed and never echo the
  rejected value, and the board is loaded through the same workspace confinement as
  `preview_route`. Budgets - subjects, rules, checks and a deadline - come from configuration.
  Rules and proposals are structured enough that flags would be a poor interface, so the CLI takes
  them from an optional workspace-confined JSON document whose fields are restricted to `rules` and
  `proposals`: the board, constraints and subjects always come from the flags, so the document
  cannot redirect the request at a different board.
- A transport-parity test asserting the tool returns byte-identical structured content over stdio
  and streamable-HTTP, and a subprocess test asserting it is registered on a stateless HTTP server.
  Unlike a render or a schematic artifact, a placement preview holds no capability handle, so there
  is nothing a stateless deployment cannot resolve.

### Changed

- Scene objects now report `locked`, so copper the board's author pinned is distinguishable from
  copper a proposal may move. It is a field rather than a third partition: the static/mutable split
  is by *kind* and is exhaustive, while lockedness is a per-object property its author can toggle
  without changing what kind of thing the object is - a third collection would hide segments from
  any consumer that walked only the two documented ones. Kinds with no such concept, an outline
  contour or a net class, report `null` rather than `false`.
- Scene pad geometry now carries `roundrect_radius_nm`, without which a rounded-rectangle pad could
  not be reconstructed from the scene.
- Scene truncation reports `annotations_returned` and `annotations_omitted` alongside the object
  counts. `ceiling_hit` names the first ceiling reached; the two `*_omitted` counts are the
  authoritative signal, because objects and annotations are charged against separate budgets and
  both can truncate in a single response.
- The schematic capability store's TTL, LRU, locking and digest-recheck logic moved into a shared
  `BoundedArtifactStore` so the render store inherits the reviewed discipline rather than repeating
  it. The schematic store keeps its exact public contract, including the cross-check of the
  retained artifact object that catches post-insertion tampering, and its existing tests pass
  unchanged as the regression proof.
- `--black-and-white` is now forced on the render, for determinism rather than aesthetics: colour
  output follows the active KiCad theme, and black-and-white output is byte-identical across themes.
- KiCad renders against a **read-only** private snapshot, which is stricter than the zone-fill path.
  Given a writable directory KiCad drops a `.kicad_prl` beside the input; the read-only snapshot
  removes that side effect rather than relocating it, and a test asserts the workspace is unchanged
  down to the board's inode and mtime.
- The capability inventory now lists placement preview as implemented and names DRC binding for
  placement, apply, and post-placement observation as planned rather than done.

### Fixed

- **Pad orientation was double-counted, transposing the extents of every non-square pad on a
  rotated footprint.** A pad's angle in a KiCad file is already resolved into the board frame -
  KiCad rewrites every pad angle when a footprint is rotated - so adding the footprint's rotation
  on top counted the turn twice. Established against KiCad 10.0.5 rather than from documentation: a
  4mm x 1mm pad written `(at 3 0)` inside a footprint placed at 90 degrees is drawn by
  `kicad-cli pcb export svg` at the rotated position but with its extents still 4mm x 1mm. Position
  rotates; shape does not.

  **This changes geometry on boards with rotated non-square pads.** Pad cores feed routing
  obstacles and same-net attachment, and pad bounds feed scene region queries, so proposals on such
  boards may differ from previous releases. On this repository's own CopperTone board - where 30 of
  55 pads are non-square with a non-zero angle - different-net pad bounding-box overlaps drop from
  **6 to 1**, against `kicad-cli pcb drc` reporting zero violations; the single survivor is an
  oval-against-roundrect pair whose *boxes* clip at a corner both shapes round away. Route coverage
  is unchanged at 13 of 14 nets `already_connected` (`GND` still needs `include_fill_authority`),
  and the scene still returns 123 objects.

  The defect survived because the two test layers each missed the other's half: the adapter-level
  rotation fixture contained only square pads, so it could not observe a transposition, while the
  non-square metamorphic cases built `Pad` objects directly and never exercised the adapter. Both
  are now closed - the fixture carries a non-square rotated pad, and a new oracle test compares
  Board IR's pad extents against the geometry KiCad itself plots, which is the first test here that
  checks the adapter against something other than itself.
- Region bounds no longer under-report an arc. Bounding an arc by its start, middle and end points
  ignores the bulge between them, so a window touching only the sweep was told the board was empty
  there. The cardinal extrema the sweep actually crosses are now included, using exact integer
  circumcentre and orientation arithmetic with no floating point; worst-case slack over 400
  randomised arcs is 4nm, always outward.
- Region bounds no longer under-report an obliquely rotated pad. Board IR accepts any pad angle -
  only *footprint* transforms are restricted to quarter turns - so swapping width and height on
  quadrant parity alone under-bounded every oblique pad. Quarter turns keep their exact extents;
  any other angle falls back to the pad rectangle's circumscribed circle, which contains it at
  every rotation and needs no trigonometry. Bounds may now only ever be too large: returning an
  object that turns out to be just outside a window is harmless, while omitting one that overlaps
  is not recoverable by the caller.
- `include_annotations` no longer bypasses the response budget. Board text was collected without a
  ceiling, so a board with enough footprint properties could grow the annotation list past the
  length the response contract itself advertises. Annotations are now charged against
  `max_scene_annotations` (default 5,000, configurable) and truncation is reported explicitly as
  `annotations_returned` / `annotations_omitted`.
- An `around_ref_id` radius can no longer push the resolved window outside the coordinate range the
  contract advertises. The anchor and the radius are each in range but their sum need not be, so
  the window is clamped - losslessly, because every board coordinate is inside that range, so a
  window already covering it cannot select more by growing.

## [0.3.0] - 2026-08-04

### Added

- A metamorphic test family over the routing pipeline: whole-board rotation by 90, 180 and 270
  degrees, reflection across each axis, lattice-safe translation, and endpoint swap. Each relation
  transforms a board and asserts that the router's conclusion travels with it - same result arm,
  same diagnostic code, same connection counts, and identical length, bend, proximity and total
  cost. Cost is a genuine invariant because the transformed board's legal path set is exactly the
  image of the original's; exact vertex equality under the inverse transform is asserted only on
  boards whose optimum is unique, because the expansion order and the `(iy, ix)` heap tie-break are
  not rotation-equivariant and a different-but-equally-optimal route is a correct answer rather than
  a defect. The rotation relations cover a board of rotated, non-square pads - the class that hid
  the footprint-rotation defect - and a second relation works at the adapter level instead,
  comparing `parse(rotate(board))` with `rotate(parse(board))` on the committed rotated-footprint
  fixture. That is the relation the y-down defect would have failed, and it is checked to be
  discriminating: all twelve pads match the correct quarter turn and none matches the mirrored one.
  These relations answer the complement of a pseudo-oracle - not "is this answer right" but "is this
  the same board" - and they need no KiCad.

- Cached zone fill may now serve as connectivity evidence, but only against a fresh KiCad refill.
  KiCad refills a private disposable copy and the recomputed pour is compared with the board's
  cache; matching means the two are the same geometry, so there is no question which one a claim
  describes, and a mismatch is a typed `stale_fill` refusal rather than a silent preference for
  either version. Comparison is over canonical geometry - islands sorted and digested by layer, net
  and exact integer vertices - because KiCad rewrites and reorders a board wholesale on save, so a
  byte diff of the file says nothing about whether the fill changed. An **island** is the unit
  rather than a zone: verified empirically against a board authored to force two disjoint regions,
  KiCad 10.0.5 emits one `filled_polygon` node per connected region, so copper touching different
  islands is not connected and a committed fixture pins that. `ZoneFillAuthority` refuses
  construction when its digests differ, so a stale record cannot exist to be misread. The workspace
  board is never refilled: `--refill-zones --save-board` reaches only the disposable copy, the three
  existing negative assertions still hold, and the source is recaptured and compared afterwards.
  The whole path is opt-in through `include_fill_authority`, because it spawns KiCad and must never
  happen implicitly. Fill stays out of Board IR, so snapshots and their digests are unchanged and
  the router never fetches evidence itself. Scope is connectivity only; using exact fill as a
  tighter routing obstacle would change routed geometry on every zoned board and needs its own
  measurement. On the repository's own CopperTone board this resolves `GND`, taking recognition to
  **14 of 14** - joined by two fill islands and six vias - while without the flag it still refuses,
  which is the honest default. See [ADR-0021](docs/adr/0021-zone-fill-authority.md).

- A same-net through via is now a connectivity joint rather than a blanket veto, so a net already
  joined across copper layers reports `already_connected` instead of being refused. Routing is
  unchanged and stays single-layer: a net that still needs new copper while carrying a via keeps its
  existing refusal. The via's core is its **annulus**, never the drill hole - a square inscribed in
  the outer circle would claim the one region that certainly is not copper - so the ring is covered
  by four axis-aligned rectangles, one per side, with the hole radius taken as the ceiling of half
  the drill and each rectangle's far corner satisfying `a^2 + b^2 <= R^2` in exact integers. Those
  four are unioned atomically because the annulus is one piece of physical copper joined by a plated
  barrel; deriving its self-connectivity from rectangle overlap would report a via as four separate
  objects. Objects now connect only when they share a layer, and a through via shares every layer,
  which is exactly what makes it a joint. Board IR admits through vias only and validates that they
  span the complete stack, so blind, buried and microvias stay fail-closed at the adapter.
  `RouteConnection` gains a `vias` count and its invariant becomes
  `attachment_segments + pad_count + vias`. Same-net zones still veto the claim, because a stale or
  unfilled zone cannot prove connectivity. On the repository's own CopperTone board this takes
  recognition from 11 of 14 nets to **13 of 14**: `VCC` and `L_OUT` are joined through their vias,
  while `GND` stays refused honestly because it carries a same-net zone. A committed `via-joint`
  fixture runs front stub to via to back stub to via to front stub, corroborated by board-level
  KiCad DRC reporting zero unconnected items, and the check is discriminating because removing
  either the via or the back-layer stub makes KiCad report an unconnected item. See
  [ADR-0020](docs/adr/0020-via-aware-connectivity.md).

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

- The mypy floor is raised to `>=2.3,<3`, matching the version CI runs and the one pinned in the
  development environment. Newer mypy narrows exhaustive enum branches differently, so a single
  supported generation removes a class of version-skew failure that had to be checked by hand.

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

[Unreleased]: https://github.com/seunghyukchoe/copper-mcp/compare/v0.4.0...HEAD
[0.4.0]: https://github.com/seunghyukchoe/copper-mcp/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/seunghyukchoe/copper-mcp/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/seunghyukchoe/copper-mcp/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/seunghyukchoe/copper-mcp/releases/tag/v0.1.0
