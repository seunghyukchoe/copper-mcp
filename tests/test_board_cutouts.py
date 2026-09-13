"""Cutouts are missing board material, never an ignored drawing or an enlarged outline."""

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from copper_mcp.adapters import KiCadConstraintProfile, parse_kicad_bytes
from copper_mcp.board_ir import (
    BoardIRValidationError,
    NetClass,
    decode_snapshot_json,
    encode_snapshot,
)

FIXTURE = Path(__file__).parent / "fixtures/board-ir-v0.5/footprint-cutout.kicad_pcb"


def profile():
    net_class = NetClass("class:default", "Default", 250_000, 250_000, 800_000, 400_000)
    return KiCadConstraintProfile(net_classes=(net_class,), default_net_class_id=net_class.id)


def test_footprint_cutout_reaches_a_versioned_snapshot_without_dropping_material_constraints():
    converted = parse_kicad_bytes(FIXTURE.read_bytes(), profile())
    assert converted.snapshot is not None, converted.diagnostics
    snapshot = converted.snapshot
    assert snapshot.schema_version == "0.5.0"
    assert len(snapshot.content.outline) == 1
    assert len(snapshot.content.outline[0].holes) == 1
    hole = snapshot.content.outline[0].holes[0]
    assert {(point.x, point.y) for point in hole.points} == {
        (20_000_000, 20_000_000),
        (30_000_000, 20_000_000),
        (30_000_000, 30_000_000),
        (20_000_000, 30_000_000),
    }
    owners = [footprint for footprint in snapshot.content.footprints if footprint.owns_outline]
    assert len(owners) == 1 and owners[0].id.endswith("000000000005")
    assert owners[0].locked is False
    assert owners[0].outline_cutouts == snapshot.content.outline[0].holes
    assert decode_snapshot_json(encode_snapshot(snapshot)) == snapshot
    downgraded = json.loads(encode_snapshot(snapshot))
    downgraded["schema_version"] = "0.4.0"
    with pytest.raises(BoardIRValidationError):
        decode_snapshot_json(json.dumps(downgraded).encode())


def test_cutout_cannot_lose_its_footprint_owner():
    from dataclasses import replace

    from copper_mcp.board_ir.canonical import make_snapshot

    converted = parse_kicad_bytes(FIXTURE.read_bytes(), profile())
    assert converted.snapshot is not None
    snapshot = converted.snapshot
    unowned = replace(
        snapshot.content,
        footprints=tuple(
            replace(footprint, outline_cutouts=()) for footprint in snapshot.content.footprints
        ),
    )
    with pytest.raises(
        BoardIRValidationError, match="every cutout requires exactly one footprint owner"
    ):
        make_snapshot(unowned, schema_version="0.5.0")


def test_cutout_snapshot_satisfies_published_schema():
    converted = parse_kicad_bytes(FIXTURE.read_bytes(), profile())
    assert converted.snapshot is not None
    schema = json.loads((FIXTURE.parents[3] / "schemas/board-ir/0.5.0.schema.json").read_text())
    Draft202012Validator(schema).validate(json.loads(encode_snapshot(converted.snapshot)))


def test_shared_native_zone_has_explicit_per_layer_projections():
    from dataclasses import replace

    from copper_mcp.board_ir import make_snapshot

    source = (
        FIXTURE.read_bytes().rsplit(b")", 1)[0]
        + b"""
    (zone (net "N") (layers "F.Cu" "B.Cu")
      (uuid "84000000-0000-0000-0000-000000000009")
      (hatch edge 0.5) (connect_pads (clearance 0.25)) (min_thickness 0.25)
      (fill yes (thermal_gap 0.5) (thermal_bridge_width 0.5))
      (polygon (pts (xy 1 1) (xy 49 1) (xy 49 49) (xy 1 49))))
    )"""
    )
    converted = parse_kicad_bytes(source, profile())
    assert converted.snapshot is not None, converted.diagnostics
    snapshot = converted.snapshot
    zones = snapshot.content.zones
    assert len(zones) == 2
    assert {zone.layer_id for zone in zones} == {"layer:F.Cu", "layer:B.Cu"}
    assert {zone.source_zone_id for zone in zones} == {
        "zone:kicad:84000000-0000-0000-0000-000000000009"
    }
    assert len({zone.id for zone in zones}) == 2
    assert zones[0].boundary == zones[1].boundary
    assert decode_snapshot_json(encode_snapshot(snapshot)) == snapshot
    schema = json.loads((FIXTURE.parents[3] / "schemas/board-ir/0.5.0.schema.json").read_text())
    Draft202012Validator(schema).validate(json.loads(encode_snapshot(snapshot)))
    changed = replace(snapshot.content, zones=(zones[0], replace(zones[1], clearance_nm=1)))
    with pytest.raises(BoardIRValidationError, match="source-zone projections disagree"):
        make_snapshot(changed, schema_version="0.5.0")


