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
to be inferred — and it is reported *where the objects would have been*, not only in the
`truncation` record. The object ceilings are spent over whole kinds, so every array you get back is
complete for the region and layers you asked for: `vias: []` means the region holds no vias, and
nothing else. A kind that did not fit is replaced by
`{"observation": "withheld_by_ceiling", "ceiling_hit": …, "objects_omitted": N}` in its own slot,
which is an object rather than an array precisely so that code reading the collection cannot mistake
it for an empty one. When you see one, ask for a smaller region rather than a larger ceiling.
Kinds are offered the budget smallest first, which in practice admits the outline and the rules —
the two you need in order to choose a window — ahead of the tens of thousands of segments. That is
a greedy ordering and not a guarantee: `outline` is a withholdable kind like any other, so read its
slot rather than assuming it is there.

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

**Zone fill and the apply token.** Over MCP only, `preview_route` accepts `include_fill_authority`.
With it set, CopperMCP refills a private disposable copy of the board with KiCad and admits the
cached pour only if the refill reproduces it exactly; verified foreign islands then replace that
zone's conservative envelope on the layer they were proved on, and the response carries a typed
`routing_effect` saying which role the evidence played. A cache KiCad does not reproduce refuses
`stale_fill` rather than answering from either version. **A candidate the pour shaped cannot be
applied**: `preview_route` returns it with no `apply_token` even when `include_apply_token` is set,
because apply runs in a later process holding no fill evidence and could only replay under the
looser envelope. `include_fill_authority` with `include_drc` is supported and the evidence binds;
`include_fill_authority` with `include_apply_token` returns a candidate and no token
([ADR-0103](adr/0103-a-candidate-records-the-model-that-produced-it.md)). The `copper-mcp
preview-route` CLI command has no equivalent flag.

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

The response proves four things and claims nothing else: pad overlap, board-outline containment,
keepout respect, and courtyard overlap. `pad_overlap` is three-valued, so `inconclusive` means
neither clearance nor collision could be proven rather than that something is wrong.
`infeasible_constraints` is never conflated with `budget_exhausted`. Locked footprints reject
movement proposals.

`courtyard_overlap` **is evaluated** — it is three-valued, `proven_clear`, `inconclusive`, or
`violated`, and only a violation makes the candidate illegal. Read it for exactly what it covers:

- It compares rings **per courtyard layer, not per footprint side**. `F.CrtYd` and `B.CrtYd` are
  independent physical layers, so an `F.CrtYd` shape never collides with a `B.CrtYd` one — but a
  footprint may draw on the layer opposite its own side, and when it does, that keep-out is compared
  on the layer it was drawn on. Two coincident `B.CrtYd` rectangles collide whichever sides their
  footprints sit on, which is what `kicad-cli` 10.0.5 reports
  ([ADR-0097](adr/0097-courtyard-layer-decides-the-side.md)).
- It covers the Board IR `0.2` courtyard subset only: simple closed orthogonal rings — unfilled
  `fp_rect`, unfilled `fp_poly`, and degree-two closed `fp_line` chains. Curves, diagonals, fills,
  and open or branching chains are refused by the Board IR contract before a placement view exists,
  so an unsupported courtyard is a refusal rather than a silent pass.
- **All of one footprint's rings are one region, filled even-odd.** A ring nested inside another is
  a *hole*, not a second solid, so the centre of a donut courtyard — an RF shield can, a socket — is
  legitimately occupiable. This matches what KiCad computes when it builds its courtyard cache.
- **The comparison models KiCad's cached-courtyard inset.** KiCad contracts each cached outline by
  5,000 nm, so a zero-clearance collision needs 10,000 nm of penetration. Measured against real
  `kicad-cli` 10.0.5: 9,999 nm is clear and 10,000 nm reports `courtyards_overlap`, on both edge-on
  and corner-only overlap. Edge and corner contact are legal, matching KiCad's zero-clearance
  default.

Read the three values for exactly what each claims:

| Value | What it claims |
|---|---|
| `proven_clear` | The regions share no area. Contraction only shrinks a region, so this is a proof for any supported ring shape. |
| `violated` | The shared area is at least 10,000 nm across on both axes, so KiCad's contracted caches provably meet. Exact parity. |
| `inconclusive` | The regions overlap by less than that. KiCad calls it clear and the raw geometry calls it a collision, so **neither is claimed**. Like an inconclusive `pad_overlap`, it is not a failure and a candidate is still produced. |

If your workflow needs the stricter reading, treat `inconclusive` as a failure yourself — that is
what the third value is for. It is never rewritten to `proven_clear`.

