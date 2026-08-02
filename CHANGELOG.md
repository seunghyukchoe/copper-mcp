# Changelog

All notable changes are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Initial Apache-2.0 project foundation and governance.
- Secure, bounded inspection for `.kicad_pcb` files.
- Versioned board-manifest and candidate JSON schemas.
- MCP tools for server information, board inspection, candidate validation, and comparison.
- Correctness-first candidate ranking and routing backend contracts.
- GitHub issue forms, CI, CodeQL, dependency auditing, release automation, and project ledgers.

### Security

- Workspace confinement protects against parent-path and symlink escapes.
- Secret-bearing files, private boards, job stores, and generated artifacts are ignored by default.
- MCP network transport binds to loopback unless explicitly reconfigured.
- The development dependency floor excludes pytest versions affected by `PYSEC-2026-1845`.

[Unreleased]: https://github.com/seunghyukchoe/copper-mcp/compare/v0.1.0...HEAD
