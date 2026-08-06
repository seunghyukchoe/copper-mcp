# Vendored KiCad PCM schemas

These two files are **not CopperMCP schemas**. They are verbatim copies of KiCad's own Plugin and
Content Manager metadata schemas, vendored so that
[`tests/test_pcm_package.py`](../../tests/test_pcm_package.py) can validate the package this
repository builds without a network fetch. Nothing in `src/` reads them.

They are vendored rather than fetched because a validation gate that reaches the network is not a
gate: it fails when GitLab is unreachable and passes differently on the day upstream edits a
pattern. A vendored copy makes the schema a reviewed, dated input like any other, and an upstream
change becomes a deliberate re-vendoring with a diff someone reads.

| File | Retrieved from | Retrieved | SHA-256 | Bytes |
|---|---|---|---|---|
| `pcm.v1.schema.json` | <https://go.kicad.org/pcm/schemas/v1> | 2026-08-06 | `ed07f48d3dceb3af723bba347c6b90d3fc74228b3d73bcb8954850c39f8d9015` | 13907 |
| `pcm.v2.schema.json` | <https://go.kicad.org/pcm/schemas/v2> | 2026-08-06 | `693506bc17d3736fc42769f82df9a970e23834db58ac5b506607541b75cc7865` | 11344 |

`go.kicad.org` is the authority KiCad's own submission CI uses: the addons-metadata validator
re-downloads from those two short links at CI time rather than reading the convenience copies kept
beside it. On 2026-08-06 they resolved to the KiCad source tree, not the addons repository:

- <https://gitlab.com/kicad/code/kicad/-/raw/master/kicad/pcm/schemas/pcm.v1.schema.json>
- <https://gitlab.com/kicad/code/kicad/-/raw/master/kicad/pcm/schemas/pcm.v2.schema.json>

Both are JSON Schema draft-07 and both declare `$ref: "#/definitions/Package"` at the root, so a
*package* `metadata.json` validates against the whole document directly. The same file also carries
the `Repository`, `RepositoryResource`, and `PackageArray` definitions, which describe the
repository index (`repository.json`, `packages.json`) rather than a package — CopperMCP publishes no
repository index and uses neither.

CopperMCP validates its package against **both** versions. v2 is what a new package should target
and what KiCad 10.0+ requests, but a `plugin`-typed package is also served to KiCad 6.0–9.x through
the down-converted v1 lists, and v1 is the stricter document: it closes `type` to a three-member
enum and `license` to a fixed ~100-entry list where v2 accepts any string. Passing both is the
claim we actually want to make.

To re-vendor, replace the files, update the digests and date above, and read the diff. See
[the PCM distribution research note](../../docs/research/kicad-pcm-distribution-v1.md) for what the
schemas do and do not constrain, and for the rules the official repository's CI enforces on top of
them.
