# ADR-0143: Project parity uses native liveness and immutable candidates

- Status: Proposed; private implementation under review
- Date: 2026-09-07
- Owners: CopperMCP maintainers
- Related: [ADR-0142](0142-project-connectivity-erc-binds-execution-and-source-freshness.md),
  [ADR-0141](0141-project-erc-prepares-an-explicit-rule-and-library-derivative.md),
  [ADR-0084](0084-authoritative-source-to-board-parity.md)

## Decision

Implement ordinary captured-project parity as a distinct private operation. Do not broaden
Circuit Intent's exact source-replay contract, invent intent identifiers or reuse its deliberately
footprint-less accounting invariant. Existing v1 wrappers retain their meanings and accepted sets.

Share the already-reviewed native execution setup between ERC and parity. It owns private source
copies, fixed global library/font configuration, a sealed KiCad 10.0.5 backend, per-original-source
native syntax validation and final input/state/backend verification. It exposes no request-selected
executable, plugin or authority registry. The context is repr-redacted and has no apply authority.

The new parity operation takes captured project inputs, explicit symbol libraries and candidate
board bytes with an exact expected SHA-256 revision. Never read the workspace board. Observe every
captured source file before execution and before delivery, while retaining the immutable candidate.
These checks are boundary freshness observations, not an atomic editor transaction.

After source validation, stage a separate same-stem board/schematic copy under the private context.
Retain captured hierarchy, text variables and supplied symbol libraries. Use a distinct project
derivative containing fixed warning-or-stronger parity severities, retaining stronger in-profile
errors and clearing board exclusions. Other board settings/custom rules are not silently certified:
this is a parity-specific profile, not full DRC or fabrication validation. Bind the original capture,
source execution and every parity derivative/candidate byte separately. Account for both retained
input trees within the operator context limits, as well as source and candidate byte ceilings.

## Native evidence

Run fixed `pcb drc --schematic-parity` commands twice with JSON output and all severity classes.
Do not request save, zone refill, caller flags or violation exit-code aggregation. Require clean
execution and a strict complete C-locale diagnostic grammar: the native violation, unconnected-item
and parity counts, followed by the exact private report target. All counts must match the report.
Missing, extra, malformed, oversized or mismatched diagnostics refuse; an empty parity array alone
does not prove that native parity ran.

This follows the pinned native CLI's same-stem lookup and silent check-disable behavior in
`pcbnew_jobs_handler.cpp` (18fb9289, lines 2351–2413 and 2463–2468). Real disposable controls observed
zero issues for a matching assigned-footprint board, one net conflict after altering a net, and
an empty report with no parity marker when the schematic was absent. The original inputs remained
unchanged. These synthetic controls are not the held-out acceptance corpus.

Factor neutral parity observations from the existing parser, preserving the legacy wrapper's
component-accounting invariant and verdict categories. The new project caller requires all seven
native parity checks to remain enabled, validates companion finding structure, and binds full
normalized report details, not just counts. Normalize root date and collection order only, after
removing the private temporary-directory spelling. Retain cooperative deadline checkpoints.

Any parity finding produces fail; unequal repeated otherwise-clean observations are inconclusive.
A pass is parity only. Companion DRC counts were obtained under the parity profile and cannot
establish full DRC; that domain is explicitly inconclusive. Simulation and fabrication remain not
run, and application authority remains none. No public MCP or optimization/v1 change is made.

## Validation and remaining gates

Publication begins with the neutral report adapter and unchanged legacy wrapper only. The
candidate executor described above remains a separately reviewed follow-on; this first slice
does not execute ordinary-project parity or grant a project-parity verdict.

Keep real matching/mismatching candidates, malformed child source, workspace-board independence,
source preservation and repeat-digest controls separate from command doubles. Fault tests cover
missing/wrong markers, changed inputs, ignored checks, unsupported findings, output bounds,
nonzero exits and divergent reports. Re-run all ERC and legacy parity tests after extraction.
Independent review and final-source full/hosted validation are required before publication.

This is not general library/model validation, BOM reconciliation, calibrated SI/PI/thermal/EMC,
fabrication approval, held-out route/placement quality, human consent, strict mutation or release.
The original five-area objective and its critical gates remain unchanged.
