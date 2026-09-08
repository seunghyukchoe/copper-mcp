# ADR-0161: Measured optimization is an end-to-end versioned workflow

- Status: Proposed; implementation in progress
- Date: 2026-09-08
- Owners: CopperMCP maintainers
- Related: [ADR-0133](0133-native-optimization-execution-and-host-confirmation.md),
  [ADR-0157](0157-verified-host-delivery-consent-is-challenge-bound.md),
  [ADR-0158](0158-layered-trees-bind-complete-pad-connectivity.md),
  [ADR-0159](0159-composed-clearance-is-an-input-bound-measurement.md)

## User-visible outcome

An explicit optimization/v2 MCP launch runs the actual isolated optimizer, compares identity
and proposed placements under equal budgets, and returns a persistent, input-bound evaluation
package through the existing status/export/review flow. A schema or measurement helper without
this production consumer does not deliver the feature. Native project checks and broader routing
are integrated into this same workflow; unsupported work remains an explicit blocker.

## Version and authority boundaries

Unversioned launches retain v1 interpretation, canonical values and approval semantics. V1
does not call the new clearance measurement. V2 has an explicit schema branch and versioned
request, candidate, judge, package and job identities. Both versions are dispatched consistently
through intake, isolation, persistence, status, export and review; mixed bindings are rejected.
The advertised MCP accepted set expands explicitly, not by relabelling a v1 field.

V2 project declarations name bounded, digest-bound schematic/project files and symbol libraries.
Captured bytes and native ERC/parity evidence are derived internally using the existing trusted
adapters. Supplied observations never become execution evidence. Project parity remains distinct
from ERC and binds the final candidate bytes. Declared projects require both checks before review.
Effective required domains include mandatory profile checks and any caller-added requirements;
caller input cannot remove mandatory DRC/DFM or the ERC required by a declared project.

V2 also binds an explicit complete-default DRC validation profile. On the private derivative
only, enable the five reviewed native-default suppressed categories as warnings when absent
or ignored: missing_courtyard, track_not_centered_on_via, tuning_profile_track_geometries,
footprint_filters_mismatch and footprint_type_mismatch. Preserve stronger explicit severities,
all other settings and exclusions. Bind and disclose the profile and effective private context.
Any remaining suppression still makes required evidence inconclusive. The source project is
never rewritten; v1 continues to require an operator-supplied complete profile for its positive
native control. This neither waives findings nor turns bounded DFM into manufacturing sign-off.

Required-domain fail, inconclusive or not-run still blocks selection/review/application. No new
apply operation, unattended save, provider-selected geometry, authority registry or executable
selection is introduced by this feature. Metadata and geometry retain the existing owner,
delivery, expiry and restart gates; unrelated durable-export redesign is out of scope.

## Comparable candidate evaluation

Retain identity placement in the candidate count before bounded heuristic screening. Freeze the
population before routing. V2 defaults to at most four candidates and 256 route attempts, with
a 4096-attempt hard maximum; these are explicit optimizer-profile limits, not test-budget changes.
Existing other hard maxima and v1 limits remain unchanged.

Preallocate equal per-slot routing/evaluation caps, seed schedules and wall allowances from the
remaining global budget. Half of the obstacle-check allowance is for routing and half for
mandatory verification/measurement; split each half evenly across slots. Expansions, attempts
and repair allowances are also divided equally. Remainders and unused slot allowances cannot
fund another slot. Root budget charges include conservative search reservations for proposal
and mandatory replay, including failures, separately identified from measured successful probe
counts and other actual charges. Use consistent branch-search units across routers. Refuse
insufficient allocations before expensive work; do not silently drop the identity control.
Candidate routing and judging execute contiguously under their slot allowance. V2 may expose
an explicit evaluating phase; v1 lifecycle states remain unchanged.

V2 uses a distinct optimization-only placement rendering path. Preserve the legacy serializer
and apply subset, but admit ordinary front/back footprint poses without side flips in the private
v2 derivative. Unmoved expressions remain byte-identical. Known footprint-local graphics/metadata
stay local; owned absolute pad/text angles change with the footprint rotation. Unsupported
pose-carrying syntax refuses instead of being partly transformed. Retain complete identity,
source/snapshot, lock, scope and exact modeled reparse checks. This does not widen a file-apply
surface. The native mixed-side movement control exposed this missing production connection.

Coordinate semantics follow the [KiCad format reference](https://dev-docs.kicad.org/en/file-formats/sexpr-intro/#_coordinates_and_sizes)
and the pinned 10.0.5 writer's pad/text/text-box paths: pad and ordinary text angles are written
absolute, while text-box angles subtract the parent footprint orientation. Do not treat every
angle-bearing child identically or silently discard unsupported rendering caches.

Record every attempted slot, its budget and fixed outcome. Record only observed baseline metrics.
An incomplete or unavailable comparison cannot become a quality-improvement claim. Preserve the
legality/DRC, connectivity, congestion/clearance, vias, length, intent/displacement and deterministic
tie-break order. No soft score can waive a hard gate.

A v2 identity that needs no new routing search can remain reviewable only when its exact identity
binding, original source bytes and all original board/snapshot revisions are preserved, added
copper/vias and displacement are zero, and every ordinary validation gate still passes. Keep the
zero-probe count truthful. This exception does not cover arbitrary zero-displacement alternatives
or change v1's guard.

## Measured clearance

Measure the final composed snapshot using the existing complete-input helper. Bind the observation
to final snapshot and constraint digests, method, charged work and its own digest. A margin is a
strict signed safe-JSON integer or absent; do not clamp a negative margin or use zero for unavailable.
Out-of-range input refuses. This is an IR-class distance observation, not native rule, fabrication
or physics authority. Cancellation and exhausted budgets remain failures, not unavailable metrics.

If any comparison member lacks a measurement, omit clearance from the population's ranking and
disclose that choice. A measured zero remains distinct from unavailable. Packages bind the full
comparison interpretation so review cannot approve a different measurement or candidate population.

## Acceptance

Require an actual MCP request through the isolated child, tree routing, mandatory checks, SQLite
and export; existing success fixtures that replace isolation cannot prove this flow. Add native
owned-fixture coverage without changing source board bytes. Verify v1 golden values and zero new
measurement calls; v2 roundtrip/reopen, fair budgets, failed controls, signed/unavailable values,
project/candidate freshness, cancellation and malformed-child refusal. Real-host approval,
three-profile development demonstrations, external conversion/fill/repair and held-out quality
remain required portions of the milestone/program, not implied by unit success.
