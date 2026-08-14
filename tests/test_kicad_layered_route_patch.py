from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path

import pytest

from copper_mcp.adapters import (
    KiCadConstraintProfile,
    KiCadLayeredRoutePatchError,
    parse_kicad_bytes,
    render_kicad_layered_candidate_board,
)
from copper_mcp.board_ir import NetClass, PointNM
from copper_mcp.routing import (
    LayeredAStarSettings,
    LayeredBoardRouter,
    LayeredRouteCandidate,
    LayeredRouteRequest,
    VerifiedFill,
    canonical_layered_candidate_bytes,
)

FIXTURE = Path(__file__).parent / "fixtures" / "route-candidate" / "blocked-pad.kicad_pcb"
F_CU = "layer:F.Cu"
B_CU = "layer:B.Cu"


def _profile() -> KiCadConstraintProfile:
    net_class = NetClass(
        id="class:default",
        name="Default",
        clearance_nm=250_000,
        track_width_nm=250_000,
        via_diameter_nm=800_000,
        via_drill_nm=400_000,
    )
    return KiCadConstraintProfile(net_classes=(net_class,), default_net_class_id=net_class.id)


def _candidate() -> tuple[
    bytes,
    KiCadConstraintProfile,
    object,
    LayeredRouteRequest,
    LayeredRouteCandidate,
]:
    source = FIXTURE.read_bytes()
    profile = _profile()
    conversion = parse_kicad_bytes(source, profile)
    assert conversion.diagnostics == ()
    assert conversion.snapshot is not None
    snapshot = conversion.snapshot
    pads = tuple(
        pad for pad in snapshot.content.pads if pad.net_id == snapshot.content.pads[0].net_id
    )
    request = LayeredRouteRequest(
        board_revision=snapshot.snapshot_digest,
        net_id=pads[0].net_id,
        start_pad_id=pads[0].id,
        end_pad_id=pads[1].id,
        start_layer_id=F_CU,
        end_layer_id=F_CU,
        grid_step_nm=1_000,
        settings=LayeredAStarSettings(via_cost=2),
    )
    result = LayeredBoardRouter().propose(snapshot, request)
    assert result.diagnostic is None
    assert result.candidate is not None
    return source, profile, snapshot, request, result.candidate


def test_layered_candidate_serializes_via_and_round_trips_without_mutation() -> None:
    source, profile, snapshot, request, candidate = _candidate()

    first = render_kicad_layered_candidate_board(
        source, snapshot, candidate, profile, request=request
    )
    second = render_kicad_layered_candidate_board(
        source, snapshot, candidate, profile, request=request
    )

    assert first == second
    assert source != first
    reparsed = parse_kicad_bytes(first, profile)
    assert reparsed.diagnostics == ()
    assert reparsed.snapshot is not None
    assert reparsed.snapshot.content.source.generator == "copper-mcp"
    added_segments = tuple(
        item
        for item in reparsed.snapshot.content.segments
        if item.id not in {s.id for s in snapshot.content.segments}
    )
    added_vias = tuple(
        item
        for item in reparsed.snapshot.content.vias
        if item.id not in {v.id for v in snapshot.content.vias}
    )
    assert {item.layer_id for item in added_segments} == {F_CU, B_CU}
    # Endpoint-via transitions are intentionally excluded from the candidate contract.  The
    # blocked-pad route therefore returns to F.Cu before the final SMD pad, yielding three
    # serialized segments for two through-vias.
    assert len(added_segments) == len(candidate.patch.paths)
    assert len(added_segments) == 3
    assert len(added_vias) == candidate.cost.via_count
    assert all((via.start_layer_id, via.end_layer_id) == (F_CU, B_CU) for via in added_vias)