One thing remains out of scope and is not implied by a `proven_clear` result: there is no
configurable courtyard clearance. The check answers overlap, not separation, so it cannot express a
"keep 0.2 mm between parts" rule. Also unmodelled, and reported as `inconclusive` rather than
guessed: courtyards thinner than the 10,000 nm threshold, whose behaviour under KiCad's contraction
was measured as orientation-dependent.

**The preview never applies a placement and is not bound to KiCad DRC evidence.** A placement also
invalidates any route candidate bound to the same board revision.

## Apply a route candidate

Apply a previewed route candidate to a board. **This is the only CLI command that changes a board
file**, it is disabled unless you opt in, and it applies route patches only. CopperMCP has exactly
one other mutating operation, the MCP-only `apply_placement_candidate` described
[below](#apply-a-placement-candidate); there is no CLI equivalent for it.

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

## Apply a placement candidate

`apply_placement_candidate` is CopperMCP's **second and last** mutating operation, and it exists
only as an MCP tool — there is no CLI equivalent. It writes a footprint pose rather than copper.

It is gated exactly like route apply and then some. The same `COPPER_MCP_ALLOW_APPLY=1` flag must be
set, and the tool additionally requires its own single-use token, issued by `preview_placement` with
`include_apply_token: true` and bound to the exact candidate, board revision, and path. The token's
operation domain is placement, so a route token can never authorize a placement write and a
placement token can never authorize a route write. Neither the flag nor a token can be minted by a
model. The request takes `board`, the `candidate` manifest from the preview, `apply_token`,
`expect_board_revision`, and `constraints`.

The write discipline is the same as route apply: lockfile refusal, the board digest compared before
the edit and again immediately before replacement, a timestamped pre-apply copy as the only undo,
byte-preserving splice, and an atomic replace that is verified afterwards.

What it admits is narrower than route apply. Only the source-preserving front-side orthogonal
footprint subset replays: a footprint must be on the front side, at an orthogonal rotation, carry
exactly one native KiCad identity, and use unfilled rectangular `fp_rect` courtyard centerlines. A
side flip, a locked footprint, a non-orthogonal angle, a derived identity, a no-op candidate, or any
unsupported property, text, fabrication graphic, library identity, or 3D-model pose refuses before a
single byte is written. The response reports `footprints_moved` and `bytes_changed`; the KiCad-open
and DRC stages report `not_run`.

Moving a footprint moves its pads, so applying a placement invalidates any route candidate bound to
the same base revision.

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

To ask KiCad itself, use the separate ERC surface, which renders the same intent and checks it with
the authoritative `kicad-cli sch erc`:

```bash
copper-mcp --workspace . schematic-erc intent/rc-low-pass.json
```

That result reports `passed` (KiCad found no error-severity violation) and `clean` (no findings and
no ignored checks at all) as two independent signals, plus a round trip that re-reads the schematic
through `kicad-cli sch export netlist` and compares the recovered components and nets against the
source intent. The bounded passive fixture is `passed: true, clean: false` — it genuinely produces
KiCad warnings, and those stay visible. Electrical validation and board readiness remain explicit
non-claims. Because the checked snapshot carries no `.kicad_pro`, KiCad applies its default
severities, so the verdict is not necessarily what your own project reports.

To ask whether a board you already have implements that intent's connectivity, use the
source-to-board parity surface, which needs an existing `.kicad_pcb` inside the workspace:

```bash
copper-mcp --workspace . source-to-board-parity intent/rc-low-pass.json \
  --board boards/rc-low-pass.kicad_pcb
```

The board is read and never written, and `kicad-cli pcb drc --schematic-parity` decides the verdict.
Read the result carefully: `schematic_board_parity: "passed"` says the board matches **the intent's
connectivity**, not that it matches the schematic file `render-schematic` wrote for you. That file
marks every symbol `on_board no` — correct for a delivery artifact with no footprint assignments —
and such a symbol never enters KiCad's board-side netlist, so comparing against it would report a
correct board and a wrong one identically. The board is compared against a board-eligible
*projection* of the same intent instead, whose digest is reported separately under
`parity_projection`. The verdict is refused outright unless KiCad demonstrably accounted for every
component, because an empty parity result is also what a check that never ran produces. ERC,
footprint correctness, electrical validation, and board readiness stay explicit non-claims, and only
per-type counts are returned — no net names, references, or coordinates.

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
export COPPER_MCP_ALLOW_LIVE_IPC=1
copper-mcp-server
```

`COPPER_MCP_ALLOW_LIVE_IPC` is required and defaults to off. Talking to a running editor is an
outbound action against your machine rather than a read of a file you handed the server, so it
follows the same rule as `COPPER_MCP_ALLOW_APPLY`: the value must be exactly `0` or `1`, and
anything else — `true`, `yes`, an empty string — is a configuration error rather than a silent
enable. With it off, every live tool stays listed and answers with a refusal naming the flag, and
the server reads no IPC socket from the environment and opens none.

Call the read-only MCP tool `inspect_live_board` for a redacted digest/metadata probe. To request
semantic geometry from the active editor, call `observe_live_board_scene` with the same constraints
and region fields as `observe_board_scene`, but set `board` to the literal `live`.

KiCad 9/10 requires a running GUI session, and the tools refuse a newer KiCad than the installed
`kicad-python` binding by default. Include both returned digests on a repeat call when you need a
stale-session refusal. Live routing, placement, DRC, and apply remain separate gates.

The IPC observer and the KiCad PCB-editor plugin report only a live board digest, version
compatibility, and bounded object counts; they never mutate KiCad or expose board text, net names,
UUIDs, or geometry.

### Install the KiCad plugin from the Plugin and Content Manager

The PCB-editor half of that surface ships as a KiCad addon package,
`com.github.seunghyukchoe.coppermcp-live-observer`, requiring KiCad 9.0.1 or newer. Install it
through **Tools → Plugin and Content Manager**, then finish two steps the PCM cannot do for you:

```bash
# 1. Into the interpreter shown under Preferences > Plugins, not necessarily this one.
python -m pip install 'copper-mcp[kicad]'
# 2. In the environment KiCad is launched from, before KiCad starts.
export COPPER_MCP_ALLOW_LIVE_IPC=1
```

**Installing the plugin grants nothing by itself.** It is the observation half of a boundary whose
server half is off by default, and the flag above is the same one that gates every live surface
described in this document. Without it the plugin's action prints
`CopperMCP IPC observer unavailable: KicadIpcDisabledError` and reads nothing — it does not fall
back to discovering a socket. Without the pip install it refuses with a message naming that step.
Neither is a bug; the button being inert until you have authorized the host is the design.

CopperMCP is not on PyPI, so KiCad cannot install it for you: the PCM resolves a plugin's
`requirements.txt` against PyPI under `--only-binary :all:`, and the shipped file therefore
installs nothing on purpose. KiCad builds the per-plugin environment with `--system-site-packages`,
which is what makes step 1 visible to the plugin. `KICAD_API_TOKEN`, which KiCad hands to every
launched plugin, never leaves the plugin process.

See [`hardware/kicad-ipc-plugin/README.md`](../hardware/kicad-ipc-plugin/README.md) for the
development install, the package build, and the submission checklist, and
[the PCM distribution research note](research/kicad-pcm-distribution-v1.md) for the format itself.

## Live apply: what is gated, and what is not implemented

`apply_live_candidate` is the surface that will one day push a candidate into the running editor as
one undo step. **The mutation is not implemented.** The tool verifies every precondition — the
operator opt-in, a live-scoped single-use capability, the editor session, the board serialization,
the converted Board IR snapshot, and the candidate's own identity and geometry replayed against the
board the editor is holding — and then refuses with `capability_not_implemented`. The response's
`preconditions_verified` lists exactly the checks that ran, so reaching that code tells you your
capability and all three revisions were good.

It needs **two** opt-ins, both exact `0`/`1` and both default off:

```bash
export COPPER_MCP_ALLOW_LIVE_IPC=1
export COPPER_MCP_ALLOW_LIVE_APPLY=1
```

`COPPER_MCP_ALLOW_APPLY` is neither sufficient nor required. It authorises replacing a file on
disk, which is a different capability: enabling it does not enable live mutation, and you do not
have to enable it to get live mutation. Requiring it would mean anyone who wants to touch only the
editor must also grant permission to overwrite their boards.

Get a capability from `preview_live_layered_route` with `include_apply_token: true`. It is minted
only for a `routed` proposal and only when live apply is enabled; otherwise `apply_token` is
`null`. The capability is bound to the candidate, the board revision, the converted snapshot **and**
the editor session — via the instance identity KiCad reports, which is regenerated per editor
process — so it cannot survive a KiCad restart and cannot be replayed against the
file-backed `apply_candidate`.

When the mutation lands it will change the in-memory document only — one entry in KiCad's own undo
stack, no file written until you save — and it will re-observe the result rather than assume it.
See [ADR-0074](adr/0074-live-ipc-one-undo-commit-apply.md) and the
[IPC apply research](research/ipc-apply-v1.md) for why the protocol makes that re-observation
mandatory rather than optional.

## Related documents

- [MCP API contract](architecture/mcp-api.md) — the tool surface in contract terms.
- [Security and threat model](architecture/security-model.md) — why each boundary above exists.
- [Board IR contract](architecture/board-ir.md) — the supported board subset.
