# Live IPC apply: what KiCad's commit API does and does not guarantee

**Research date:** 2026-08-06 · **Reviewed against:** `kicad-python` 0.7.1 (built against KiCad
10.0.1), KiCad `10.0` and `master` branches.

This document covers exactly one question: **what can CopperMCP prove about a mutation pushed
into a running KiCad editor over the IPC API?** It is a dated snapshot of external evidence,
gathered for [ADR-0074](../adr/0074-live-ipc-one-undo-commit-apply.md), and it is not revised as
the code moves.

It refuses to claim anything about mutation through the SWIG bindings, about the schematic
editor (which has no IPC API in 9 or 10), or about KiCad 11, whose IPC surface is documented as
gaining file export and headed commit requests that this snapshot did not examine.

Two of its findings are load-bearing negatives — facts that are *absent* from the protocol — and
those are stated as explicit negatives rather than left as gaps, because a design that assumed
either one would be unsound.

## 1. The commit API is real, and it is not scoped to a document

`kipy` exposes three methods on `Board` (`kipy/board.py`, unchanged between 0.7.1 and `main`):

```python
def begin_commit(self) -> Commit: ...
def push_commit(self, commit: Commit, message: str = ""): ...
def drop_commit(self, commit: Commit): ...
```

The docstring on `begin_commit` states the property the issue was opened for:

> If you do not call begin_commit, any changes made to the board will be committed immediately,
> which will result in multiple steps being added to the undo history. If you call begin_commit,
> changes made to the board will not be reflected in the editor until you call push_commit.

Three facts qualify it.

**`Commit` is not a context manager.** `kipy.common_types.Commit` has an `id` property and
nothing else — no `__enter__`, no `__exit__`, in 0.7.1 or on `main`. `with board.begin_commit()`
does not work, and a caller that forgets `drop_commit` on the failure path leaves state behind
(see §5).

**`BeginCommit` carries no document identifier.** The protobuf message is empty:

```proto
message BeginCommit { }
message BeginCommitResponse { KIID id = 1; }
message EndCommit { KIID id = 1; CommitAction action = 2; string message = 3; }
enum CommitAction { CMA_UNKNOWN; CMA_COMMIT; CMA_DROP; }
```

KiCad's own source records why: `common/api/api_handler_editor.cpp` carries the comment
*"Before 11.0, commit requests had no header so we assume they are for the PCB editor"*. A
commit is therefore scoped to **the client, not the board** — the server keeps
`std::map<std::string, std::pair<KIID, std::unique_ptr<COMMIT>>> m_commits` keyed by the
request envelope's `client_name`.

**A push does not write the file.** `COMMIT::Push()` mutates the in-memory `BOARD` and adds one
undo entry. Nothing reaches the `.kicad_pcb` until a separate `Board.save()`. That is a safety
property, not a limitation: a live apply changes what the operator sees and can undo, and leaves
their file exactly as it was.

## 2. There is no revision, no dirty flag, and no compare-and-swap

**Load-bearing negative.** Grepping every generated `.pyi` under `kipy/proto/` for
`dirty|modified|revision|digest|checksum|hash` yields exactly one hit: `TitleBlockInfo.revision`,
a user-authored metadata string. The IPC protocol has:

- no document revision or modification counter,
- no "is dirty" flag,
- no ETag, version stamp, or conditional-write primitive of any kind.

`update_items` matches items by UUID and, per the proto comment, *"All other properties of the
items are updated from those passed in this call."* A lost update is silent and undetectable at
the protocol level.

The consequence for CopperMCP is precise: **a live compare-and-swap cannot be atomic with the
mutation.** The best available construction is to re-read `Board.get_as_string()` immediately
before staging and compare it against the bound revision, which *narrows* the window between the
check and the write but cannot close it. Any design that presents a live apply as
conflict-proof would be claiming a guarantee the protocol does not offer.

## 3. Document identity: bindable to a serialization, not to a file