def test_layered_serializer_rejects_tampered_identity_and_stale_revision() -> None:
    source, profile, snapshot, request, candidate = _candidate()
    tampered = replace(candidate, patch=replace(candidate.patch, width_nm=250_001))
    with pytest.raises(KiCadLayeredRoutePatchError, match="identity"):
        render_kicad_layered_candidate_board(source, snapshot, tampered, profile, request=request)

    stale = replace(candidate, base_revision=f"sha256:{'1' * 64}")
    stale = replace(
        stale,
        candidate_id=f"sha256:{hashlib.sha256(canonical_layered_candidate_bytes(stale)).hexdigest()}",
    )
    with pytest.raises(KiCadLayeredRoutePatchError, match="stale"):
        render_kicad_layered_candidate_board(source, snapshot, stale, profile, request=request)


# --- The serialization boundary replays under the model that produced the candidate ------------

ZONE_FIXTURE = Path(__file__).parent / "fixtures" / "route-candidate" / "blocked-zone.kicad_pcb"


def _zone_candidates() -> tuple[
    bytes,
    KiCadConstraintProfile,
    object,
    LayeredRouteRequest,
    LayeredRouteCandidate,
    LayeredRouteRequest,
    LayeredRouteCandidate,
]:
    """One board, two candidates: one routed under the envelope, one under the exact pour."""

    source = ZONE_FIXTURE.read_bytes()
    profile = _profile()
    conversion = parse_kicad_bytes(source, profile)
    assert conversion.diagnostics == ()
    assert conversion.snapshot is not None
    snapshot = conversion.snapshot
    zone = snapshot.content.zones[0]
    pads = snapshot.content.pads
    envelope_request = LayeredRouteRequest(
        board_revision=snapshot.snapshot_digest,
        net_id=pads[0].net_id,
        start_pad_id=pads[0].id,
        end_pad_id=pads[1].id,
        start_layer_id=F_CU,
        end_layer_id=F_CU,
        grid_step_nm=500_000,
        settings=LayeredAStarSettings(via_cost=2),
    )
    island = VerifiedFill(
        net_id=zone.net_id,
        layer_id=F_CU,
        points=(
            PointNM(18_000_000, 11_000_000),
            PointNM(22_000_000, 11_000_000),
            PointNM(22_000_000, 14_000_000),
            PointNM(18_000_000, 14_000_000),
        ),
        source_revision=snapshot.content.source.revision,
    )
    fill_request = replace(envelope_request, verified_fill=(island,))
    envelope = LayeredBoardRouter().propose(snapshot, envelope_request)
    fill_routed = LayeredBoardRouter().propose(snapshot, fill_request)
    assert envelope.candidate is not None
    assert fill_routed.candidate is not None
    return (
        source,
        profile,
        snapshot,
        envelope_request,
        envelope.candidate,
        fill_request,
        fill_routed.candidate,
    )


def test_a_fill_routed_layered_candidate_serializes_when_its_own_fill_is_supplied() -> None:
    source, profile, snapshot, _, envelope, fill_request, fill_routed = _zone_candidates()

    rendered = render_kicad_layered_candidate_board(
        source, snapshot, fill_routed, profile, request=fill_request
    )

    assert rendered != source
    # Not vacuous: the pour genuinely changes the route, so this is not the envelope candidate
    # being re-serialized under a different name.
    assert fill_routed.fill_binding is not None
    assert envelope.fill_binding is None
    assert fill_routed.patch != envelope.patch


def test_serializing_a_fill_routed_layered_candidate_without_its_fill_names_the_evidence() -> None:
    """The understated direction, reported under its own message rather than as a bad candidate."""

    source, profile, snapshot, envelope_request, _, _, fill_routed = _zone_candidates()

    with pytest.raises(KiCadLayeredRoutePatchError, match="was not supplied for replay"):
        render_kicad_layered_candidate_board(
            source, snapshot, fill_routed, profile, request=envelope_request
        )


def test_serializing_an_envelope_layered_candidate_with_fill_it_never_saw_refuses() -> None:
    """The overstated direction: fill that retires envelopes this candidate's search never lost."""

    source, profile, snapshot, _, envelope, fill_request, _ = _zone_candidates()

    with pytest.raises(KiCadLayeredRoutePatchError, match="was not supplied for replay"):
        render_kicad_layered_candidate_board(
            source, snapshot, envelope, profile, request=fill_request
        )
