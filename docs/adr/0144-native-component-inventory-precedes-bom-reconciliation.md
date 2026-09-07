# ADR-0144: Native component inventory precedes BOM reconciliation

- Status: Proposed; private implementation under review
- Date: 2026-09-07
- Owners: CopperMCP maintainers
- Related: [ADR-0141](0141-project-erc-prepares-an-explicit-rule-and-library-derivative.md),
  [ADR-0142](0142-project-connectivity-erc-binds-execution-and-source-freshness.md),
  [ADR-0143](0143-project-parity-uses-native-liveness-and-immutable-candidates.md)

## Decision

Capture KiCad's resolved nonvirtual component inventory from immutable project inputs before
reconciling declared BOM/model references. Reuse the fixed authenticated project execution context
and supplied-library closure. Do not derive effective references from possibly stale display labels
or borrow Circuit Intent identities. Existing electrical-inputs/v1 declarations remain unchanged.

The initial profile uses two fixed `sch export netlist --format kicadxml` executions, without caller
presets, flags or variants. Require successful exits, empty diagnostics, unchanged inputs/backend,
and matching normalized inventories. An annotation warning is not successful inventory evidence,
even if native execution returns zero. Original workspace sources must still match before execution
and delivery. No board file is read or saved.

Bind captured project, prepared execution context, native syntax checks, executable/authentication,
command policy and component records. Keep references, values, footprints, datasheets and library
identifiers private and repr-redacted. A disclosed projection carries counts/digests and explicit
no-BOM-validation, no-model-validation and no-apply fields, never an engineering pass.

## Native semantics and limits

KiCad's XML schema E contains component references, library identifiers, sheet paths and unit UUIDs.
Its XML sheet paths omit the root UUID; translate against the already-verified hierarchy, requiring
exact sheet coverage. A root/nested synthetic export control produced the same R1/C1 records and
three expected sheets, with exit zero, empty diagnostics and unchanged source bytes.

Native export combines units of a component and omits virtual references beginning with `#`.
The exported UUID list is not a complete census of every placed unit across different sheets.
Preserve that limitation; this inventory must not masquerade as full placed-symbol identity or
physical validity. Excluded-from-BOM, excluded-from-board and DNP markers remain explicit. A custom
property sharing a flag's name but carrying a value must not spoof the native flag.

External XML is untrusted despite backend authentication. Bound bytes, structure, fields and
processing time; disallow DTD/entity declarations and external resolution. Duplicate critical
nodes, references, UUIDs or sheets refuse. Ordinary custom metadata is not interpreted as a new
authority. Late parsing, mutation and divergent observations must not publish an inventory.

## Follow-on and acceptance

The first publication contains the XML reader and private records only. The native double-export
executor is a separate reviewed follow-on; parser acceptance is not authenticated execution or
source freshness. Keep this distinction when using the inventory design described above.

Reconciliation must check actual captured BOM artifact content and explicit item-to-component
bindings, not just declaration counts or artifact hashes. Model-library bytes still require model
definition, pin/interface and validity checks; neither component metadata nor BOM agreement proves
simulation calibration, circuit function, device ratings, thermal behavior or manufacturing fitness.

Keep known-good, known-bad, incomplete and unsupported input controls, native executions and command
doubles separate. Independent review, all supported interpreters, final-source full validation and
protected hosted gates are required. No new MCP operation, v1 approval meaning, readiness percentage,
strict live transaction, human consent or release is added by this inventory slice.

## Sources

- [KiCad 10 CLI netlist export](https://docs.kicad.org/10.0/en/cli/cli.html#schematic-export-netlist).
- [Schematic instance paths and symbol instances](https://dev-docs.kicad.org/en/file-formats/sexpr-schematic/).
- Pinned native 18fb9289: `eeschema_jobs_handler.cpp:408` (warning/exit semantics),
  `netlist_exporter_xml.cpp:258` (resolved component records and unit grouping),
  `netlist_exporter_base.cpp:82` (virtual-reference omission), `sch_symbol.cpp:647` (effective references).