def test_single_layer_route_avoids_cutout_and_replays():
    from copper_mcp.routing import AStarRouter, AStarSettings, RouteRequest

    converted = parse_kicad_bytes(FIXTURE.read_bytes(), profile())
    assert converted.snapshot is not None
    snapshot = converted.snapshot
    request = RouteRequest(
        board_revision=snapshot.snapshot_digest,
        net_id=snapshot.content.nets[0].id,
        layer_id="layer:F.Cu",
        seed=0,
        settings=AStarSettings(grid_step_nm=1_000_000, region_margin_nm=15_000_000),
    )
    router = AStarRouter()
    result = router.propose(snapshot, request)
    assert result.candidate is not None, result.diagnostic
    candidate = result.candidate
    assert candidate.router_version == "astar-grid/0.8.0"
    for path in candidate.patch.paths:
        for a, b in zip(path.vertices, path.vertices[1:], strict=False):
            assert (
                max(a.x, b.x) < 19_875_000
                or min(a.x, b.x) > 30_125_000
                or max(a.y, b.y) < 19_875_000
                or min(a.y, b.y) > 30_125_000
            )
    assert router.replay(snapshot, candidate).candidate == candidate


def test_existing_copper_crossing_a_cutout_is_not_an_already_connected_net():
    from copper_mcp.routing import AStarRouter, AStarSettings, RouteRequest

    source = (
        FIXTURE.read_bytes().rsplit(b")", 1)[0]
        + b"""
    (segment (start 10 25) (end 40 25) (width 0.25) (layer "F.Cu")
      (net "N") (uuid "84000000-0000-0000-0000-000000000010")))"""
    )
    converted = parse_kicad_bytes(source, profile())
    assert converted.snapshot is not None, converted.diagnostics
    snapshot = converted.snapshot
    request = RouteRequest(
        snapshot.snapshot_digest,
        snapshot.content.nets[0].id,
        "layer:F.Cu",
        0,
        AStarSettings(grid_step_nm=1_000_000),
    )
    result = AStarRouter().propose(snapshot, request)
    assert result.connected is None and result.candidate is None
    assert result.diagnostic.code == "unsupported_geometry"


def test_diagonal_track_contacts_annulus_but_not_empty_drill():
    from dataclasses import replace

    from copper_mcp.board_ir import PointNM, Segment, Via
    from copper_mcp.routing.astar import _track_contacts_via

    via = Via("via:test", "net:test", PointNM(0, 0), 600_000, 300_000, "layer:F.Cu", "layer:B.Cu")
    diagonal = Segment(
        "segment:test",
        "net:test",
        "layer:F.Cu",
        PointNM(0, 0),
        PointNM(2_000_000, 2_000_000),
        150_000,
    )
    assert _track_contacts_via(diagonal, via)
    assert not _track_contacts_via(replace(diagonal, end=PointNM(1_000, 1_000)), via)
    assert not _track_contacts_via(replace(diagonal, start=PointNM(500_000, 500_000)), via)


def test_existing_cross_layer_diagonal_connection_survives_cutout_workflow():
    from copper_mcp.routing import AStarSettings, RouteRequest
    from copper_mcp.routing.astar import _multilayer_via_count, _WorkBudget

    source = (
        FIXTURE.read_bytes()
        .replace(b"(at 10 25)", b"(at 8 10)")
        .replace(b"(at 40 25)", b"(at 12 12)")
        .replace(
            b'(footprint "OwnedRight" (layer "F.Cu")', b'(footprint "OwnedRight" (layer "B.Cu")'
        )
        .replace(
            b'(layers "F.Cu" "F.Mask" "F.Paste")\n      (net "N") '
            b'(uuid "84000000-0000-0000-0000-000000000004")',
            b'(layers "B.Cu" "B.Mask" "B.Paste")\n      (net "N") '
            b'(uuid "84000000-0000-0000-0000-000000000004")',
        )
    )
    source = (
        source.rsplit(b")", 1)[0]
        + b"""
    (segment (start 8 10) (end 10 10) (width 0.15) (layer "F.Cu")
      (net "N") (uuid "84000000-0000-0000-0000-000000000010"))
    (segment (start 10 10) (end 12 12) (width 0.15) (layer "B.Cu")
      (net "N") (uuid "84000000-0000-0000-0000-000000000011"))
    (via (at 10 10) (size 0.6) (drill 0.3) (layers "F.Cu" "B.Cu")
      (net "N") (uuid "84000000-0000-0000-0000-000000000012")))"""
    )
    converted = parse_kicad_bytes(source, profile())
    assert converted.snapshot is not None, converted.diagnostics
    snapshot = converted.snapshot
    request = RouteRequest(
        snapshot.snapshot_digest,
        snapshot.content.nets[0].id,
        "layer:F.Cu",
        0,
        AStarSettings(grid_step_nm=1_000_000),
    )
    pads = tuple(pad for pad in snapshot.content.pads if pad.net_id == request.net_id)
    assert _multilayer_via_count(
        snapshot, request, pads, _WorkBudget(settings=request.settings, cancelled=None)
    ) == (1, 0)


