#!/usr/bin/env python3
"""Route the public two-pad FreeRouting comparison fixture with CopperMCP.

This is deliberately a narrow, non-MCP benchmark runner.  The comparison harness invokes it
in a disposable child workspace, so it proves the same preview-and-apply path exposed to the
MCP server without granting an external benchmark command any access to a user's workspace.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

# The comparison harness starts this process with an allowlisted environment. Keep the runner
# self-contained by importing the checked-out CopperMCP package, not an ambient installation.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from copper_mcp.adapters import KiCadConstraintProfile, parse_kicad_bytes  # noqa: E402
from copper_mcp.apply import apply_route_candidate  # noqa: E402
from copper_mcp.board_ir import NetClass, ParseLimits  # noqa: E402
from copper_mcp.config import Settings  # noqa: E402
from copper_mcp.route_preview import preview_route  # noqa: E402

_CONSTRAINTS = {
    "clearance_nm": 250_000,
    "track_width_nm": 250_000,
    "via_diameter_nm": 800_000,
    "via_drill_nm": 400_000,
}


def _profile() -> KiCadConstraintProfile:
    net_class = NetClass(id="class:request", name="Request", **_CONSTRAINTS)
    return KiCadConstraintProfile(net_classes=(net_class,), default_net_class_id=net_class.id)


def route_fixture(source_path: Path, output_path: Path, seed: int) -> bytes:
    """Return a deterministic applied board for the public ``AUDIO`` two-pad fixture."""

    source = source_path.read_bytes()
    profile = _profile()
    conversion = parse_kicad_bytes(source, profile, ParseLimits())
    if conversion.snapshot is None or conversion.diagnostics:
        raise ValueError("fixture source is outside CopperMCP's supported Board IR")

    # ``preview_route`` is intentionally exercised through its workspace boundary, exactly as
    # an MCP request would be.  It can only read this temporary copy of the public fixture.
    with tempfile.TemporaryDirectory(prefix="copper-two-pad-", dir=output_path.parent) as directory:
        workspace = Path(directory)
        board_name = "two-pad.kicad_pcb"
        (workspace / board_name).write_bytes(source)
        preview = preview_route(
            {
                "board": board_name,
                "net": "AUDIO",
                "layer": "F.Cu",
                "seed": seed,
                "constraints": dict(_CONSTRAINTS),
                "settings": {},
            },
            Settings(workspace=workspace),
        )
    if preview.candidate is None:
        raise ValueError("CopperMCP did not produce a candidate for the public fixture")

    applied = apply_route_candidate(source, conversion.snapshot, preview.candidate, profile)
    return applied.content


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("seed", type=int)
    arguments = parser.parse_args()
    if arguments.source.resolve() == arguments.output.resolve():
        parser.error("source and output must be distinct paths")
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    content = route_fixture(arguments.source, arguments.output, arguments.seed)
    temporary = arguments.output.with_suffix(arguments.output.suffix + ".tmp")
    temporary.write_bytes(content)
    temporary.replace(arguments.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
