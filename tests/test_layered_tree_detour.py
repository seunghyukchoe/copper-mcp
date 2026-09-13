"""A same-layer endpoint pair may still need a different routing layer."""

from test_layered_tree_profile_bounds import _ordinary_source
from test_layered_tree_router import _changed_snapshot, _profile

from copper_mcp.adapters.kicad_board_ir import parse_kicad_bytes
from copper_mcp.adapters.kicad_layered_tree_patch import (
    render_kicad_layered_tree_candidate_board,
)
from copper_mcp.board_ir import Keepout, PointNM, Ring, Segment, Via, ViaKind
from copper_mcp.routing.layered_astar import LayeredAStarSettings
from copper_mcp.routing.layered_tree_contracts import LayeredTreeTerminal
from copper_mcp.routing.layered_tree_router import LayeredTreeRequest, LayeredTreeRouter
from copper_mcp.routing.layered_tree_verifier import (
    verify_layered_tree_candidate,
    verify_reparsed_layered_tree_connectivity,
)


def _source_contact_case(layers: tuple[str, str, str], xs: tuple[int, int, int]) -> bytes:
    footprints = []
    for index, (side, x) in enumerate(zip(layers, xs, strict=True)):
        footprints.append(
            f'''(footprint "SourceContactPad" (layer "{side}.Cu")
              (uuid "80000000-0000-0000-0000-{2 * index + 1:012x}") (at {x} 10)
              (pad "1" smd rect (at 0 0) (size 1 1)
                (layers "{side}.Cu" "{side}.Mask" "{side}.Paste") (net "TREE")
                (uuid "80000000-0000-0000-0000-{2 * index + 2:012x}")))'''
        )
    return (
        '(kicad_pcb (version 20260206) (generator "copper-mcp") '
        '(generator_version "0.1.0") (layers (0 "F.Cu" signal) '
        '(2 "B.Cu" signal) (25 "Edge.Cuts" user)) '
        + " ".join(footprints)
        + " (gr_rect (start 0 0) (end 24 20) (stroke (width 0.1) (type default)) "
        '(fill no) (layer "Edge.Cuts") '
        '(uuid "80000000-0000-0000-0000-000000000100")))'
    ).encode("ascii")


def _route_source_contact(source: bytes):
    profile = _profile()
    parsed = parse_kicad_bytes(source, profile)
    assert parsed.snapshot is not None and not parsed.diagnostics
    snapshot = parsed.snapshot
    pads = tuple(sorted(snapshot.content.pads, key=lambda pad: pad.id))
    request = LayeredTreeRequest(
        snapshot.snapshot_digest,
        pads[0].net_id,
        tuple(LayeredTreeTerminal(pad.id, pad.layer_ids[0]) for pad in pads),
        grid_step_nm=1_000_000,
        settings=LayeredAStarSettings(max_vias=2),
    )
    return profile, snapshot, pads, request, LayeredTreeRouter().propose(snapshot, request)


def test_front_only_terminals_can_detour_with_remaining_via_budget() -> None:
    source = (
        _ordinary_source(2, 3)
        .replace(b"(at 16 10)", b"(at 24 10)")
        .replace(b"(at 12 10)", b"(at 16 10)")
        .replace(b'(layer "B.Cu")', b'(layer "F.Cu")')
        .replace(b'(layers "B.Cu" "B.Mask" "B.Paste")', b'(layers "F.Cu" "F.Mask" "F.Paste")')
    )
    parsed = parse_kicad_bytes(source, _profile())
    assert parsed.snapshot is not None and not parsed.diagnostics
    barrier = Keepout(
        "keepout:front-barrier",
        ("layer:F.Cu",),
        Ring(
            (
                PointNM(10_000_000, -1_000_000),
                PointNM(11_000_000, -1_000_000),
                PointNM(11_000_000, 21_000_000),
                PointNM(10_000_000, 21_000_000),
            )
        ),
        True,
        False,
        False,
        False,
        False,
    )
    snapshot = _changed_snapshot(parsed.snapshot, keepouts=(barrier,))
    pads = tuple(sorted(snapshot.content.pads, key=lambda pad: pad.id))
    request = LayeredTreeRequest(
        snapshot.snapshot_digest,
        pads[0].net_id,
        tuple(LayeredTreeTerminal(pad.id, "layer:F.Cu") for pad in pads),
        grid_step_nm=1_000_000,
        settings=LayeredAStarSettings(max_vias=2),
    )
    result = LayeredTreeRouter().propose(snapshot, request)
    assert result.candidate is not None, result.diagnostic
    assert result.candidate.metrics.vias == 2
    assert any(
        path.layer_id == "layer:B.Cu"
        for branch in result.candidate.branches
        for path in branch.paths
    )
    assert verify_layered_tree_candidate(result.candidate, snapshot, expected_request=request).ok


