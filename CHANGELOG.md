# Changelog

All notable changes are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

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
