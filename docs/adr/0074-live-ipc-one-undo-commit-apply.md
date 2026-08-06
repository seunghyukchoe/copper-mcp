# ADR-0074: Gate live editor mutation on its own consent, and ship its preconditions before it

- Status: Accepted
- Date: 2026-08-06
- Owners: `@seunghyukchoe`
- Related: ADR-0025, ADR-0029, ADR-0044, ADR-0059, ADR-0069, D-149, SEC-120, R-114, issue #68

## Context

ADR-0025 shipped a file-level apply and excluded IPC apply on a technical ground: IPC "mutates an
in-memory document whose state cannot be bound to a file digest, so revision binding would be
unsound there". ADR-0069 then made live IPC observation operator-gated and recorded, in as many
words, that the opt-in "enables observation only" and that "no live mutation, DRC, fill, apply,
electrical, or fabrication authority is added or implied".

Issue #68 asks for the thing both of those deferred: a genuine single-undo-step transaction
pushed into a running KiCad. The [IPC apply research](../research/ipc-apply-v1.md) establishes
that the transaction primitive is real (`begin_commit` / `push_commit` / `drop_commit`, available
since KiCad 9.0, not marked experimental) — and that four of the properties a destructive surface
would want from it are absent from the protocol:

- there is **no revision, dirty flag, or compare-and-swap** of any kind, so a lost update is
  silent and undetectable;
- `kipy`'s `create_items` / `update_items` **discard the per-item status the wire protocol
  returns**, so the binding cannot tell a caller whether its items landed;
- `push_commit` carries **no documented atomicity guarantee**, and KiCad's own handler has a
  mid-batch early return that leaves earlier items staged;
- a user undo during an open commit, a concurrent API client, and a commit orphaned by a client
  crash are **not detectable** by any client.

Two of those are load-bearing negatives rather than gaps in our research, and one of them —
discarded per-item status — removes the basis on which a mutation could report success.

## Decision

### Live mutation gets a third flag, and does not reuse the conjunction of the other two

`Settings.allow_live_apply` is read from `COPPER_MCP_ALLOW_LIVE_APPLY` under the same exact
`{"0", "1"}` membership rule as the other two flags: no case folding, no truthiness, default off.
A live apply requires **`COPPER_MCP_ALLOW_LIVE_APPLY=1` and `COPPER_MCP_ALLOW_LIVE_IPC=1`**.

**Not the conjunction of the existing pair.** Reading `ALLOW_APPLY ∧ ALLOW_LIVE_IPC` as consent
to mutate a running editor would silently reinterpret two grants that were made for other things.
ADR-0069 recorded that the live opt-in enables observation only; ADR-0025's flag is documented as
replacing a file on disk. An operator who set both — an entirely ordinary configuration, since
the two surfaces shipped independently — would wake up having authorised something neither flag
described. Consent that can be composed upward is not consent.

**And `COPPER_MCP_ALLOW_APPLY` is deliberately *not* required.** The tempting "require all three,
it can only narrow" is wrong in the one direction that matters: it would force an operator who
wants live mutation and no file mutation to enable file mutation to get it, handing the model
`apply_candidate` as a side effect. A consent gate must never make the narrower capability
reachable only through the broader one.

`ALLOW_LIVE_IPC` *is* required, because live apply is strictly a superset of live observation —
same socket, same document, same read — so requiring it forces no widening at all. The two are
checked separately and refuse with distinct codes, so the operator learns which grant is missing
rather than being told "something is off".

This also answers a question KiCad's own consent model leaves open. The API server is disabled by
default and requires Preferences → Plugins → *Enable KiCad API*; but once it is on, **there is no
per-call consent** and every API call executes unattended. KiCad's opt-in authorises a class of
plugins to exist. It does not authorise this mutation to happen. That is our flag's job.

### The capability token binds a session, not a path