ADR-0025 excluded IPC apply on the ground that it "mutates an in-memory document whose state
cannot be bound to our file digest". That is still true, and it is now precisely characterisable.

`DocumentSpecifier` carries `board_filename` as a **bare filename**, and its `ProjectSpecifier`
carries `name` and `path` (the project directory). `Project.path` joined with `Board.name`
reconstructs an on-disk path — but only when the board actually sits in its project directory,
which `save_as` can falsify.

`Board.get_as_string()` (`SaveDocumentToString`) returns the in-memory board in KiCad's own board
file format. So the in-memory document *does* have a content identity, and it is the only one
available. The difference between that digest and the digest of the file on disk is exactly the
missing dirty flag.

The correct reading is therefore: **bind to the serialization, never to the file.** A live apply
that claimed a file digest would be asserting something about bytes it never read.

## 4. Mutation calls discard the per-item status the protocol returns

```python
def create_items(self, items) -> List[Wrapper]: ...
def update_items(self, items) -> List[BoardItem]: ...
def remove_items(self, items): ...
def remove_items_by_id(self, items): ...  # since 0.4.0
```

The wire protocol returns a per-item `ItemStatusCode` (`ISC_OK`, `ISC_INVALID_TYPE`,
`ISC_EXISTING`, `ISC_NONEXISTENT`, `ISC_IMMUTABLE`, `ISC_INVALID_DATA`) and, for deletion,
`ItemDeletionStatus`. **`kipy` discards all of it.** `create_items` is, in full:

```python
return [
    unwrap(result.item) for result in self._kicad.send(command, CreateItemsResponse).created_items
]
```

`result.status` is never inspected. The overall-status field's own proto comment warns that it
*"may return IRS_OK even if no items were modified"*.

This is the single most consequential finding for CopperMCP. **The binding cannot tell us whether
our items landed.** A partially-failed create raises nothing and returns a same-length list. So a
live apply that reported "applied" on the strength of a successful `push_commit` would be
reporting an assumption. The only sound outcome vocabulary is the one the file-backed surface
already uses: re-observe after the push, and say `applied_but_unverified` whenever the
re-observation cannot confirm the expected result.

`create_items` has no docstring at all; `update_items` documents that returned items *"may be
different from the input items if any updates failed to apply (for example, if any properties
were out of range and were clamped)"* — silent clamping is a documented behaviour, not a defect.

## 5. Failure modes, and how each maps to a typed refusal

`kipy.errors` defines exactly three classes, in 0.7.1 and on `main`:

```python
class ConnectionError(Exception)   # shadows the builtin
class ApiError(Exception)          # .code -> ApiStatusCode
class FutureVersionError(Exception)
```

There is no `ApiTimeoutError` and no `ApiConnectionError`. `kipy/client.py` converts *every*
`pynng` exception, timeouts included, into `kipy.errors.ConnectionError`, so **a request that
timed out and an editor that exited are indistinguishable by type** and separable only by
string-matching the underlying message. `ApiError.raw_message` is `return self.raw_message` — an
infinite recursion present in 0.7.1 and on `main`; only `.code` and `str(exc)` are safe to touch.

