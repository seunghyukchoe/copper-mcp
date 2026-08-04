# Security and Threat Model

## Assets

- Proprietary PCB geometry, net names, constraints, stackups, and component choices.
- Filesystem integrity and unsaved KiCad editor state.
- MCP and model-provider credentials.
- Candidate provenance and benchmark integrity.

A known limitation, stated rather than implied: workspace reads check their deadline before and
after, not *during*. Descriptors are opened non-blocking, which defeats a FIFO, and special files
and symlinks are rejected outright — but `O_NONBLOCK` has no effect on a regular file, so a read
from a stalled network or FUSE mount can block past any deadline this process set. Interrupting it
would need a signal or a reader thread, and adding either to every read is a larger change than the
risk warrants for a local-first tool. What bounds the exposure today is that sizes are checked
before reading, KiCad subprocesses carry their own timeouts, and a stall is a hang rather than a
wrong answer. A workspace on unreliable network storage is outside what this bound covers.

Terminology used throughout: DRC evidence is an **attestation** — a statement about named subjects
bound to their digests, refused when any binding fails — while release-ledger rows are **provenance**
in the SLSA sense, and the ledgers as a whole are an append-only **transparency record** rather than
a cryptographic transparency log. There is no Merkle tree or signed checkpoint; Git history is the
only integrity mechanism behind them. Emitting DRC evidence in the in-toto Statement envelope is
future work, not a current property.
- Compute budgets for routing and AI inference.

## Trust boundaries

| Boundary | Representative threats | Required controls |
|---|---|---|
| MCP input → server | Path traversal, oversized payloads, excessive agency | Typed validation, workspace allowlist, size/budget limits, separate apply permission |
| MCP wrapper/content/output schemas | Scalar/list shape confusion, extra-field smuggling, validation errors echoing proprietary values | Closed schemas at every wrapper and nested object, non-echo rejection of scalar/list/extra input, output-schema validation |
| Board file → parser | Malformed data, parser DoS, hidden secrets, validate/reopen replacement race | Descriptor-anchored bounded reads, no-follow path walk, before/after descriptor state check, fuzz/property tests, no execution, generic errors |
| Server → KiCad CLI | Argument injection, hangs, oversized or incompatible reports, context-file floods, stale evidence, inherited global configuration/plugins or credential-bearing environment, library-table escape to host or remote resources | Validated executable, fixed argument vector, minimal child environment, private configuration/state roots and working directory, snapshot-confined file-table dependencies only, environment/absolute/remote/plugin URI rejection, symlink/special-file rejection, POSIX file ceiling, cumulative byte/file-count bounds, discovery/process timeouts, strict contract, revision recheck |
| AI output → policy | Prompt injection, invalid commands, cost exhaustion | Allowlisted typed actions, deterministic validation, token/iteration budgets |
| Router → KiCad | Candidate/source/context misbinding, stale state, unsafe copper | Exact replay, immutable multi-revision evidence, live-context recheck, private candidate snapshot, separate apply authorization |
| Routing job ledger/worker/manifest → local SQLite | Board/prompt leakage, stale-worker overwrite, guessed or expired handles, unbounded retention | Closed redacted records and candidate summaries only, source/snapshot and candidate CAS binding, content-addressed idempotent specs/manifests, SQLite transaction plus `job_id`/revision update guard, bounded payload/count/TTL, single-worker CAS lease, cooperative cancellation, stale-lease recovery, uniform unknown/expired errors; no route geometry, request/result repository, remote auth, candidate export, or MCP Tasks claim |
| MCP input → preview | Unbounded search, unsupported-subset confusion, geometry disclosure, unverified candidates, stale fill provenance | Strict typed request parsing, caller-supplied constraints, wall-clock deadline over integer ceilings, fail-closed conversion with code-only diagnostics, freshness-bound opt-in fill authority with typed routing-effect provenance, opt-in authoritative DRC that fails closed, no write or job side effect |
| Board IR scene → placement preview | Proprietary geometry disclosure, cross-revision subject confusion, truncated detail mistaken for complete geometry, unsupported courtyards treated as legal, locked-footprint movement | Region and object/detail ceilings, typed reference durability, quarantined author text, source/snapshot revision equality, fail-closed footprint conversion, explicit `not_modelled` legality, locked-move refusal, no placement apply |
| Benchmark catalog → offline runner | Licence laundering, fabricated capability claims, copied third-party circuits, path/symlink escape, artifact substitution or replacement races, hidden network intake | Reference-only source records, no downloader, strict bounded schema, single-read validation snapshot, repository-confined paths, artifact and licence hashes, evidence-derived claims, explicit safety/derivation fields, original or separately open fixtures only |
| Circuit Intent → schematic renderer | Malformed or oversized model topology, reference confusion, incomplete connectivity, S-expression injection, output amplification, false electrical/PCB claims | Strict bounded codec, typed IDs, complete-pin validation, canonical digest, escaped strings, original embedded symbols, deterministic 1 MB pure renderer, empty footprints, `on_board=no`, explicit non-claims |
| MCP schematic build → artifact resource | Proprietary topology in model context, guessable capability, cross-client disclosure, unbounded retention, digest used as authorization, TTL mistaken for secure erasure | Redacted tool result, independent 256-bit opaque token, stdio-only process-local store, no listing/logging/persistence, 15-minute access expiry with documented lazy reclamation, 16-entry/16 MiB limits, 1 MB (1,000,000 bytes) artifact limit, uniform unavailable error, digest recheck |
| CLI schematic build → workspace | Traversal, symlink overwrite, replacement race, suffix ambiguity, partial final file, implicit model-directed mutation | Explicit output argument, descriptor-anchored workspace input, exact lowercase `.kicad_sch`, create-exclusive atomic publication through a held directory, final-byte/parent-identity verification, no overwrite mode |
| KiCad report/private snapshot → evidence | Symlink/FIFO blocking, report replacement, duplicate/non-finite/deep JSON, untracked child writes | No-follow nonblocking descriptor capture of regular reports, strict bounded JSON, read-only full-tree snapshot validation before accepting evidence |
| Artifact object → capability store | Post-insertion mutation corrupts content identity or byte accounting | Detached entry snapshot of exact content, digest, and size; digest recheck and accounting use only stored fields |
| Release request → tag | Tagging unreviewed or unrecorded source | Dated version section in changelog, append-only `Ready` authorization tied to exact commit and clean full gate; publication remains separate |
| Remote client → HTTP | Spoofing, token theft, cross-tenant access | TLS, OAuth, scoped authorization, per-principal jobs, rate limits |
| Dependency → build | Compromise, typosquat, vulnerable package | Locking, Dependabot, CodeQL, dependency audit, build attestations |

