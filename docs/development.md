# Development Guide

## Supported environment

- Python 3.11–3.13.
- Git 2.40 or newer recommended.
- KiCad is optional for unit tests. When `kicad-cli` is available, the authoritative DRC integration
  test runs against a temporary copy of the synthetic fixture.

Create a virtual environment and install all checks:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev,security]"
pre-commit install --install-hooks
```

## Validation levels

| Command | Purpose |
|---|---|
| `make test` | Pytest unit and contract suite. |
| `make lint` | Ruff plus version and ledger structure. |
| `make typecheck` | Strict static typing. |
| `make security` | Secret and dependency audit. |
| `make build` | Wheel and source distribution. |
| `make check` | Full pre-release gate. |

Tests should not require network access or proprietary boards. GPU and KiCad integration tests must
be separately marked and have deterministic CPU or fixture-based coverage where practical.

On macOS, CopperMCP also checks the standard KiCad application path. Elsewhere, put `kicad-cli` on
`PATH` or set `COPPER_MCP_KICAD_CLI`. Do not add CLI argument passthrough: the DRC adapter's fixed
argument vector is a security boundary. The current bounded-execution helper requires a POSIX host;
unsupported platforms fail closed before starting KiCad.

DRC runs against a private mirror of the board's workspace-relative context. Keep project-local
symbol/footprint libraries and library tables below `COPPER_MCP_WORKSPACE`; external environment or
global-library dependencies may make DRC results host-dependent and must be declared in benchmark
provenance. Tune `COPPER_MCP_MAX_DRC_CONTEXT_BYTES` only after reviewing the workspace scope.
File-count and discovery-time ceilings are separately configurable through
`COPPER_MCP_MAX_DRC_CONTEXT_FILES` and `COPPER_MCP_MAX_DRC_CONTEXT_SCAN_SECONDS`.

The internal `run_route_candidate_drc()` service accepts only a typed `RouteCandidate` and
`KiCadConstraintProfile`. It captures the original context, reconstructs Board IR from captured
bytes, invokes the replay-verified disposable serializer, and runs the patched in-memory context
through the same fixed KiCad DRC path. Its evidence binds candidate, Board IR, raw source, patched
board, patched context, and nested summary revisions. Tests for this boundary must prove negative
violation evidence, stale board/rule/library rejection, context budgets, temporary cleanup, deep
evidence immutability, and source inode, mtime, byte, and workspace-entry preservation where real
KiCad is available.

Its only public caller is `route_preview.preview_route()`, reached through the `preview_route` MCP
tool and `copper-mcp preview-route`, and only when the caller sets `include_drc`.
`COPPER_MCP_MAX_ROUTE_PREVIEW_SECONDS` bounds preview wall-clock time above the router's integer
budgets. Preview must remain free of file writes, durable candidates, and jobs; a candidate-file
export, persistence, or apply action still needs a separate public contract and security review.

`run_layered_route_candidate_drc()` follows the same boundary for the narrow two-signal-layer
proposal. It requires the original `LayeredRouteRequest`, replays the candidate before serialization,
and binds full-stack through-vias to the private KiCad DRC context. The layered evidence remains an
internal, read-only gate; it is not exposed through MCP or CLI and cannot issue an apply token.

## Public request boundary

Every public service that accepts a JSON-shaped request parses it through `request_boundary.py`.
Add or tighten a rule there rather than in a service, so field, type, range, boolean, and character
handling cannot drift between `inspect_board_ir`, `preview_route`, and whatever comes next. Services
keep their own `RequestError` subclass and translate at their own parse entry point, so callers can
still discriminate. Routing and clearance constraints are always caller-supplied typed values; a
board file must never be able to supply its own. Add a rejection test with every new field.

`board_ir_service.summarize_board_ir()` is the read-only structural surface. It must keep returning
counts, digests, units, and standard KiCad layer names only — never coordinates, net names, pad or
net identities, UUIDs, or source bytes — and its disclosure regression test must keep asserting
that.

## Board IR development

Board IR `0.1.0` is a strict public contract. Start with
[`docs/architecture/board-ir.md`](architecture/board-ir.md),
[`ADR-0005`](adr/0005-canonical-board-ir.md), and the
[`0.1.0` JSON Schema](../schemas/board-ir/0.1.0.schema.json).

The pure domain API is exported by `copper_mcp.board_ir`. Use `make_content` and `make_snapshot` for
programmatic construction, `encode_snapshot` for byte-stable JSON, and `decode_snapshot_json` for
untrusted bytes. Do not hand-build digest fields: construction computes the semantic constraint
digest, and decoding verifies both that digest and the snapshot digest.

The current KiCad entry point is `parse_kicad_bytes(source, profile, limits)`. It accepts source
bytes and a separate typed `KiCadConstraintProfile`, then returns a fail-closed `ConversionResult`.
It is intentionally not a filesystem writer or a route/apply service. A missing snapshot or error
diagnostic must stop downstream work; never continue with a partial board.

The v0.1 adapter is pinned to KiCad PCB format `20260206`. Treat every newly accepted S-expression
head, positional atom, graphics layer, zone option, or via treatment as a semantic change requiring
an explicit allowlist decision and a fail-closed regression. Cached `filled_polygon` geometry is not
authoritative Board IR content. Preserve quoted-versus-bare atom meaning, reject ambiguous native
identities, and keep diagnostic fields independent of source-controlled names. JSON input is budgeted
lexically before DOM construction. JSON Schema ceilings and operational `ParseLimits` are separate:
third-party schema-valid input can still be rejected by a local security budget, while public
CopperMCP writers must remain readable with the default limits.

When extending the model or adapter:

1. Decide whether canonical meaning changes. If it does, follow the versioning process in ADR-0005
   rather than changing `0.1.0` in place.
2. Preserve exact integer conversion and reject geometry that cannot be represented without an
   explicit, reviewed rule.
3. Add valid and invalid fixtures for the construct, including budget, graphics-layer,
   positional-atom, and malformed-input cases.
4. Add deterministic encode/decode, digest, geometry, and fail-closed adapter tests.
5. Update the accepted/rejected support matrix, decision/risk ledgers when applicable, and changelog.

Tests must use synthetic or redistributable fixtures. Private boards, raw diagnostic content, and
source bytes must not be copied into logs, snapshots, or MCP responses.

Run the instrumented CopperTone conversion benchmark from the repository root:

```bash
PYTHONPATH=src python scripts/benchmark_board_ir.py --iterations 7 --warmups 2
```

The JSON report records the exact commit and dirty state, input and snapshot digests, object counts,
structural limits, timing samples, and incremental peak memory while `tracemalloc` is enabled. This
measures KiCad-to-Board-IR conversion only; it is not an autorouting performance result. Check in a
result only from a clean tree and append, rather than replacing, benchmark evidence.

Run the synthetic two-pin optimality comparison from the repository root:

```bash
make PYTHON=.venv/bin/python benchmark-routing
```

This invokes the production A* candidate backend and a benchmark-only zero-heuristic Dijkstra
oracle over the same bounded integer state graph. The report retains the straight, detour,
exact-clearance, and expected-no-path fixtures; checks completion and exact cost agreement on every
iteration; and records deterministic counters plus instrumented runtime and incremental peak memory.
It does not invoke KiCad, perform authoritative DRC, establish production throughput, or compare
CopperMCP with another router. Generate recorded evidence to a path outside the repository so the
embedded Git dirty-state check remains meaningful, then append the reviewed result and ledger entry.

Run the external-corpus routing benchmark from the repository root:

```bash
make PYTHON=.venv/bin/python benchmark-external-corpus
```

This imports the committed MIT-licensed SimpleRouteJson corpus through the benchmark-only seam in
`copper_mcp.benchmarks` and routes every imported net with the existing single-layer router. It is
offline: it reads only files already in the tree and verifies each against
`benchmarks/corpora/tscircuit-benchmark/manifest.json` before importing it. The report records the
outcome of *every* attempted net under its exact `RouteFailureCode`, so the refusal breakdown is
part of the result rather than an error path, and a baseline that is not installed is recorded as
`not_run` rather than estimated. It does not invoke KiCad, apply copper, or compare CopperMCP with
another router. See [B-088](ledgers/benchmark-ledger.md) and the
[research note](research/open-baseline-benchmarks-v1.md) for the licensing determination and the
limits on what a number from this corpus can claim.

### Adding an external corpus

1. **Check the licence first**, before any file is committed, and record the determination with the
   URLs it came from in a dated research note.
2. If redistribution is permitted, commit the upstream `LICENSE`, an `ATTRIBUTION.md`, a
   `manifest.json` with a SHA-256 for every upstream file, and a subset chosen by a rule fixed in
   advance. If it is not, commit the manifest and a fetch script and nothing else.
3. Record the corpus's provenance limits — whether the boards are human-designed, and whether any
   router was in the loop when they were built — next to the data.
4. Route it through Board IR and the existing typed refusals. An import path that special-cases the
   corpus is measuring the harness, not the router.

## Adding a schema conformance fixture

`schemas/` holds the published JSON Schema files for CopperMCP's wire-visible payloads. A schema
file can drift from the Python model that is supposed to satisfy it — a field renamed on one side,
not the other — without any test noticing, because the model's own boundary tests validate against
its own runtime rules, not against the schema file a third party would actually load. Direct schema
conformance tests close that gap by loading the schema itself and checking fixtures against it.

`tests/test_schema_conformance.py` covers the board manifest, candidate, and DRC summary schemas
this way; `test_board_ir_schema.py`, `test_circuit_ir.py`, `test_circuit_intent_service.py`, and
`test_audio_benchmarks.py` cover the rest. Each schema's coverage is recorded in the
`_KNOWN_SCHEMAS` mapping in `tests/test_schema_conformance.py`, and a completeness test there fails
if a new file appears under `schemas/` without a matching entry — so a new schema cannot ship
without either a fixture or a recorded reason it does not need one.

To add a fixture for a schema (new or existing):

1. Put fixtures under `tests/fixtures/schema-conformance/<schema-name>/`, one `valid.json` and one
   `invalid-<condition>.json` per malformed condition you are covering. Name the invalid fixture
   after the condition it demonstrates (`invalid-missing-required-field.json`,
   `invalid-malformed-sha256-<field>.json`, `invalid-negative-count.json`,
   `invalid-unexpected-additional-property.json`, `invalid-wrong-schema-version.json`) so a reader
   never has to open the file to know what it is testing. Keep fixtures minimal; do not paste a
   real board, job, or candidate wholesale.
2. Where the schema has a Python model with a `to_dict()` method, build the valid fixture from a
   real instance (round-tripped through `json.dumps`/`json.loads`, since a dataclass may use a
   `tuple` where the wire format uses a JSON array) and assert the fixture equals that payload —
   this proves the fixture is the schema's real published shape, not merely *a* shape the schema
   happens to accept.
3. Load the schema with `jsonschema.Draft202012Validator`, call `Draft202012Validator.check_schema`
   on it once, and assert the valid fixture produces no errors and each invalid fixture produces at
   least one.
4. If a fixture reveals that the real payload disagrees with the schema, do not change either side
   to make the test pass. Record the mismatch as its own pinned regression test explaining what was
   found, note it in the changelog, and open a separate, minimal-reproduction bug — a schema is a
   public contract change and must go through the same review as one (see "Adding a public
   contract" below).
5. Add the new schema's relative path to `_KNOWN_SCHEMAS` in `tests/test_schema_conformance.py`
   naming the test module that covers it, even when that module is a different file.

## Adding a public contract

1. Open an RFC issue.
2. Write or update an ADR.
3. Add JSON Schema and valid/invalid fixtures.
4. Add compatibility tests.
5. Document migration and lifecycle expectations.
6. Update the decision ledger and changelog.

## Dependency policy

Runtime dependencies require a clear justification, active maintenance, compatible licence, and
security review. Standard-library solutions are preferred for small boundary functions. Lockfiles
will be introduced before the first release once the supported packaging workflow is finalized.
