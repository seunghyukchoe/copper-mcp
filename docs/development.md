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

When extending the model or adapter:

1. Decide whether canonical meaning changes. If it does, follow the versioning process in ADR-0005
   rather than changing `0.1.0` in place.
2. Preserve exact integer conversion and reject geometry that cannot be represented without an
   explicit, reviewed rule.
3. Add valid and invalid fixtures for the construct, including budget and malformed-input cases.
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