The file surface's `ApplyBinding` names a workspace-relative path, because a file is identified by
where it is. An open document is not: `BeginCommit` carries no document identifier at all — the
server keys commits by the client's *name* — and `DocumentSpecifier` offers only a bare
`board_filename` plus a project directory that `save_as` can falsify.

`LiveApplyBinding` therefore replaces `relative_path` with the opaque process-local session
revision already used by the live preview CAS, and binds `(candidate_id, base_revision,
board_revision, session_revision, operation="live_route")`. That session revision is derived from
the instance identity *the editor itself reports* — KiCad's API server generates one `KIID` per
process and stamps it into every response header, and its own add-on documentation names
detecting a mid-session restart as the intended use — so **a token minted against one editor
cannot verify after that editor restarts**, because the value the editor reports has changed.

An earlier draft of this record justified the same conclusion by the process-local PBKDF2 salt.
That reasoning was wrong and an adversarial review caught it: the salt and `KICAD_API_TOKEN` are
both fixed for the lifetime of the *CopperMCP* process, which a restarting KiCad cannot write to,
so a value derived from them identifies this server and observes nothing about the editor. The
salt still serves its own narrower purpose — it keeps the published revision an opaque handle
rather than an offline-testable fingerprint of the editor's credential — but it is not what makes
the revision move.

Separation from the file surface is by **HMAC domain**, not by a field value:
`copper-mcp/live-apply-token/v1` against `copper-mcp/apply-token/v1`. No arrangement of field
values can make a file token verify against the live surface even if every shared field were
identical.

### Bind to the serialization, never to the file

ADR-0025's objection stands and is now precisely characterisable. `Board.get_as_string()` returns
the in-memory document in KiCad's board file format, so the in-memory document *does* have a
content identity — and the difference between that digest and the on-disk file's digest is
exactly the dirty flag the protocol does not expose.

So a live apply binds three values, checked in this order against what the editor holds at that
moment: the **session** (which editor process), the **board serialization digest** (which
document state), and the **Board IR snapshot digest** (which converted view). Three, not one,
because they go stale independently: a restart moves the first without the second, and a
conversion-affecting setting moves the third without the first two. No value is ever
auto-refreshed.

### The compare-and-swap is a narrowing, and the contract says so

There is no conditional write in the protocol. The best available construction is to re-read
immediately before staging, which shrinks the window between check and write without closing it.
This ADR records that plainly rather than presenting the surface as conflict-proof, and the
residual window is [R-114](../ledgers/risk-register.md), not a mitigation bullet.

### One commit, dropped in a `finally`, held for the shortest possible interval

The mutation is exactly one `begin_commit` → stage → `push_commit`, giving one entry in the
editor's undo stack. `drop_commit` runs on every failure path.

This is not only how one undo step is obtained. KiCad's `handleCreateUpdateItemsInternal` returns
mid-loop on a deserialization failure with earlier items already staged; **without** an explicit
commit those items sit in a dangling commit that the next API call's undo step sweeps up. An
explicit commit is the only construction under which a mid-batch failure is recoverable.

No round trip happens between `begin_commit` and `push_commit`, because the interval during which
a user undo could collide with an open commit is exactly that interval, and shortening it is the
only mitigation a client has.

### The undo is KiCad's, and no file is touched

`COMMIT::Push()` mutates the in-memory board and adds an undo entry; nothing reaches the
`.kicad_pcb` until a separate `Board.save()`, which this surface never calls. There is therefore
**no pre-apply copy and no backup path** on this surface — offering one would imply a file
changed. The undo is Ctrl+Z. That is the whole point of the issue, and it is a strictly better
undo story than ADR-0025's manual file restore.

### The result is re-observed, and `applied_but_unverified` is the default

`kipy` discards the per-item `ItemStatusCode` the wire protocol returns; a partially-failed create
raises nothing and returns a same-length list, and the overall status "may return IRS_OK even if
no items were modified". **There is no basis for inferring success from a call that returned.**

