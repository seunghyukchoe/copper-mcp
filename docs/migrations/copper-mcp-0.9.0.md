# Migrating a deployment to CopperMCP 0.9.0

> This note is being written as 0.9.0 is assembled. It currently covers one change; further
> sections are appended by the pull requests that land them.

## 1. `BOARD_IR_SCHEMA_VERSION` moves from `0.2.0` to `0.3.0`

This is the change most likely to break a 0.8.0 deployment, and unlike everything in the 0.8.0
note it is a **decision** rather than a compatibility fix. It is
[ADR-0105](../adr/0105-a-schema-version-moves-with-its-accepted-set.md), filed out of
[issue #172](https://github.com/seunghyukchoe/copper-mcp/issues/172), recorded as `D-197`.

### What breaks

**A persisted `0.2.0` Board IR envelope no longer decodes.** There is no auto-migration and none is
offered, exactly as `0.1` → `0.2` had none: **re-convert from the source `.kicad_pcb`**. The
conversion is deterministic, so the snapshot digest you get back is the one you had.

**The diagnostic vocabulary gains one code: `schema.version`.** An envelope that is well-formed
Board IR at a version this build does not accept now refuses with `schema.version` at locator
`snapshot.schema_version`, where it previously refused with `schema.invalid`. **This affects `0.1`
envelopes too**, which have always taken that path. If you branch on the code, add the new one;
`schema.invalid` now means what it says — the bytes are not Board IR.

The reason is worth a sentence, because it is the same class as the rest of this note. A persisted
`0.2.0` envelope conforms to `0.2.0`-as-published *exactly*; it is refused **because** it does, at a
superseded version. The old message — "JSON does not conform to Board IR v0.2" — told the caller the
opposite of the truth. The new one names the version this build accepts and tells you to
re-convert, and it is derived from the version constant so it cannot go stale the way its
predecessor did. It never repeats the version your document declared: that string is
caller-controlled, and no CopperMCP diagnostic echoes caller-controlled bytes.

**`BoardIrSummary.ir_schema_version` now reports `0.3.0`.** A client asserting the literal `0.2.0`
fails. A client that reads it and moves on is unaffected.

### What does not break, and this is the point

**No content address moves.** The snapshot digest is `sha256:157661bf…` before and after —
identical — and so are the constraint digest and the source revision. The digest is taken over
`_content_payload`, which carries no schema version; the version appears only in the envelope.
Downstream identities bind `base_revision = snapshot_digest`, so **there is no cascade**: every
stored route candidate, layered candidate, placement candidate and bundle still binds to the same
base revision it always did.

The encoded envelope is **4,280 bytes before and after** — `"0.2.0"` and `"0.3.0"` are the same
width — and differs at **exactly one byte**. This is verified rather than asserted: substituting
`0.2.0` back into a `0.3.0` envelope reproduces the `v0.8.0` bytes exactly, and
`tests/test_board_ir_schema.py::test_the_version_bump_moved_the_envelope_by_its_version_string_and_nothing_else`
checks that against a recorded digest on every run.

**The accepted set is unchanged.** `schemas/board-ir/0.3.0.schema.json` differs from `0.2.0` in
three strings: `$id`, `title`, and the `schema_version` `const`. No modelled field, type or
invariant moves. If your board converted under 0.8.0 it converts identically now.

### `0.2.0`-as-published spans three accepted sets, and this is the part to read carefully

`schemas/board-ir/0.2.0.schema.json` is **frozen permanently** at its `v0.8.0` bytes. It is not
corrected retroactively, and the reason is that a correction cannot reach the copies that matter —
they are in eight release tags, in the sdist and wheel on PyPI, and in whatever you downloaded.
Editing it would produce a *fourth* accepted set for one version string.

So, plainly:

> **`0.2.0` as published spans three different accepted sets across `v0.5.0`–`v0.8.0`.** They are
> not interchangeable. The authoritative copy for any snapshot you hold is **the one shipped
> alongside the release that produced it** — not the newest copy, and not simply the one whose
> version string matches.

| The `0.2.0` schema as published at | Accepts a `$defs/footprint` carrying |
|---|---|
| `v0.5.0`, `v0.6.0` | `courtyards` only |
| `v0.7.0` | `+ courtyard_circles`; `net_id` may be `null` on via, segment and arc |
| `v0.8.0` | `+ far_side_courtyards`, `+ far_side_courtyard_circles` |

`$defs/footprint` sets `additionalProperties: false`, so the incompatibility runs one way: a
document produced at `v0.8.0` carrying a far-side courtyard is **rejected** by the `v0.5.0` copy.
The reverse is fine. If you validate stored snapshots, pin the schema copy to the release that
produced them; `git show v0.7.0:schemas/board-ir/0.2.0.schema.json` retrieves any of the three.

**This is not confined to Board IR.** The same in-place practice produced two more instances, and
one of them breaks in the *other* direction — a required-key addition, which invalidates documents
already written rather than making an old consumer over-refuse:

| Release | Schema | Change | Direction |
|---|---|---|---|
| `v0.3.0` | `audio-benchmark-catalog/0.1.0` | `expected_pad_count` became a **required** key; a `multi-pin-route-preview` enum member was added | **narrowing** and widening |
| `v0.7.0` | `drc-summary` | `clean` became a **required** key | **narrowing** |

Neither file is versioned forward here — no document under either has been re-emitted — but both are
inside the rule from now on, and both are inside the gate.

### What stops the next one

`scripts/check_schema_sets.py`, in `make lint` and in CI. It fails when any `schemas/**/*.json`
accepted set changes while the declared version does not, in **either** direction, and it names
which. The four historical instances are carried as exemptions keyed `(file, version, tag)`, each
naming `D-197`; an exemption that matches no real drift fails the run, so the list cannot become a
suppression mechanism.

It also fails when a published schema is deleted, and when a release tag exists that its own tag
list omits. An exemption may only name a **release tag** — a live break cannot be waved through by
keying one to the working tree — and its recorded direction must be one the comparison actually
observed.

**An accepted-set gate cannot enforce a byte freeze**, so it does not pretend to. Both frozen
schemas, `board-ir/0.1.0` and `board-ir/0.2.0`, additionally carry a **sha256 pin of their exact
published bytes**. That is what closes the three silent routes an accepted-set comparison cannot
see: deletion, a cosmetic rewrite, and an edit to a keyword it does not watch. The active `0.3.0` is
deliberately not pinned — it is expected to change, with its version.

It is a floor rather than a proof, and `R-151` records what it cannot see: a tightened `pattern`, a
lowered `maximum`, a re-pointed `$ref`. A green run means no *watched* keyword moved.

### What is deliberately unchanged

About twenty refusal messages still read `Board IR v0.2`. They name the IR **model generation**,
which this release does not move — no modelled field, type or invariant changes — and rewriting them
would be a blanket replacement across a published refusal surface for a change about the envelope's
version alone. If you match on those strings, they are stable. ADR-0105 does not claim they are
unambiguous to a reader holding only the string.
