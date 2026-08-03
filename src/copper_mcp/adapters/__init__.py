"""Pure source-format adapters for CopperMCP domain contracts."""

from copper_mcp.adapters.kicad_board_ir import (
    KiCadConstraintProfile,
    net_id_for_name,
    parse_kicad_bytes,
)
from copper_mcp.adapters.kicad_route_patch import (
    KiCadRoutePatchError,
    render_kicad_candidate_board,
)
from copper_mcp.adapters.kicad_schematic import (
    KiCadSchematicArtifact,
    render_kicad_schematic,
)

__all__ = [
    "KiCadConstraintProfile",
    "KiCadRoutePatchError",
    "KiCadSchematicArtifact",
    "net_id_for_name",
    "parse_kicad_bytes",
    "render_kicad_candidate_board",
    "render_kicad_schematic",
]