| Situation | What KiCad/kipy does | CopperMCP's typed refusal |
|---|---|---|
| API server disabled in Preferences | socket absent; `ConnectionError` | `live_editor_unavailable` |
| Editor closed before the capture | `ConnectionError` | `live_editor_unavailable` |
| Editor closed mid-apply | `ConnectionError`; an open commit dies with the process, unnotified | `applied_but_unverified` if the push may have landed; `live_editor_unavailable` if it provably did not |
| Request timed out | `ConnectionError`, same type as above | `deadline_expired` when CopperMCP's own budget expired first; otherwise `live_editor_unavailable` |
| KiCad newer than the binding | `FutureVersionError` from `check_version()` | `unsupported_kicad_version` |
| KiCad older than the binding | `check_version()` passes silently; failure arrives later as `AS_UNIMPLEMENTED` | `live_editor_unavailable` |
| `KICAD_API_TOKEN` mismatch | `ApiError(AS_TOKEN_MISMATCH)` | `live_editor_unavailable` |
| Editor busy with an interactive operation | `ApiError(AS_BUSY)` from `CanAcceptApiCommands()` | `live_editor_unavailable` |
| KiCad still starting | `ApiError(AS_NOT_READY)` | `live_editor_unavailable` |
| A commit is already open for this client name | `ApiError(AS_BAD_REQUEST)`, *"already has a commit in progress"* | `live_editor_unavailable` |
| Board changed between preview and apply | nothing; no detection exists | `stale_board_revision`, from CopperMCP's own re-read |
| Editor restarted between preview and apply | the reported instance identity changes | `stale_session`, or `live_editor_unavailable` first if the restart also rotated `KICAD_API_TOKEN` |
| User presses Ctrl+Z while a commit is open | undocumented and unguarded | not detectable; see §6 |
| Another API client mutates concurrently | independent `COMMIT` objects on the same `BOARD`; no locking | not detectable; see §6 |

`checkForBusy()` guards only on `PCB_EDIT_FRAME::CanAcceptApiCommands()`, which is
`!interactiveOperationInProgress()`.

## 6. What cannot be detected at all

Three hazards have no protocol-level signal, and a design has to state them rather than mitigate
them.

**A user undo during an open commit.** Nothing in the handler reconciles an open `BOARD_COMMIT`
— which holds raw `BOARD_ITEM*` pointers — against a keyboard undo that removed those items. A
keyboard undo is not an "interactive operation in progress" when the next API request arrives, so
`checkForBusy()` does not block it. No guard exists in the source; the hazard of reverting a
commit over freed pointers follows from that, and is our inference rather than documented
behaviour. The only mitigation available to a client is to keep the commit open for the shortest
possible interval — begin, stage, push, with no intervening round trips.

**A concurrent API client.** Commits are keyed by `client_name`, so two clients hold independent
`COMMIT` objects mutating one `BOARD`, with no locking and no conflict detection. Two clients
sharing a `client_name` collide on `m_commits`.

**An orphaned commit.** `common/api/api_server.cpp` is a plain nng REQ/REP server with no
connection-lifecycle tracking — no disconnect callback, no client registry beyond the per-request
`client_name` string. A client that dies with a commit open leaves the `m_commits` entry in place
until the editor exits. `kipy` defaults `client_name` to `'anonymous-' + 8 random characters` per
connection, so a reconnecting client cannot recover its own orphan; that the entry leaks follows
from the absence of any cleanup code.

## 7. `push_commit` has no documented atomicity guarantee, and the source shows a hole

**Load-bearing negative.** No atomicity claim appears in the `kipy` docstrings, in the `.proto`
comments, or on dev-docs.kicad.org.

The source shows a concrete partial-edit path. In `api_handler_pcb.cpp`,
`handleCreateUpdateItemsInternal` records a per-item status and `continue`s for most failures,
but a `Deserialize` failure returns mid-loop:

```cpp
if( !item->Deserialize( anyItem ) ) {
    e.set_status( ApiStatusCode::AS_BAD_REQUEST );
    ...
    return tl::unexpected( e );
}
```

Earlier items in the same batch have already been staged through `commit->Add(...)` /
`commit->Modify(...)`. The early return skips the trailing implicit
`if( !m_activeClients.count( aClientName ) ) pushCurrentCommit(...)`, so **without** an explicit
`begin_commit` those staged items sit in a dangling commit that the *next* API call's undo step
will sweep up. With an explicit `begin_commit`, a `drop_commit` on the failure path reverts them
correctly.

That is a direct argument for the design: an explicit commit is not merely how you get one undo
step, it is the only way to make a mid-batch failure recoverable.

Without `begin_commit`, each mutation call auto-pushes its own undo step — `pcbnew` labels them
*"Created items via API"*, *"Modified items via API"*, *"Deleted items via API"* — which is
exactly the multi-step undo history the issue exists to avoid.

