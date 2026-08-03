# ADR-0014: Canonical circuit intent and deterministic schematic rendering

- Status: Accepted
- Date: 2026-08-03
- Owners: `@seunghyukchoe`

## Context

CopperMCP can inspect and preview routes on a local KiCad board, but it has no logical circuit
contract. Treating a web page, prompt, schematic file, or PCB netlist as interchangeable “circuit
input” would erase provenance and let model output bypass deterministic topology validation. Reusing
Board IR would also mix logical intent with physical geometry and fabrication constraints.

The first logical contract must be small enough to audit and must generate a real KiCad schematic
without claiming that values are electrically correct, symbols are production-qualified, ERC is
clean, or a PCB can be fabricated.

## Decision

CopperMCP defines the discriminator `copper.circuit-intent` at schema version `0.1.0`. The contract
is an immutable, content-addressed, MCP-independent topology snapshot with a strict JSON codec and a
versioned JSON Schema.

### v0.1 logical subset

- A circuit has one stable `circuit:` ID, a project name, a title, components, nets, and ports.
- Components are independently identified two-pin resistors or non-polarized capacitors. Each has a
  kind-compatible reference and an opaque display value; v0.1 does not interpret units or select
  values.
- Nets contain explicit `(component_id, pin)` connections. Ports identify external input, output,
  bidirectional, or passive interfaces by referencing one net.
- Every component pin appears on exactly one net and the two pins of a component must belong to
  different nets. References, IDs, names, connections, and port-net assignments are unique. A
  one-pin net requires a port; unknown and duplicate references fail.
- Counts, input bytes, string lengths, JSON depth, and total connections are bounded. Unknown fields,
  duplicate keys, floating-point numbers, non-finite values, control characters, invalid UTF-8, and
  unsupported schema versions fail closed.

Components, nets, connections, and ports are normalized by stable identity before canonical JSON is
encoded. UTF-8, sorted keys, compact separators, no non-finite numbers, and one trailing newline
produce byte-stable content. `snapshot_digest` is SHA-256 over the normalized `content` object, not
the envelope that contains the digest.

### Deterministic KiCad adapter

The initial adapter is a pure function from one verified snapshot to new in-memory `.kicad_sch`
bytes. It targets schematic format `20250114`, identifies its generator as `copper_mcp`, embeds only
original CopperMCP resistor and capacitor symbols, and performs no library lookup or network access.

Layout is presentation metadata, not circuit intent. Components use a fixed integer hundredth-mm
grid; connections are represented with local labels and one direction-shaped global label for each
declared port. Embedded symbols and instances carry no footprint and set `on_board=no`. All object
identifiers are deterministic UUIDs whose version-4 and RFC variant bits are structurally
compatible with the KiCad field while their remaining bits derive from the intent digest and object
role. This reproducibility differs from randomly generated UUIDv4 values and therefore requires
collision tests plus real KiCad parsing. The adapter returns an artifact digest and source-intent
digest, writes no file, and never mutates the input snapshot.

KiCad CLI parsing, SVG rendering, and netlist export may verify format and connectivity. They do not
constitute ERC, simulation, value selection, footprint qualification, schematic/PCB parity,
manufacturability, or fabrication approval. Authoritative ERC and parity require a separate
fixed-argument, bounded, private-snapshot security review.

### Versioning

Changing serialized fields, topology meaning, normalization, digest projection, component pin
semantics, or the interpretation of a kind or port direction requires a new Circuit Intent schema
version. Adding a KiCad output format behind the same logical contract may remain on `0.1.0` only if
the logical meaning is unchanged and the adapter has independent fixtures and compatibility tests.

## Consequences

- AI and future MCP tools can propose a small logical topology without authoring KiCad syntax.
- The deterministic core, not a model, owns reference integrity, complete pin assignment, canonical
  bytes, and schematic serialization.
- Original embedded symbols avoid runtime symbol-table dependence and third-party library-content
  redistribution.
- v0.1 deliberately cannot express active devices, polarized parts, power flags, hierarchy, buses,
  differential signals, footprints, simulation models, placement, or PCB constraints.
- An electrically poor but structurally valid value remains possible; later policy and authoritative
  evidence gates must not reinterpret structural validity as engineering approval.

## Alternatives considered

- Generate KiCad S-expressions directly from model output: rejected because syntax validity is not
  topology validation and generated identifiers would not bind to a canonical intent.
- Extend Board IR with schematic fields: rejected because logical circuit meaning and physical board
  geometry have different lifecycles and evidence.
- Resolve installed KiCad symbol libraries at runtime: rejected for v0.1 because library tables and
  versions would make output environment-dependent and complicate redistribution provenance.
- Claim ERC in the first slice: rejected until a bounded fixed-argument schematic CLI adapter and
  report contract receive their own tests and security review.

## References

- [KiCad schematic file format](https://dev-docs.kicad.org/en/file-formats/sexpr-schematic/)
- [KiCad common S-expression syntax](https://dev-docs.kicad.org/en/file-formats/sexpr-intro/index.html)
- [KiCad 10 command-line interface](https://docs.kicad.org/10.0/en/cli/cli.html)
