from __future__ import annotations

import json
import shutil
from dataclasses import replace
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


@pytest.mark.parametrize(
    ("name", "vertices"),
    [("zone-fill-fresh", 148), ("zone-fill-islands", 186), ("zone-fill-stale", 148)],
)
def test_the_vertex_budget_binds_at_the_board_total_and_nowhere_else(
    name: str, vertices: int
) -> None:
    """The budget is a board-wide total, not a per-island one.

    ``zone-fill-islands`` is the case that separates them: 186 vertices across two islands, the
    larger of which is 94. A budget of 100 admits either island alone and must still refuse the
    board, because the cost being metered is every vertex materialised from the document rather
    than the widest ring in it.
    """

    source = (FIXTURES / f"{name}.kicad_pcb").read_bytes()

    for budget in (3, vertices // 2, vertices - 1):
        with pytest.raises(ZoneFillError) as refusal:
            read_fill_islands(source, max_vertices=budget)
        # The reader is called on the cached pour and on KiCad's recomputation of it and cannot
        # tell them apart, so it names neither. It also names the budget and never the count it
        # observed, which would be board content (ADR-0079).
        assert str(refusal.value) == "zone fill exceeds the configured vertex budget"
    assert sum(len(island.points) for island in read_fill_islands(source, max_vertices=vertices))


@pytest.mark.parametrize(
    ("name", "vertices"),
    [("zone-fill-fresh", 148), ("zone-fill-islands", 186), ("zone-fill-stale", 148)],
)
def test_raising_the_vertex_budget_can_only_turn_a_refusal_into_an_answer(
    name: str, vertices: int
) -> None:
    """Issue #165's safety obligation, discharged as a property of the budget itself.

    ``max_vertices`` reaches exactly one expression in this module -- the guard that aborts
    ``_points`` -- so it can decide *whether* a read happens and never *what* it returns. The
    consequence a caller cares about is that raising the budget is answer-preserving: every
    board that already read at 50,000 reads the same islands and digests identically at the
    calibrated default and at the environment ceiling, and the only outcome that can move is a
    budget refusal becoming a real answer.

    This deliberately does not argue from an equality between two runs with and without a
    change. The claim being pinned is stronger and one-directional: the refusal threshold is
    *exactly* the board's vertex total, and above it the value does not depend on the budget at
    all.
    """

    source = (FIXTURES / f"{name}.kicad_pcb").read_bytes()
    baseline = read_fill_islands(source, max_vertices=vertices)

    shipped_default = Settings(workspace=FIXTURES).max_fill_vertices
    for budget in (vertices, vertices + 1, 50_000, shipped_default, 1_000_000):
        admitted = read_fill_islands(source, max_vertices=budget)
        assert admitted == baseline
        assert fill_digest(admitted) == fill_digest(baseline)
        assert sum(len(island.points) for island in admitted) == vertices


def test_a_stale_fill_authority_record_cannot_be_constructed() -> None:
    """Freshness is a type invariant, not a flag a caller could forget to read."""

    with pytest.raises(ValueError, match="match a fresh refill"):
        ZoneFillAuthority(
            source_revision=DIGEST,
            context_revision=DIGEST,
            source_fill_digest=DIGEST,
            refilled_fill_digest=OTHER_DIGEST,
            kicad_version="10.0.5",
            fill_polygon_count=1,
            fill_vertex_count=4,
        )
    intact = ZoneFillAuthority(
        source_revision=DIGEST,
        context_revision=DIGEST,
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


@pytest.mark.skipif(not REAL_KICAD_CLI.is_file(), reason="KiCad CLI is not installed")
def test_the_authority_answer_is_the_same_at_every_budget_that_admits_the_board(
    tmp_path: Path,
) -> None:
    """Issue #165 end to end: the budget gates a refusal and decides nothing else.

    The whole path is run at a ladder of budgets that straddles the board's 148 vertices. Below
    it every run refuses as a *resource* problem and names the cached read; at or above it every
    run returns byte-identical evidence, including the refill KiCad recomputes each time. So the
    default this project ships chooses which boards get an answer, never which answer they get.
    """

    settings = _workspace(tmp_path, "zone-fill-fresh")
    board = tmp_path / "zone-fill-fresh.kicad_pcb"
    before = board.read_bytes()

    for budget in (3, 147):
        with pytest.raises(KiCadCliError, match="cached zone fill could not be read"):
            run_zone_fill_authority(
                "zone-fill-fresh.kicad_pcb", replace(settings, max_fill_vertices=budget)
            )

    answers = [
        run_zone_fill_authority(
            "zone-fill-fresh.kicad_pcb", replace(settings, max_fill_vertices=budget)
        )[0].to_dict()
        for budget in (148, 50_000, settings.max_fill_vertices, 1_000_000)
    ]

    assert all(answer == answers[0] for answer in answers)
    assert answers[0]["fill_vertex_count"] == 148
    assert board.read_bytes() == before


@pytest.mark.skipif(not REAL_KICAD_CLI.is_file(), reason="KiCad CLI is not installed")
def test_one_budget_meters_the_cached_pour_and_the_recomputed_one(tmp_path: Path) -> None:
    """`max_fill_vertices` is charged twice per proof, against two populations (#165).

    A *stale* board is by definition one whose cache and refill differ, so their sizes are free
    to differ too, and here they do: this fixture caches 148 vertices in one island and KiCad
    recomputes 186 across two. Every budget in [148, 185] therefore admits the operator's board
    and then refuses this server's own recomputation of it -- a resource refusal standing where
    the honest `stale_fill` answer belongs, about a document the operator never wrote and cannot
    inspect.

    The refusal must at least say which document ran out. It did not: the reader hardcoded
    "cached", so the refilled call site produced "refilled zone fill could not be read: cached
    zone fill exceeds the configured vertex budget".

    Calibration is what keeps the gap from binding in practice -- the shipped default is 3.8x
    the largest pour measured on real boards -- and that is a reason to size the number well, not
    a reason to believe one read is the same population as the other.
    """

    settings = _workspace(tmp_path, "zone-fill-stale")

    with pytest.raises(KiCadCliError, match=r"^cached zone fill could not be read") as cached:
        run_zone_fill_authority(
            "zone-fill-stale.kicad_pcb", replace(settings, max_fill_vertices=147)
        )
    assert "cached zone fill exceeds" not in str(cached.value)

    with pytest.raises(KiCadCliError, match=r"^refilled zone fill could not be read") as refilled:
        run_zone_fill_authority(
            "zone-fill-stale.kicad_pcb", replace(settings, max_fill_vertices=148)
        )
    assert "cached zone fill exceeds" not in str(refilled.value)

    for budget in (186, 50_000, settings.max_fill_vertices):
        with pytest.raises(ZoneFillStaleError):
            run_zone_fill_authority(
                "zone-fill-stale.kicad_pcb", replace(settings, max_fill_vertices=budget)
            )


def test_zone_fill_authority_requires_a_reachable_kicad(tmp_path: Path) -> None:
    _workspace(tmp_path, "zone-fill-fresh")

    with pytest.raises(KiCadCliError):
        run_zone_fill_authority(
            "zone-fill-fresh.kicad_pcb",
            Settings(workspace=tmp_path, kicad_cli=tmp_path / "absent-kicad-cli"),
        )


@pytest.mark.skipif(not REAL_KICAD_CLI.is_file(), reason="KiCad CLI is not installed")
def test_project_rules_are_part_of_the_freshness_argument(tmp_path: Path) -> None:
    """A pour computed under different clearances is a different pour.

    Fill depends on net-class clearances, which live in the project file beside the board.
    Refilling without that context recomputes a pour the board never had, so the comparison
    would be between two boards that were never the same board. Here the project contradicts
    the cached pour, and the authority must notice.
    """

    board = Path(__file__).parent.parent / "hardware" / "coppertone-buffer"
    shutil.copy2(board / "coppertone-buffer.kicad_pcb", tmp_path / "b.kicad_pcb")
    project = json.loads((board / "coppertone-buffer.kicad_pro").read_text(encoding="utf-8"))
    for net_class in project["net_settings"]["classes"]:
        net_class["clearance"] = 1.0
    (tmp_path / "b.kicad_pro").write_text(json.dumps(project), encoding="utf-8")
    settings = Settings(
        workspace=tmp_path,
        kicad_cli=REAL_KICAD_CLI,
        max_drc_report_bytes=8 * 1024 * 1024,
    )

    with pytest.raises(ZoneFillStaleError):
        run_zone_fill_authority("b.kicad_pcb", settings)


@pytest.mark.skipif(not REAL_KICAD_CLI.is_file(), reason="KiCad CLI is not installed")
def test_authority_records_the_context_it_was_computed_under(tmp_path: Path) -> None:
    board = Path(__file__).parent.parent / "hardware" / "coppertone-buffer"
    shutil.copy2(board / "coppertone-buffer.kicad_pcb", tmp_path / "b.kicad_pcb")
    shutil.copy2(board / "coppertone-buffer.kicad_pro", tmp_path / "b.kicad_pro")
    settings = Settings(
        workspace=tmp_path,
        kicad_cli=REAL_KICAD_CLI,
        max_drc_report_bytes=8 * 1024 * 1024,
    )

    authority, _ = run_zone_fill_authority("b.kicad_pcb", settings)

    assert authority.context_revision.startswith("sha256:")
    assert authority.context_revision != authority.source_revision
    assert authority.fill_polygon_count == 2
