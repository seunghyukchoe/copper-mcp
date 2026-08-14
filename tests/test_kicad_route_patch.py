from __future__ import annotations

import hashlib
import shutil
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest

import copper_mcp.adapters.kicad_route_patch as route_patch
from copper_mcp import __version__
from copper_mcp.adapters import (
    KiCadConstraintProfile,
    KiCadRoutePatchError,
    net_id_for_name,
    parse_kicad_bytes,
    render_kicad_candidate_board,
)
from copper_mcp.board_ir import NetClass, ParseLimits, PointNM
from copper_mcp.config import Settings
from copper_mcp.kicad_cli import run_board_drc
from copper_mcp.routing import (
    AStarRouter,
    RouteCandidate,
    RouteRequest,
    VerifiedFill,
    canonical_candidate_bytes,
)

FIXTURE = Path(__file__).parent / "fixtures" / "route-candidate" / "two-pad.kicad_pcb"
_DISCOVERED_KICAD_CLI = shutil.which("kicad-cli")
REAL_KICAD_CLI = (
    Path(_DISCOVERED_KICAD_CLI)
    if _DISCOVERED_KICAD_CLI is not None
    else Path("/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli")
)
EMPTY_DIGEST = f"sha256:{'0' * 64}"


def _profile() -> KiCadConstraintProfile:
    net_class = NetClass(
        id="class:default",
        name="Default",
        clearance_nm=250_000,
        track_width_nm=250_000,
        via_diameter_nm=800_000,
        via_drill_nm=400_000,
    )
    return KiCadConstraintProfile(
        net_classes=(net_class,),
        default_net_class_id=net_class.id,
    )


def _snapshot_and_candidate(
    source: bytes | None = None,
) -> tuple[bytes, KiCadConstraintProfile, RouteCandidate]:
    source = FIXTURE.read_bytes() if source is None else source
    profile = _profile()
    conversion = parse_kicad_bytes(source, profile)
    assert conversion.diagnostics == ()
    assert conversion.snapshot is not None
    snapshot = conversion.snapshot
    result = AStarRouter().propose(
        snapshot,
        RouteRequest(
            board_revision=snapshot.snapshot_digest,
            net_id=net_id_for_name("AUDIO"),
            layer_id="layer:F.Cu",
            seed=23,
        ),
    )
    assert result.diagnostic is None
    assert result.candidate is not None
    return source, profile, result.candidate


def test_a_route_splice_preserves_a_thermal_bridge_angle_token_byte_exactly() -> None:
    """The unmodelled spoke angle remains authoritative in the source board after routing."""

    token = b"      (thermal_bridge_angle 405.5)\n"
    source = FIXTURE.read_bytes().replace(b'      (net "AUDIO")', token + b'      (net "AUDIO")', 1)
    source, profile, candidate = _snapshot_and_candidate(source)
    conversion = parse_kicad_bytes(source, profile)
    assert conversion.snapshot is not None

    rendered = render_kicad_candidate_board(source, conversion.snapshot, candidate, profile)

    assert rendered != source
    assert rendered.count(token) == 1
    patched = parse_kicad_bytes(rendered, profile)
    assert patched.snapshot is not None
    assert patched.unmodelled_thermal_bridge_angle_pad_count == 1


def _rehash(candidate: RouteCandidate) -> RouteCandidate:
    digest = f"sha256:{hashlib.sha256(canonical_candidate_bytes(candidate)).hexdigest()}"
    return replace(candidate, candidate_id=digest)


def _with_back_copper(source: bytes) -> bytes:
    closing = source.rfind(b"\n)")
    assert closing > 0
    segment = b"""
  (segment
    (start 5 5)
    (end 6 5)
    (width 0.25)
    (layer "B.Cu")
    (net "AUDIO")
    (uuid "ffffffff-ffff-ffff-ffff-ffffffffffff")
  )"""
    return source[:closing] + segment + source[closing:]


