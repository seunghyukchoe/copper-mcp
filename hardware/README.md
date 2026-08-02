# Audio Board Lab

This directory contains open hardware used to exercise CopperMCP against real KiCad projects.
Software and hardware have different licensing and acceptance gates: read the `LICENSE`, provenance,
constraints, and validation ledger inside each board directory before reusing or fabricating it.

| Board | Status | Verified evidence | Licence |
|---|---|---|---|
| [CopperTone Stereo Line Buffer](coppertone-buffer/) | `0.1.0-preview`; not approved for fabrication | KiCad 10.0.5 DRC: 0 violations and 0 unconnected items; complete Gerber/drill/render/STEP export pipeline | CERN-OHL-S-2.0 |

DRC-clean does not mean electrically correct, safe, manufacturable, or production-ready. Each board
must separately record schematic/ERC parity, footprint qualification, fabrication review, physical
prototype testing, and measured performance before its fabrication gate can change.
