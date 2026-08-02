# Changelog

All notable changes are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Initial Apache-2.0 project foundation and governance.
- Secure, bounded inspection for `.kicad_pcb` files.
- Fixed-argument, read-only KiCad JSON DRC with bounded severity, connectivity, ignored-check, and
  violation-type summaries plus stale-context checks.
- Versioned board-manifest, DRC-summary, and candidate JSON schemas.
- MCP tools for server information, board inspection, KiCad DRC, candidate validation, and comparison.
- Correctness-first candidate ranking and routing backend contracts.
- GitHub issue forms, CI, CodeQL, dependency auditing, release automation, and project ledgers.

### Security

- GitHub Actions are pinned to reviewed immutable commits; the release workflow uses the current
  official attestation action and explicitly scoped artifact-metadata permission.
- Workspace confinement protects against parent-path and symlink escapes.
- Secret-bearing files, private boards, job stores, and generated artifacts are ignored by default.
- MCP network transport binds to loopback unless explicitly reconfigured.
- KiCad execution uses a validated executable, fixed arguments, discarded logs, a POSIX child-process
  file ceiling, cumulative byte/file-count and discovery-time bounds, non-overlapping snapshot
  lifetimes, timeouts, strict contract parsing, and before/after DRC-context revision checks.
- The development dependency floor excludes pytest versions affected by `PYSEC-2026-1845`.

[Unreleased]: https://github.com/seunghyukchoe/copper-mcp/compare/v0.1.0...HEAD
