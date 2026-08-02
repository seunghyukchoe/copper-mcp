# Public media assets

Assets in this directory are public project media, not routing or benchmark evidence.

| Asset | Purpose | Provenance | Validation |
|---|---|---|---|
| `coppermcp-social-preview.png` | GitHub social preview and launch media | Generated on 2026-08-03 with OpenAI's built-in image generation tool from a maintainer-authored prompt, then center-cropped and resized with macOS `sips` | 1280×640 PNG; visible text reviewed; SHA-256 `79b2bf2a6ef5a92365afc47904d907d4c3ae6b82e51b05521d19cf0ce3713161` |
| `coppertone-kicad-editor.jpg` | Factual development screenshot for launch updates | Captured on 2026-08-03 from KiCad PCB Editor 10.0.5 after opening the public CopperTone board | 1225×769 JPEG; board title and status bar reviewed; SHA-256 `240073b98ac0421407f18c5c6391ef8cd14cb8126731f0a58aaf22c97731d70d` |

The social preview was intentionally constrained to the exact project name and the line “Open PCB
automation for humans & agents.” It contains no benchmark, safety, completion, fabrication, or
compatibility claim. Generated artwork committed to this repository is distributed under the
repository's Apache-2.0 license.

The KiCad screenshot shows the board editor reporting 55 pads, 9 vias, 53 track segments, 14 nets,
and 0 unrouted items. These are design-file counts, not electrical, fabrication, or performance
evidence. The underlying hardware preview remains separately licensed and gated as documented in
`hardware/coppertone-buffer/`.