def test_scene_carries_cutouts_and_charges_their_vertices():
    from dataclasses import replace

    from copper_mcp.circuit_scene import observe_board_scene
    from copper_mcp.config import Settings
    from copper_mcp.mcp_contracts import CircuitSceneToolResponse

    settings = Settings(workspace=FIXTURE.parent.resolve())
    request = {
        "board": FIXTURE.name,
        "constraints": {
            "clearance_nm": 250_000,
            "track_width_nm": 250_000,
            "via_diameter_nm": 800_000,
            "via_drill_nm": 400_000,
        },
        "region": {"min_x_nm": 0, "min_y_nm": 0, "max_x_nm": 50_000_000, "max_y_nm": 50_000_000},
    }
    document = observe_board_scene(request, settings).to_dict()
    assert document["scene_version"] == "0.5.0"
    assert document["static"]["outline"][0]["geometry"]["holes_nm"]
    CircuitSceneToolResponse.model_validate(document)
    document["scene_version"] = "0.4.0"
    with pytest.raises(ValueError):
        CircuitSceneToolResponse.model_validate(document)
    bounded = observe_board_scene(request, replace(settings, max_scene_vertices=4)).to_dict()
    assert not isinstance(bounded["static"]["outline"], list)


@pytest.mark.parametrize("tree", [False, True])
def test_layered_routes_avoid_cutouts(tree):
    from copper_mcp.routing import LayeredBoardRouter, LayeredRouteRequest
    from copper_mcp.routing.layered_tree_contracts import LayeredTreeTerminal
    from copper_mcp.routing.layered_tree_router import LayeredTreeRequest, LayeredTreeRouter
    from copper_mcp.routing.layered_tree_verifier import verify_layered_tree_candidate

    source = (
        FIXTURE.read_bytes()
        .replace(
            b'(footprint "OwnedRight" (layer "F.Cu")', b'(footprint "OwnedRight" (layer "B.Cu")'
        )
        .replace(
            b'(layers "F.Cu" "F.Mask" "F.Paste")\n      (net "N") '
            b'(uuid "84000000-0000-0000-0000-000000000004")',
            b'(layers "B.Cu" "B.Mask" "B.Paste")\n      (net "N") '
            b'(uuid "84000000-0000-0000-0000-000000000004")',
        )
    )
    if tree:
        source = source.replace(b'(pad "M" smd rect', b'(pad "M" smd rect (net "N")')
    converted = parse_kicad_bytes(source, profile())
    assert converted.snapshot is not None, converted.diagnostics
    snapshot = converted.snapshot
    pads = tuple(pad for pad in snapshot.content.pads if pad.net_id is not None)
    if tree:
        request = LayeredTreeRequest(
            snapshot.snapshot_digest,
            pads[0].net_id,
            tuple(LayeredTreeTerminal(pad.id, pad.layer_ids[0]) for pad in pads),
            grid_step_nm=1_000_000,
        )
        result = LayeredTreeRouter().propose(snapshot, request)
    else:
        request = LayeredRouteRequest(
            snapshot.snapshot_digest, pads[0].net_id, pads[0].id, pads[1].id, grid_step_nm=1_000_000
        )
        result = LayeredBoardRouter().propose(snapshot, request)
    assert result.candidate is not None, result.diagnostic
    candidate = result.candidate
    assert candidate.router_version.endswith("/0.2.0")
    paths = (
        tuple(path for branch in candidate.branches for path in branch.paths)
        if tree
        else candidate.patch.paths
    )
    for path in paths:
        for a, b in zip(path.vertices, path.vertices[1:], strict=False):
            # Every orthogonal centerline segment stays outside the inflated rectangular hole.
            assert (
                max(a.x, b.x) < 19_875_000
                or min(a.x, b.x) > 30_125_000
                or max(a.y, b.y) < 19_875_000
                or min(a.y, b.y) > 30_125_000
            )
    if tree:
        assert verify_layered_tree_candidate(candidate, snapshot, expected_request=request).ok


