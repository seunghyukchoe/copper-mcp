# Validation ledger

This ledger records what was actually checked for CopperTone `0.1.0-preview`.
It is evidence for a board-file preview, not approval to fabricate.

## 2026-08-03 — KiCad 10.0.5 reference run

Command, from this directory's parent repository:

```sh
hardware/coppertone-buffer/validate.sh
```

Result: **pass** (process exit 0).

| Check or export | Observed result |
| --- | --- |
| PCB parse and zone refill | Pass; board saved after refill |
| DRC included severities | Error, warning, exclusion |
| DRC violations | 0 |
| Unconnected items | 0 |
| Outline | Present, 52.0000 mm × 30.0000 mm |
| Copper layers | 2 |
| Minimum track width | 0.2500 mm |
| Vias | 9 through; 0 blind, buried, or micro |
| Pads | 38 SMD, 15 plated through-hole, 2 NPTH |
| Components | 26 footprints total |
| Gerber export | 7 layers plus X2 job file |
| Drill export | Separate PTH and NPTH, maps and report |
| Visual export | Top/bottom PNG and front-copper SVG |
| Mechanical export | STEP created |

Machine-readable evidence is in [`drc.json`](drc.json),
[`board-stats.json`](board-stats.json), and [`SHA256SUMS`](SHA256SUMS).

## Checks intentionally not claimed

- There is no source schematic, so ERC was not run and `--schematic-parity`
  would have no legitimate source to compare.
- KiCad's report lists its default ignored checks, including missing
  courtyards, track-endpoint centering, footprint filters, and footprint type.
  No DRC exclusions were added to the project, but ignored check classes are
  not equivalent to a pass and require independent review.
- No SPICE simulation or independent netlist review has been performed.
- No PCB was ordered, assembled, powered, or connected to audio equipment.
- No DC offset, current, gain, noise, distortion, response, crosstalk,
  clipping, stability, ESD, EMC, thermal, or reliability measurement exists.
- The STEP assembly omits J1/J2 bodies because the referenced SJ1-3513N model
  is absent from the tested KiCad macOS model package. Pads and holes are
  present in manufacturing outputs; mechanical fit is unqualified.

## Next acceptance gate

The next release may not change `approved_for_fabrication` until a reviewed
schematic, ERC, PCB/schematic parity, footprint qualification, independent fab
review, and recorded prototype measurements are all present. Append results to
this ledger; do not rewrite or delete prior validation entries.
