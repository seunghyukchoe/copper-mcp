# CopperTone changelog

All notable hardware-preview changes are recorded here. This ledger follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and uses Semantic
Versioning-style preview identifiers. It does not imply fabrication approval.

## [Unreleased]

### Changed

- Native board objects now use stable semantic UUIDv5 identities.
- Default validation rebuilds and checks the snapshot in a temporary directory;
  tracked artifacts change only through the explicit `--refresh-artifacts` mode.

### Required

- Source schematic, ERC, and PCB/schematic parity validation.
- Footprint qualification and prototype measurement campaign.

## [0.1.0-preview] - 2026-08-03

### Added

- Reproducible KiCad 10 generator for a 52 mm × 30 mm stereo line buffer.
- OPA1656 unity-gain channels, filtered mid-rail bias, AC-coupled I/O, dual
  ground pours, mounting keepouts, test points, and fabrication outputs.
- One-command DRC, statistics, render, Gerber, drill, SVG, STEP, and hash
  pipeline.
- BOM, constraints, provenance, hardware licence notice, and validation ledger.

### Validation

- KiCad 10.0.5 DRC: 0 violations and 0 unconnected items.
- No ERC, schematic parity, simulation, physical assembly, or audio measurement.
