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

Each ledger has its own zero-padded, three-digit ID space. `scripts/check_ledgers.py` validates
these IDs on every `make lint` and every CI run: it parses the `D-`, `R-`, `SEC-`, and `B-` spaces
and **fails** on a duplicate number, an identifier that is not zero-padded to three digits, or a row
that goes backwards in a ledger that is strictly increasing. It **reports** gaps without failing.
The table below is part of that check — the checker compares it against what the ledgers actually
contain, so it cannot go stale unnoticed.

| Ledger | Prefix | Highest allocated | Next free |
|---|---|---|---|
| [Decision ledger](decision-ledger.md) | `D-` | `D-173` | `D-174` |
| [Risk register](risk-register.md) | `R-` | `R-130` | `R-131` |
| [Security review ledger](security-ledger.md) | `SEC-` | `SEC-127` | `SEC-128` |
| [Benchmark ledger](benchmark-ledger.md) | `B-` | `B-095` | `B-096` |
| [Release ledger](release-ledger.md) | none — keyed by version | `0.6.0` | n/a |

The rules:

1. **Allocate in the pull request that lands the entry, not before.** The "next free" numbers above
   go stale the moment another branch merges. Two concurrent branches that both reserve `D-137`
   early will collide — and that is not hypothetical. Six numbers each name two unrelated entries
   today: `D-137`, `D-139`, `D-140`, `B-076`, `B-078`, and `B-082`. In every case the colliding
   rows landed in different places in the same document and Git merged both without a conflict.
   Read the ledger at the tip of your rebase target, take the next number, and if a rebase moves it,
   renumber before merging. Updating the row above is the cheap safety net: it is one line per ID
   space, so two branches that both take the next number now conflict *textually* and Git refuses
   the merge instead of accepting it silently.
2. **Numbers are never reused.** A gap is permanent, and the checker reports gaps as information
   rather than failing on them. `D-039`, `SEC-021`, and `B-006` are unused because their entries
   were withdrawn before merge. `SEC-114` has no recorded claimant at all; it was skipped during
   the same parallel-branch period that produced the six collisions. Either way the number is
   spent, so that an external reference resolves to nothing rather than to an unrelated entry. Do
   not fill a gap to tidy the sequence.
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
   its parent and `B-` numbers are not monotonic in document order — the checker therefore makes no
   ordering claim about it. The other four ledgers are strictly increasing in document order, and
   the checker enforces that.

   Because a replay is the one legal way to repeat a number, it is also the one way an accidental
   duplicate could hide. `scripts/check_ledgers.py` therefore carries the ten existing replays in a
   closed `REPLAY_SUB_ENTRIES` list keyed to their exact heading text, and being listed is necessary
   but not sufficient: the heading must still be a `####` sub-entry, and the `###` entry it replays
   must already appear earlier in the document. Adding an eleventh replay means editing that list and
   saying what it re-measures. A listed exception that stops matching a real heading is itself a
   failure, so an exception cannot be added and then quietly forgotten.
5. **IDs are three digits, zero-padded**, including past 100 (`D-136`, not `D-0136`). ADR references
   are four digits (`ADR-0065`) and are links into `docs/adr/`, never ledger-allocated IDs. The
   checker rejects a badly padded identifier rather than silently accepting it as a new number.

Six historical collisions predate this check and are recorded rather than repaired, because a
renumbered row would rewrite append-only history and break every external citation: `D-137`,
`D-139`, and `D-140` (see `D-143`); `B-076`, `B-078`, and `B-082` (see `B-084`). Merging two
independently appended blocks also displaced document order in the decision ledger, so the `D-137`
block now follows the `D-139`/`D-140` block; the same correction records that. Each is carried in
the checker's closed `RECORDED_COLLISIONS` list, keyed to the correction that documents it, so it
is reported on every run while a *new* duplicate still fails the build. Registering a collision is
not a way to accept one: it requires
landing the dated correction note first.

The same "allocate at merge, never reuse" rule governs ADR numbers, and
`scripts/check_adr_numbers.py` enforces it the same way — one number per file, headings that match
filenames, one index row per ADR, and a next-unused number that matches reality. See
[the ADR gap tombstones](../adr/README.md#adding-an-adr) for the equivalent unused numbers.

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
