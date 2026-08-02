# ADR-0004: Authoritative KiCad CLI DRC gate

- Status: Accepted
- Date: 2026-08-03

## Context

Candidate manifests previously carried self-reported DRC metrics. Implementing routing geometry
before an authoritative validation gate would make correctness claims impossible to verify. KiCad
10 exposes a documented JSON DRC command that works without introducing live-editor mutation.

## Decision

CopperMCP will treat fixed-argument `kicad-cli pcb drc` output as the authoritative PCB DRC evidence
for the `0.1.x` foundation. The adapter validates the executable, confines boards to the configured
workspace, snapshots the board plus matching project/custom-rule files and workspace-local KiCad
library tables/assets into a path-preserving private temporary directory, uses a fixed argument
vector, and forbids save/refill and arbitrary variable flags. Snapshot bytes and file count are
cumulatively bounded as they are read, discovery has a wall-clock deadline, and the pre-run byte
dictionary is released before KiCad starts so before/after captures do not overlap. A dedicated
POSIX child wrapper applies `RLIMIT_FSIZE` before replacing itself with KiCad, so report growth is
bounded during execution without unsafe multithreaded `preexec_fn` use. The adapter bounds runtime,
accepts only documented result codes, requires the reviewed JSON contract, verifies report source
and requested severities, and rejects results if the source DRC-context hash changes during
execution. Temporary execution also contains KiCad's per-user `.kicad_prl` side effect outside the
source workspace.

The public summary contains aggregate severity, connectivity, ignored-check, and violation-type
counts. Raw descriptions, coordinates, UUIDs, and net names remain local to the temporary report
and are not returned through MCP.

## Consequences

- Future routers gain a real correctness gate before route generation exists.
- KiCad becomes an optional runtime dependency for DRC, while inspection remains dependency-light.
- DRC execution currently requires POSIX resource limits and fails closed elsewhere.
- Project-relative libraries should be self-contained below the configured workspace; external or
  global library resolution remains an environment-provenance concern.
- New KiCad DRC schema versions fail closed until explicitly reviewed.
- DRC success still does not prove signal integrity, power integrity, EMC, manufacturability, or
  hardware safety.