def _three_pad_source() -> bytes:
    third_footprint = b"""  (footprint "CopperMCP_RoutePad"
    (layer "F.Cu")
    (uuid "20000000-0000-0000-0000-000000000006")
    (at 20 22 0)
    (pad "1" smd rect
      (at 0 0 0)
      (size 2 2)
      (layers "F.Cu" "F.Mask" "F.Paste")
      (net "AUDIO")
      (uuid "20000000-0000-0000-0000-000000000007")
    )
  )
"""
    return FIXTURE.read_bytes().replace(b"  (gr_rect", third_footprint + b"  (gr_rect")


def _pre_batched_candidate() -> tuple[bytes, KiCadConstraintProfile, RouteCandidate]:
    source = _three_pad_source()
    profile = _profile()
    conversion = parse_kicad_bytes(source, profile)
    assert conversion.snapshot is not None
    assert conversion.diagnostics == ()
    snapshot = conversion.snapshot
    result = AStarRouter.for_replay(
        router_version="astar-grid/0.4.0",
        policy="orthogonal-a-star-v1",
        ordering_policy="component-mst-v1",
        pad_count=3,
    ).propose(
        snapshot,
        RouteRequest(
            board_revision=snapshot.snapshot_digest,
            net_id=net_id_for_name("AUDIO"),
            layer_id="layer:F.Cu",
            seed=23,
        ),
    )
    assert result.candidate is not None
    return source, profile, result.candidate


def test_candidate_board_render_is_deterministic_read_only_and_round_trips() -> None:
    source, profile, candidate = _snapshot_and_candidate()
    snapshot = parse_kicad_bytes(source, profile).snapshot
    assert snapshot is not None
    before_mtime = FIXTURE.stat().st_mtime_ns

    first = render_kicad_candidate_board(source, snapshot, candidate, profile)
    second = render_kicad_candidate_board(source, snapshot, candidate, profile)

    assert first == second
    assert source == FIXTURE.read_bytes()
    assert FIXTURE.stat().st_mtime_ns == before_mtime
    assert first != source
    patched = parse_kicad_bytes(first, profile)
    assert patched.diagnostics == ()
    assert patched.snapshot is not None
    assert len(patched.snapshot.content.segments) == 1
    segment = patched.snapshot.content.segments[0]
    assert segment.start == PointNM(10_000_000, 15_000_000)
    assert segment.end == PointNM(30_000_000, 15_000_000)
    assert segment.width_nm == 250_000
    assert segment.net_id == net_id_for_name("AUDIO")
    assert segment.layer_id == "layer:F.Cu"
    assert segment.id.startswith("segment:kicad:")
    assert patched.snapshot.content.source.revision != snapshot.content.source.revision
    assert patched.snapshot.content.source.generator == "copper-mcp"
    assert b'(generator "copper-mcp")' in first
    assert f'(generator_version "{__version__}")'.encode() in first


def test_pre_batched_multi_pin_candidate_replays_and_renders_byte_deterministically() -> None:
    source, profile, candidate = _pre_batched_candidate()
    snapshot = parse_kicad_bytes(source, profile).snapshot
    assert snapshot is not None
    original_identity = canonical_candidate_bytes(candidate)

    first = render_kicad_candidate_board(source, snapshot, candidate, profile)
    second = render_kicad_candidate_board(source, snapshot, candidate, profile)

    assert candidate.router_version == "astar-grid/0.4.0"
    assert candidate.ordering_policy == "component-mst-v1"
    assert canonical_candidate_bytes(candidate) == original_identity
    assert first == second
    patched = parse_kicad_bytes(first, profile)
    assert patched.snapshot is not None
    assert patched.diagnostics == ()
    assert len(patched.snapshot.content.segments) == sum(
        len(path.vertices) - 1 for path in candidate.patch.paths
    )


@pytest.mark.parametrize(
    ("field", "value"),
    (("router_version", "unknown-router-v1"), ("ordering_policy", "batched-1-steiner-v1")),
)
def test_render_refuses_unknown_or_invalid_historical_replay_dispatch(
    field: str, value: str
) -> None:
    source, profile, candidate = _pre_batched_candidate()
    snapshot = parse_kicad_bytes(source, profile).snapshot
    assert snapshot is not None
    altered = _rehash(replace(candidate, candidate_id=EMPTY_DIGEST, **{field: value}))

    with pytest.raises(KiCadRoutePatchError, match="router compatibility is unsupported"):
        render_kicad_candidate_board(source, snapshot, altered, profile)


