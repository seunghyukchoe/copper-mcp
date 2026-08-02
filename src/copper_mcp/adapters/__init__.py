"""Pure source-format adapters for CopperMCP domain contracts."""

from copper_mcp.adapters.kicad_board_ir import (
    KiCadConstraintProfile,
    net_id_for_name,
    parse_kicad_bytes,
)

__all__ = ["KiCadConstraintProfile", "net_id_for_name", "parse_kicad_bytes"]
