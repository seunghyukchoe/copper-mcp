# Release Process

CopperMCP uses Semantic Versioning, annotated `vMAJOR.MINOR.PATCH` tags, generated GitHub release
notes, build attestations, and an append-only release ledger.

## Prepare

1. Confirm the milestone is complete and all blocking issues are closed.
2. Run `make check` from a clean checkout.
3. Update `pyproject.toml`, `src/copper_mcp/__init__.py`, `CITATION.cff`, and any versioned
   schemas. `scripts/check_version.py` requires the first two to agree.
4. Re-pin the version-coupled evidence. CopperMCP writes
   `(generator_version "<package version>")` into every board and schematic it renders, so any
   digest taken over rendered bytes changes with the version and is reproducible only by the
   version that recorded it. Concretely: regenerate the route-bundle benchmark artifact with
   `PYTHONPATH=src python scripts/benchmark_route_bundle.py --write` (real KiCad required) and
   record the regeneration as a benchmark-ledger entry, and re-pin `SCHEMATIC_ARTIFACT_DIGEST` in
   `tests/test_golden_identities.py`. Every other golden identity is version-independent and must
   **not** move; a pin that changes here and is not on this list is a real contract change, not a
   release chore.
5. Move relevant `CHANGELOG.md` entries from **Unreleased** into a dated version section.
6. Add migration notes for every breaking or behaviorally significant change.
7. Complete the release-ledger row, including validation and security status.
8. Run `python scripts/check_version.py --tag vX.Y.Z`.

## Dry run

Before creating a tag, run the release verifier against the intended version from `main`:

```bash
gh workflow run release.yml --ref main -f version=vX.Y.Z
gh run watch
```

The manual workflow runs the version check, complete test and security gate, and distribution build,
then retains the artifacts for 14 days. It cannot attest or publish a GitHub release. The `publish`
job is restricted to a pushed `v*.*.*` tag, so reviewing dry-run artifacts does not create a public
release or a release attestation.

## Publish

1. Merge the release pull request to `main`.
2. Create and push an annotated tag: `git tag -a vX.Y.Z -m "CopperMCP X.Y.Z"`.
3. The tag-triggered release workflow rebuilds, tests, audits, attests, and creates the GitHub
   release.
4. Verify checksums, provenance, generated notes, and downloadable artifacts.

Publishing to PyPI is intentionally disabled until package ownership, trusted publishing, and a
separate supply-chain review are complete.

## After release

- Verify installation from the GitHub artifact in a clean environment.
- Update the release ledger with the final tag, commit, release URL, and any known issues.
- Open follow-up issues instead of silently editing released artifacts.
- For a security release, follow the coordinated disclosure plan in the private advisory.