def test_round_trip_preserves_canonical_order_with_existing_back_copper() -> None:
    source = _with_back_copper(FIXTURE.read_bytes())
    profile = _profile()
    conversion = parse_kicad_bytes(source, profile)
    assert conversion.snapshot is not None
    assert conversion.diagnostics == ()
    snapshot = conversion.snapshot
    result = AStarRouter().propose(
        snapshot,
        RouteRequest(
            board_revision=snapshot.snapshot_digest,
            net_id=net_id_for_name("AUDIO"),
            layer_id="layer:F.Cu",
            seed=23,
        ),
    )
    assert result.candidate is not None

    rendered = render_kicad_candidate_board(
        source,
        snapshot,
        result.candidate,
        profile,
    )
    patched = parse_kicad_bytes(rendered, profile)

    assert patched.snapshot is not None
    assert patched.diagnostics == ()
    assert len(patched.snapshot.content.segments) == 2
    assert {segment.layer_id for segment in patched.snapshot.content.segments} == {
        "layer:F.Cu",
        "layer:B.Cu",
    }
    segment_ids = [segment.id for segment in patched.snapshot.content.segments]
    assert segment_ids == sorted(segment_ids)


@pytest.mark.parametrize("source_generator", ["pcbnew", None])
def test_render_rewrites_or_inserts_third_party_writer_metadata(
    source_generator: str | None,
) -> None:
    source = FIXTURE.read_bytes().replace(b'  (generator_version "0.1.0")\n', b"")
    source = source.replace(
        b'  (generator "copper-mcp")\n',
        f'  (generator "{source_generator}")\n'.encode() if source_generator is not None else b"",
    )
    profile = _profile()
    conversion = parse_kicad_bytes(source, profile)
    assert conversion.snapshot is not None
    assert conversion.diagnostics == ()
    snapshot = conversion.snapshot
    result = AStarRouter().propose(
        snapshot,
        RouteRequest(
            board_revision=snapshot.snapshot_digest,
            net_id=net_id_for_name("AUDIO"),
            layer_id="layer:F.Cu",
            seed=23,
        ),
    )
    assert result.candidate is not None

    rendered = render_kicad_candidate_board(source, snapshot, result.candidate, profile)
    patched = parse_kicad_bytes(rendered, profile)

    if source_generator is not None:
        assert f'(generator "{source_generator}")'.encode() not in rendered
    assert rendered.count(b'(generator "copper-mcp")') == 1
    assert rendered.count(f'(generator_version "{__version__}")'.encode()) == 1
    assert patched.snapshot is not None
    assert patched.diagnostics == ()
    assert patched.snapshot.content.source.generator == "copper-mcp"


def test_quoted_net_name_round_trips_without_expression_injection() -> None:
    net_name = 'A"UDIO\\LAB'
    source = FIXTURE.read_bytes().replace(
        b'(net "AUDIO")',
        rb'(net "A\"UDIO\\LAB")',
    )
    profile = _profile()
    conversion = parse_kicad_bytes(source, profile)
    assert conversion.snapshot is not None
    assert conversion.diagnostics == ()
    snapshot = conversion.snapshot
    result = AStarRouter().propose(
        snapshot,
        RouteRequest(
            board_revision=snapshot.snapshot_digest,
            net_id=net_id_for_name(net_name),
            layer_id="layer:F.Cu",
            seed=23,
        ),
    )
    assert result.candidate is not None

    rendered = render_kicad_candidate_board(
        source,
        snapshot,
        result.candidate,
        profile,
    )
    patched = parse_kicad_bytes(rendered, profile)

    assert rb'(net "A\"UDIO\\LAB")' in rendered
    assert patched.snapshot is not None
    assert patched.diagnostics == ()
    assert patched.snapshot.content.segments[0].net_id == net_id_for_name(net_name)


