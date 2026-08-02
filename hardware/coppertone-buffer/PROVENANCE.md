# Design provenance

CopperTone is an original CopperMCP Contributors board-first engineering
preview created on 2026-08-03. The placement, routing, net assignments, and
generator source in this directory were manually authored for this repository.
No vendor reference PCB layout was copied, and CopperMCP did not autoroute or
apply the copper.

## Primary technical references

- [Texas Instruments OPA1656 product page](https://www.ti.com/product/OPA1656)
  and [datasheet](https://www.ti.com/lit/ds/symlink/opa1656.pdf): supply limits,
  pinout, electrical characteristics, and layout guidance.
- [Same Sky SJ1-351X datasheet](https://www.sameskydevices.com/product/resource/sj1-351x.pdf):
  connector identity and dimensional reference. The local footprint remains
  unqualified and must be checked independently before fabrication.
- [WIMA MKS 2 product data](https://www.wima.de/en/our-product-range/metallized-capacitors/mks-2/):
  470 nF film capacitor family, dimensions, pitch, and ordering-code stem.
- [Panasonic EEEFK1E100R](https://industrial.panasonic.com/ww/products/pt/aluminum-cap-smd/models/EEEFK1E100R)
  and [EEEFK1E470P](https://industrial.panasonic.com/ww/products/pt/aluminum-cap-smd/models/EEEFK1E470P):
  electrolytic candidate values and case families.
- [KiCad 10 command-line documentation](https://docs.kicad.org/10.0/en/cli/cli.html):
  DRC, render, statistics, Gerber, drill, SVG, and STEP commands.
- [CERN Open Hardware Licence](https://cern-ohl.web.cern.ch/home): definitive
  source for the CERN-OHL-S-2.0 licence family.

## KiCad library assets

The PCB references 3D models distributed with KiCad by `${KICAD10_3DMODEL_DIR}`.
Those model files are not copied into this directory. The SJ1-3513N connector
body is intentionally absent from the render because KiCad 10.0.5's footprint
references a 3D file that is not present in the macOS model package; the jack
pads and holes are still included in every manufacturing export. KiCad states that its
official symbol, footprint, and 3D-model libraries are licensed under
[CC BY-SA 4.0 with a design-file exception](https://www.kicad.org/libraries/license/).
Each downstream user is responsible for applying the current library terms.

The footprint geometry emitted by `generate_board.py` is local source under
CERN-OHL-S-2.0. A 3D model rendering successfully is not dimensional
qualification. The BOM therefore keeps all orderable parts at `VERIFY` or
`QUALIFY` status.

## Generated files

`coppertone-buffer.kicad_pcb`, `coppertone-buffer.kicad_pro`, `metrics.json`,
the `manufacturing/`, `mechanical/`, `media/`, and machine-readable validation
outputs are derived from `generate_board.py` plus KiCad 10.0.5. Hashes are
recorded in `validation/SHA256SUMS`. The generator derives native KiCad object
identities with UUIDv5 from semantic object keys so an unchanged board does not
receive fresh random identities on each replay. KiCad export files still carry
volatile creation metadata; `validate.sh` therefore verifies the committed
snapshot read-only by default, while `--refresh-artifacts` is the explicit
operation that replaces public evidence and its hashes.
