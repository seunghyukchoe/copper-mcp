# CopperTone Stereo Line Buffer

> **Engineering preview — not approved for fabrication or sale.** This is a
> board-first KiCad 10 artifact intended to exercise CopperMCP's inspection and
> validation workflow. It has no source schematic, ERC, PCB/schematic parity
> proof, simulation, assembled prototype, or measured audio data yet.

![CopperTone top render](media/coppertone-buffer-top.png)

CopperTone is a compact, 9 V, two-channel line-level audio buffer built around
the OPA1656 dual JFET-input op amp. The input channels are AC-coupled and biased
to a filtered mid-supply reference; each amplifier is wired at unity gain. The
outputs add 47 ohm isolation, AC coupling, and 100 kohm discharge resistors.

This is deliberately a **line buffer**, not a headphone amplifier. Do not
connect speakers, low-impedance headphones, phantom power, mains, automotive
power, or any safety-critical load.

## Snapshot

| Item | Preview value |
| --- | --- |
| Board | 52 mm × 30 mm, 2-layer FR-4, 1.6 mm |
| Supply | 9 V DC only, J3 pin 1 = `9V_RAW`, pin 2 = `GND` |
| Active device | TI OPA1656IDR, SOIC-8 |
| Audio I/O | 3.5 mm stereo TRS, J1 input and J2 output |
| Signal gain | 1 V/V nominal per channel |
| Input high-pass estimate | 3.4 Hz from 470 nF and 100 kohm, before source effects |
| Output high-pass estimate | load-dependent; about 1.6 Hz into 10 kohm |
| Layout | Ground pours on both layers; no high-impedance input vias |
| KiCad DRC | 0 violations, 0 unconnected items with KiCad 10.0.5 |
| Electrical validation | Not performed |

The estimates above are first-order calculations, not specifications or
measurements. See [`constraints.yaml`](constraints.yaml) and
[`validation/README.md`](validation/README.md) for the exact validation gates.

## Reproduce the design and outputs

Install KiCad 10 and Python 3.11 or newer, then run:

```sh
./validate.sh
```

Set `KICAD_CLI=/absolute/path/to/kicad-cli` if the CLI is not on `PATH`. The
script regenerates the PCB, refills zones, fails on any DRC warning or error,
and exports:

- DRC JSON and board statistics under `validation/`;
- Gerber and Excellon files under `manufacturing/`;
- top/bottom PNG and copper SVG under `media/`;
- a STEP assembly under `mechanical/`; and
- SHA-256 hashes in `validation/SHA256SUMS`.

`generate_board.py` is the preferred editable source for this preview. The
checked-in `.kicad_pcb` is its generated, zone-filled result. The generator is
deterministic until KiCad refills zones and may be regenerated at any time.

## Circuit walk-through

1. J3 accepts a 9 V DC source. D1 provides series reverse-polarity protection.
2. R1/R2 create `VREF`; C5/C6 filter the virtual ground.
3. J1 tip/ring pass through C1/C2; R3/R4 bias both op-amp inputs to `VREF`.
4. U1A/U1B are unity-gain followers.
5. R5/R6 isolate capacitive cables; C3/C4 block DC; R7/R8 reference J2 to ground.
6. C7 is local U1 supply bypass; C8 is supply bulk decoupling.

## Required work before ordering boards

- Capture and independently review a source schematic.
- Run ERC and KiCad `--schematic-parity`; reconcile every pin and net.
- Verify every custom footprint against the latest manufacturer drawing,
  especially J1/J2 and electrolytic polarity/orientation.
- Confirm component lifecycle, stock, and orderable suffixes in `BOM.csv`.
- Review creepage, clearances, enclosure, ESD, cable-grounding, and power input.
- Assemble at least two prototypes and record DC offsets, current, noise,
  frequency response, crosstalk, clipping margin, load stability, and thermals.
- Obtain an independent fabrication review and explicitly change
  `approved_for_fabrication` only after all gates pass.

## CopperMCP boundary

The geometry is manually authored in the deterministic Python generator.
CopperMCP did **not** autoroute or apply copper to this board. The preview is a
real artifact for testing CopperMCP's current inspect/hash/count and bounded
candidate-validation path while the router and explicit apply operation remain
future work. AI output must never bypass KiCad checks or human authorization.

## Licensing

The source in this directory is licensed under
[`CERN-OHL-S-2.0`](LICENSE). Referenced KiCad 3D models are not copied into this
directory and remain under the KiCad library terms described in
[`PROVENANCE.md`](PROVENANCE.md). There is no CERN endorsement.
