"""Discoverable MCP schemas that do not pre-validate private circuit values.

The MCP framework normally validates arguments before invoking a handler. Its
validation errors may include the rejected input, which is unacceptable for a
private circuit. ``CircuitIntentToolContent`` therefore accepts any runtime JSON
value so the redacting domain boundary always handles it, while advertising the
same bounded object shape through ``WithJsonSchema``.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, WithJsonSchema


class _ClosedContract(BaseModel):
    """Base for machine contracts that reject undocumented output fields."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


def _inline_json_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Inline local references so the schema can be nested in a tool argument."""

    document = model.model_json_schema(by_alias=True)
    definitions = document.pop("$defs", {})

    def expand(value: Any) -> Any:
        if isinstance(value, dict):
            reference = value.get("$ref")
            if isinstance(reference, str) and reference.startswith("#/$defs/"):
                name = reference.removeprefix("#/$defs/")
                target = definitions.get(name)
                if not isinstance(target, dict):
                    raise RuntimeError("MCP contract contains an unresolved schema reference")
                return expand(deepcopy(target))
            return {key: expand(item) for key, item in value.items()}
        if isinstance(value, list):
            return [expand(item) for item in value]
        return value

    expanded = expand(document)
    if not isinstance(expanded, dict):  # pragma: no cover - Pydantic always returns an object
        raise RuntimeError("MCP contract schema is malformed")
    return expanded


CircuitId = Annotated[
    str,
    Field(pattern=r"^circuit:[a-z0-9][a-z0-9._-]{0,63}$"),
]
ComponentId = Annotated[
    str,
    Field(pattern=r"^component:[a-z0-9][a-z0-9._-]{0,63}$"),
]
NetId = Annotated[
    str,
    Field(pattern=r"^net:[a-z0-9][a-z0-9._-]{0,63}$"),
]
PortId = Annotated[
    str,
    Field(pattern=r"^port:[a-z0-9][a-z0-9._-]{0,63}$"),
]
Digest = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]


class CircuitComponentContract(_ClosedContract):
    """One supported two-pin passive in an MCP Circuit Intent argument."""

    id: ComponentId
    kind: Literal["resistor", "capacitor_unpolarized"]
    reference: Annotated[str, Field(pattern=r"^[RC][1-9][0-9]{0,5}$")]
    value: Annotated[
        str,
        Field(min_length=1, max_length=64, pattern=r"^[^\u0000-\u001f\u007f]+$"),
    ]


class CircuitConnectionContract(_ClosedContract):
    """One component-pin membership in a logical net."""

    component_id: ComponentId
    pin: Literal["1", "2"]


class CircuitNetContract(_ClosedContract):
    """One bounded logical net."""

    id: NetId
    name: Annotated[str, Field(pattern=r"^[A-Za-z][A-Za-z0-9_.+/:\-]{0,63}$")]
    connections: Annotated[
        list[CircuitConnectionContract],
        Field(min_length=1, max_length=128),
    ]


class CircuitPortContract(_ClosedContract):
    """One external logical port."""

    id: PortId
    net_id: NetId
    direction: Literal["input", "output", "bidirectional", "passive"]


class CircuitIntentContentContract(_ClosedContract):
    """Advertised structured content for ``render_circuit_schematic``."""

    circuit_id: CircuitId
    project_name: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]{2,63}$")]
    title: Annotated[
        str,
        Field(min_length=1, max_length=120, pattern=r"^[^\u0000-\u001f\u007f]+$"),
    ]
    components: Annotated[
        list[CircuitComponentContract],
        Field(min_length=1, max_length=64),
    ]
    nets: Annotated[list[CircuitNetContract], Field(min_length=1, max_length=128)]
    ports: Annotated[list[CircuitPortContract], Field(max_length=32)]


# Runtime acceptance is intentionally broad. The handler delegates every value,
# including scalars and arrays, to the non-echoing Circuit Intent decoder.
CircuitIntentToolContent = Annotated[
    Any,
    WithJsonSchema(_inline_json_schema(CircuitIntentContentContract)),
]


class CircuitCountsContract(_ClosedContract):
    """Redacted topology counts."""

    components: Annotated[int, Field(ge=1, le=64)]
    nets: Annotated[int, Field(ge=1, le=128)]
    ports: Annotated[int, Field(ge=0, le=32)]


class CircuitIntentSummaryContract(_ClosedContract):
    """Redacted Circuit Intent provenance."""

    schema_: Literal["copper.circuit-intent"] = Field(alias="schema")
    schema_version: Literal["0.1.0"]
    intent_digest: Digest
    counts: CircuitCountsContract


class SchematicRetentionContract(_ClosedContract):
    """Exact capability-access and lazy-reclamation semantics."""

    scope: Literal["process"]
    ttl_seconds: Literal[900]
    persistent: Literal[False]
    reclamation: Literal["lazy_on_access_or_process_exit"]


class SchematicArtifactContract(_ClosedContract):
    """Redacted schematic artifact metadata returned over stdio MCP."""

    kind: Literal["kicad_schematic"]
    mime_type: Literal["application/x-kicad-schematic"]
    format_version: Literal["20250114"]
    artifact_digest: Digest
    intent_digest: Digest
    size_bytes: Annotated[int, Field(ge=1, le=1_000_000)]
    resource_uri: Annotated[
        str,
        Field(
            pattern=(
                r"^pcb://artifacts/schematic/[A-Za-z0-9_-]{43}/"
                r"circuit\.kicad_sch$"
            )
        ),
    ]
    retention: SchematicRetentionContract


class SchematicVerificationContract(_ClosedContract):
    """Exact performed and explicitly unperformed verification stages."""

    intent_topology: Literal["passed"]
    artifact_digest: Literal["passed"]
    provenance_binding: Literal["passed"]
    deterministic_replay: Literal["passed"]
    kicad_cli_parse: Literal["not_run"]
    erc: Literal["not_run"]
    schematic_board_parity: Literal["not_run"]
    electrical_validation: Literal["not_run"]
    board_ready: Literal[False]


class CircuitSchematicToolResponse(_ClosedContract):
    """Strict structured output contract for the stdio schematic tool."""

    schema_: Literal["copper.circuit-schematic-build"] = Field(alias="schema")
    schema_version: Literal["0.1.0"]
    status: Literal["rendered"]
    intent: CircuitIntentSummaryContract
    artifact: SchematicArtifactContract
    verification: SchematicVerificationContract


__all__ = [
    "CircuitIntentContentContract",
    "CircuitIntentToolContent",
    "CircuitSchematicToolResponse",
]
