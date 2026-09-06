# ADR-0141: Project ERC prepares an explicit rule and library derivative

- Status: Proposed; private preparation adapter
- Date: 2026-09-07
- Owners: CopperMCP maintainers
- Related: [ADR-0139](0139-captured-project-bytes-precede-electrical-execution.md),
  [ADR-0140](0140-project-erc-observations-do-not-invent-intent-identities.md)

## Decision

Prepare a separate immutable execution input set from an existing schematic-project capture
and explicitly supplied symbol-library bytes. Never rewrite the source project. This private
adapter does not launch KiCad, produce an ERC finding, register an MCP tool or grant authority.
The complete project executor remains a separate integration and review gate.

Recheck the captured file bindings, byte digests, aggregate capture identity and hierarchy before
preparation. Use copied CaptureLimits and a shared cooperative deadline. Count captured source,
supplied libraries and generated execution bytes against the existing per-file/aggregate ceilings.
At most 64 libraries are accepted. Each must have a bounded unambiguous nickname, exact byte
digest and a referenced native library body. Do not look up host libraries or inherit a global
library table. The executor must separately establish its confined filesystem and environment.

Require the exact referenced library set. Build a local symbol table with generated filenames
under a reserved directory and one fixed executor-owned environment key. Private output contains
the original schematic bytes, explicit library bytes, generated table and a project derivative;
its digest binds every execution path and byte digest. A separate profile digest binds the rule
defaults, pin matrix, outside-profile categories and symbol-equivalence policy.

The initial profile is pinned to KiCad 10.0.5 connectivity checks. Enable every in-profile rule
at least at its native floor and at least as a warning; preserve stronger user severity and pin
conflict settings within that profile. Check without ERC waivers, recording the original count
and every rule change. Native-ignored historical keys are explicitly recorded and omitted from
the derivative rather than silently interpreted as current checks.

Simulation-model, footprint-link and footprint-filter checks are explicitly outside this
connectivity profile. They are disabled in this derivative even when enabled in the source,
and that change is recorded. Their absence is not a pass: separate model, footprint/parity and
fabrication authorities remain necessary. No complete-ERC or complete-engineering claim follows
from preparing this narrower execution input.

Require exact ordered flat symbol-body equivalence, ignoring only lexical whitespace/offsets
and the root library nickname. Native library-mismatch ERC uses base-item pin comparison, not
the full electrical-type comparison, so absence of that finding is insufficient. Reject placed-symbol
cached-name overrides and used inherited symbols in this initial profile. Unused inheritance
must remain contained and acyclic. These are disclosed support limits, not evidence of ordinary
board coverage or a substitute for the original broader development target.

Pinned native sources: [ERC comparison call](https://gitlab.com/kicad/code/kicad/-/blob/18fb9289ff0efdca53c0352ed81a0973f0a6b58c/eeschema/erc/erc.cpp#L1777),
[explicit base-item dispatch](https://gitlab.com/kicad/code/kicad/-/blob/18fb9289ff0efdca53c0352ed81a0973f0a6b58c/eeschema/lib_symbol.cpp#L2234)
and [base comparison fields](https://gitlab.com/kicad/code/kicad/-/blob/18fb9289ff0efdca53c0352ed81a0973f0a6b58c/eeschema/sch_item.cpp#L708).

Reject uncaptured embedded dependencies, unsupported project dependencies/settings versions and
unbound time/version-control substitutions. The preparer is not a native syntax validator;
the subsequent executor must prove that each original source actually loads, bind its backend
and effective settings, and recheck execution state and the source before returning evidence.

## Validation and remaining gates

Focused tests cover source preservation, strict rule changes, waiver disclosure, pin floors,
exact library bindings, unsupported dependencies, body equivalence and capture/budget refusals.
Independent review and final-source integration validation remain required. Passing these tests
does not establish native project execution, calibrated models, schematic/PCB parity, physics,
fabrication, readiness, approval or live mutation. Raw prepared files remain private.
