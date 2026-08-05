# Usage guide

Every CopperMCP command and MCP tool, with the limits each one declares. The repository
[`README.md`](../README.md) is the short version; this document is the complete one.

The CLI and the MCP server are two surfaces over the same application services, so a capability
described here behaves identically through either. Where they differ — artifact delivery, and the
apply authorization token — the difference is stated explicitly.

Prerequisites: Python 3.11 or newer. See the [development guide](development.md) for the full
environment and quality gate.

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev,security]"
make check
```

## Inspect a board

Read a board without modifying it:

```bash
copper-mcp --workspace /absolute/path/to/boards inspect example.kicad_pcb
```

Check whether a board is representable by the supported Board IR subset. This reports counts and
digests rather than geometry, names, or identities:

```bash
copper-mcp --workspace /absolute/path/to/boards board-ir example.kicad_pcb \
  --clearance-nm 250000 --track-width-nm 250000 \
  --via-diameter-nm 800000 --via-drill-nm 400000
```

## Run authoritative KiCad DRC

Run authoritative KiCad DRC and return only bounded aggregate evidence:

```bash
export COPPER_MCP_KICAD_CLI=/absolute/path/to/kicad-cli  # optional when discoverable
copper-mcp --workspace /absolute/path/to/boards drc example.kicad_pcb
```

The DRC adapter never accepts arbitrary KiCad flags and never requests zone refill or board save.
It mirrors the board, matching project/rule files, and workspace-local KiCad library assets into a
private snapshot through descriptor-anchored, no-symlink reads. File-table dependencies are accepted
only when they remain inside that snapshot; environment-expanded, absolute, remote, and plugin URIs
are rejected before KiCad starts. The child runs from a private working directory and is isolated
from the invoking user's global configuration and environment. Snapshot bytes and child side
effects are bounded cumulatively, report growth is limited in the child process, and results are
discarded when captured context changes. Context discovery also has file-count and wall-clock
ceilings, and the pre-run byte snapshot is released before KiCad starts.

Keep KiCad projects self-contained below the configured workspace, with any libraries referenced as
project-relative `${KIPRJMOD}/` paths from an `fp-lib-table` or `sym-lib-table` beside the board
file. No other library location is read, and design-block library entries are rejected.

DRC-clean is not a substitute for electrical, signal-integrity, manufacturability, or hardware
review.

## Observe a board as a semantic scene

Observe a region of a board as a typed semantic scene. The region is mandatory — either an exact
nanometre bounding box or one object reference with a radius — because full detail inside a stated
window is more useful than a summary of everything:

```bash
copper-mcp --workspace /absolute/path/to/boards observe-scene example.kicad_pcb \
  --clearance-nm 250000 --track-width-nm 250000 \
  --via-diameter-nm 800000 --via-drill-nm 400000 \
  --region 0 0 30000000 30000000
```

Objects come back split into `static` (outline, footprints, pads, keepouts, rules) and `mutable`
(segments, arcs, vias, zones), each named by the Board IR reference it already carries so you can
refer to it in a later call. Footprints include pose, side, lock, pad ownership, and the supported
courtyard rings. Board text is omitted unless `--include-annotations` is passed, and even then it is
confined to a separate `annotations` collection marked untrusted: it is written by whoever authored
the board, and it is data to be read, never instructions to follow. Net names never appear at all.

Truncation against object, vertex, and annotation ceilings is reported explicitly rather than left
to be inferred.

### Deterministic render

Add `--render out/board.svg` to also write a deterministic SVG of the board's copper. The path
must be new and end in lowercase `.svg`; observation never overwrites a file. Two renders of an
unchanged board are byte-identical after a named `title-line-v1` canonicalization, and the response
records the digest and the exact inputs it was taken under. A truncated export is refused rather
than digested.

The render draws copper and the board outline only — silkscreen and fabrication layers are excluded
because KiCad embeds their text literally in the SVG — and it covers the whole board rather than the
requested region. It is an orientation aid: where it and the scene disagree, the scene is right.

## Preview a route

Preview one route without modifying the board, then optionally validate it with KiCad:

```bash
copper-mcp --workspace /absolute/path/to/boards preview-route example.kicad_pcb \
  --net AUDIO --layer F.Cu \
  --clearance-nm 250000 --track-width-nm 250000 \
  --via-diameter-nm 800000 --via-drill-nm 400000 --drc
