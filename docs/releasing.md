# Release Process

CopperMCP uses Semantic Versioning, annotated `vMAJOR.MINOR.PATCH` tags, generated GitHub release
notes, build attestations, and an append-only release ledger.

## Prepare

1. Confirm the milestone is complete and all blocking issues are closed.
2. Run `make check` from a clean checkout.
3. Update `pyproject.toml`, `src/copper_mcp/__init__.py`, `CITATION.cff`,
   `hardware/kicad-ipc-plugin/pcm/metadata.json`, and any versioned schemas.
   `scripts/check_version.py` requires the first two to agree, and
   `scripts/build_pcm_package.py` refuses to build a KiCad package whose declared version has
   drifted from `pyproject.toml`.
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

## Commit-bound artifacts and squash merges

A branch commit's SHA never survives its own merge: squash-merge discards branch
commits and linear history forbids merge commits, so no strategy this repository
allows preserves one, and an artifact that binds its own revision refuses to load
from a fresh clone of `main`. Anything a benchmark artifact binds must therefore
outlive the merge — either a revision already on the default branch, which is the
mechanism today and which hosted CI's ancestry test enforces by refusing a
branch-only binding before merge (`D-241`), or the content itself by blob hash,
which no merge strategy can move (#259). This governs every pull request that
publishes or republishes such an artifact; that is usually not the release pull
request, and when it is, plan the republish step before merging rather than after
discovering the binding is gone (#250, #252).

## Publish

1. Merge the release pull request to `main`.
2. Create and push an annotated tag: `git tag -a vX.Y.Z -m "CopperMCP X.Y.Z"`.
3. The tag-triggered release workflow rebuilds, tests, audits, attests, and creates the GitHub
   release. It builds three artifacts, not two: the wheel, the source distribution, and the KiCad
   Plugin and Content Manager package. All three land in `dist/`, so `actions/attest` covers the
   PCM archive under the same `subject-path: dist/*` as the Python distributions and no separate
   attestation step exists to fall out of step.
4. Verify checksums, provenance, generated notes, and downloadable artifacts.
5. Confirm the release carries `coppermcp-live-observer-X.Y.Z.zip` and
   `coppermcp-live-observer-X.Y.Z.metadata.json`. The `.metadata.json` sidecar is the
   repository-side document for a KiCad addon submission: it is the in-archive metadata plus a
   `download_url`, `download_sha256`, `download_size`, and `install_size` measured from the
   archive that was just built. Nothing in it is transcribed by hand.
6. Re-derive the archive locally and confirm it matches the published asset byte for byte:

   ```bash
   python scripts/build_pcm_package.py --expect-version vX.Y.Z --no-write
   ```

   The build is reproducible, so the printed `download_sha256` must equal the digest of the
   downloaded release asset. A mismatch means the release was not built from this source.

Publishing to PyPI is intentionally disabled until package ownership, trusted publishing, and a
separate supply-chain review are complete. This is also why the PCM package's `requirements.txt`
installs nothing: KiCad resolves it against PyPI, so it cannot be used to deliver CopperMCP itself.
See [the PCM distribution research note](research/kicad-pcm-distribution-v1.md).

## Submit the KiCad package

Submission to the official KiCad addon repository is a **human step**, deliberately not automated.
It is a merge request against a third party's repository under a real GitLab account, and it
carries attestations — maintainer identity, licensing, content policy — that only the maintainer
can make. The release workflow prepares everything up to that point and stops.

The checklist is in
[`hardware/kicad-ipc-plugin/README.md`](../hardware/kicad-ipc-plugin/README.md#submitting-to-the-official-kicad-addon-repository).
Note that a published version is immutable in the KiCad repository — `download_sha256`,
`download_size`, and `install_size` can never change for a version already merged — so submit only
after the GitHub release asset is final and publicly downloadable.

## After release

- Verify installation from the GitHub artifact in a clean environment.
- Update the release ledger with the final tag, commit, release URL, and any known issues.
- Open follow-up issues instead of silently editing released artifacts.
- For a security release, follow the coordinated disclosure plan in the private advisory.
