# Synthetic tscircuit output-validation fixtures

These fixtures were authored for CopperMCP and contain no geometry copied from an upstream board,
bug report, or gist. They preserve only the minimum failure shape described publicly in the linked
tscircuit issues.

- `cross-net-same-layer-crossing.json` models issue
  [#1964](https://github.com/tscircuit/tscircuit-autorouter/issues/1964): two independently valid
  same-layer routes cross at one point.
- `via-near-pad.json` models issue
  [#2058](https://github.com/tscircuit/tscircuit-autorouter/issues/2058): post-processed via copper
  overlaps an adjacent net's pad while the wire centreline remains outside that pad.

The files are test-only evidence. They are not tscircuit corpus samples, routing-quality evidence,
or authoritative DRC fixtures.
