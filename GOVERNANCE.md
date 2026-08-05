# Governance

## Principles

CopperMCP is maintained in the open. Technical decisions prioritize electrical correctness,
reproducibility, user control, privacy, and sustainable contributor experience.

## Roles

- **Contributors** submit issues, documentation, tests, code, datasets, or reviews.
- **Reviewers** have demonstrated subject knowledge and may approve changes in an owned area.
- **Maintainers** merge changes, manage releases, moderate the community, and handle security
  reports.
- The initial lead maintainer is `@seunghyukchoe`.

Roles are earned through sustained, constructive contributions. Maintainers may nominate new
reviewers or maintainers in a public governance issue. Security-sensitive removals may be handled
privately, followed by an appropriate public record.

## Decision process

- Routine changes use issues and pull requests.
- Durable architectural decisions use ADRs under [`docs/adr/`](docs/adr/README.md). An ADR is
  immutable after acceptance except for its status and links to superseding records.
- Major compatibility, governance, licensing, data-policy, or security changes require an RFC issue,
  at least seven days for public comment, and maintainer approval.
- When consensus is not possible, maintainers document the chosen tradeoff and dissent in the ADR.

## The record

Decisions, risks, benchmark evidence, security reviews, and release state are kept in append-only
[ledgers](docs/ledgers/README.md). Rows are never rewritten; a correction is a new dated entry that
names what it supersedes. These are a transparency record backed by Git history, not a cryptographic
transparency log — the [ledger index](docs/ledgers/README.md) states exactly which properties are
and are not claimed.

## Review

Every pull request requires review, and branch protection requires that every review conversation be
resolved before merge. Changes to a surface that writes to a user's file additionally require a
dedicated adversarial review recorded in the pull request, as described in
[CONTRIBUTING.md](CONTRIBUTING.md). Merges to a protected branch require direct maintainer approval.

## Releases

Maintainers follow [`docs/releasing.md`](docs/releasing.md). Release authorization is a separate,
deliberate act: a `Ready` row in the [release ledger](docs/ledgers/release-ledger.md) naming a
validated source commit, then a metadata commit, then the tag. Every release requires a green
validation suite, changelog entry, release-ledger entry, signed or annotated tag, generated release
notes, and build provenance. No handoff, issue, or pull-request comment authorizes a release.

## Conflicts of interest

Reviewers disclose employment, financial, research, or competitive interests that could reasonably
affect a decision. A conflicted maintainer should request an independent review.
