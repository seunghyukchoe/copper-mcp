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
| [Decision ledger](decision-ledger.md) | `D-` | `D-223` | `D-224` |
| [Risk register](risk-register.md) | `R-` | `R-174` | `R-175` |
| [Security review ledger](security-ledger.md) | `SEC-` | `SEC-161` | `SEC-162` |
| [Benchmark ledger](benchmark-ledger.md) | `B-` | `B-128` | `B-129` |
| [Release ledger](release-ledger.md) | none — keyed by version | `0.6.0` | n/a |

The rules:

The 2026-08-24 parallel closure wave pre-assigned `D-219`/`R-170`/`SEC-158`/`ADR-0119`
to the authoritative-signoff lane and `D-220`/`R-171` to its sibling lane. This record therefore
takes `D-221`/`R-172`/`SEC-159`/`ADR-0120` rather than racing either branch. `R-169` was deliberately
spent when that wave was allocated and remains a permanent gap. If either sibling is abandoned,
its identifiers remain spent under rule 2.

The same wave reserved `B-125`–`B-127` for sibling lanes and `B-128` for M3 E4. B-128 is now
consumed here; the sibling reservations remain unavailable until their lanes land or explicitly
release them.

1. **Allocate in the pull request that lands the entry, not before.** The "next free" numbers above
   go stale the moment another branch merges. Two concurrent branches that both reserve `D-137`
   early will collide — and that is not hypothetical. Six numbers each name two unrelated entries
   today: `D-137`, `D-139`, `D-140`, `B-076`, `B-078`, and `B-082`. In every case the colliding
   rows landed in different places in the same document and Git merged both without a conflict.
   Read the ledger at the tip of your rebase target, take the next number, and if a rebase moves it,
   renumber before merging. Updating the row above is the cheap safety net: it is one line per ID
   space, so two branches that both take the next number now conflict *textually* and Git refuses
   the merge instead of accepting it silently.
   Taking a number *above* the next free one is the other half of the same safety net, and it is
   how `D-181`/`R-138` were allocated: two branches were open on the same base, one holding
   `D-179`/`R-136` and one holding `D-180`/`R-137`, so this record stepped over both rather than
   racing them. `D-180` and `R-137` have since landed from their own branch, which is the
   mechanism working as intended — stepping over a live claim costs nothing when it lands.
   `D-179` and `R-136` are still live claims and not gaps at the moment of writing; if that
   branch is abandoned its numbers become permanent gaps under rule 2, like any other spent
   number.