```

An AI client does not need the hidden KiCad net name. It can copy a `net_id`, `board_revision`, and
`snapshot_digest` from `observe_board_scene` into the revision-bound selector:

```bash
copper-mcp --workspace /absolute/path/to/boards preview-route example.kicad_pcb \
  --net-ref-id net:name:... \
  --expect-board-revision sha256:... --expect-snapshot-digest sha256:... \
  --layer F.Cu --clearance-nm 250000 --track-width-nm 250000 \
  --via-diameter-nm 800000 --via-drill-nm 400000
```

The preview writes no file, creates no job, and stores no candidate. A changed board or Board IR
snapshot returns `stale_revision` instead of routing against state the client has not observed. It
succeeds only for the documented Board IR and single-layer routing subset; anything else returns a
typed diagnostic or bounded conversion-code counts.

The response contains the geometry CopperMCP generated, so hosts that must not disclose generated
copper to a model should not enable the `preview_route` tool.

## Preview a placement

Validate a proposed footprint placement without modifying the board:

```bash
copper-mcp --workspace /absolute/path/to/boards preview-placement example.kicad_pcb \
  --clearance-nm 250000 --track-width-nm 250000 \
  --via-diameter-nm 800000 --via-drill-nm 400000 \
  --subject footprint:kicad:<uuid> --subject footprint:kicad:<uuid>
```

Placement rules and proposals name objects only by the references a scene returned — there is no way
to write an absolute coordinate — so every position in the response was derived by CopperMCP and
snapped to an explicit grid. The seven rule kinds carry exact integer parameters, and the language
has no way to state an absolute coordinate or to permit an overlap.

The response proves three things and claims nothing else: pad overlap, board-outline containment,
and keepout respect. `pad_overlap` is three-valued, so `inconclusive` means neither clearance nor
collision could be proven rather than that something is wrong. Courtyard overlap is reported as
`not_modelled` because the bounded side-aware evaluator is not implemented yet, and
`infeasible_constraints` is never conflated with `budget_exhausted`. Locked footprints reject
movement proposals.

**The preview never applies a placement and is not bound to KiCad DRC evidence.** A placement also
invalidates any route candidate bound to the same board revision.

## Apply a route candidate

Apply a previewed route candidate to a board. **This is the only command that changes a board
file**, it is disabled unless you opt in, and it applies route patches only:

```bash
COPPER_MCP_ALLOW_APPLY=1 copper-mcp --workspace /absolute/path/to/boards \
  apply-candidate example.kicad_pcb \
  --candidate preview.json --expect-board-revision sha256:... \
  --clearance-nm 250000 --track-width-nm 250000 \
  --via-diameter-nm 800000 --via-drill-nm 400000