def test_render_rejects_revision_derived_geometry_identities() -> None:
    source = (
        b"\n".join(line for line in FIXTURE.read_bytes().splitlines() if b"(uuid " not in line)
        + b"\n"
    )
    profile = _profile()
    conversion = parse_kicad_bytes(source, profile)
    assert conversion.snapshot is not None
    assert conversion.diagnostics == ()
    snapshot = conversion.snapshot
    result = AStarRouter().propose(
        snapshot,
        RouteRequest(
            board_revision=snapshot.snapshot_digest,
            net_id=net_id_for_name("AUDIO"),
            layer_id="layer:F.Cu",
            seed=23,
        ),
    )
    assert result.candidate is not None

    with pytest.raises(KiCadRoutePatchError, match="native uuid or tstamp"):
        render_kicad_candidate_board(source, snapshot, result.candidate, profile)


def test_candidate_board_render_rejects_stale_tampered_and_non_replayed_inputs() -> None:
    source, profile, candidate = _snapshot_and_candidate()
    snapshot = parse_kicad_bytes(source, profile).snapshot
    assert snapshot is not None

    with pytest.raises(KiCadRoutePatchError, match="do not match the snapshot"):
        render_kicad_candidate_board(source + b"\n", snapshot, candidate, profile)

    with pytest.raises(KiCadRoutePatchError, match="candidate is stale"):
        render_kicad_candidate_board(
            source,
            snapshot,
            replace(candidate, base_revision=EMPTY_DIGEST),
            profile,
        )

    with pytest.raises(KiCadRoutePatchError, match="identity verification failed"):
        render_kicad_candidate_board(
            source,
            snapshot,
            replace(candidate, candidate_id=EMPTY_DIGEST),
            profile,
        )

    forged = _rehash(
        replace(
            candidate,
            candidate_id=EMPTY_DIGEST,
            router_version="forged-router-v1",
        )
    )
    with pytest.raises(KiCadRoutePatchError, match="router compatibility is unsupported"):
        render_kicad_candidate_board(source, snapshot, forged, profile)


def test_candidate_board_render_enforces_output_budget_before_round_trip() -> None:
    source, profile, candidate = _snapshot_and_candidate()
    snapshot = parse_kicad_bytes(source, profile).snapshot
    assert snapshot is not None
    limits = replace(ParseLimits(), max_input_bytes=len(source) + 1)

    with pytest.raises(KiCadRoutePatchError, match="input-byte budget"):
        render_kicad_candidate_board(
            source,
            snapshot,
            candidate,
            profile,
            limits=limits,
        )


def test_candidate_board_render_enforces_total_object_budget() -> None:
    source, profile, candidate = _snapshot_and_candidate()
    limits = replace(ParseLimits(), max_objects=10)
    base_over_budget = parse_kicad_bytes(
        source,
        profile,
        replace(limits, max_objects=9),
    )
    assert base_over_budget.snapshot is None
    assert tuple(item.code for item in base_over_budget.diagnostics) == ("budget.exceeded.objects",)

    conversion = parse_kicad_bytes(source, profile, limits)
    assert conversion.snapshot is not None
    assert conversion.diagnostics == ()

    with pytest.raises(KiCadRoutePatchError, match="object budget"):
        render_kicad_candidate_board(
            source,
            conversion.snapshot,
            candidate,
            profile,
            limits=limits,
        )


def test_native_identity_scan_is_precomputed_and_rejects_uuid_collision() -> None:
    source, profile, candidate = _snapshot_and_candidate()
    snapshot = parse_kicad_bytes(source, profile).snapshot
    assert snapshot is not None

    _, normalized_identities = route_patch._source_structure(
        b'(root (tstamp "ABCDEF") (uuid "01234567-89AB-CDEF-0123-456789ABCDEF"))',
        ParseLimits(),
    )
    assert normalized_identities == {
        "abcdef",
        "01234567-89ab-cdef-0123-456789abcdef",
    }

    with patch.object(
        route_patch,
        "_source_structure",
        wraps=route_patch._source_structure,
    ) as identity_scan:
        render_kicad_candidate_board(source, snapshot, candidate, profile)
    assert identity_scan.call_count == 1

    with patch.object(
        route_patch,
        "_segment_uuid",
        return_value="20000000-0000-0000-0000-000000000001",
    ):
        with pytest.raises(KiCadRoutePatchError, match="identity collides"):
            render_kicad_candidate_board(source, snapshot, candidate, profile)