So the post-push state is re-read, re-converted, and compared against source-plus-patch, exactly
as the file surface's third assertion does. `applied` requires that comparison to hold.
`applied_but_unverified` — carrying the real observed digest, never "nothing changed" — is what a
push whose effect cannot be confirmed reports, including when the connection dies after staging.

### This slice ships the preconditions and refuses the mutation

`apply_live_candidate` performs, in order: consent, capability token, session CAS, board CAS,
Board IR snapshot CAS, and a full re-derivation of the candidate's identity and geometry against
the board the editor is holding. Then it refuses with `capability_not_implemented`, from the exact
point at which `begin_commit` would be called.

Every other refusal code is reachable and tested. The alternative — a mutation shipped alongside
its own design review — is precisely the shape this repository already decided against for
`apply_candidate`: a destructive surface merges after adversarial review, not with it.

The token is **not consumed** by any of this. Nothing was spent, and a legitimate retry after a
transient refusal must still work.

## Consequences

- `COPPER_MCP_ALLOW_LIVE_APPLY` is a new environment variable. Absent or `"0"`, the tool refuses
  before parsing the request and before any socket is opened; any other spelling is a
  configuration error at startup, not a silent default.
- `apply_live_candidate` is **listed** while disabled and answers with a refusal naming the flag,
  for the reason ADR-0069 and ADR-0025 both record: an absent tool is indistinguishable from an
  unimplemented one, so a client cannot explain the situation and will retry.
- `preview_live_layered_route` gains `include_apply_token`. It mints a live-scoped token only for
  a `routed` result and only when the operator opt-in is on; the layered preview response gains an
  `apply_token` field that is `null` on every other path and on the file-backed surface.
- `capability_not_implemented` is a real, documented outcome, distinguishable from every refusal
  before it by `preconditions_verified` — which lists only the checks that actually ran. A caller
  that reaches it knows its capability and all three revisions were good.
- No live mutation, DRC, fill, save, or electrical authority is added. `Board.save()`,
  `Board.revert()`, `create_items`, `update_items`, and `remove_items` are not called anywhere in
  this repository.
- Three hazards remain undetectable and are recorded rather than mitigated: a user undo during an
  open commit, a concurrent API client, and a commit orphaned by a client crash.

## Alternatives considered

- **Reuse `COPPER_MCP_ALLOW_APPLY` alone, or with `ALLOW_LIVE_IPC`.** Rejected above: it
  retroactively widens two existing grants.
- **Require all three flags.** Rejected: it makes the narrower capability reachable only by
  enabling the broader one.
- **Hide the tool when the flag is off.** Rejected for the reason already recorded twice in this
  repository.
- **Reuse `ApplyBinding` with a new `operation` value.** Rejected. Operation-as-a-field is
  separation by value inside one MAC domain; a live capability deserves its own domain, and the
  binding's *shape* differs anyway — a session where a path would be.
- **Bind the live apply to the on-disk file digest.** Rejected: the surface never reads that file,
  so the claim would be unfounded, and the digests differ precisely when the document is dirty.
- **Offer a pre-apply file copy as the undo.** Rejected: no file changes, so a backup path would
  imply one did. KiCad's own undo step is the undo.
- **Report `applied` when `push_commit` returns without raising.** Rejected on the research: the
  binding discards the status that would justify it.
- **Ship the mutation behind the flag and review it after.** Rejected. This repository's law for
  destructive capability is adversarial review *before* merge, and a mutation that cannot yet say
  whether it happened is not shippable in either direction.

## References

- [ADR-0025: Apply a route candidate by splicing bytes](0025-file-level-candidate-apply.md)
- [ADR-0069: Gate live KiCad IPC on an operator opt-in](0069-operator-gated-live-ipc-observation.md)
- [ADR-0044: Live layered route proposals are session- and snapshot-bound](0044-live-layered-route-proposal.md)
- [Live IPC apply research](../research/ipc-apply-v1.md)
- [Safe candidate application references](../research/safe-apply-references.md)
