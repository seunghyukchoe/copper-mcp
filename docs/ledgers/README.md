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

## Allocating IDs

Each ledger has its own zero-padded, three-digit ID space. Nothing validates these IDs
automatically — `scripts/check_ledgers.py` checks that each ledger exists, carries its heading, and
that every benchmark artifact matches its own self-digest, but it does not look at IDs at all. The
convention below is therefore enforced by review, and getting it wrong is cheap to do and annoying
to unwind.

| Ledger | Prefix | Highest allocated | Next free |
|---|---|---|---|
| [Decision ledger](decision-ledger.md) | `D-` | `D-137` | `D-138` |
| [Risk register](risk-register.md) | `R-` | `R-110` | `R-111` |
| [Security review ledger](security-ledger.md) | `SEC-` | `SEC-113` | `SEC-114` |
| [Benchmark ledger](benchmark-ledger.md) | `B-` | `B-076` | `B-077` |
| [Release ledger](release-ledger.md) | none — keyed by version | `0.5.0` | n/a |

The rules:

1. **Allocate in the pull request that lands the entry, not before.** The "next free" numbers above
   go stale the moment another branch merges. Two concurrent branches that both reserve `D-137`
   early will collide, and because nothing checks for duplicates the collision merges silently.
   Read the ledger at the tip of your rebase target, take the next number, and if a rebase moves it,
   renumber before merging.
2. **Numbers are never reused.** A gap is permanent. `D-039`, `SEC-021`, and `B-006` are unused
   because their entries were withdrawn before merge; they stay unused so that an external reference
   to a withdrawn ID resolves to nothing rather than to an unrelated entry. Do not fill a gap to
   tidy the sequence.
3. **A correction gets a new ID.** Because rows are append-only, a superseding or clarifying entry
   is a new entry that names what it corrects — never an edit to the original. `B-075`
   ("held-out audio evidence-source provenance correction") is the model: it states what it
   supersedes, what it leaves immutable, and what it does not claim. This holds even when the
   original is *plainly* wrong and the fix is *trivially* safe: `B-076` records the corrected
   targets for two broken Markdown links rather than repointing them in place, and
   `scripts/check_doc_links.py` carries those two targets as named exemptions. Convenience is
   exactly the pressure this rule exists to resist — a record that can be tidied is not a record.
4. **A replay of an existing benchmark reuses that benchmark's ID**, as a `####` sub-entry whose
   heading reads `B-0NN — <what changed> replay`. A replay that measures something new is a new
   `B-` number instead. The benchmark ledger is organized by topic, so a replay sub-entry sits with
   its parent and `B-` numbers are not monotonic in document order. The other four ledgers are
   strictly increasing in document order; keep them that way.
5. **IDs are three digits, zero-padded**, including past 100 (`D-136`, not `D-0136`). ADR references
   are four digits (`ADR-0065`) and are links into `docs/adr/`, never ledger-allocated IDs.

The same "allocate at merge, never reuse" rule governs ADR numbers. See
[ADR-0027's tombstone](../adr/README.md#adding-an-adr) for the equivalent gap.

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