## 8. Version availability and consent

`begin_commit`/`push_commit`/`drop_commit` carry no `.. versionadded::` marker, unlike roughly
twenty-five other methods that do, so they date from the initial release and are available from
**KiCad 9.0**. In 9.0 the IPC API is *"only implemented in the PCB editor"*; in both 9 and 10 it
*"only supports communication with a running instance of the KiCad GUI"* and has *"no support for
plotting or exporting files"*, both added in 11.

The API is **not** labelled experimental in 9 or 10. dev-docs describes it as *"designed to be a
stable interface that does not change when KiCad's internals are refactored"*, following protobuf
compatibility practice. The one explicitly unstable surface is `KiCad.run_action()`, whose own
docstring warns it is *"not intended for use other than by API developers"*.

`check_version()` raises `FutureVersionError` **only** when KiCad is newer than the binding; an
older KiCad passes silently and fails later at the call site. Feature availability is per patch
release — `Board.get_connected_items` is `versionadded 0.7.0 (KiCad 10.0.1)`,
`get_enabled_layers` is `0.5.0 (KiCad 9.0.5)` — so a version check is a necessary and
insufficient precondition.

**The API server is off by default and requires a user opt-in** (Preferences → Plugins → *Enable
KiCad API*). Once it is on, **there is no per-call consent**: every API call executes unattended.
That is the whole reason CopperMCP needs consent of its own — KiCad's opt-in authorises *a class
of plugins to exist*, not *this mutation to happen*.

Socket resolution (`kipy.kicad._default_socket_path`) prefers `$KICAD_API_SOCKET`, then a
platform temp path, defaulting to `ipc:///tmp/kicad/api.sock`; if `api.sock` is taken the server
uses `api-{PID}.sock`, so the default path is unreliable with multiple editors running. The
default `timeout_ms` is 2000.

## 9. What this means for the design

1. **The undo story finally becomes KiCad's own.** One `begin_commit` → stage → `push_commit`
   produces exactly one entry in the editor's undo stack, and the file on disk is untouched.
   ADR-0025's manual pre-apply-copy undo is not needed here, and would be misleading if offered.
2. **`applied_but_unverified` is mandatory, not a fallback.** §4 removes any basis for inferring
   success from a call that returned.
3. **The CAS is a narrowing, never a guarantee.** §2 means the honest contract says so out loud.
4. **`drop_commit` belongs in a `finally`, and even that is not total** — §5's connection death
   can take away the ability to drop.
5. **Three unmitigable hazards** (§6) belong in a risk row, not in a mitigation section.

## References

- [kicad-python on PyPI](https://pypi.org/project/kicad-python/) — 0.7.1 wheel
- [kicad-python repository](https://gitlab.com/kicad/code/kicad-python) — `kipy/board.py`,
  `kipy/errors.py`, `kipy/client.py`, `kipy/common_types.py`
- [kipy API reference: Board](https://docs.kicad.org/kicad-python-main/board.html) ·
  [KiCad](https://docs.kicad.org/kicad-python-main/kicad.html)
- KiCad source: [`common/api/api_handler_editor.cpp`](https://gitlab.com/kicad/code/kicad/-/raw/master/common/api/api_handler_editor.cpp) ·
  [`pcbnew/api/api_handler_pcb.cpp`](https://gitlab.com/kicad/code/kicad/-/raw/10.0/pcbnew/api/api_handler_pcb.cpp) ·
  [`common/api/api_server.cpp`](https://gitlab.com/kicad/code/kicad/-/raw/10.0/common/api/api_server.cpp)
- [KiCad IPC API dev docs](https://dev-docs.kicad.org/en/apis-and-binding/ipc-api/index.html) ·
  [For add-on developers](https://dev-docs.kicad.org/en/apis-and-binding/ipc-api/for-addon-developers/index.html)
- [Safe candidate application references](safe-apply-references.md) — the v0.1 exclusion this
  document supersedes
- [KiCad IPC observer research](kicad-ipc-references.md)
