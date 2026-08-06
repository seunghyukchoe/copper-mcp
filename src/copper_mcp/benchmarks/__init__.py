"""Benchmark-only import seams.

Nothing in this package is part of the MCP tool surface. These modules exist so that
``scripts/`` and ``tests/`` can turn external benchmark corpora into ordinary Board IR
snapshots and routing requests, which then go through exactly the same deterministic core,
canonical verification, and typed-refusal gates as every other entry path. No module here is
imported by ``copper_mcp.tools``, ``copper_mcp.mcp_server``, or any request boundary, and
adding one there would be a public-contract change requiring its own ADR.
"""

from copper_mcp.benchmarks.simple_route_json import (
    SIMPLE_ROUTE_JSON_ADAPTER_VERSION,
    ImportedNet,
    ImportedProblem,
    ImportRefusalCode,
    ImportStatistics,
    SimpleRouteJsonImportError,
    SimpleRouteJsonImportLimits,
    import_simple_route_json,
    mm_token_to_nm,
)

__all__ = [
    "SIMPLE_ROUTE_JSON_ADAPTER_VERSION",
    "ImportRefusalCode",
    "ImportStatistics",
    "ImportedNet",
    "ImportedProblem",
    "SimpleRouteJsonImportError",
    "SimpleRouteJsonImportLimits",
    "import_simple_route_json",
    "mm_token_to_nm",
]