def test_cutout_owner_stays_fixed_while_other_footprints_remain_movable():
    from copper_mcp.placement import build_placement_view, parse_placement_intent
    from copper_mcp.placement.legalizer import evaluate_placement
    from copper_mcp.placement.route_scoring import project_legal_candidate_snapshot
    from copper_mcp.placement.solver import _movable_refs

    source = FIXTURE.read_bytes()
    converted = parse_kicad_bytes(source, profile())
    assert converted.snapshot is not None
    snapshot = converted.snapshot
    view = build_placement_view(source, snapshot)
    owner = next(item.ref_id for item in view.footprints.values() if item.owns_outline)
    intent = parse_placement_intent(
        {
            "board": FIXTURE.name,
            "subjects": sorted(view.footprints),
            "constraints": {
                "clearance_nm": 250_000,
                "track_width_nm": 250_000,
                "via_diameter_nm": 800_000,
                "via_drill_nm": 400_000,
            },
        }
    )
    assert owner not in _movable_refs(intent, view)
    assert len(_movable_refs(intent, view)) == 2
    identity = evaluate_placement(intent, snapshot, view)
    assert identity.candidate is not None, identity.diagnostic
    assert project_legal_candidate_snapshot(identity.candidate, snapshot, view) == snapshot
    from dataclasses import replace

    from copper_mcp.placement.contracts import PlacementProposal

    refused = evaluate_placement(
        replace(intent, proposals=(PlacementProposal(subject=owner, offset_x_nm=1_000_000),)),
        snapshot,
        view,
    )
    assert refused.candidate is None


@pytest.mark.parametrize("pad_angle,pad_size", [(0, b"2 2"), (45, b"2 2"), (45, b"6 8")])
def test_non_owner_placement_wholly_inside_cutout_is_illegal(pad_angle, pad_size, tmp_path):
    from copper_mcp.config import Settings
    from copper_mcp.placement import build_placement_view, parse_placement_intent
    from copper_mcp.placement.legalizer import evaluate_placement
    from copper_mcp.placement_preview import preview_placement

    source = (
        FIXTURE.read_bytes()
        .replace(
            b'(pad "1" smd rect (at 0 0)', f'(pad "1" smd rect (at 0 0 {pad_angle})'.encode(), 1
        )
        .replace(b"(size 2 2)", b"(size " + pad_size + b")", 1)
    )
    converted = parse_kicad_bytes(source, profile())
    assert converted.snapshot is not None
    snapshot = converted.snapshot
    view = build_placement_view(source, snapshot)
    subject = next(item.ref_id for item in view.footprints.values() if item.origin.x == 10_000_000)
    request = {
        "board": FIXTURE.name,
        "subjects": [subject],
        "constraints": {
            "clearance_nm": 250_000,
            "track_width_nm": 250_000,
            "via_diameter_nm": 800_000,
            "via_drill_nm": 400_000,
        },
        "proposals": [{"subject": subject, "offset_x_nm": 15_000_000}],
    }
    intent = parse_placement_intent(request)
    result = evaluate_placement(intent, snapshot, view)
    assert result.candidate is None
    assert result.diagnostic is not None and result.diagnostic.code == "illegal_placement"
    assert result.diagnostic.legality is not None
    assert result.diagnostic.legality.outline_containment == "violated"
    (tmp_path / FIXTURE.name).write_bytes(source)
    preview = preview_placement(request, Settings(workspace=tmp_path))
    assert preview.candidate is None and preview.apply_token is None
    assert preview.diagnostic is not None and preview.diagnostic.code == "illegal_placement"
    assert preview.diagnostic.legality is not None
    assert preview.diagnostic.legality.outline_containment == "violated"


@pytest.mark.parametrize(
    "replacement",
    [
        b"(start -30 -5) (end 5 5)",
        b"(start -25 -5) (end 5 5)",
        b"(start 0 0) (end 0 5)",
    ],
)
def test_invalid_cutouts_fail_closed(replacement):
    converted = parse_kicad_bytes(
        FIXTURE.read_bytes().replace(b"(start -5 -5) (end 5 5)", replacement), profile()
    )
    assert converted.snapshot is None and converted.diagnostics


def test_unhashable_snapshot_version_has_a_bounded_refusal():
    from dataclasses import replace

    from copper_mcp.board_ir.validation import validate_content

    snapshot = parse_kicad_bytes(FIXTURE.read_bytes(), profile()).snapshot
    assert snapshot is not None
    with pytest.raises(ValueError, match="schema version is unsupported"):
        replace(snapshot, schema_version=[])
    with pytest.raises(BoardIRValidationError, match="version is unsupported"):
        validate_content(snapshot.content, schema_version=[])
