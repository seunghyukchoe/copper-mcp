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
    assert patched.snapshot.content.source.generator == "copper-mcp"
    assert b'(generator "copper-mcp")' in first
    assert f'(generator_version "{__version__}")'.encode() in first


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


def test_candidate_board_render_enforces_total_object_budget() -> None:
    source, profile, candidate = _snapshot_and_candidate()
    limits = replace(ParseLimits(), max_objects=8)
    base_over_budget = parse_kicad_bytes(
        source,
        profile,
        replace(limits, max_objects=7),
    )
    assert base_over_budget.snapshot is None
    assert tuple(item.code for item in base_over_budget.diagnostics) == ("budget.exceeded",)

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