```

Close KiCad first. A lockfile beside the board is a hard refusal that names the file and is
never removed for you, because pcbnew has no external-change watcher and would silently
overwrite the applied board the next time it saves.

The board digest you pass as `--expect-board-revision` is compared before the edit and again
immediately before the file is replaced; if anything moved, the apply is refused and **never
silently re-routed**. A timestamped pre-apply copy is written beside the board first and its path
returned — **that copy is the undo, and restoring it means copying it back yourself.** It is not a
KiCad undo step, and KiCad's own `-bak` files are never touched.

The patch is spliced in so every untouched byte stays bit-identical, and publication is an atomic
replace that is verified afterwards and rolled back if it fails. Over MCP the same operation
additionally requires a single-use token issued by `preview_route`, bound to the exact candidate,
board revision, and path, and verified against a key that exists only inside the running process —
so a model cannot apply anything you did not preview.

An applied board carries no DRC evidence: what is verified is that every untouched byte is
identical, that the result reparses, and that its Board IR is the original plus the patch. There is
no merge, no lock override, and no batch apply.

## Build a schematic from Circuit Intent

Build a deterministic schematic from a strict Circuit Intent snapshot. This is the only current
durable schematic operation, and the output must be a new path inside the configured workspace:

```bash
mkdir -p /absolute/path/to/boards/artifacts  # artifacts/ is ignored by this repository
copper-mcp --workspace /absolute/path/to/boards render-schematic \
  intent/rc-low-pass.json --output artifacts/rc-low-pass.kicad_sch
```

The service records topology, digest, provenance, and deterministic-replay checks as passed. It does
not run KiCad on each build and reports KiCad parsing, ERC, and schematic-to-board parity as
`not_run`; electrical validation is also `not_run`, and board readiness is false.

The CLI refuses traversal, symlinks, a suffix other than exact lowercase `.kicad_sch`, and any
existing output rather than silently overwriting it. The input is captured from one held descriptor,
and output creation stays anchored to a held workspace-directory descriptor.

Schematic-to-board conversion, footprint assignment, and placement remain manual; the generated
schematic is not automatically connected to the board-preview workflow.

## Run the MCP server

Start the local MCP server over standard input/output:

```bash
export COPPER_MCP_WORKSPACE=/absolute/path/to/boards
copper-mcp-server
```

Example MCP client configuration:

```json
{
  "mcpServers": {
    "copper-mcp": {
      "command": "copper-mcp-server",
      "env": {
        "COPPER_MCP_WORKSPACE": "/absolute/path/to/boards",
        "COPPER_MCP_TRANSPORT": "stdio"
      }
    }
  }
}
```

Never place provider keys or proprietary board contents in committed MCP configuration. See
[`.env.example`](../.env.example) and the [security policy](../SECURITY.md).

### Artifact capabilities over stdio

On stdio, `render_circuit_schematic` accepts validated structured Circuit Intent content and returns
redacted metadata plus a non-enumerable `pcb://artifacts/schematic/...` capability. Its exact bytes
are accessible for at most 15 minutes in a 16-entry, 16 MiB process-local store. Expired entries are
removed lazily on later store activity or process exit, so expiry blocks access but does not promise
immediate memory erasure.

Fetching the resource reveals the schematic topology, so hosts decide whether to save it locally or
disclose it to a model. Schematic artifact tools and resources are disabled over streamable HTTP in
this MVP.

## Observe a running KiCad editor

To inspect a running KiCad PCB Editor through the official local IPC binding, install the optional
extra and enable KiCad's IPC server in the editor preferences:

```bash
python -m pip install -e ".[kicad]"
export COPPER_MCP_WORKSPACE=/absolute/path/to/boards
copper-mcp-server
```

Call the read-only MCP tool `inspect_live_board` for a redacted digest/metadata probe. To request
semantic geometry from the active editor, call `observe_live_board_scene` with the same constraints
and region fields as `observe_board_scene`, but set `board` to the literal `live`.

KiCad 9/10 requires a running GUI session, and the tools refuse a newer KiCad than the installed
`kicad-python` binding by default. Include both returned digests on a repeat call when you need a
stale-session refusal. Live routing, placement, DRC, and apply remain separate gates.

The IPC observer and the KiCad PCB-editor plugin report only a live board digest, version
compatibility, and bounded object counts; they never mutate KiCad or expose board text, net names,
UUIDs, or geometry.

## Related documents

- [MCP API contract](architecture/mcp-api.md) — the tool surface in contract terms.
- [Security and threat model](architecture/security-model.md) — why each boundary above exists.
- [Board IR contract](architecture/board-ir.md) — the supported board subset.
