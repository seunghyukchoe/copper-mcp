# Changelog

All notable changes are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

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
  existing obstacle-work budget. Same-net zones remain partial routing, cached KiCad fill is not
  trusted, and CopperTone remains at zero of fourteen previewable `F.Cu` nets because nine are
  multi-pin and all five two-pin nets already carry same-net copper. A committed `blocked-zone`
  fixture verifies deterministic read-only adapter-to-preview routing without claiming fill-aware
  KiCad DRC.
- Through vias outside the routed net are now selected-layer obstacles built from their outer
  diameter, rather than a board-level rejection. A via on the routed net still fails closed as
  partial routing. On the repository's own CopperTone board this moved the failure from "nine vias
  reject everything" to per-net diagnostics; current re-measurement still previews zero of fourteen
  nets because multi-pin and already-partially-routed nets remain unsupported.
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

### Fixed

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

[Unreleased]: https://github.com/seunghyukchoe/copper-mcp/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/seunghyukchoe/copper-mcp/releases/tag/v0.1.0
