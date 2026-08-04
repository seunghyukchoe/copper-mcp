"""Pure source-format adapters for CopperMCP domain contracts."""

from copper_mcp.adapters.kicad_board_ir import (
    KiCadConstraintProfile,
    net_id_for_name,
    parse_kicad_bytes,
)
from copper_mcp.adapters.kicad_layered_route_patch import (
    KiCadLayeredRoutePatchError,
    render_kicad_layered_candidate_board,
)
from copper_mcp.adapters.kicad_route_patch import (
    KiCadRoutePatchError,
    render_kicad_candidate_board,
)
from copper_mcp.adapters.kicad_schematic import (
    KiCadSchematicArtifact,
    render_kicad_schematic,
)
from copper_mcp.adapters.kicad_schematic_parity import (
    KiCadSchematicParityError,
    KiCadSchematicParityErrorCode,
    KiCadSchematicParityEvidence,
    KiCadSchematicParityLimits,
    verify_kicad_schematic_parity,
)

__all__ = [
    "KiCadConstraintProfile",
    "KiCadLayeredRoutePatchError",
    "KiCadRoutePatchError",
    "KiCadSchematicArtifact",
    "KiCadSchematicParityError",
    "KiCadSchematicParityErrorCode",
    "KiCadSchematicParityEvidence",
    "KiCadSchematicParityLimits",
    "net_id_for_name",
    "parse_kicad_bytes",
    "render_kicad_candidate_board",
    "render_kicad_layered_candidate_board",
    "render_kicad_schematic",
    "verify_kicad_schematic_parity",
]