## STRIDE summary

- **Spoofing:** remote deployment requires authenticated principals; a job ID is not authorization.
- **Tampering:** board and candidate content hashes detect stale or modified inputs.
- **Repudiation:** future mutations record principal, revision, policy, model digest, seed, and result.
- **Information disclosure:** local-first defaults, redacted logs, ignored private workspaces, and no
  automatic model upload.
- **Denial of service:** bounded file sizes, candidate counts, runtime, memory, net counts, and model
  loops.
- **Elevation of privilege:** read, route, export, cancel, and apply remain separate capabilities.

## Current controls

The `0.4.x` board-facing surface remains non-mutating, including route preview, scene observation
and placement preview. The only durable writes are two explicitly named, create-new exports - a
schematic and a board render - each refused if its path already exists, and neither able to
overwrite a board. Board rendering additionally runs KiCad against a **read-only** private snapshot,
so the exporter cannot write even the `.kicad_prl` it drops beside a writable input. Workspace files are captured through descriptor-anchored, no-follow path walks; the
same final descriptor supplies type/size validation, bytes, and before/after mutation checks, so a
validated pathname is never reopened for the operation. Network transport binds to loopback, secret
patterns are scanned, and no AI provider is enabled. KiCad DRC runs with fixed arguments against a
path-preserving private context snapshot and emits a bounded aggregate summary; save/refill flags and
raw finding details are not exposed. File-table entries may name only dependencies that resolve
inside the captured snapshot. Environment-expanded, absolute, remote, and plugin-backed URIs are
rejected before execution, preventing KiCad from consulting uncaptured host or network resources.
The DRC child receives
only `PATH=os.defpath`, `LANG=C`, and `LC_ALL=C`, plus private per-run `HOME`, `KICAD_CONFIG_HOME`,
`KICAD_DOCUMENTS_HOME`, `XDG_CONFIG_HOME`, `XDG_CACHE_HOME`, `XDG_DATA_HOME`, `XDG_STATE_HOME`,
`XDG_RUNTIME_DIR`, and `TMPDIR`. No caller environment entries, credentials, or KiCad overrides are
inherited. It runs with its private `TMPDIR` as the working directory rather than the repository or
live workspace. The private state tree rejects symlinks and special files and shares the existing
per-file, file-count, cumulative-byte, and scan-time ceilings. The child process also receives a
file-size ceiling before KiCad starts, and the source context is re-hashed afterward. For route
candidates, only one captured source is parsed, the candidate must match its
Board IR base and exact deterministic replay, and only an in-memory board payload is replaced before
the same DRC path runs. Evidence binds candidate, Board IR, original source, patched board, patched
context, and nested summary revisions; copied violation counts are immutable, must sum to the
aggregate findings, and KiCad's exit code must agree with report finding presence. The private
board is part of a complete private input-context recapture after KiCad exits, and any private or live
board/rule/library change discards the result. Candidate bytes and raw findings are not exposed
through MCP or CLI.

