from __future__ import annotations

import importlib.util
import math
import re
import sys
from pathlib import Path
from types import ModuleType

import pytest

GENERATOR = Path(__file__).parent.parent / "hardware" / "coppertone-buffer" / "generate_board.py"


def _generator() -> ModuleType:
    spec = importlib.util.spec_from_file_location("coppertone_generate_board", GENERATOR)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # The generator declares dataclasses, which resolve annotations through sys.modules.
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(spec.name, None)
    return module


def test_the_mounting_hole_keepout_circumscribes_its_required_radius() -> None:
    """Every point within the required radius must be inside the keep-out polygon.

    An octagon whose vertices sit on the radius has its edges pulled inward by cos(22.5
    degrees), which leaves a ring of the required area unprotected. Only the circumscribing
    octagon covers the whole circle.
    """

    module = _generator()
    centre_x, centre_y = 3.5, 3.5
    points = [
        (float(x), float(y))
        for x, y in re.findall(r"\(xy ([-\d.]+) ([-\d.]+)\)", module.keepout(centre_x, centre_y))
    ]

    assert len(points) == 8
    required = module.KEEPOUT_RADIUS_MM
    for index, start in enumerate(points):
        end = points[(index + 1) % len(points)]
        # Distance from the centre to this edge, which is what has to reach the radius.
        edge_x, edge_y = end[0] - start[0], end[1] - start[1]
        length = math.hypot(edge_x, edge_y)
        area = abs(edge_x * (centre_y - start[1]) - edge_y * (centre_x - start[0]))
        # Coordinates are emitted at 0.1 um resolution, so allow one quantum of rounding.
        # The defect this guards against was 0.217 mm short - three orders of magnitude more.
        assert area / length >= required - 1e-4, index
    # And it stays snug: no edge is further out than the circumscribing octagon needs.
    for start in points:
        assert (
            math.dist(start, (centre_x, centre_y)) <= required / math.cos(math.radians(22.5)) + 1e-9
        )


def test_the_committed_board_still_carries_the_older_inscribed_keepout() -> None:
    """The generator is fixed; the committed board is deliberately not regenerated here.

    Regenerating would change every coordinate and invalidate every measurement recorded
    against this board, so it is a separate, deliberate step. This test records the gap rather
    than hiding it, and must be updated in the same change that regenerates the board.
    """

    board = GENERATOR.parent / "coppertone-buffer.kicad_pcb"
    text = board.read_text(encoding="utf-8")
    module = _generator()
    fixed_vertex_radius = module.KEEPOUT_RADIUS_MM / math.cos(math.radians(22.5))

    # The committed octagons still use the inscribed radius, so their vertices sit nearer the
    # centre than a regenerated board's would.
    assert "(xy 6.1331 2.35)" in text or "(xy 2.35 6.1331)" in text or "6.133" in text
    assert fixed_vertex_radius > module.KEEPOUT_RADIUS_MM


@pytest.mark.parametrize("directory", ["validation", "mechanical", "media"])
def test_validate_script_recreates_generated_directories_from_empty(directory: str) -> None:
    """Stale files from an earlier export must not be hashed into SHA256SUMS."""

    script = (GENERATOR.parent / "validate.sh").read_text(encoding="utf-8")
    refresh = script[script.index("refresh_artifacts() {") :]
    remove = refresh.index("\n  rm -rf")
    make = refresh.index("\n  mkdir -p")

    assert remove < make, "generated trees must be cleared before they are recreated"
    assert f'"$demo_dir/{directory}"' in refresh[remove:make]
