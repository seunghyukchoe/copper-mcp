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

### Next production connection: native full-board intake

The pinned development projects require native conversion before the existing workflow can
capture their geometry. Implement an explicit v2 `input_mode: native-full-board`; the default
`observed-snapshot` mode and all v1 launches retain their required observed snapshot/target scope.
Native full-board mode requires the original board digest, derives the imported snapshot and
all nets with at least two pads, and refuses conflicting explicit target selection. A supplied
expected snapshot must match; an absent one is permitted only in this explicit mode. Never
exclude already-connected targets, routing failures or later unsupported targets from that scope.

Run the reviewed native upgrade on two private copies under the inherited absolute job deadline.
Preserve original board/project bytes and every existing object identity. Only native-generated,
unique mandatory Datasheet/Description field UUIDs beneath preserved source footprint UUIDs may
receive deterministic UUID5 identities from original digest, footprint identity and field name.
Check collisions and refuse other unexplained conversion differences. This rule comes from the
actual audio conversion, not an assumption that all UUIDs are disposable. Compare complete
normalized bytes and bind original/normalized revisions, executable/version, fixed command and
normalization method into the v2 request and exported package. Variable pre-normalization output
digests are observations, not stable request identities. Executable consistency does not prove
vendor or shared-library authentication.

Original raw context remains freshness authority. Geometry, placement intent and routing operate
on the explicit imported baseline. The isolated child and review path must reproduce the same
import from fresh originals under their existing deadline and consent checks. Imported identity
may have zero routing work only when it equals that verified baseline; it must disclose conversion
and must not claim equality with raw original bytes. Add no apply surface or unattended saving.

The same workflow must preserve supported drawing-layer dimensions and native footprint-filter
metadata instead of stripping them. Unsupported copper/outline/pose semantics still refuse.
Original-project source freshness, replay, tampering, conflicting modes and imported identity need
production-consumer tests. The real three-profile demonstration still requires fill, repair and
external routing; native import alone is not a completed milestone.

Native upgrade behavior follows the [KiCad 10 CLI documentation](https://docs.kicad.org/10.0/en/cli/cli.html#pcb-upgrade).

### Integrated verification

The isolated worker runs under a small standard-library guardian that remains the owned process
group leader. Only the guardian holds the private status-pipe writer; it closes that descriptor
in the worker before exec, observes the worker's actual exit status, and then parks. A successful
status triggers parent group termination before any guardian reap or wait for stdout EOF, followed
by buffered-output draining and ordinary response validation. Permission-denied cleanup, a missing
expected group, malformed status or unexpected guardian exit cannot become success. The guardian
also kills its own group at the inherited deadline plus the existing five-second cleanup grace,
or on detected status-reader loss. This does not authorize work past the original work deadline.
Every native-import and review context scan receives remaining-deadline settings, including the
post-import freshness scan; sub-second remainder refuses before another scan starts.

Failed terminal v2 jobs expose a separate `blocked-evaluation` package through the existing status
and export tools. It is a canonical projection of the owner-bound terminal record and recorded
comparison blockers, not a selected candidate or a fabricated JudgeReport. It explicitly carries
no approval, apply or geometry authority and labels its evidence as terminal metadata only.
Status supplies its digest; export checks that digest and the record revision. This projection
may be reconstructed after private inputs expire or the server restarts because its complete
source is already-retained redacted metadata. Normal candidate export, human confirmation and v1
semantics are unchanged. No geometry request or approval prompt is allowed for a blocked report.

V2 candidate fill is distinct from ADR-0021's read-only check of a user's existing cache. The
optimizer may generate fresh fill only in private candidate copies. Two fixed native refills
must agree; modeled geometry, zone intent and original context must remain unchanged. Only
native-produced cache expressions may be spliced into the candidate, preserving other source
expressions. Bind the input/output board and snapshot revisions, context, executable and canonical
fill into the exported package. Refresh after copper changes and recheck every target before the
complete-board DRC/judge gate. Old v1 fill authority continues to reject stale caches.

The production tree path now shares the pair router's foreign-zone obstacle construction and
fill admission. Conservative envelopes remain when there is no verified fill; supplied islands
must pass the existing shape, aggregate size, revision, backing-zone and containment gates.
Tree candidates record the existing canonical fill digest and replay/structural request checks
bind it in both directions. Absent fill remains absent from canonical identity, preserving old
non-zoned candidate addresses. The v1 optimizer still refuses zoned composition. Selected-net
zone attachment and partial pre-existing copper repair remain distinct unsupported tree cases;
foreign-pour support must not be described as negotiated rip-up/reroute.

V2 optimization now stages one bounded existing-copper reset per candidate when its equal slot
allocation includes a repair round. Selection is restricted to declared target nets with existing
copper that are disconnected or attached to a footprint whose pose actually changed within the
declared movable scope. A moved pad can still touch old copper; that does not make retaining its
old route a useful placement comparison. Unchanged placement receives the same repair allowance
and can use it to complete partial routing, but connected unchanged nets retain their copper.

Only selected unlocked segment/via/arc expressions are removed from private bytes. Group-member
references at any admitted nesting level refuse rather than becoming dangling metadata.
For unchanged footprints, positive copper cores may prove connection, but failure to do so is
not disconnection evidence. A separate bounded graph of enclosing pad/track/via/arc/fresh-fill
geometry must prove the target disconnected before a reset is admitted. Overlapping envelopes,
unmodelled arcs or missing fill authority remain inconclusive and refuse unchanged-net repair.
This keeps the connected-unchanged retention rule intact without treating approximations as
electrical authority. Source/snapshot equality, native IDs,
byte-preserving splices and a complete modeled round trip establish the exact removal. The package
binds input/output identities, the original placed baseline, removed-ID and net-scope digests,
counts and policy; no raw removed geometry is retained in metadata. The batch is charged before
source work. Fresh fill, complete routing/connectivity and mandatory composed-board checks must
then succeed before anything is published. Failure or cancellation discards the private derivative,
not a user's edited document. V1, all apply surfaces and human consent semantics remain unchanged.
This whole-affected-net reset is not negotiated local-window repair, and it does not establish
zone attachment, general arc routing or held-out improvement.

Each comparison slot receives a nontransferable share of remaining native-output bytes as well
as its existing work/time allocation. Existing connected copper is checked with the established
multilayer connectivity kernel under batched, precharged predicate ceilings; recognizing an
existing connection must not consume a fabricated full routing search. V2 trace-length and via
quality use the complete evaluated target-net copper, separate from newly added copper used by
identity/no-search checks. Unsupported target arcs still refuse rather than contributing zero
length. This is not a physics metric or a change to v1's interpretation.

Require an actual MCP request through the isolated child, tree routing, mandatory checks, SQLite
and export; existing success fixtures that replace isolation cannot prove this flow. Add native
owned-fixture coverage without changing source board bytes. Verify v1 golden values and zero new
measurement calls; v2 roundtrip/reopen, fair budgets, failed controls, signed/unavailable values,
project/candidate freshness, cancellation and malformed-child refusal. Real-host approval,
three-profile development demonstrations, external conversion/fill/repair and held-out quality
remain required portions of the milestone/program, not implied by unit success.
