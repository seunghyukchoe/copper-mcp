# ADR-0052: Emit candidate DRC as a redacted in-toto Statement payload

- **Status:** Accepted
- **Date:** 2026-08-05

## Context

CopperMCP already produces immutable, candidate-bound aggregate DRC evidence from a private
KiCad replay. The evidence is useful to machine consumers, but its current project-specific shape
does not interoperate directly with standard attestation tooling. The project must not turn that
interoperability step into a signing, persistence, or board-disclosure capability.

The in-toto Statement v1 format provides the standard outer shape. The Link v0.3 predicate is a
good fit because it maps the candidate to the operation's subject, names input materials, and
permits opaque byproducts/environment metadata without requiring raw command lines or board data.

## Decision

Expose a deterministic unsigned Statement payload alongside every candidate DRC evidence record:

- `_type` is `https://in-toto.io/Statement/v1` and `predicateType` is
  `https://in-toto.io/attestation/link/v0.3`.
- The sole subject is the fixed-name `route-candidate` descriptor containing the candidate
  SHA-256 digest. Materials use fixed names for the source board, Board IR base, patched board,
  and patched DRC context revisions, each as a required SHA-256 ResourceDescriptor.
- The predicate uses the fixed step name `kicad-candidate-drc`, an empty command array, aggregate
  `DrcSummary` byproducts, the `disposable-candidate` scope, and bounded KiCad/schema/unit
  environment metadata.
- Canonical JSON serialization sorts keys, uses compact separators, rejects non-finite values,
  and produces UTF-8 bytes. This is deterministic serialization, not a cryptographic signature.
- The MCP contract validates the closed Statement shape but keeps the field optional for clients
  that send older evidence records. No board bytes, paths, net names, UUIDs, coordinates, prompts,
  credentials, raw findings, or model output are accepted or emitted by this projection.

This increment does not emit DSSE envelopes, signatures, public keys, verification results, durable
attestations, remote transport, or apply authority. Those are separate future decisions and must
not be inferred from the presence of a Statement payload.

## Consequences

Standard in-toto consumers can parse and inspect candidate DRC evidence without a CopperMCP-specific
adapter, while the current redaction and candidate immutability invariants remain intact. The
payload is not authenticated or non-repudiable until a separately authorized signer and verifier
are designed and tested.

## References

- [in-toto Statement v1](https://github.com/in-toto/attestation/blob/main/spec/v1/statement.md)
- [in-toto envelope and DSSE guidance](https://github.com/in-toto/attestation/blob/main/spec/v1/envelope.md)
- [in-toto Link v0.3 predicate](https://github.com/in-toto/attestation/blob/main/spec/predicates/link.md)