def test_xy_equal_pads_on_different_layers_do_not_claim_attachment() -> None:
    source = _ordinary_source(2, 3).replace(b"(at 12 10)", b"(at 8 10)")
    parsed = parse_kicad_bytes(source, _profile())
    assert parsed.snapshot is not None and not parsed.diagnostics
    snapshot = parsed.snapshot
    pads = tuple(sorted(snapshot.content.pads, key=lambda pad: pad.id))
    request = LayeredTreeRequest(
        snapshot.snapshot_digest,
        pads[0].net_id,
        tuple(LayeredTreeTerminal(pad.id, pad.layer_ids[0]) for pad in pads),
        grid_step_nm=1_000_000,
        settings=LayeredAStarSettings(max_vias=2),
    )
    result = LayeredTreeRouter().propose(snapshot, request)
    assert result.candidate is not None, result.diagnostic
    assert not result.candidate.branches[0].attachment_only
    assert result.candidate.branches[0].vias
    assert verify_layered_tree_candidate(result.candidate, snapshot, expected_request=request).ok


def test_spanning_via_is_real_contact_for_coincident_different_layer_pad() -> None:
    source = _ordinary_source(2, 3).replace(b"(at 12 10)", b"(at 8 10)")
    parsed = parse_kicad_bytes(source, _profile())
    assert parsed.snapshot is not None and not parsed.diagnostics
    snapshot = parsed.snapshot
    pads = tuple(sorted(snapshot.content.pads, key=lambda pad: pad.id))
    net_id = pads[0].net_id
    assert net_id is not None
    connected = _changed_snapshot(
        snapshot,
        segments=(
            Segment(
                "segment:via-contact-front",
                net_id,
                "layer:F.Cu",
                PointNM(8_000_000, 10_000_000),
                PointNM(16_000_000, 10_000_000),
                250_000,
            ),
            Segment(
                "segment:via-contact-back",
                net_id,
                "layer:B.Cu",
                PointNM(8_000_000, 10_000_000),
                PointNM(9_000_000, 10_000_000),
                250_000,
            ),
        ),
        vias=(
            Via(
                "via:spanning-contact",
                net_id,
                PointNM(8_000_000, 10_000_000),
                800_000,
                400_000,
                "layer:F.Cu",
                "layer:B.Cu",
                ViaKind.THROUGH,
            ),
        ),
    )
    result = verify_reparsed_layered_tree_connectivity(
        connected, net_id, tuple(pad.id for pad in pads)
    )
    assert result.ok


def test_mixed_layer_source_pad_contact_replays_and_roundtrips() -> None:
    source = _source_contact_case(("F", "F", "B"), (8, 8, 16))
    profile, snapshot, pads, request, result = _route_source_contact(source)
    assert result.candidate is not None, result.diagnostic
    candidate = result.candidate
    assert candidate.branches[0].attachment_only
    assert LayeredTreeRouter().replay(snapshot, candidate, request).candidate == candidate
    assert verify_layered_tree_candidate(candidate, snapshot, expected_request=request).ok
    rendered = render_kicad_layered_tree_candidate_board(
        source, snapshot, candidate, profile, request=request
    )
    reparsed = parse_kicad_bytes(rendered, profile)
    assert reparsed.snapshot is not None and not reparsed.diagnostics
    assert verify_reparsed_layered_tree_connectivity(
        reparsed.snapshot, candidate.net_id, tuple(pad.id for pad in pads)
    ).ok


def test_all_same_layer_source_pad_contact_adds_no_copper() -> None:
    source = _source_contact_case(("F", "F", "F"), (8, 8, 8))
    profile, snapshot, pads, request, result = _route_source_contact(source)
    assert result.candidate is not None, result.diagnostic
    candidate = result.candidate
    assert all(branch.attachment_only for branch in candidate.branches)
    assert candidate.metrics.wire_length_nm == candidate.metrics.vias == 0
    assert verify_layered_tree_candidate(candidate, snapshot, expected_request=request).ok
    rendered = render_kicad_layered_tree_candidate_board(
        source, snapshot, candidate, profile, request=request
    )
    reparsed = parse_kicad_bytes(rendered, profile)
    assert reparsed.snapshot is not None and not reparsed.diagnostics
    assert reparsed.snapshot.content.segments == ()
    assert reparsed.snapshot.content.vias == ()
    assert verify_reparsed_layered_tree_connectivity(
        reparsed.snapshot, candidate.net_id, tuple(pad.id for pad in pads)
    ).ok
