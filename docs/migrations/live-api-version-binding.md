# The live KiCad surface binds to a version window, and `compatible` now means less

The live IPC surfaces stop delegating their compatibility decision to `kicad-python`'s
`check_version()` boolean and apply a declared window instead (ADR-0128, D-236, R-186). If you
deploy a live surface, three things change for you, and one of them is a claim getting *weaker*
on purpose.

## Read this first: `compatible` was previously reported for editors it had never checked

`kipy.KiCad.check_version()` raises only when the connected KiCad is **strictly newer** than the
version the binding was built against. It returns `True` for every older editor, however far
behind. CopperMCP consumed that boolean directly, so against the installed binding's bundled
API version of `10.0.1`:

| Your KiCad | Before | Now |
|---|---|---|
| 10.0.1 (exact match) | `compatible` | `compatible` |
| 10.0.5 | **refused** | `future_api_unverified`, observed |
| 10.0.2, 10.1.0 | **refused** | `future_api_unverified`, observed |
| 10.0.0 | `compatible` | `legacy_api_unverified`, observed |
| 9.x, 8.x | **`compatible`** | **refused**, naming both versions |

The two bold cells are the point. A KiCad one patch release ahead of the binding was refused; a
KiCad a whole major behind it was certified as verified-compatible. Both are corrected here.

**If you were relying on `compatibility == "compatible"` as a gate, it will now fire strictly
less often.** That is intended: it now means the editor and the binding report the same
`major.minor.patch`, and nothing weaker. If your check was "did the live read succeed", test for
the absence of a refusal instead — every accepted verdict is a successful, observed read.

## 1. `allow_future_api` is gone

The argument is removed from `capture_live_board`, `capture_live_editor_context` and
`inspect_live_board`. It was never an MCP argument, so **no MCP caller is affected**. If you call
these functions directly from Python and passed `allow_future_api=True`, delete the argument: the
behaviour it used to buy is now the default within the window, and there is no longer any way to
widen the window past a major boundary.

This also fixes an asymmetry you may have hit: `inspect_live_editor_context` never passed the
flag through, so it refused editors that `inspect_live_board` could observe. Both surfaces now
answer identically.

## 2. Two new fields, and one renamed key

Both live contracts move to schema version **`0.2.0`** (`copper.live-editor-context` likewise).

**`document_binding`** is added to the board observation and the editor context, always
`in_memory_unsaved_state_unobservable`. It says that `board_digest` is the digest of KiCad's
*in-memory* document and never of a file on disk. It is **not** a dirty flag — the IPC API
exposes none, and a `unsaved_changes` field would be fabricated. B-138 measured the gap it warns
about: 165,571 live bytes against 166,070 on disk for the same board. If you were treating
`board_digest` as comparable to a file hash, stop; it never was (ADR-0074).

**The editor context gains `kicad_version`, `api_version` and `compatibility`**, which it
previously computed and discarded. A caller could not tell a verified read from an unverified one
on that surface at all.

**`object_counts["nets"]` is renamed to `object_counts["net_declarations"]`.** This is the only
change here that breaks a consumer loudly, and that is deliberate. The key counts top-level
`(net …)` declarations; a KiCad 10 document carries none, referencing nets by name on items
instead. B-138 measured it reporting `0` against an editor holding 15 nets. The count was correct
and its name was not.

**No replacement net count is published.** Deriving one from item references would produce a
number never checked against `Board.get_nets()`, and shipping an unverified count under the name
callers trust is exactly the defect being fixed. If you need a real net count, convert to Board IR
and read `object_counts["nets"]` there — that key is a genuine net collection and is unchanged.

## 3. Live apply is stricter than the read surfaces

`apply_live_candidate` requires a **verified** binding and refuses both acceptances with
`unsupported_kicad_version`, naming the verdict and both versions. A read that drifts can publish
the verdict saying so; a mutation cannot, because the board has already changed by the time a
caller reads any caveat. If you were previewing and applying against a KiCad that does not match
your binding exactly, the preview still works and the apply now refuses. Align the versions, or
install a `kicad-python` built against your KiCad.

## What is not claimed

Nothing here is a claim about any KiCad other than the 10.0.5 that B-138 observed, or any
`kicad-python` other than 0.7.1. The window's acceptance paths are exercised against fakes that
reproduce `check_version()`'s measured asymmetry; **no version pair other than B-138's has been
run against a real editor.**
