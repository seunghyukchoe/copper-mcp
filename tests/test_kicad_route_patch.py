from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path

import pytest

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
    canonical_candidate_bytes,
)

FIXTURE = Path(__file__).parent / "fixtures" / "route-candidate" / "two-pad.kicad_pcb"
REAL_KICAD_CLI = Path("/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli")
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


def _snapshot_and_candidate() -> tuple[bytes, KiCadConstraintProfile, RouteCandidate]:
    source = FIXTURE.read_bytes()
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


def test_candidate_board_render_rejects_stale_tampered_and_non_replayed_inputs() -> None:
    source, profile, candidate = _snapshot_and_candidate()
    snapshot = parse_kicad_bytes(source, profile).snapshot
    assert snapshot is not None

    with pytest.raises(KiCadRoutePatchError, match="do not match the snapshot"):
        render_kicad_candidate_board(source + b"\n", snapshot, candidate, profile)

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
    with pytest.raises(KiCadRoutePatchError, match="deterministic router replay"):
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
    assert summary.base_revision == f"sha256:{hashlib.sha256(rendered).hexdigest()}"
    assert board.read_bytes() == rendered
    assert board.stat().st_mtime_ns == board_mtime
    assert FIXTURE.read_bytes() == source
    assert FIXTURE.stat().st_mtime_ns == source_mtime
