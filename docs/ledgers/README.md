# Project Ledgers

Ledgers are append-only operational records. They complement Git history by making project decisions,
risks, benchmark evidence, security reviews, and release state easy to audit.

- [Decision ledger](decision-ledger.md)
- [Risk register](risk-register.md)
- [Release ledger](release-ledger.md)
- [Benchmark ledger](benchmark-ledger.md)
- [Security review ledger](security-ledger.md)

Correct factual errors with a dated amendment rather than silently deleting historical entries.
Pull requests must update the relevant ledger whenever they materially affect it.

## What these records are, in standard terms

Naming the pattern each record follows makes it clear what it does and does not guarantee.

- The ledgers together are a **transparency record**: append-only, human-readable, and reviewed in
  the open. They are **not a cryptographic transparency log** — there is no Merkle tree, no signed
  checkpoint, and no inclusion proof. **Git history is the only integrity mechanism**, so the
  tamper-evidence available is exactly the tamper-evidence of the repository's commit graph and
  whatever signing and branch protection the project applies to it. An entry corrected out of band
  would be visible in `git log` and nowhere else.
- The [release ledger](release-ledger.md) records **provenance** in the SLSA sense: which source
  commit was verified, by which workflow, producing which artifacts.
- Candidate DRC evidence is structurally an **attestation**: a statement about named subjects,
  bound to their digests — candidate, Board IR base revision, source board, patched board and
  patched context — and refused when any binding fails. Candidate DRC responses now include an
  unsigned, redacted in-toto Statement **payload** using the Link v0.3 predicate. The payload is
  deterministic and machine-checkable, but it is not signed, persisted, or wrapped in DSSE; those
  authentication and transport steps remain future work on the [roadmap](../roadmap.md).
- The [benchmark ledger](benchmark-ledger.md) records **content-addressed measurement artifacts**;
  each run file is validated against its own self-digest.

Calling these by their standard names is deliberate. It should be obvious which properties are
claimed, and equally obvious that cryptographic non-repudiation is not among them.
