# ADR-0133: Execute native optimization with separate evidence, disclosure, and human consent

- Status: Proposed
- Date: 2026-09-05
- Related: [ADR-0132](0132-supervised-optimization-keeps-evidence-and-consent-separate.md),
  [ADR-0131](0131-bundle-drc-evidence-is-composed-not-cherry-picked.md),
  [v0.13 plan](../plans/v0.13-supervised-optimization.md)

## Decision

Build native execution above the immutable optimization foundation and the existing routers,
legalizer, source-preserving serializers, and private KiCad adapters. Snapshot original board and
rule context, resolve explicit targets and movable scope, and include context and implementation
fingerprints in request identity. A no-movement run carries a verified identity operation.

Persist redacted lifecycle/package metadata with owner checks, atomic CAS, leased worker fences,
bounded retention and cumulative work reservations. Keep private inputs/results separately bounded
and ephemeral. Unknown, failed, cancelled or stale work cannot publish a selected package.

Production native jobs run in a fresh isolated Python process. Inventory sources before import,
use an empty private bytecode-cache prefix, verify prepared request identity against the queued
record, and recheck the inventory after execution. The parent bounds I/O and the process group.
A long-running MCP process must not execute cached modules while recording newer disk sources.
Approval and final completion are persisted in one transaction after consuming host consent.

Native routing composes replayed per-net derivatives, preserving already-connected targets and
through-via spans over two through eight signal layers. This is an initial strategy rather than
general negotiated multilayer optimization. Cross-layer multi-pin trees and zoned compositions
refuse until their stronger composition/fill contracts exist. No model supplies geometry.

Run KiCad DRC twice over the final composition and its frozen context. The bounded DFM profile
uses that DRC evidence and makes no blanket fabrication claim. Optional Circuit Intent ERC is
checked independently and does not establish schematic/PCB parity. Missing physics authorities,
suppressed checks, unavailable tools and disagreement remain explicit non-success. Coarse
track/via occupancy is a soft score; zero clearance headroom is only a conservative lower bound.

Register five stdio execution tools. The local operator owner context comes from a private
server-owned key, never from MCP arguments. Network transports refuse until they have a trusted
authenticated owner integration. Elicitation support is not proof of a human: the operator must
explicitly enable confirmation only for a host whose human UI has been verified. Model-provided
confirmation capabilities/booleans are not accepted as authorization.

Package disclosure and package approval are separate. Metadata is owner-bound; a complete
candidate-board resource additionally requires host disclosure consent, has a bounded five-minute
lifetime, and is revoked on cancellation. Approval consumes exact consent through repository CAS
and never grants either existing apply capability. Source and intent changes invalidate approval.

## Remaining gates

Production external conversion and genuine contained smokes, fresh candidate fill, broader
schematic ERC, bounded repair integration, Orca policy scheduling and quality measurement,
held-out routing/placement quality, hosted CI calibration, and real host UI acceptance remain
release gates. Native tests or shape-valid receipts cannot satisfy them. The package version
stays 0.12.0 until the established release process authorizes a release.
