# ADR-0128: The live version window is KiCad's own, and an acceptance is never published as a proof

- Status: Accepted
- Date: 2026-09-01
- Owners: `@seunghyukchoe`
- Related: [B-138](../ledgers/benchmark-ledger.md) (the measurement that forced this),
  [D-236](../ledgers/decision-ledger.md), [R-186](../ledgers/risk-register.md),
  [ADR-0074](0074-live-ipc-one-undo-commit-apply.md) (binds to the serialization, never the file),
  [ADR-0069](0069-operator-gated-live-ipc-observation.md) (the operator opt-in this sits behind),
  [ADR-0029](0029-read-only-kicad-ipc-observer.md) (the read-only observer),
  [ADR-0121](0121-a-refusal-is-an-answer-and-a-crash-is-not.md) (a refusal is an answer),
  [ADR-0123](0123-a-container-refusal-that-names-no-field-is-the-defect.md) (a refusal names its
  subject), [ADR-0105](0105-a-schema-version-moves-with-its-accepted-set.md) (why the schema
  version moves here), [issue #68](https://github.com/seunghyukchoe/copper-mcp/issues/68)

## Context

[B-138](../ledgers/benchmark-ledger.md) put this project's live surfaces in front of a real
KiCad 10.0.5 editor for the first time and recorded the result: **three of four refused it.**
`inspect_live_board` on its default path and `inspect_live_editor_context` both raised
`KicadIpcVersionError`; the capability oracle skipped; and an observation was obtainable only
through `allow_future_api=True`, a flag deliberately excluded from the MCP surface. The shipped
live surface could not speak to the KiCad the operator actually had installed.

That is the visible half. Reading `kicad-python` 0.7.1's own source for this decision surfaced
the other half, which is worse.

### What the binding's check actually does

`kipy.KiCad.check_version()` is four lines:

```python
kicad_version = self.get_version()
api_version = self.get_api_version()
if kicad_version > api_version:
    raise FutureVersionError(...)
return True
```

`KiCadVersion.__gt__` compares only the `(major, minor, patch)` tuple. So the check is
**asymmetric**: it raises for a strictly newer editor and returns `True` for *every* older one.
Enumerated against the installed binding's bundled `KICAD_API_VERSION` of `10.0.1`:

| Editor | `check_version()` | CopperMCP published, before this ADR |
|---|---|---|
| 10.0.0 | returns `True` | `compatible` |
| 10.0.1 | returns `True` | `compatible` |
| 10.0.2 | raises | refusal |
| **10.0.5** | **raises** | **refusal** (B-138's case) |
| 11.0.0 | raises | refusal |
| **9.0.0** | **returns `True`** | **`compatible`** |
| **8.0.4** | **returns `True`** | **`compatible`** |
| **0.0.0** | **returns `True`** | **`compatible`** |

CopperMCP consumed that boolean and stamped `compatibility: "compatible"` whenever it was
`True`. **So the shipped surface refused a KiCad one patch release ahead of its binding, and
silently certified a KiCad two major versions behind it as verified-compatible.** It refused
the safe case and accepted the dangerous one. The forbidden direction — accepting an
unverified API silently — was not a risk this decision had to weigh. It was already shipping.

### What KiCad actually guarantees

The guarantee is a **Protobuf wire-compatibility guarantee, not a semver guarantee**. From
KiCad's [IPC API developer documentation](https://dev-docs.kicad.org/en/apis-and-binding/ipc-api/index.html),
the API is "designed to be a stable interface that does not change when KiCad's internals are
refactored", and new versions "may introduce new messages and fields, but will not modify the
meaning of existing messages and fields". Deprecated messages and fields "will be supported for
at least one major version of KiCad after the deprecation is announced."

Two things follow, and they point in opposite directions.

**There is no patch-level API freeze, and the project's own rules presume the opposite.** KiCad's
[rules for its own developers](https://dev-docs.kicad.org/en/apis-and-binding/ipc-api/for-kicad-developers/index.html)
say "Never remove fields in a bugfix release of KiCad" and instruct contributors to annotate
added fields with a note "such as `// Since: 9.0.1`" — a *patch* version. The convention exists
because patch releases add API surface. They observably do: the
[KiCad 10.0.1 release notes](https://www.kicad.org/blog/2026/04/KiCad-10.0.1-Release/) add
barcode support, title-block writes, connected-item queries and dimension reads to the IPC API,
and [kicad-python's README](https://gitlab.com/kicad/code/kicad-python/-/blob/main/README.md)
pins features to patch-level minimums — `9.0.4`, `9.0.5`, `9.0.8`, `10.0.1`. **A patch series is
not a stable surface, so "same minor" cannot mean "verified".**

**But the meaning of what already exists does not change within a major.** That is the whole
content of the wire guarantee, and it is exactly the property a *reader* needs. A binding built
at 10.0.1 talking to 10.0.5 will not misread anything it knows about; it will merely be unaware
of what 10.0.5 added. The risk in the newer direction is **incompleteness, not misparsing**.

The older direction has no such cover. Nothing in KiCad's documentation addresses a client newer
than the editor, and kicad-python's own patch-level minimums prove the client surface outruns
older editors. A call into a command that does not exist yet fails — but it fails **loudly**,
as an `ApiError` at call time, not as a silent misreading.

### Two options this decision wanted and cannot have

**A capability probe.** Enumerating every message in KiCad's four command proto files
([base_commands](https://gitlab.com/kicad/code/kicad/-/blob/master/api/proto/common/commands/base_commands.proto),
[editor_commands](https://gitlab.com/kicad/code/kicad/-/blob/master/api/proto/common/commands/editor_commands.proto),
and the envelope/base types) finds **no capability or feature-discovery mechanism at all**. The
only substitute is failure-based probing on `AS_UNIMPLEMENTED`/`AS_UNHANDLED` from
[envelope.proto](https://gitlab.com/kicad/code/kicad/-/blob/master/api/proto/common/envelope.proto),
and that is unusable twice over: `kipy/client.py` collapses every non-`AS_OK` status into a
generic `ApiError` with no distinction, and probing is destructive because it means actually
issuing the command. **"Minor-tolerant with a capability probe" is not an option that exists.**

**A negotiated API version.** There is none. `KICAD_API_VERSION` is generated by
[kicad-python's build.py](https://gitlab.com/kicad/code/kicad-python/-/blob/main/build.py) running
`git describe` against a vendored KiCad submodule, so the "API version" *is* a KiCad version tag.
The `ApiRequestHeader` in envelope.proto carries only `kicad_token` and `client_name` — **no
version field and no handshake**. The two version numbers this project compares are the only
compatibility signal the protocol offers.

## Decision

**The compatibility window is the major version, because that is exactly where KiCad's own two
guarantees lapse.** Within a major, field meanings are stable and deprecated fields survive;
across a major boundary, both promises expire and a field the binding still reads may be gone or
repurposed. That is the one situation that could turn a live read into a *wrong* answer rather
than a failed one, and it is the only thing this decision refuses.

Inside the window, every pair is **accepted and observed**, and the response says which of three
things happened. Outside it, the surface refuses and the refusal names both versions.

| Verdict | Condition | Meaning |
|---|---|---|
| `compatible` | editor and binding report the same `major.minor.patch`, and `check_version()` agreed | **Verified.** The only verdict carrying a proof. |
| `future_api_unverified` | editor newer, same major | Accepted. Field meanings are guaranteed; the binding may be unaware of additions. |
| `legacy_api_unverified` | editor older, same major | Accepted. No guarantee covers this; a call may not exist and will fail loudly. |
| *refusal* | major versions differ | `KicadIpcVersionError` naming both versions and the reason. |

Three properties make this more than a relabelling.

**1. `compatible` is exact-match, and nothing else.** Not "same minor" — the `// Since: 9.0.1`
convention and 10.0.1's added commands rule that out. The response carries `kicad_version` and
`api_version` alongside the verdict, so the *direction and magnitude* of any drift stay
recoverable by the caller; what the verdict adds is the one bit a caller cannot derive, namely
whether this build proved anything. `VERIFIED_API_COMPATIBILITY` is a set of exactly one member,
deliberately a set rather than an equality test, so that widening it is a visible edit.

**2. Acceptance is the default and there is no flag.** `allow_future_api` is **retired**, not
re-defaulted. The asymmetry B-138 found — `inspect_live_editor_context` refusing an editor
`inspect_live_board` could observe — was not a missing line of code, it was a flag that one call
site remembered and another did not. A boolean that must be threaded through every call site to
avoid a spurious refusal is a defect generator. Removing it makes the class of bug impossible
rather than fixing one instance, and it means no caller can widen the window either.

**3. The window is tiered by what a surface can do.** Read surfaces accept all three verdicts,
because a read's worst case is an incomplete answer *accompanied by the verdict saying so*.
**Live apply requires `compatible`** and refuses both acceptances. A mutation has no disclosure to
hide behind: the board changes either way, and "the write went out against an API this build
never verified" is not a caveat a caller can act on afterwards.

### Two disclosures the same measurement forced

**`object_counts["nets"]` is renamed to `net_declarations`.** B-138 measured it reporting `0`
against an editor holding 15 nets. The count was never wrong — it counts top-level `(net …)`
declarations, and a KiCad 10 document carries none, referencing nets by name on items instead.
The *name* was wrong, and a reader taking `nets: 0` as "this board has no nets" would have been
misled by a field that was working correctly. **No derived cardinality replaces it.** Counting
distinct net names off item references would produce a number that has never been checked against
`Board.get_nets()`, and publishing an unverified count under the name a caller trusts is the
error this rename exists to stop, committed a second time in a new place. Silence beats a wrong
number; a correctly-named number beats silence.

**Every live observation now states what its digest binds.** `document_binding` is
`in_memory_unsaved_state_unobservable`. B-138 measured 165,571 live bytes against 166,070 on
disk — [ADR-0074](0074-live-ipc-one-undo-commit-apply.md)'s gap, measured rather than argued for
the first time. This field does **not** report a dirty flag, because there is none to report:
`kipy` 0.7.1's `Board` offers `save`, `save_as` and `get_project` and nothing that reports
modified state. A `unsaved_changes: true|false` field would be fabricated. What the surface can
truthfully say is what it bound, and saying it is what stops a reader inferring the on-disk file
from a digest that was never taken of it. ADR-0074 already refused to bind a live read to the
file; this makes that refusal legible to a caller instead of leaving it in an ADR.

## Consequences

- **The live surface can speak to a current KiCad.** B-138's 10.0.5 editor is observable on
  every read surface with no flag, carrying `future_api_unverified`.
- **A KiCad two majors behind is now refused rather than certified.** This is a behaviour change
  in the safe direction and the deployer-visible one; the migration note carries it.
- **`compatible` means less than it did, and now means something true.** A caller who checked
  `compatibility == "compatible"` will see it fire strictly less often.
- **IPC schema moves to `0.2.0`** on both live contracts: the `compatibility` accepted set gains
  a member and `document_binding` is added, which is [ADR-0105](0105-a-schema-version-moves-with-its-accepted-set.md)'s
  rule applied to a Pydantic contract rather than a published JSON schema.
- **`object_counts` consumers keyed on `nets` break loudly** with a `KeyError` rather than
  silently reading a number that meant something else.
- **Live apply is stricter than before** in the drifted directions and unchanged otherwise. It
  remains withheld at its own boundary for the reasons ADR-0118 and ADR-0120 record; this
  decision does not open it.

## Alternatives considered

- **Keep the exact pin (status quo).** Rejected: it refuses reality — a patch release ahead of
  the binding — while silently certifying a two-major-old editor. It is not conservative; it is
  wrong in both directions at once.
- **Make `allow_future_api` default to true.** Rejected. It leaves the flag, so it leaves the
  defect that one call site forgets it, and it addresses only the newer direction while the
  older direction keeps publishing `compatible`.
- **Patch-tolerant within a minor, publishing `compatible`.** Rejected on KiCad's own
  documentation: the `// Since: 9.0.1` convention and 10.0.1's added IPC commands are direct
  evidence that a patch series adds API surface. `compatible` would be a claim the evidence
  contradicts.
- **Minor-tolerant with a declared compatibility surface and a capability probe.** Rejected as
  **not implementable**, on measurement rather than preference: there is no capability or
  feature-discovery message anywhere in KiCad's command protos, `kipy` collapses the status codes
  that would substitute for one, and probing would mean issuing the command for real.
- **Refuse the older direction outright.** Rejected as needlessly useless. A binding one patch
  ahead of the editor is an ordinary install state, the commands these surfaces use are the most
  stable in the API, and the failure mode is a loud call-time error rather than a wrong reading.
  It is disclosed instead of refused — but it is never called `compatible`.
- **Accept across a major boundary with a third disclosure.** Rejected. That is the one region
  where KiCad permits a field to be removed or to change meaning, so it is the one region where
  a read could be confidently wrong. A disclosure does not help a caller who has been handed a
  misparsed board.
- **Derive a net count from item references to replace `nets: 0`.** Rejected: unverifiable
  against `Board.get_nets()` without a live session, and shipping an unchecked number under the
  name a caller trusts repeats the defect being fixed.
- **Compare the live digest against the on-disk file to derive a dirty flag.** Rejected;
  ADR-0074 already rejected it. The surface never reads that file, and bytes differing is not
  the same proposition as the document being dirty.
