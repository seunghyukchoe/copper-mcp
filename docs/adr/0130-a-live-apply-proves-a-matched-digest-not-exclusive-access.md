# ADR-0130: A live apply proves a matched digest, not exclusive access

- Status: Proposed
- Date: 2026-09-02
- Owners: `@seunghyukchoe`
- Related: ADR-0025, ADR-0069, ADR-0074, ADR-0103, ADR-0120, ADR-0129, B-138, B-144, D-243,
  R-189, SEC-168, SEC-175, issue #68

## Context

[ADR-0074](0074-live-ipc-one-undo-commit-apply.md) shipped every precondition a live apply needs
and then refused the mutation from the exact point `begin_commit` would be called. Issue #68 has
been parked since, on four stated reasons. Two of them have since been answered by other work, and
this record exists because the remaining two are **structural** and a design has to name them
rather than wait for them to go away.

**What #242 resolved.** The park's reasons 1 and 2 were that CopperMCP's default MCP path refused a
current KiCad outright, and that the editor-context surface had no override at all.
ADR-0129 replaced the binding's asymmetric `check_version()` boolean with a declared version
window, retired `allow_future_api`, and made **live apply the one surface that requires
`compatible`** — the exact-match verdict, the only one carrying a proof. Those two reasons are
gone, and the strictness live apply inherits is the correct half of the window.

