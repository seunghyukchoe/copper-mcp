# ADR-0015: Bounded Circuit Intent schematic delivery

- Status: Accepted
- Date: 2026-08-03
- Owners: `@seunghyukchoe`

## Context

ADR-0014 defines a strict Circuit Intent snapshot and a pure KiCad schematic renderer, but neither
is reachable through the application, CLI, or MCP surfaces. Returning generated schematic bytes in
ordinary tool output would unnecessarily place the complete logical design in model context.
Writing a model-requested path implicitly would instead add a filesystem mutation boundary without
clear user authorization. A public MVP also needs to distinguish deterministic structural evidence
from KiCad parsing, ERC, board parity, electrical review, and fabrication readiness.

CopperMCP's longer-term MCP goal is a high-fidelity perception-action loop: an AI can observe a
versioned semantic and visual circuit scene, propose placement intent, and inspect bounded previews,
while deterministic code owns snapping, connectivity, clearance, rule validation, provenance, and
any eventual file mutation. The first public schematic operation must be a safe foundation for that
goal without pretending to implement placement or direct editor control.

## Decision

CopperMCP exposes one protocol-independent Circuit Intent build service. It accepts either a strict
verified snapshot JSON document or an already-decoded structured `content` object at a trusted
application boundary. Structured content is validated, normalized, and content-addressed by the
service; callers never provide KiCad S-expressions and need not calculate a digest. The service
renders the accepted snapshot twice and requires byte-identical immutable artifacts. It returns a
redacted build record containing only schemas, digests, format, byte and topology counts, and an
explicit verification matrix. It does not return titles, values, references, net or port names,
UUIDs, source JSON, or schematic bytes.

The verification matrix reports deterministic topology, artifact digest, source-provenance binding,
and replay checks as `passed`. It reports KiCad CLI parsing, ERC, schematic-to-board parity,
electrical validation, and board readiness as `not_run` or `false`. Repository integration evidence
may separately prove that a committed fixture parses and exports exact connectivity with one
reviewed KiCad version; that evidence does not upgrade an individual build result.

### CLI export

`copper-mcp render-schematic INTENT.json --output NEW.kicad_sch` is an explicit durable export. The
input is a bounded strict Circuit Intent snapshot captured through one descriptor-anchored workspace
read: every path component is opened relative to a held workspace descriptor without following
symlinks, and the same final descriptor supplies validation, bytes, and mutation checks. The output
parent must already resolve inside that workspace, the suffix must be exactly lowercase
`.kicad_sch`, and the final path must not exist. A complete private temporary file is linked into
place atomically with create-exclusive semantics; symlink targets, traversal, case-variant suffixes,
overwrites, and partial final files are rejected. The writer re-reads and verifies the exact bytes
through the held directory boundary before success. No CLI flag can weaken the schema or artifact
ceilings.

### MCP artifact resource

`render_circuit_schematic` accepts one structured Circuit Intent `content` object. The MCP adapter
stores the returned immutable bytes only in a process-local, non-enumerable capability store and
adds an opaque `pcb://artifacts/schematic/{token}/circuit.kicad_sch` URI to the redacted metadata.
Tokens carry at least 256 bits of randomness and are authorization capabilities, while content
digests remain identity only. The store is bounded to 16 artifacts, 16 MiB total, and 1 MiB per
artifact, with deterministic least-recently-used eviction. A capability becomes unreadable exactly
15 minutes after creation; reads do not extend that deadline. Expired entries are reclaimed lazily
when the store next handles a read or insertion, or when the process exits. Therefore TTL is an
access-control guarantee, not an immediate memory-erasure guarantee: stale objects, returned byte
copies, allocator pages, crash dumps, or swapped memory may persist beyond expiry. Invalid, expired,
and evicted tokens fail without disclosing which state occurred. Reads re-verify the artifact digest.
There is no listing API, persistence, filesystem write, or logging of content or tokens.

The artifact tool and resource are enabled only for the default stdio transport in this MVP.
Process-global artifact resources are disabled over streamable HTTP until authenticated principals,
session isolation, authorization, and per-principal quotas exist. The tool is marked non-read-only
because it changes ephemeral server state, non-destructive, non-idempotent because it issues a new
capability, and closed-world because it performs no network operation.

## Consequences

- Agents can submit bounded logical content and obtain a real retrievable KiCad schematic without
  authoring or receiving KiCad syntax in ordinary tool context.
- CLI users opt into one visible new-file creation; MCP rendering remains process-local and
  nonpersistent and does not mutate their workspace, without claiming immediate memory erasure.
- A retrieved resource necessarily reveals the accepted logical topology. Hosts remain responsible
  for deciding whether that resource may enter a remote model context.
- Expiry or eviction can require a caller to render again. The stable intent and artifact digests
  allow equality checks without making the capability URI permanent; they do not prove secure
  deletion of every in-memory copy.
- The MVP contains two honest, disconnected workflows: Circuit Intent to schematic, and existing
  board inspection/route preview. Schematic-to-board conversion and placement remain manual.
- A future high-fidelity Circuit Scene IR and placement-candidate contract can add semantic/visual
  observation and bounded placement proposals without changing the rule that models never apply
  unvalidated KiCad mutations directly. That IR, placement preview, and apply path do not exist in
  this MVP.

## Alternatives considered

- Return schematic text inline: rejected because it amplifies responses and discloses the whole
  circuit to the model by default.
- Use the artifact digest as the resource secret: rejected because content identity is not
  authorization and digests may appear in logs or public ledgers.
- Persist MCP artifacts in the workspace: rejected because a render call would become an implicit
  filesystem mutation and leave proprietary data behind.
- Enable the process-global store over stateless HTTP: rejected because a URI capability alone does
  not provide principal or session isolation.
- Require the model to submit a snapshot digest: rejected for structured MCP input because the
  deterministic service owns canonicalization and provenance.
- Include authoritative ERC in this MVP: deferred by ADR-0014 until its fixed-argument schematic
  subprocess and report contract receive a separate security review. The generated passive fixture
  also produces reviewed KiCad warnings that must not be relabeled as clean evidence.

## References

- [MCP resources](https://modelcontextprotocol.io/specification/2025-11-25/server/resources)
- [MCP tool result schema](https://modelcontextprotocol.io/specification/2025-11-25/schema)
- [KiCad 10 command-line interface](https://docs.kicad.org/10.0/en/cli/cli.html)
