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
- Durable architectural decisions use ADRs under `docs/adr/`.
- Major compatibility, governance, licensing, data-policy, or security changes require an RFC issue,
  at least seven days for public comment, and maintainer approval.
- When consensus is not possible, maintainers document the chosen tradeoff and dissent in the ADR.

## Releases

Maintainers follow `docs/releasing.md`. Every release requires a green validation suite, changelog
entry, release-ledger entry, signed or annotated tag, generated release notes, and build provenance.

## Conflicts of interest

Reviewers disclose employment, financial, research, or competitive interests that could reasonably
affect a decision. A conflicted maintainer should request an independent review.
