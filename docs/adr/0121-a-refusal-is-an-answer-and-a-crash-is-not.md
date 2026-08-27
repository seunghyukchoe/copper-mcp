# ADR-0121: A refusal is an answer and a crash is not

- **Status:** Accepted
- **Date:** 2026-08-27
- **Related:** [ADR-0002](0002-mcp-adapter.md),
  [ADR-0108](0108-typed-refusal-at-the-single-layer-fill-seam.md),
  [ADR-0120](0120-withheld-apply-authority-has-a-closed-reason.md),
  [D-225](../ledgers/decision-ledger.md), [D-226](../ledgers/decision-ledger.md),
  [R-176](../ledgers/risk-register.md), [SEC-163](../ledgers/security-ledger.md),
  [issue #217](https://github.com/seunghyukchoe/copper-mcp/issues/217)

## Context

CopperMCP refuses a great deal on purpose. A path that leaves the workspace, a constraint outside
its range, a manifest that omits a required field, a live capture with the operator flag off — each
is an *answer* to what the caller asked, and the reason is the useful part of it. A `KeyError` deep
in a parser is not an answer at all; it is a defect, and its text is an internal detail that has no
business reaching a model.

Through `mcp` 2.0.1 the SDK made no such distinction. `MCPServer.call_tool()` collapsed every
escaping exception into `ToolError(f"Error executing tool {name}: {exc}")`, so a deliberate refusal
and an unhandled crash arrived at the caller in the same shape, carrying the same kind of text.
From 2.1.0 the SDK classifies: `ToolError`, `ResourceError` and `MCPError` stay anticipated and
keep their message, and everything else is rewrapped as `UnexpectedToolError` whose message is
replaced by a bare `Error executing tool <name>` while the traceback goes to the server log.

That is the better contract, and CopperMCP was on the wrong side of it. Its refusals are typed
`ValueError` subclasses — `request_boundary.RequestError` and eight sibling families — so on the
2.1 line every one of them would have been classified as a crash and every reason withheld.
[D-225](../ledgers/decision-ledger.md) capped the dependency at `<2.1.0` rather than adapt the
~30 tool paths under time pressure, and [R-176](../ledgers/risk-register.md) recorded the cost of
that hold: while the cap stood, the excessive-agency harness's
`BOUNDARY_EXCEPTIONS = {"ToolError", "ValidationError"}` check could not make the crash-versus-
refusal distinction it documents itself as making, so its 15 passing `budget_dos` cases were
evidence that the call failed rather than that it was refused.

The audit that cap deferred is the substance of this record. It also found two paths where the
weakness was not the adapter's vocabulary but the source's: `models.candidate_from_dict` and
`tools.compare_candidates` refused untrusted input with a bare `ValueError`, which is
indistinguishable from a defect *at the point it is raised*, not merely at the boundary.

## Decision

**A deliberate refusal is translated to mcp `ToolError` at the adapter, by explicit exception
type, and nowhere else.**

`mcp_server._ANTICIPATED_REFUSALS` is a closed tuple of exception classes. Tool registration wraps
every tool body — `CopperMCPServer.tool()` applies `_refusals_as_tool_errors`, so a tool added
later inherits the contract instead of having to remember it — and the wrapper re-raises exactly
those types as `ToolError`, message unchanged, cause preserved. Everything else propagates
untouched and is classified by the SDK as the crash it is.

**The direction of error is not symmetric, and the design follows the asymmetry.** A refusal
reported as a crash costs the caller its reason: bad, and safe. A crash reported as a refusal
dresses an unhandled defect as a deliberate answer: that is a false statement about the server's
own behaviour, and it is the failure mode this whole boundary exists to prevent. So the list is an
enumeration that grows one audited type at a time. `except Exception`, `except ValueError`, and
"catch `UnexpectedToolError` and inspect it" are all rejected below for the same reason.

**Where a refusal is untyped at its source, it is typed at its source, not laundered at the
adapter.** `models.ManifestContractError` is new: every rejection in `copper_mcp.models` — the
protocol-boundary manifest decoder — now carries it. It is defined in `models` rather than reused
from `request_boundary` because `request_boundary` imports `board_ir`, and that module's contract
forbids pulling the deterministic core in through it. It subclasses `ValueError`, so no existing
`except ValueError` around a decode changes behaviour.

**No core module imports `mcp`.** [ADR-0002](0002-mcp-adapter.md) makes MCP an adapter, and the
translation is the adapter's job. The only two refusals written in MCP's vocabulary directly are
the two transport guards inside `mcp_server` itself (`observe_board_scene`'s render flag and
`render_circuit_schematic`), which are adapter-local by construction.

**`BOUNDARY_EXCEPTIONS` stays `{"ToolError", "ValidationError"}` and `UnexpectedToolError` is
never added to it.** That set is the excessive-agency harness's definition of "the adapter
refused"; `UnexpectedToolError` means the adapter crashed, and adding it would delete the
distinction this record just created. One detail there is load-bearing and easy to lose: the check
compares `type(error).__name__`, not `isinstance`. `UnexpectedToolError` *subclasses* `ToolError`,
so an `isinstance` check would silently readmit every crash. The name comparison is the reason it
does not.

### The audit

Every exception type that can escape a tool body in `src/copper_mcp/mcp_server.py`, classified.
Reachability was established two ways: a static walk of the call graph from each of the ~30 tool
entry points, and the dynamic evidence of the suite and the excessive-agency harness run against
`mcp` 2.1.1. The static walk is an under-approximation — an unresolved call contributes nothing —
so it is a floor on what escapes, which is the safe direction for "what must be translated" and
the wrong direction for "what is safe to ignore"; the dynamic runs cover the gap.

**Anticipated — a refusal of the caller's request. Translated in `_ANTICIPATED_REFUSALS`.**

| Exception type | Why it is an answer | Where translated |
|---|---|---|
| `RequestError` | "an untrusted request payload violates its declared contract" | `_ANTICIPATED_REFUSALS` |
| `BoardIrError` *(subclass)* | Board IR inspection request malformed | via `RequestError` |
| `CircuitSceneError` *(subclass)* | scene request malformed or unhonourable | via `RequestError` |
| `RoutePreviewError` *(subclass)* | route-preview request malformed | via `RequestError` |
| `RouteBundleError` *(subclass)* | route-bundle request malformed | via `RequestError` |
| `LayeredRoutePreviewError` *(subclass)* | layered preview malformed at its trust boundary | via `RequestError` |
| `LiveEditorContextError` *(subclass)* | live context unobservable or stale | via `RequestError` |
| `WorkspaceViolationError` | "a caller attempts to access data outside the configured workspace" | `_ANTICIPATED_REFUSALS` |
| `WorkspaceStaleError` *(subclass)* | pre-rename refusal; target untouched | via `WorkspaceViolationError` |
| `PlacementError` | "an untrusted placement request violates its declared contract" | `_ANTICIPATED_REFUSALS` |
| `PlacementPreviewError` *(subclass)* | opt-in preview cannot be honoured safely | via `PlacementError` |
| `ApplyRequestError` | "an untrusted apply request violates its declared contract" | `_ANTICIPATED_REFUSALS` |
| `PostPlacementObservationError` | "a fixed, non-echoing refusal for post-placement evidence" | `_ANTICIPATED_REFUSALS` |
| `BoardFormatError` | "an input is not a supported KiCad board" | `_ANTICIPATED_REFUSALS` |
| `KicadIpcDisabledError` | live capture attempted without the operator opt-in | `_ANTICIPATED_REFUSALS` |
| `ManifestContractError` **(new)** | protocol-boundary manifest violates its contract | `_ANTICIPATED_REFUSALS` |
| `RoutingJobServiceError` | durable-job request refused | already: the four routing tools, **fixed message** |
| `ExternalCandidatePublicError` | external candidate refused at its public boundary | already: `verify_external_route_candidate` |
| *(argument shape)* | unknown structured-wrapper fields | already: `CopperMCPServer.call_tool` |
| *(transport)* | render/schematic delivery is stdio-only | now raised as `ToolError` in the adapter |

`RoutingJobServiceError` and `ExternalCandidatePublicError` are deliberately **absent** from
`_ANTICIPATED_REFUSALS`. Both are already translated at their own handlers, and the routing-job
handlers replace the message with a fixed one rather than passing it through; listing them would
add a second, laxer path for the same types.

**A crash — not an answer. Left to the SDK to classify, message withheld.**

| Exception type | Why it is not an answer |
|---|---|
| `LiveApplyError` | its own contract: "a caller-side programming fault, never for an untrusted request" |
| `ApplyServiceError` | its own contract: "only for conditions that cannot be expressed as a typed refusal" |
| `WorkspacePostRenameError` | the board has already changed; nothing about this is a refusal |
| `KiCadCliError`, `ZoneFillStaleError` | the trusted DRC adapter could not produce valid evidence |
| `SceneRenderError` | render machinery failed; the caller asked about a board, not a renderer |
| `SExprError`, `CstError` | parser/CST machinery internal to conversion |
| `KiCadRoutePatchError`, `KiCadLayeredRoutePatchError`, `KiCadPlacementPatchError` | serializer faults |
| `KiCadSchematicParityError` | parity adapter fault |
| `ZoneFillError` | cached fill geometry could not be read within bounds |
| `KicadIpcError` and its other subclasses | environment and transport failures, not request refusals |
| `RuntimeError`, `TypeError`, `ValueError`, `KeyError`, `RecursionError`, `MemoryError` | untyped; provenance unknown by construction |
| `UnicodeError` | a `ValueError` subclass, and never *raised* in this repository — it reaches the boundary only by re-raise out of a `kicad_ipc` handler, which is a decode fault, not a refusal |

`ApplyTokenError` appears in neither list because it **cannot escape a tool body**: all three of
its raise sites are caught inside `apply/service.py` and `live_apply.py` and converted to a typed
structured refusal before the adapter is reached.

## Consequences

- A caller on the 2.1 line learns *why* a request was refused, exactly as on 2.0. The messages are
  unchanged, so this restores a surface rather than opening one — see
  [SEC-163](../ledgers/security-ledger.md).
- A caller on the 2.1 line no longer receives internal text from a crash. That is a small
  improvement in disclosure and a large one in honesty: a crash now looks like a crash.
- The excessive-agency harness's `budget_dos` family means what it says for the first time. The
  committed artifact's counts do not move — 136 cases, 90 passed, 0 failed, 46 not run, on both
  dependency lines — but the 15 `budget_dos` passes now rest on a distinction the harness can
  actually make. [R-176](../ledgers/risk-register.md) is closed by this, not papered over.
- The `mcp` range widens from `<2.1.0` to `<2.2.0`. It is not `<3.0.0`: what moved the refusal
  contract was a *minor* release inside 2.x, so "a minor bump cannot move this surface" is exactly
  the assumption this history refutes. `2.0.0` and `2.1.1` are the two resolutions the full suite
  is run against, and `<2.2.0` admits every published 2.x and nothing beyond what was exercised.
- **The maintenance rule that upper bound implies, stated so it is a rule rather than a habit:
  admitting a new `mcp` minor means re-running the dual matrix on it — the full suite against both
  the new resolution and the existing lower bound, plus the excessive-agency evaluation and this
  ADR's mutation spec on each — and only then moving the bound.** The audit table above is a claim
  about how one specific SDK classifies an escaping exception. That claim was true of 2.0.x, was
  silently falsified by 2.1.0, and is re-established here by measurement rather than by reading a
  changelog. A minor bump taken on the strength of semantic versioning alone would repeat exactly
  the step `D-225` had to undo.
- Adding a tool requires nothing new; registration carries the contract. Adding a *refusal type*
  requires an audit and an entry in the table above, which is the intended friction.
- Thirteen committed mutants under
  [`docs/mutants/2026-08-27-mcp-refusal-contract.json`](../mutants/2026-08-27-mcp-refusal-contract.json)
  pin the translation clause, the closed enumeration, the message pass-through, the registration
  wrapping, both source-level typed refusals, both transport guards, and the harness boundary set
  — `RC13` admits `UnexpectedToolError` to `BOUNDARY_EXCEPTIONS` and is killed, so the decision
  above is a test rather than a paragraph. They are killed under both `mcp` 2.0.0 and 2.1.1,
  because a test that only fails on one line would prove nothing about the other.

## Alternatives considered

**Catch `UnexpectedToolError` in `CopperMCPServer.call_tool` and re-classify from `__cause__`.**
Rejected twice over. `UnexpectedToolError` does not exist on the 2.0 line, so the adapter would
need a version-conditional import to support the range it declares; and the classification would
then happen *after* the SDK has already logged a traceback at ERROR for something that was never a
crash. Translating inside the tool body means the SDK's own decision is correct the first time.

**`except Exception` (or `except ValueError`) at the boundary.** This is the forbidden direction
written as a convenience. Every crash listed above would arrive at the model as a deliberate
refusal carrying internal text — a `RecursionError` from a parser presented as an answer about the
caller's board. `except ValueError` is narrower and no better in kind: `ZoneFillError`,
`SExprError`, `CstError` and every serializer fault are `ValueError`s. Mutants `RC02` and `RC03`
are exactly these two, and the crash-classification tests kill both.

**Make `RequestError` subclass mcp's `ToolError` in `request_boundary`.** The smallest diff by
far, and it puts an `mcp` import in the deterministic core, which [ADR-0002](0002-mcp-adapter.md)
forbids and which would make the routing core unusable outside an MCP host. The whole point of the
adapter boundary is that the core does not know what protocol is carrying it.

**Reuse `RequestError` for the manifest decoder instead of adding `ManifestContractError`.**
`request_boundary` imports `board_ir`, and `models`' contract says importing it "must never pull
in the deterministic core". The new type costs one class and keeps that edge absent.

**Wrap the ~30 `@mcp.tool()` functions individually.** Equivalent behaviour, and a tool added later
silently opts out. Wrapping at registration makes the contract structural; mutant `RC08` removes
it and `test_every_registered_tool_carries_the_refusal_translation` catches it.

**Leave the two bare-`ValueError` refusal paths as they were and add `ValueError` to the audited
list.** This is the same forbidden widening dressed as a targeted fix, and it would have made
`validate_candidate` and `compare_candidates` report a genuine defect as a refusal. Typing them at
the source costs one class and keeps the enumeration closed.

**Widen the pin to `<3.0.0`.** The evidence does not support it. The full suite passes on `2.0.0`
and `2.1.1`; it says nothing about a 2.2 that has not been published, and the reason this ADR
exists is that a minor release changed this exact contract once already.