2. **Numbers are never reused.** A gap is permanent, and the checker reports gaps as information
   rather than failing on them. `D-039`, `SEC-021`, and `B-006` are unused because their entries
   were withdrawn before merge. `SEC-114` has no recorded claimant at all; it was skipped during
   the same parallel-branch period that produced the six collisions. Either way the number is
   spent, so that an external reference resolves to nothing rather than to an unrelated entry. Do
   not fill a gap to tidy the sequence.

   `D-177`/`R-134` and `D-178`/`R-135` are rule 1 worked through to its end. Both were held by
   open branches when `D-179`/`R-136` was allocated, so `D-179`/`R-136` took numbers above both
   rather than colliding with them; both branches have since landed and filled their own numbers,
   so this left no gap at all. Had either been abandoned its number would simply have been spent.
   Nothing recycles a number in either outcome.

   `D-184`/`D-185`, `R-139`/`R-140` and `SEC-135`/`SEC-136` are the same mechanism running again
   at the time of writing: two branches were open on this same base holding one pair each (issues
   #140 and #141), so `D-186`/`R-141`/`SEC-137` stepped over both rather than racing them. They
   are **live claims, not gaps**, and the checker reports them as unallocated because it cannot
   see an unmerged branch. If either branch is abandoned its numbers become permanent gaps like
   any other spent number, and this paragraph becomes the correction to make. Both branches have
   since landed and filled their own numbers — `D-185`/`R-140`/`SEC-136` with issue #141's
   branch, `D-184`/`R-139`/`SEC-135` with PR #150 — so that round left no gap at all. The
   mechanism then ran a third time, in both directions at once: PR #154 was open holding
   `D-187`/`R-142` (with ADR-0097 and B-101) when this record allocated `D-188`/`R-143`/
   `SEC-138`/`B-102` above it — and #154, unable to confirm from its worktree that `SEC-138` was
   free, took `SEC-139` and stepped over *this* record's live claim in return. Both directions
   are rule 1 working as designed; whichever lands second resolves the registry line's textual
   conflict.

   The sixth round is issue #164's record (`D-198`/`R-152`/`SEC-146`, ADR-0106) stepping over two
   sibling waves at once. Branches on [issue #172](https://github.com/seunghyukchoe/copper-mcp/issues/172)
   (`D-197`/`R-151`/`SEC-145`, `ADR-0105`) and [issue #110](https://github.com/seunghyukchoe/copper-mcp/issues/110)
   (`D-199`/`R-153`/`B-110`, `ADR-0107`) were open on this same base, so this record took numbers
   above the first rather than racing it and did **not** step over the second, which sits above it
   by pre-assignment. #110's branch has since landed and filled `D-199`, `R-153`, `B-110` and
   `ADR-0107` — that half left no gap, and ADR-0107's own text called `ADR-0106` a live claim and
   stepped over it in return, which is rule 1 running in both directions inside one round.
   `D-197`, `R-151` and `ADR-0105` have since **landed** with #172's record, so they are real
   rows rather than gaps. `SEC-145` was pre-assigned to that branch and **declined** — the change
   widened no input surface — so #180 stepped over it to take `SEC-146`, and `SEC-145` is now a
   permanent spent number like any other deliberate gap, and
   this paragraph becomes the correction to make. This record also **declined its pre-assigned `B-109`
   under rule 4**: it measured nothing new. The quality question was already answered by `B-105` at
   zero changed verdicts and is explicitly not claimed, and the reachability figure the record
   needed -- 14 of 18 corpus boards carrying an island above the ordered-layer ceiling -- is
   `B-108`'s existing per-run count, cited rather than re-measured. No `B-` number was spent.

   The seventh round is the first one where the stepping-over was **decided up front rather than
   discovered**. Four branches were opened concurrently on the same base at `4a5fa65`, and each
   received a disjoint block before any of them read a ledger. All four have now landed: the
   evidence record holds `D-200`/`D-201`/`R-154`/`B-111`; the process-checker record holds
   `D-202`/`R-155`; the pad-reader survey holds `D-203`/`R-156`/`B-112`; and this record (issues
   [#65](https://github.com/seunghyukchoe/copper-mcp/issues/65) and
   [#110](https://github.com/seunghyukchoe/copper-mcp/issues/110)) holds
   `D-204`/`R-157`/`B-113`/`B-114`. Pre-assignment is rule 1's stepping-over with the race removed:
   each later merge resolved this table's textual conflict instead of discovering an identifier
   collision in an already-merged document.

   The 2026-08-20 wave is pre-assignment again, and it is the first round where the *unlanded*
   half of a block leaves the record. Four lanes were opened on the same base with disjoint
   blocks, R holding `B-124`/`D-218` and S holding `ADR-0119`/`D-219`/`SEC-158`/`B-125`/`R-170`.
   R landed as [#210](https://github.com/seunghyukchoe/copper-mcp/pull/210). Before S landed, the
   main line consumed later identifiers, so conflict resolution retained `ADR-0119` but reassigned
   S to `D-223`, `SEC-161`, and `R-174`; its original allocations remain spent under rule 2. This
   is why **`R-169` is a permanent gap** rather than an invitation to tidy the sequence. S also
   **declined its pre-assigned `B-125`**. Its slice
   opens a claim path rather than measuring one, and the machine it was built on has no
   `kicad-cli`, so the only benchmark it could have written would have been a number nothing ran;
   the rows that qualify a DRC count under
   [ADR-0109](../adr/0109-a-drc-count-carries-the-comparability-it-was-taken-with.md) exist
   precisely so that does not happen quietly. Lanes C and M were never pushed and their blocks —
   `B-126`/`B-127`/`D-220`/`R-171` and `D-221`/`B-128`/`ADR-0120` — remain unspent live claims
   rather than gaps until those branches land or are abandoned.

   Two security numbers from that round were deliberately declined and are permanent spent
   numbers. The evidence record did not take its pre-assigned `SEC-147`; it adds validation and
   comparability qualification without widening an input surface. The process-checker record
   declined `SEC-148` because its only surface change is an additive output map on
   `BoardIrSummary`; no new byte is accepted from a board, MCP argument, or environment value.
   This record likewise spends no `SEC-` number: the sweep in `B-114` committed zero third-party
   bytes, and the comparison artifact in `B-113` reads only files already in the tree. The process
   record took no `B-` number either: its hosted durations calibrate CI rather than benchmark
   product behaviour and live in `.github/ci-budget-calibration.json`.

   The fifth round is this record stepping over **three** live claims at once, in every ID space
   at the same time. Issue #165 (`D-195`/`R-150`/`SEC-144`/`B-108`, ADR-0104) was allocated while
   PRs #160 and #162 were open holding `D-192`/`D-193`, `R-147`/`R-148`, `SEC-142` and
   `B-105`/`B-106`, and a sibling branch on issue #163 held `D-194`/`R-149`/`SEC-143`/`B-107`, so
   this record took numbers above all of them rather than racing any. `D-192` through `D-194`,
   `R-147` through `R-149`, `SEC-141` through `SEC-143` and `B-104` through `B-107` are **live
   claims, not gaps**, and the checker reports the unfilled ones as unallocated because it cannot
   see an unmerged branch. `SEC-141` is the one number here with no recorded claimant, spent the
   same way `SEC-114` was. If any of those branches is abandoned its numbers become permanent gaps
   like every other spent number, and this paragraph becomes the correction to make. ADR-0104 was
   allocated over ADR-0101/0102/0103 by the same rule, recorded in
   [the ADR gap tombstones](../adr/README.md#adding-an-adr).

   The fourth round is the #116 close-out stepping over **two** live claims at once: two branches
   were open on this base holding `D-189`/`R-144` (issue #152) and `D-190`/`R-145` (issue #153), so
   that record took `D-191`/`R-146` above both rather than racing either. `D-189`, `D-190`, `R-144`
   and `R-145` are **live claims, not gaps**, and the checker reports them as unallocated because it
   cannot see an unmerged branch; if either branch is abandoned its numbers become permanent gaps
   like any other spent number, and this paragraph becomes the correction to make. That record also
   declined a pre-assigned `B-103` under rule 4: it re-ran an existing runner over the same corpus,
   which is a replay of `B-099` and not a new benchmark, so no `B-` number was spent.

   The fifth round is the widest step-over yet, and it is the mechanism paying for itself. Issue
   #110's record found `D-189`/`D-190`/`R-144`/`R-145`/`SEC-140`/`B-103` held by open branches, a
   sibling wave holding `D-192`/`R-147`, and `ADR-0099` claimed **twice** by two branches that had
   collided on it. Rather than race any of them it took `D-193`/`R-148`/`SEC-142`/`B-106` and
   `ADR-0102`, clear above every live claim in both spaces. Every intervening number is a live
   claim, not a gap, and the checker reports them as unallocated because it cannot see an unmerged
   branch. The `ADR-0099` collision is the case rule 1 exists for, observed in the wild: two
   branches that both read the same "next free" line and neither of which will conflict textually
   with the other, because they will write the same value.

   The sixth round runs the mechanism from the other end, and it is the first one written *after*
   part of it resolved rather than while every claim was still live. Issue #172's record took the
   *lowest* free numbers -- `D-197`/`R-151` and `ADR-0105` -- while two siblings on the same base
   stepped over it by agreement: issue #110 holding `ADR-0107`/`D-199`/`R-153`/`B-110`, and issue
   #164 holding `ADR-0106`/`D-198`/`R-152`/`SEC-146`/`B-109`. **#110's set has since landed** as
   PR #179, so `ADR-0107`, `D-199`, `R-153` and `B-110` are filled and are not claims of any kind.
   **#164's set is still open**, so `ADR-0106`, `D-198`, `R-152`, `SEC-146` and `B-109` are **live
   claims, not gaps**, and the checker reports the unfilled ones as unallocated because it cannot
   see an unmerged branch; if that branch is abandoned they become permanent gaps like any other
   spent number, and this paragraph becomes the correction to make. Note the consequence of the
   landing order: `D-199` merged before `D-197`, and the ledger reads correctly anyway because
   rows are ordered by ID rather than by merge date -- which is the case rule 1's closing
   paragraph describes, observed again here. Issue #172's record also declined a pre-assigned
   `SEC-145` under the same discipline rule 4 applies to `B-` numbers: it checked rather than
   assumed, found the whole `src/` diff to be one string literal and the codec's change to accept
   strictly *less* input, and spent no number for a security review with nothing to review.

   **A row that has only been pushed to a branch is not yet a record, and rule 3 does not reach
   it.** Adversarial review found a false mechanism in `D-186` while its pull request was still
   open. The first instinct was to append a correction row, and that was wrong. Append-only
   protects the *merged* ledger and the external citations that resolve against it; an unmerged
   row has neither reader nor citation, so amending it destroys nothing — while appending would
   have landed a false mechanism on `main` *together with* its correction, where `main` can
   instead carry only the truth. `D-186` and `R-141` were therefore corrected **in place** before
   merge, and no numbers were spent. Pushing a branch is not publishing a row. Rule 3 still
   governs everything that has landed: once a row is on `main` someone may have cited it, and it
   is corrected by a new ID and never edited.

   `D-179`/`R-136` then landed *after* `D-180`/`R-137`, and that is not a breach of rule 2. A
   number is allocated when its pull request opens, not when it merges, so a branch held open
   across another's merge lands below the tip and momentarily looks like it is filling a gap.
   The rule forbids **claiming a spent number to tidy the sequence**; it does not require merge
   order to match numeric order, and the ledgers are ordered by ID rather than by merge date
   precisely so that this case reads correctly afterwards.
3. **A correction gets a new ID.** Because rows are append-only, a superseding or clarifying entry
   is a new entry that names what it corrects — never an edit to the original. `B-075`
   ("held-out audio evidence-source provenance correction") is the model: it states what it
   supersedes, what it leaves immutable, and what it does not claim. This holds even when the
   original is *plainly* wrong and the fix is *trivially* safe: `B-076` records the corrected
   targets for two broken Markdown links rather than repointing them in place, and
   `scripts/check_doc_links.py` carries those two targets as named exemptions. `D-182` is the same
   move for a link whose target resolves but whose *label* names the wrong ADR: `D-155` stands as
   written, the correction is a new row, and the misnamed label is carried in that checker's
   `EXEMPT_LABEL_RECORDS` list under the same discipline. Convenience is exactly the pressure this
   rule exists to resist — a record that can be tidied is not a record.
4. **A replay of an existing benchmark reuses that benchmark's ID**, as a `####` sub-entry whose
   heading reads `B-0NN — <what changed> replay`. A replay that measures something new is a new
   `B-` number instead. The benchmark ledger is organized by topic, so a replay sub-entry sits with
   its parent and `B-` numbers are not monotonic in document order — the checker therefore makes no
   ordering claim about it. The other four ledgers are strictly increasing in document order, and
   the checker enforces that.

   Because a replay is the one legal way to repeat a number, it is also the one way an accidental
   duplicate could hide. `scripts/check_ledgers.py` therefore carries the thirteen existing replays
   in a closed `REPLAY_SUB_ENTRIES` list keyed to their exact heading text, and being listed is
   necessary but not sufficient: the heading must still be a `####` sub-entry, and the `###` entry it
   replays must already appear earlier in the document. Adding another replay means editing that list
   and saying what it re-measures. A listed exception that stops matching a real heading is itself a
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