@pytest.mark.skipif(not REAL_KICAD_CLI.is_file(), reason="KiCad CLI is not installed")
def test_real_kicad_drc_accepts_disposable_candidate_without_source_mutation(
    tmp_path: Path,
) -> None:
    source, profile, candidate = _snapshot_and_candidate()
    snapshot = parse_kicad_bytes(source, profile).snapshot
    assert snapshot is not None
    source_mtime = FIXTURE.stat().st_mtime_ns
    rendered = render_kicad_candidate_board(source, snapshot, candidate, profile)
    board = tmp_path / "two-pad-candidate.kicad_pcb"
    board.write_bytes(rendered)
    board_mtime = board.stat().st_mtime_ns

    summary = run_board_drc(
        board.name,
        Settings(workspace=tmp_path, kicad_cli=REAL_KICAD_CLI),
    )

    assert summary.passed
    assert summary.error_count == 0
    assert summary.warning_count == 0
    assert summary.unconnected_count == 0
    assert summary.kicad_version.startswith("10.")
    assert summary.base_revision == f"sha256:{hashlib.sha256(rendered).hexdigest()}"
    assert board.read_bytes() == rendered
    assert board.stat().st_mtime_ns == board_mtime
    assert FIXTURE.read_bytes() == source
    assert FIXTURE.stat().st_mtime_ns == source_mtime


SEGMENT_OUTLINE_FIXTURE = (
    Path(__file__).parent / "fixtures" / "route-candidate" / "two-pad-segment-outline.kicad_pcb"
)


def test_candidate_board_renders_on_an_assembled_gr_line_outline() -> None:
    """Issue #126 on the route side: an assembled outline no longer refuses the render.

    This fixture is ``two-pad.kicad_pcb`` with its one ``gr_rect`` outline drawn the way every
    surveyed real board draws it -- four ``gr_line`` segments -- each carrying its own uuid, so
    the contour takes the ADR-0087 composite identity.  The render must succeed, leave every
    outline byte untouched, and round-trip: the appended segment is the only geometry change
    and the contour's name does not move, which is exactly what the revision-derived name
    could not do.
    """

    source = SEGMENT_OUTLINE_FIXTURE.read_bytes()
    profile = _profile()
    conversion = parse_kicad_bytes(source, profile)
    assert conversion.diagnostics == ()
    assert conversion.snapshot is not None
    snapshot = conversion.snapshot
    assert snapshot.content.outline[0].id.startswith("contour:assembled:")
    result = AStarRouter().propose(
        snapshot,
        RouteRequest(
            board_revision=snapshot.snapshot_digest,
            net_id=net_id_for_name("AUDIO"),
            layer_id="layer:F.Cu",
            seed=23,
        ),
    )
    assert result.candidate is not None

    rendered = render_kicad_candidate_board(source, snapshot, result.candidate, profile)

    assert rendered != source
    assert rendered.count(b"gr_line") == source.count(b"gr_line")
    patched = parse_kicad_bytes(rendered, profile)
    assert patched.diagnostics == ()
    assert patched.snapshot is not None
    assert len(patched.snapshot.content.segments) == 1
    assert patched.snapshot.content.outline[0].id == snapshot.content.outline[0].id
    assert SEGMENT_OUTLINE_FIXTURE.read_bytes() == source


def test_assembled_outline_render_still_refuses_a_missing_member_identity() -> None:
    """Mutation check: remove one member uuid and the whole board must stay unappliable."""

    source = (
        b"\n".join(
            line
            for line in SEGMENT_OUTLINE_FIXTURE.read_bytes().splitlines()
            if b'uuid "20000000-0000-0000-0000-000000000013"' not in line
        )
        + b"\n"
    )
    profile = _profile()
    conversion = parse_kicad_bytes(source, profile)
    assert conversion.diagnostics == ()
    assert conversion.snapshot is not None
    snapshot = conversion.snapshot
    assert ":derived:" in snapshot.content.outline[0].id
    result = AStarRouter().propose(
        snapshot,
        RouteRequest(
            board_revision=snapshot.snapshot_digest,
            net_id=net_id_for_name("AUDIO"),
            layer_id="layer:F.Cu",
            seed=23,
        ),
    )
    assert result.candidate is not None

    with pytest.raises(KiCadRoutePatchError, match="native uuid or tstamp"):
        render_kicad_candidate_board(source, snapshot, result.candidate, profile)