ADR-0129 is cited by number and not linked throughout this record. It is in flight on
[#242](https://github.com/seunghyukchoe/copper-mcp/pull/242) and is not on `main` at the base of
this branch, so a link would resolve to nothing until that pull request lands. The citation is
deliberate rather than an omission: this record's §4.1 *depends* on ADR-0129's decision, and saying
so by number is honest about the dependency being unmerged.

**What did not change.** Reason 4 is the protocol, and [B-144](../ledgers/benchmark-ledger.md) has
now measured it as a closed enumeration rather than argued it from reading. Across **17**
generated `.proto` files, **253** messages and **891** fields, a deliberately over-broad
16-substring sweep for a document generation, a dirty bit, or a conditional-write precondition
returns **7** hits, and **not one of them is about document state**: two are IPC-2581 export
options, two are the `kicad_token` server-identity field, two are the KiCad *application* version,
and the seventh is `TitleBlockInfo.revision` — a title-block string a human types into the drawing
sheet. `BeginCommit` carries **zero fields**. `EndCommitResponse` carries **zero fields**.
`SaveDocumentToString` accepts a `DocumentSpecifier` and nothing else, so there is no "as of" to
ask for and no revision to be told. The negative is now exhaustive over a set that could have
contained a counterexample.

And [SEC-168](../ledgers/security-ledger.md) is the other half: the endpoint is a Unix socket with
**no intra-uid authentication**, exposing `create_items` / `update_items` / `delete_items` and the
commit primitives to any process running as the operator. B-144 measured the mechanism rather than
restating it: `kipy`'s `_default_kicad_token()` returns the **empty string** when `KICAD_API_TOKEN`
is unset (`kipy/kicad.py:62`), the wire contract rejects a token only when it is *non-empty*, and
the client then **adopts the server's token out of the first reply** (`kipy/client.py:85-86`). The
token is something the server tells the client. It is not something the client must know.

So a write path has to be designed against exactly these facts: no revision, no dirty flag, no
conditional write, and no authentication. This record says what such a path can honestly promise.

## Decision

Every rule below states its **direction of error** — which way it is wrong when it is wrong —
because a safety rule whose failure direction is unstated is not a safety rule.

### 1. The compare-and-swap is client-built, and its guarantee is about a digest

There is no server-side revision, so CopperMCP builds its own from the only observable the protocol
offers: **board content**. `Board.get_as_string()` (`kipy/board.py:575`) returns the in-memory
document in KiCad's own board file format, which gives the open document a content identity even
though the protocol will not name one.

The sequence is fixed and has no optional steps:

| # | Step | Direction of error |
|---|---|---|
| 1 | **Read.** Serialize the live board; digest it. | — |
| 2 | **Publish.** The digest goes to the caller as `board_revision`, and the caller holds it. | Disclosing a digest of a document the caller already asked to see adds nothing. |
| 3 | **Re-read.** At apply time, serialize again and digest again. | A transport failure here refuses; it never proceeds on a stale digest. |
| 4 | **Compare.** Equal → continue. Unequal → `stale_board_revision`, before any commit is opened. | Refuses a board that moved. Cannot detect a board that moved *and moved back*, which is byte-identical and correctly treated as unchanged. |
| 5 | **Write**, inside one commit (§2). | — |
| 6 | **Re-read and verify.** Serialize, digest, convert, and compare against source-plus-patch. | Equal → `applied`. Anything else → `applied_but_unverified` carrying the **real observed digest**, never "nothing changed". |

**The guarantee, written exactly as it must appear in the tool's own documentation:**

> **The write landed on a board whose serialization digest equalled the digest the caller supplied,
> at the moment CopperMCP compared them.** It is not a claim that no other client wrote to this
> board — before, during, or after — and it is not a claim that the board still matches that
> digest now.

**The race window that remains, and its size.** Between step 4 and step 5, and again between step
5 and step 6, another client can mutate the board and neither CopperMCP nor KiCad will say so. The
window is bounded by **IPC round trips, not by anything CopperMCP controls**: at minimum a
`BeginCommit`, one `CreateItems`, an `EndCommit`, and a `SaveDocumentToString` — four
request/reply exchanges against a `pynng` REQ/REP socket, each taking as long as a single-threaded
editor takes to answer. CopperMCP can make the count minimal and it can put a deadline on the
whole sequence. It cannot make the window zero, and it cannot make it short by policy, because the
editor's latency is the editor's.

This is not a theoretical race. SEC-168 established that any same-uid process can write to this
socket, so the other writer needs no privilege it does not already have. **The CAS is a narrowing.
ADR-0074 said so; this record keeps saying so and now says how wide the remaining gap is.**

**What is deliberately not built.** A retry loop around a failed compare. Retrying converts "the
board moved" into "the board moved and we wrote anyway on the next look", which is a strictly worse
outcome dressed as robustness. A failed compare is terminal for that apply, and the caller re-reads
and re-previews.

### 2. Exactly one pushed commit, and the atomicity that gives is partial

One `begin_commit` → stage → `push_commit`, with **no intervening round trip** between begin and
push. That is the whole mutation, and it produces one entry in the editor's undo stack, so the
operator's Ctrl-Z reverts the whole apply.

**Is that what KiCad's commit model gives?** The client-side facts are measured (B-144): the
primitives exist at `kipy/board.py:310` / `:325` / `:334`, `push_commit` sends `EndCommit` with
`CMA_COMMIT`, `drop_commit` sends the same message with `CMA_DROP`. The docstring says a push
"will result in a single undo step being added to the undo history". **That is a docstring, not a
measurement**, and this ADR does not upgrade it into one. That one apply produces exactly one undo
entry is the **first thing the live probe in §7 must demonstrate** (exit condition X4), and
until it does, the one-undo-commit claim is a design intent rather than an established property.

**What happens on a partial failure.** KiCad's `handleCreateUpdateItemsInternal` returns mid-loop
on a deserialization failure with earlier items already staged (recorded in the
[IPC apply research](../research/ipc-apply-v1.md) §7 from KiCad's own source, and *not* re-derived
here). So a batch can fail with part of itself staged. The client's response is `drop_commit`, in a
`finally`, on every failure path.

**Is drop guaranteed? No, and the honest statement is three-part:**

- **Drop is issued** on every failure path, unconditionally.
- **Drop can itself fail.** It is another IPC round trip. If the connection died — and B-144
  measured that `kipy` collapses every transport failure, timeouts included, into one
  `ConnectionError` class out of a **three-class** error module, so a timeout and a dead editor are
  indistinguishable by type — the drop never arrives.
- **A commit orphaned that way is not recoverable by this client.** `kipy` generates a fresh random
  `client_name` per `KiCad()` object (`kipy/kicad.py:59`, measured), and the server keys commits by
  client name, so a reconnecting CopperMCP cannot find its own orphan. It stays in the editor's
  `m_commits` until the editor exits.

**Failure atomicity, stated as what can and cannot be promised:**

| | Promise |
|---|---|
| **Can promise** | A successful apply is one pushed commit and therefore one undo entry (pending X4's probe). A failure *detected before* `push_commit` issues a `drop_commit`. A failure at any point leaves no file changed, because no `Board.save()` is ever called. |
| **Cannot promise** | That a failed batch left nothing staged — only that a drop was *attempted*. That the drop arrived. That a pushed commit can be undone by the client: **there is no client-side rollback of a pushed commit anywhere in the protocol.** B-144 checked for one by name — `undo`, `redo`, `rollback_commit`, `get_undo_stack` are all **absent** from `Board`'s 58 public methods. The undo is the operator's keyboard, and only the operator's. |

**Direction of error throughout: toward `applied_but_unverified`.** A mutation call that returned
proves nothing — B-144 measured that `create_items` and `update_items` read the returned item and
**never read a `status` attribute at all**, and that `remove_items` / `remove_items_by_id`
**discard the entire response**, while the wire contract's own comment says the overall status "may
return `IRS_OK` even if no items were modified". So `applied` is reachable only through step 6's
re-read, and every other terminated path reports `applied_but_unverified` with the real digest.

### 3. `applied` on a live surface means the editor's document changed, and the response says so

The live board is not the file. B-138 measured the gap on a real editor: **165,571 bytes in memory
against 166,070 on disk**, different digests, on a board nobody had edited in that session. An
apply into in-memory state that the operator never saves is a no-op on disk; an apply the operator
then discards is gone. Neither outcome is visible to CopperMCP, because the dirty flag *is* the
difference between those two digests and the protocol exposes neither.

**Decision: the live surface reuses the file surface's status vocabulary and adds a mandatory
binding disclosure.** No new status word. `applied`, `applied_but_unverified` and the refusals keep
the meanings `apply/contracts.py` already gives them, because a caller that has learned one
vocabulary must not have to learn a second one that differs in a way no name signals.

`applied` on the live surface means: **the editor's in-memory document changed and the change was
verified by re-reading that document.** It does not mean a file changed. It cannot, because
`Board.save()` is not called on this path and this project has never called it.

To make that impossible to misread, **every live apply response carries a required
`document_binding` field**, and its value on this surface is the single literal

```
in_memory_unsaved_state_unobservable
```

Three properties make this a disclosure rather than decoration. It is **required**, so it cannot be
omitted on the response a hurried caller actually reads. It is a **closed literal set** on the
model of ADR-0120's withheld-reason vocabulary, so it carries no digit, path or board-derived
value. And it names the *unobservable* thing rather than the observable one: the field does not say
"in memory", it says the saved state cannot be observed from here — which is the fact a caller
needs in order to know that it must ask a human whether to save.

**Direction of error: toward under-claiming.** A caller that reads `document_binding` and wrongly
concludes nothing happened will re-read the board and find that something did. A caller that reads
`applied` alone and wrongly concludes a file changed would ship a board that does not exist. Only
the second failure is silent, so the field is required and the vocabulary does not move.

**Consequently, no backup path and no `backup_path` field.** The file surface returns one because
it replaced bytes. This surface changes no bytes on disk, so offering a backup would assert that it
had.

### 4. Authorization is defence against CopperMCP's own mistakes, and nothing else

CopperMCP cannot add authentication to KiCad's socket. It is not its socket. Four things it **can**
do, each with its error direction, and then the plain statement of what they are worth:

1. **Require `compatible`.** Already decided in ADR-0129 (unmerged; see the note in Context) and
   unchanged here: live apply refuses both `future_api_unverified` and `legacy_api_unverified`.
   *Error direction: refuses editors it could probably have driven. Correct — a read's worst case
   is an incomplete answer carrying the verdict that says so, and an apply has no such disclosure
   to hide behind.*
2. **Require an explicit operator-granted apply token, per apply.** The existing single-use
   live-domain token under its own HMAC domain, minted only by a preview that produced a candidate,
   withheld with one of ADR-0120's eight closed reasons otherwise. *Error direction: an expired or
   already-used token refuses a legitimate retry, and the operator previews again.*
3. **Never apply against a digest CopperMCP did not itself read in this session.** The caller may
   only echo back a `board_revision` this process published; a digest arriving from anywhere else
   is refused rather than trusted. *Error direction: refuses a caller holding a correct digest
   obtained by other means. Deliberate — a digest CopperMCP did not compute is a claim, not an
   observation.*
4. **Probe the socket's ownership and mode**, and refuse when it is not the expected
   owner-writable-only endpoint (SEC-168 measured `srwxr-xr-x`, uid 501, no TCP listener). *Error
   direction: refuses a legitimately unusual deployment. Acceptable — the refusal names what it
   saw, and the operator can see the same thing.*

**And now the part that must not be softened.** Every one of those four is a check CopperMCP
performs on **itself**. A hostile local process is not slowed down by any of them: it does not ask
CopperMCP for a token, it does not read CopperMCP's flags, and it connects to the same socket with
an empty token that the server accepts. The probe in (4) is a **probe, not a guarantee** — it
reads the mode of a socket that any same-uid process may already be talking to, and it says
nothing about who else is connected. If the socket's mode is wrong, that is information. If it is
right, that is not safety.

**This is defence against CopperMCP's own mistakes — wrong board, stale digest, unauthorized
candidate, incompatible editor — and not against a hostile local process.** Recorded as
[R-189](../ledgers/risk-register.md), with the apply surface's threat model as
[SEC-175](../ledgers/security-ledger.md), extending SEC-168 from a read surface to a write one.

### 5. What apply v0.2 does not do

Each exclusion is a decision, so each says why.

- **No multi-commit apply.** One pushed commit per apply, or no apply. A second commit is a second
  undo entry, which defeats the issue's own premise, and a failure between two commits leaves a
  half-apply with no client-side rollback (§2) to undo the first.
- **No live placement apply.** The live surface mints no placement capability at all today —
  `unsupported_surface` is a real member of ADR-0120's withheld-reason set for exactly this — and
  ADR-0129's `compatible` requirement is scoped to `live_apply.py`, so it constrains the route
  surface and does not silently authorize a placement one. Placement's parity surface is bracketed
  by verdicts (ADR-0110) rather than closed, and adding a live write to a surface whose parity is
  bracketed would compound two unfinished things.
- **No apply without the evidence binding the file surface already requires.** Mirrored, not
  weakened: the same single-use token domain-separated by HMAC, the same candidate replay against
  the board being written, the same refusal of a fill-bound candidate (`fill_bound_candidate`,
  ADR-0103 — a candidate shaped by verified zone fill cannot be replayed, so a token for it names
  a write that could only fail), and the same `board_not_appliable` refusal for revision-derived
  geometry identities.
- **No save, no revert, no DRC, no fill, and no electrical or fabrication authority.**
  `Board.save()`, `Board.save_as()` and `Board.revert()` stay uncalled, as they are today.
- **No retry, no `force`, and no configuration an agent may change.** Consistent with every other
  apply surface in this repository.

### 6. Exit conditions for the park, named concretely

#68 stays parked. It is parked on a **list**, not on a mood, and each item below is either closed
by this record or is a specific thing someone must do.

| | Exit condition | State |
|---|---|---|
| **X1** | The version gate no longer refuses a current editor, and no surface can widen the window. | **Closed by ADR-0129.** |
| **X2** | The primitive census is measured rather than recalled, including the negatives. | **Closed by B-144.** 17 files, 253 messages, 891 fields, 7 sweep hits, 0 of them document state. |
| **X3** | The safety model is written down with its guarantee and its residual risk stated in the ADR's own words. | **Closed by this record**, R-189 and SEC-175. |
| **X4** | **One apply produces exactly one undo entry**, demonstrated against a real editor. | **Open — needs a live session.** Predeclared in §7. |
| **X5** | **`drop_commit` reliably reverts a partially staged batch**, demonstrated against a real editor, including the disconnect case. | **Open — needs a live session.** Predeclared in §7. |
| **X6** | **A post-push re-read verifies the write**, demonstrated end to end. | **Open — needs a live session.** Predeclared in §7. |
| **X7** | Adversarial review of the mutation before merge, per this repository's standing law for destructive capability. | **Open — after X4–X6.** |

Implementation of the mutation is gated on X4–X6 passing, and merge on X7. **A probe that fails is
also an exit**: it closes #68 as not tractable on this protocol, which is a better outcome than an
open issue.

### 7. The live probe, predeclared

The probe is **operator work** — it needs the workstation IPC server enabled and a board the
operator consents to have written — and this ADR does not schedule it. What it does is fix the
questions and the predictions **now**, so the lane that runs it cannot choose its questions after
seeing the answers. The plan is carried in the pull request body and reproduced there verbatim.

### 8. Every artifact this design produces binds content, never a branch commit

This rule is here because the project has just paid for its absence, and the incident is
load-bearing rather than illustrative.

**What happened.** [B-141](../ledgers/benchmark-ledger.md)'s recorded artifact binds a
`source_commit` of `b7c71d4d`, a commit on the branch the measurement was taken on. That branch
was **squash-merged**, which creates a new commit with a new SHA and leaves the original
unreferenced. In a fresh clone the recorded SHA resolves to nothing, so the artifact's own
validator refuses it and `main` is red on a test that was green when it merged. Nothing about the
measurement changed. Its **name for itself** stopped existing. (Pre-existing on `main` at
`8663418`, tracked, and not touched by this branch.)

**Why an apply design has to care.** Everything this record proposes hands someone a name for a
state and asks them to present it later:

| Value | Bound to | Survives a squash-merge? |
|---|---|---|
| `board_revision` (§1) | `sha256` of the board serialization — **content** | Yes. It never referred to a commit. |
| `snapshot_digest` (§1) | `sha256` of the converted Board IR — **content** | Yes. |
| The apply token (§4.2) | An HMAC over `(candidate_id, base_revision, board_revision, session_revision)` — **content plus a session identity** | Yes. |
| The live-probe artifact (§7) | *Would have been* a branch commit, on the B-141 pattern | **No.** |

The first three were already right, and they were right for their own reasons rather than by
foresight. The fourth is the one this incident changes: **the predeclared live-probe artifact
binds the tree digest of the runner and of the repository state it ran against, plus the merge
commit once one exists — never a branch commit.** A branch commit is a name whose referent the
merge strategy is entitled to delete.

**Direction of error.** A content digest that cannot be resolved to a commit is still a complete,
checkable fact about bytes; the worst case is that a reader cannot locate the history around it. A
commit SHA that has been garbage-collected is nothing at all, and worse, it turns an honest record
into a failing test. Prefer the failure that leaves the evidence intact.

**Generalised, because it is the same failure as §1's.** A digest of content is an observation. A
pointer into someone else's mutable namespace — a branch commit, a file path, an in-memory
document with no revision — is a claim about a world that can move underneath it. This ADR's whole
argument is that the live board is the second kind of thing and must be handled as such; the B-141
incident is the same lesson arriving from the other direction, in this repository's own history,
during the week this record was written.

## Consequences

- `document_binding` becomes a required field on the live apply response and a new closed literal
  set. Under [ADR-0105](0105-a-schema-version-moves-with-its-accepted-set.md) that is an emitted
  accepted set, so the schema version of every document publishing it moves when it ships. **It
  does not ship in this record**, which changes no code.
- The live apply status vocabulary does **not** grow. `applied`, `applied_but_unverified` and the
  existing refusals keep their file-surface meanings.
- The CAS's guarantee sentence is a contract obligation, not prose: the tool documentation, the
  ADR, and any published description must state it as written in §1, including its two negations.
- Three hazards ADR-0074 recorded as undetectable stay undetectable, and one of them — the
  concurrent client — is now the *named* residual risk of the write path rather than a bullet.
- The live-probe artifact's provenance fields are fixed before the probe is written (§8), so the
  lane that runs it cannot re-derive them under time pressure and reach for a branch SHA.
- #68 moves from "parked" to "designed; live probe predeclared; implementation gated on the probe".
  It stays open and stays unimplemented.

## Alternatives considered

- **Bind the live apply to the on-disk file digest.** Rejected in ADR-0074 and re-rejected on
  measurement: B-138 measured the two digests differing by 499 bytes on an unedited board. The
  surface never reads that file, so the claim would be unfounded.
- **Claim the CAS prevents lost updates.** Rejected. The window in §1 is real, the socket is
  unauthenticated, and a guarantee that is true only when nobody else is writing is a guarantee
  about the environment rather than about the code.
- **Retry the compare on failure.** Rejected in §1: it converts a detected conflict into an
  undetected one.
- **A new status word for "applied to memory", e.g. `applied_in_memory`.** Rejected. It splits a
  vocabulary the file surface already owns and would let a caller handle `applied` correctly and
  the new word not at all. A required `document_binding` field on the response is the same
  information in a place a caller cannot skip past.
- **Hold the commit open across a verification round trip.** Rejected. The interval during which a
  keyboard undo can collide with an open commit is exactly the interval the commit is open, and
  shortening it is the only mitigation a client has.
- **Add authentication in front of the socket** (a wrapper, a proxy, a filesystem ACL). Rejected as
  out of scope and misleading: it would protect a socket CopperMCP does not own, on a machine whose
  other processes are not obliged to go through the wrapper, and shipping it would imply the
  surface is defended when §4 says plainly that it is not.
- **Ship the mutation behind the flags and probe afterwards.** Rejected for the third time in this
  issue's history, on the same ground: this repository's law for destructive capability is
  adversarial review *before* merge, and a mutation whose one-undo property has never been observed
  is not ready for that review.

## References

- [ADR-0074: Gate live editor mutation on its own consent](0074-live-ipc-one-undo-commit-apply.md)
- ADR-0129 — the live IPC version window (in flight on #242, not yet on `main`; cited by number)
- [ADR-0120: withheld apply authority has a closed reason]
  (0120-withheld-apply-authority-has-a-closed-reason.md)
- [ADR-0025: Apply a route candidate by splicing bytes](0025-file-level-candidate-apply.md)
- [Live IPC apply research](../research/ipc-apply-v1.md)
- [B-144, B-138](../ledgers/benchmark-ledger.md) · [D-243](../ledgers/decision-ledger.md) ·
  [R-189](../ledgers/risk-register.md) · [SEC-175, SEC-168](../ledgers/security-ledger.md)
