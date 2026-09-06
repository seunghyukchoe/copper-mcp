# Native transaction prototype: inspected implementation boundary

Status: source preparation and interface/test-target reconnaissance only. No native guard has
been implemented, installed or executed, and stock live mutation remains refused.

The isolated KiCad source checkout is pinned to 10.0.5 commit
`18fb9289ff0efdca53c0352ed81a0973f0a6b58c`. Its API, PCB and QA sources are available under ignored
`build/kicad-native-source`. The installed KiCad application was not modified. KiCad-derived
patches must retain their GPL-3.0-or-later notices; do not label them as Apache-only core code.

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
Add native rejection/staging tests first, then real-editor fault tests in the isolated build.

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
