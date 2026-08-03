from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from copper_mcp.board_ir import PointNM
from copper_mcp.config import Settings
from copper_mcp.kicad_cli import (
    KiCadCliError,
    ZoneFillAuthority,
    ZoneFillStaleError,
    run_zone_fill_authority,
)
from copper_mcp.zone_fill import FillIsland, ZoneFillError, fill_digest, read_fill_islands

FIXTURES = Path(__file__).parent / "fixtures" / "route-candidate"
_DISCOVERED_KICAD_CLI = shutil.which("kicad-cli")
REAL_KICAD_CLI = (
    Path(_DISCOVERED_KICAD_CLI)
    if _DISCOVERED_KICAD_CLI is not None
    else Path("/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli")
)
DIGEST = f"sha256:{'a' * 64}"
OTHER_DIGEST = f"sha256:{'b' * 64}"


def _island(layer: str, points: tuple[tuple[int, int], ...]) -> FillIsland:
    return FillIsland(
        net_id="net:name:abc",
        layer_id=layer,
        points=tuple(PointNM(x, y) for x, y in points),
    )


def test_fill_digest_is_deterministic_and_order_independent() -> None:
    """KiCad rewrites a board wholesale on save, so the digest must ignore textual order."""

    first = _island("layer:F.Cu", ((0, 0), (10, 0), (10, 10)))
    second = _island("layer:B.Cu", ((5, 5), (20, 5), (20, 20)))

    assert fill_digest((first, second)) == fill_digest((first, second))
    assert fill_digest((first, second)) == fill_digest((second, first))
    assert fill_digest((first,)) != fill_digest((second,))
    assert fill_digest(()) != fill_digest((first,))


def test_fill_digest_notices_a_single_moved_vertex() -> None:
    original = _island("layer:F.Cu", ((0, 0), (10, 0), (10, 10)))
    moved = _island("layer:F.Cu", ((0, 0), (10, 0), (10, 11)))

    assert fill_digest((original,)) != fill_digest((moved,))


@pytest.mark.parametrize(
    ("name", "islands", "vertices"),
    [
        ("zone-fill-fresh", 1, 148),
        ("zone-fill-islands", 2, 186),
        ("zone-fill-stale", 1, 148),
    ],
)
def test_committed_fixtures_carry_the_expected_cached_fill(
    name: str, islands: int, vertices: int
) -> None:
    read = read_fill_islands((FIXTURES / f"{name}.kicad_pcb").read_bytes(), max_vertices=50_000)

    assert len(read) == islands
    assert sum(len(island.points) for island in read) == vertices


def test_reading_fill_fails_closed_past_the_vertex_budget() -> None:
    source = (FIXTURES / "zone-fill-fresh.kicad_pcb").read_bytes()

    with pytest.raises(ZoneFillError, match="vertex budget"):
        read_fill_islands(source, max_vertices=10)


def test_a_stale_fill_authority_record_cannot_be_constructed() -> None:
    """Freshness is a type invariant, not a flag a caller could forget to read."""

    with pytest.raises(ValueError, match="match a fresh refill"):
        ZoneFillAuthority(
            source_revision=DIGEST,
            source_fill_digest=DIGEST,
            refilled_fill_digest=OTHER_DIGEST,
            kicad_version="10.0.5",
            fill_polygon_count=1,
            fill_vertex_count=4,
        )
    intact = ZoneFillAuthority(
        source_revision=DIGEST,
        source_fill_digest=DIGEST,
        refilled_fill_digest=DIGEST,
        kicad_version="10.0.5",
        fill_polygon_count=1,
        fill_vertex_count=4,
    )
    assert intact.to_dict()["kicad_version"] == "10.0.5"


def _workspace(tmp_path: Path, name: str) -> Settings:
    shutil.copy2(FIXTURES / f"{name}.kicad_pcb", tmp_path / f"{name}.kicad_pcb")
    return Settings(
        workspace=tmp_path,
        kicad_cli=REAL_KICAD_CLI,
        max_drc_report_bytes=8 * 1024 * 1024,
    )


@pytest.mark.skipif(not REAL_KICAD_CLI.is_file(), reason="KiCad CLI is not installed")
def test_real_kicad_confirms_a_fresh_cache_without_touching_the_workspace(
    tmp_path: Path,
) -> None:
    settings = _workspace(tmp_path, "zone-fill-fresh")
    board = tmp_path / "zone-fill-fresh.kicad_pcb"
    before = board.read_bytes()
    before_stat = board.stat()

    authority, islands = run_zone_fill_authority("zone-fill-fresh.kicad_pcb", settings)

    assert authority.source_fill_digest == authority.refilled_fill_digest
    assert authority.fill_polygon_count == 1
    assert authority.fill_vertex_count == 148
    assert authority.kicad_version.startswith("10.")
    assert len(islands) == 1
    # Refill only ever happens on the disposable copy.
    assert board.read_bytes() == before
    assert board.stat().st_mtime_ns == before_stat.st_mtime_ns


@pytest.mark.skipif(not REAL_KICAD_CLI.is_file(), reason="KiCad CLI is not installed")
def test_real_kicad_rejects_a_board_whose_cache_it_disagrees_with(tmp_path: Path) -> None:
    settings = _workspace(tmp_path, "zone-fill-stale")
    board = tmp_path / "zone-fill-stale.kicad_pcb"
    before = board.read_bytes()

    with pytest.raises(ZoneFillStaleError, match="does not match a fresh KiCad refill"):
        run_zone_fill_authority("zone-fill-stale.kicad_pcb", settings)

    assert board.read_bytes() == before


@pytest.mark.skipif(not REAL_KICAD_CLI.is_file(), reason="KiCad CLI is not installed")
def test_real_kicad_reports_one_node_per_island(tmp_path: Path) -> None:
    """KiCad 10.0.5 emits a separate filled_polygon per connected region, not keyhole seams."""

    settings = _workspace(tmp_path, "zone-fill-islands")

    authority, islands = run_zone_fill_authority("zone-fill-islands.kicad_pcb", settings)

    assert authority.fill_polygon_count == 2
    assert len(islands) == 2
    assert {island.layer_id for island in islands} == {"layer:F.Cu"}


def test_zone_fill_authority_requires_a_reachable_kicad(tmp_path: Path) -> None:
    _workspace(tmp_path, "zone-fill-fresh")

    with pytest.raises(KiCadCliError):
        run_zone_fill_authority(
            "zone-fill-fresh.kicad_pcb",
            Settings(workspace=tmp_path, kicad_cli=tmp_path / "absent-kicad-cli"),
        )
