# Release Process

CopperMCP uses Semantic Versioning, annotated `vMAJOR.MINOR.PATCH` tags, generated GitHub release
notes, build attestations, and an append-only release ledger.

## Prepare

1. Confirm the milestone is complete and all blocking issues are closed.
2. Run `make check` from a clean checkout.
3. Update `pyproject.toml`, `CITATION.cff`, and any versioned schemas.
4. Move relevant `CHANGELOG.md` entries from **Unreleased** into a dated version section.
5. Add migration notes for every breaking or behaviorally significant change.
6. Complete the release-ledger row, including validation and security status.
7. Run `python scripts/check_version.py --tag vX.Y.Z`.

## Publish

1. Merge the release pull request to `main`.
2. Create and push an annotated tag: `git tag -a vX.Y.Z -m "CopperMCP X.Y.Z"`.
3. The release workflow rebuilds, tests, audits, attests, and creates the GitHub release.
4. Verify checksums, provenance, generated notes, and downloadable artifacts.

Publishing to PyPI is intentionally disabled until package ownership, trusted publishing, and a
separate supply-chain review are complete.

## After release

- Verify installation from the GitHub artifact in a clean environment.
- Update the release ledger with the final tag, commit, release URL, and any known issues.
- Open follow-up issues instead of silently editing released artifacts.
- For a security release, follow the coordinated disclosure plan in the private advisory.
