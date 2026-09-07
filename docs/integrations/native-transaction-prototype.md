# Native transaction prototype: inspected implementation boundary

Status: a local, isolated native document-observation prototype and real-frame refusal/recovery
tests are implemented and executed. Strict batch mutation is not implemented or advertised;
the installed KiCad application is unchanged and stock strict live mutation remains refused.

The isolated KiCad source checkout is based on 10.0.5 commit
`18fb9289ff0efdca53c0352ed81a0973f0a6b58c`. The verified operator-local checkout is the
`project-hierarchy` integration worktree's ignored `build/kicad-native-guard`; its reproduction
notes are in `build/native-active-commit-verified-2026-09-07.md` in that same worktree.
These local artifacts are not distributed by this note. The installed KiCad application was not modified. KiCad-derived
patches must retain their GPL-3.0-or-later notices; do not label them as Apache-only core code.

## Verified local observation increment

The isolated source reached `6faa462f49a19b07b9253d92f0a123f672dc40d8`, based on the
pinned KiCad revision above. A frame-owned document session and a digest of the serialized board
bind an observation; replacement with another board under the same filename invalidates the old
session. These observations are not atomic revision-protected mutation capabilities.

The real `qa_api_frame` executable passed eight cases and 57 assertions, including matching
content/session identity, stale-session rejection, wrong-document rejection, disabled-frame refusal,
active-commit refusal for the owning and another client, and recovery after dropping an empty commit.
The legacy `qa_api` executable passed all 40 cases and 2,106 assertions. Both processes exited zero
with no warning/error/failure lines. Fixture teardown checks that its synthetic board was neither
modified nor saved. Failure cleanup restores frame enablement and contains empty-commit drop errors.

The runtime used a pinned Linux/amd64 image, no network or host mounts, a read-only root and source/
build volume, private temporary settings, dropped capabilities, two CPUs, 4 GiB memory, Xvfb and a
180-second deadline. No installed application, customer board, provider, upstream submission or
human confirmation participated. Independent correctness review and a separate complexity pass
accepted only this local test/compile increment.

Reproduction identities (SHA-256):

- Frame-test source: `b8829306e99d46d227e79870f6427eda659fec38e65a8d825031c4c477034bb3`.
- Frame binary: `e34f89701ba53589975d269e9eb71eeac1e4248517398aeb6054cb8fb64a7d4c`.
- Complete GPL source patch: `7ced0d7d16984aa716b77a6ab694709ccf41e1705ec24f1e6f885e4d2d4cd904`;
  reverse-application checking against the clean local source passed. It remains a local artifact.

`strict_batch_apply_supported` and `project_context_bound` remain false. Empty-commit recovery
does not establish complete staging, successful mutation, one undo step, guarded post-commit rollback,
lost-acknowledgement recovery or consent. IPC-disabled build coverage also remains outstanding.

## Verified seams

- `common/api/api_server.cpp` queues API requests onto the wx event loop and dispatches handlers
  synchronously. A future guarded batch must not span independently interleavable API actions.
- `pcbnew/api/api_handler_pcb.cpp` owns PCB request registration and item create/update handling.
  Existing document validation compares filename/project information, not a guard-owned immutable
  document session plus content revision. Add those bindings rather than using title-block revision.
- `include/tool/tool_manager.h` explicitly warns that user events may occur between
  `PostAPIAction` operations and tangle commits. This is not an acceptable strict-apply path.
- `BOARD_COMMIT::Push` changes connectivity, undo and dirty state and clears staged work. Existing
  `Revert` behavior before Push is not proof of guarded rollback after a partially completed Push.
  A full-batch design must establish post-commit verification/recovery separately.

## Build and validation targets

The inspected build flags are `KICAD_IPC_API=ON` and `KICAD_BUILD_QA_TESTS=ON`. Generated `kiapi`
protocol code, `pcbnew` and the relevant shared objects must come from the same pinned build.
The existing `qa_api` and `qa_pcbnew` targets are declared in `qa/tests/api/CMakeLists.txt` and
`qa/tests/pcbnew/CMakeLists.txt`; `qa_api` depends on pcbnew and its linked object libraries.
It is not an independent tiny protocol-only build.

Existing BoardCommit tests use dummy tool environments and sometimes `SKIP_UNDO`. They can inform
staging tests, but cannot certify one real editor undo step or exclusion of interactive edits.
The observation tests above use a real frame; staging and mutation fault cases still require their
own evidence in the isolated build.

A one-item create/update experiment may help validate mechanics, but must remain an internal
prototype with no full-batch capability advertisement. It does not satisfy placement-and-routing
application, footprint/group replacement, all-client edit exclusion, post-commit rollback, lost
acknowledgement reconciliation or genuine human consent.

## Required next design gate

Implement guard-owned document/session identity, atomic revision comparison, bounded all-item
prevalidation and staging, exclusion of relevant UI/IPC/undo/save actions, a complete commit and
verification path, and durable redacted operation reconciliation. Never approximate the full
transaction by applying placement and routing separately. Tests must distinguish pre-commit
refusal, verified application, verified rollback and uncertain outcome; uncertainty blocks writes.

The [approved execution program](../plans/balanced-readiness-execution.md) requires a distinct
single-use mutation capability and all profile-required engineering results to pass. Installation
of a modified test application and real human-host acceptance remain explicit gates. This note
does not grant either approval or application authority.