The public `inspect_board_ir` and `preview_route` surfaces add no write path. Their accepted board
bytes come from the descriptor-anchored snapshot, not a later pathname reopen. Both parse untrusted
requests through one shared boundary before any file is read: unknown fields, non-integer or
out-of-range budgets, booleans supplied as integers, control characters, oversized net names, and
non-copper layer names are rejected, rejections report counts rather than echoing caller-supplied
field names, and routing constraints come only from typed caller values rather than from untrusted
board content. A wall-clock deadline starts at the operation boundary and bounds the entire preview
call — conversion, search, and the clamped KiCad timeout for optional DRC — above the existing grid,
expansion, and obstacle ceilings. Unsupported boards
return bounded diagnostic-code counts, not raw adapter text, and routing failures return typed
non-echoing diagnostics. Authoritative DRC runs only when the caller opts in, still yields aggregate
redacted evidence, and fails the call when the evidence is missing or does not bind. A preview does
return the candidate geometry and endpoint pad IDs it generated, so hosts that must not disclose
generated copper to a model should not enable the tool; source board bytes and unrelated board
objects are never returned.

Selected-layer foreign zones are treated as conservative solid polygon envelopes, never as trusted
cached fill. Each zone counts against the obstacle-object ceiling; vertex inspection, bounding-box
pruning, and every exact polygon-edge relation count against the obstacle-check ceiling and inherit
its 64-check cancellation cadence. The integer kernel uses no floating point or external geometry
library, applies the strictest routed-net, zone-net, and zone clearance, and rejects same-net zones
as partial routing. This can reject a route through a real fill void, but cannot use stale
`filled_polygon` data to permit copper through an area the zone may occupy. The deterministic core
now accepts only the separately reviewed, source-revision-bound refill/freshness evidence and uses
matching foreign fill islands as exact obstacles; the public preview advertises that provenance on
routed candidates only when the caller opts into freshness authority, with a typed routing effect.

Board IR inspection discloses only object counts, digests, units, and standard KiCad copper layer
names. Coordinates, net names, pad and net identities, UUIDs, and source bytes are excluded, and a
regression test asserts their absence from the serialized document. These controls do not make
arbitrary remote exposure safe.

The development-only audio benchmark runner has no URL client and never executes external catalog
entries. Its strict catalog checker caps JSON and artifact sizes, rejects duplicate fields and IDs,
requires HTTPS provenance URLs without credentials, confines resolved artifact and licence paths to
the repository, binds both artifact and licence bytes to SHA-256 plus licence-identity markers, and
encodes third-party references as non-redistributable. Catalog, artifact, and licence bytes are read
once under bounds and carried as one validation snapshot, so execution and report digests cannot
silently bind different rereads. Executable fixtures must state that no third-party content is
included. The runner copies the validated board bytes to a private temporary workspace, invokes
only the MCP-shared read-only services twice, requires candidate IDs for routed outcomes, derives
claims from observed structural/status evidence, and verifies that its private board copy did not
change. This does not adjudicate copyright, electrical safety, or fabrication readiness.