# ---------------------------------------------------------------------------
# The serialization boundary replays under the model that produced the candidate
# (ADR-0103, issue #163)
# ---------------------------------------------------------------------------

BLOCKED_ZONE = FIXTURE.parent / "blocked-zone.kicad_pcb"


def _fill_routed_board() -> tuple[bytes, KiCadConstraintProfile, RouteCandidate, VerifiedFill]:
    """One real KiCad board where the exact pour routes a candidate the envelope cannot."""

    source = BLOCKED_ZONE.read_bytes()
    profile = _profile()
    conversion = parse_kicad_bytes(source, profile)
    assert conversion.diagnostics == ()
    assert conversion.snapshot is not None
    snapshot = conversion.snapshot
    # The POWER zone spans x=18..22 mm, y=11..19 mm. This pour is its upper half, leaving the
    # straight y=15 mm corridor the conservative outline blocks.
    island = VerifiedFill(
        net_id=net_id_for_name("POWER"),
        layer_id="layer:F.Cu",
        points=(
            PointNM(18_000_000, 11_000_000),
            PointNM(22_000_000, 11_000_000),
            PointNM(22_000_000, 14_000_000),
            PointNM(18_000_000, 14_000_000),
        ),
        source_revision=snapshot.content.source.revision,
    )
    request = RouteRequest(
        board_revision=snapshot.snapshot_digest,
        net_id=net_id_for_name("AUDIO"),
        layer_id="layer:F.Cu",
        seed=23,
    )
    result = AStarRouter().propose(snapshot, request, verified_fill=(island,))
    assert result.diagnostic is None
    assert result.candidate is not None
    return source, profile, result.candidate, island


def test_a_fill_routed_candidate_serializes_when_its_own_fill_is_supplied() -> None:
    """`preview_route(include_fill_authority=True, include_drc=True)` refused this candidate.

    `render_kicad_candidate_board` is the function candidate DRC calls, and `_replay_candidate`
    inside it is the chokepoint that used to search the envelope model regardless.
    """

    source, profile, candidate, island = _fill_routed_board()
    conversion = parse_kicad_bytes(source, profile)
    assert conversion.snapshot is not None

    rendered = render_kicad_candidate_board(
        source,
        conversion.snapshot,
        candidate,
        profile,
        verified_fill=(island,),
    )

    assert candidate.fill_binding is not None
    assert rendered != source
    assert b"(segment" in rendered


def test_serializing_a_fill_routed_candidate_without_its_fill_names_the_missing_evidence() -> None:
    """The refusal is still a refusal, but it stops blaming the candidate for it."""

    source, profile, candidate, _ = _fill_routed_board()
    conversion = parse_kicad_bytes(source, profile)
    assert conversion.snapshot is not None

    with pytest.raises(KiCadRoutePatchError, match="was not supplied for replay"):
        render_kicad_candidate_board(source, conversion.snapshot, candidate, profile)


def test_serializing_an_envelope_candidate_with_fill_it_never_saw_refuses() -> None:
    """The dangerous direction, at the boundary a caller actually reaches.

    A verifier holding fresh fill must not use it to re-verify a candidate routed without it:
    the pour is the looser model, so that check is weaker than the search that produced the
    route.
    """

    source, profile, _, island = _fill_routed_board()
    conversion = parse_kicad_bytes(source, profile)
    assert conversion.snapshot is not None
    snapshot = conversion.snapshot
    envelope = (
        AStarRouter()
        .propose(
            snapshot,
            RouteRequest(
                board_revision=snapshot.snapshot_digest,
                net_id=net_id_for_name("AUDIO"),
                layer_id="layer:F.Cu",
                seed=23,
            ),
        )
        .candidate
    )
    assert envelope is not None
    assert envelope.fill_binding is None

    with pytest.raises(KiCadRoutePatchError, match="was not supplied for replay"):
        render_kicad_candidate_board(
            source,
            snapshot,
            envelope,
            profile,
            verified_fill=(island,),
        )