Circuit Intent public inputs remain untrusted. The strict snapshot decoder caps input, nesting,
decoded values, components, nets, ports, and connections, and callers may only tighten those
ceilings; it rejects unknown or duplicate fields and unsupported numeric scalars, validates typed
references and complete pin ownership, normalizes ordering, and verifies a canonical content
digest. Structured MCP content passes the same semantic validation, but the service computes its
digest rather than trusting a model-supplied value. The KiCad adapter accepts only a verified
immutable snapshot, escapes every S-expression string, embeds original fixed symbols without
library resolution, derives stable identities from the source digest, binds reported source/count
provenance into the content, and returns at most 1 MB of new bytes without file, network, or
subprocess access. The build service renders twice and requires byte equality.

Ordinary MCP build results contain only schema/format versions, digests, sizes, topology counts, and
explicit verification statuses. Exact bytes require a separate random capability read from a
bounded, non-enumerable, process-local store. That store is stdio-only because stateless HTTP has no
principal isolation; expiry, eviction, and unknown tokens are indistinguishable. Capability access
ends 15 minutes after insertion, but expired entries are removed only on subsequent store activity
or process exit. This is not a memory-erasure guarantee, and returned copies are outside the store's
control. The CLI instead requires an explicit new path ending in exact lowercase `.kicad_sch`, reads
its input through one held descriptor, and publishes one verified file through a held workspace
directory without overwrite. Empty
footprints, `on_board=no`, and `not_run` per-build KiCad/ERC/parity/electrical statuses prevent the
structural derivative from presenting itself as board-ready. The reviewed fixture's KiCad 10.0.5
run preserves exact nets and reduces ERC warnings from seven to four—two isolated external-port
labels and two missing private-library-configuration warnings—but is neither per-build evidence nor
an ERC-clean claim.

Circuit Scene IR `0.2.0` is a current disclosure boundary: structured observation and its optional
render can reveal placement and connectivity without returning source files. Scene requests are
region-scoped and revision-bound; objects and footprint pad relationships/courtyard vertices consume
explicit object/detail ceilings, reference durability is typed, and board-author text is quarantined
as untrusted annotation data. The normalized render is digest-bound and advisory rather than
geometric authority.

The active KiCad adapter accepts only front-side footprints with orthogonal transforms and unfilled
rectangular `fp_rect` courtyard centerlines on matching `F.CrtYd`; unsupported footprint or
courtyard forms fail closed before a scene or placement view exists. Placement subjects are
projected from the same Board IR snapshot, and the supplied source bytes must match its source
revision. AI output remains typed placement intent, a locked footprint cannot be moved, and
`courtyard_overlap` remains the one-value `not_modelled` result because no bounded side-aware
evaluator has run. Direct model-generated KiCad mutation and placement apply remain prohibited.

MCP schematic delivery validates a closed outer wrapper, closed Circuit Intent content, and closed
structured output. Scalars, lists, and extra fields at those boundaries fail without echoing the
offending field name or value. This prevents transport coercion or error text from bypassing the
redacted build record.

The KiCad report is opened through a no-follow, nonblocking descriptor and accepted only when
descriptor metadata identifies a regular file. Its JSON decoder rejects duplicate keys, non-finite
numbers, excessive nesting, and excessive decoded values before the report contract is evaluated.
KiCad receives a read-only private design snapshot, and the complete snapshot tree—not only known
board/rule files—is checked for additions, mutation, symlinks, special files, or permission changes
before evidence is accepted.

Each schematic capability-store entry detaches and retains the exact bytes, digest, and size at
insertion. Retrieval and aggregate accounting use that snapshot rather than a caller-owned artifact
object, so later alias mutation cannot change identity or evade the byte ceiling.

Release authorization is also fail-closed. A release tag requires a dated changelog section for the
same version and an append-only `Ready` release-ledger row naming the exact commit after its clean
full gate. Authorization permits tagging only; it does not claim a built artifact or published
release.

## Security acceptance for future placement mutation

Placement apply remains blocked until Board IR can preserve and replay every source span affected by
a pose edit, including the currently omitted author text, fabrication graphics, library identity,
properties, and 3D-model pose. It additionally needs explicit authorization, revision-race tests,
transaction or recoverable-undo behavior, complete audit metadata, KiCad verification,
cancellation tests, and a dedicated security-ledger review.
