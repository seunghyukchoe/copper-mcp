from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest

from copper_mcp.board_ir import (
    ConstraintSet,
    Footprint,
    FootprintSide,
    Layer,
    Net,
    NetClass,
    NetClassAssignment,
    OutlineContour,
    Pad,
    PadKind,
    PadShape,
    PointNM,
    Ring,
    Segment,
    SourceInfo,
    make_content,
    make_snapshot,
)
from copper_mcp.routing.contracts import AStarSettings, RouteRequest
from copper_mcp.routing.external_candidate_verifier import (
    EXTERNAL_ROUTE_CANDIDATE_SCHEMA,
    ExternalCandidateFailure,
    verify_external_route_candidate,
)

LAYER = "layer:F.Cu"
ROUTE_NET = "net:route"
FOREIGN_NET = "net:foreign"
START_PAD = "pad:route-left"
END_PAD = "pad:route-right"
SOURCE = f"sha256:{'d' * 64}"


def _settings() -> AStarSettings:
    return AStarSettings(
        grid_step_nm=1_000_000,
        bend_penalty_nm=500_000,
        proximity_penalty_nm=0,
        max_grid_nodes=256,
        max_expansions=1_000,
        max_obstacles=32,
        max_obstacle_checks=10_000,
    )


def _pad(identifier: str, net_id: str, point: PointNM) -> Pad:
    return Pad(
        id=identifier,
        net_id=net_id,
        center=point,
        rotation_udeg=0,
        shape=PadShape.RECT,
        kind=PadKind.SMD,
        size_x_nm=200_000,
        size_y_nm=200_000,
        roundrect_radius_nm=None,
        drill_x_nm=None,
        drill_y_nm=None,
        layer_ids=(LAYER,),
    )


def _snapshot(*, foreign_segment: bool = True):
    left = _pad(START_PAD, ROUTE_NET, PointNM(1_000_000, 5_000_000))
    right = _pad(END_PAD, ROUTE_NET, PointNM(9_000_000, 5_000_000))
    signal = NetClass(
        id="class:signal",
        name="Signal",
        clearance_nm=100_000,
        track_width_nm=200_000,
        via_diameter_nm=600_000,
        via_drill_nm=300_000,
    )
    segments = ()
    if foreign_segment:
        segments = (
            Segment(
                id="segment:foreign-wall",
                net_id=FOREIGN_NET,
                layer_id=LAYER,
                start=PointNM(5_000_000, 3_000_000),
                end=PointNM(5_000_000, 7_000_000),
                width_nm=200_000,
            ),
        )
    content = make_content(
        source=SourceInfo(format="test", revision=SOURCE, format_version="1", generator="test"),
        outline=(
            OutlineContour(
                id="contour:board",
                outer=Ring(
                    (
                        PointNM(0, 0),
                        PointNM(10_000_000, 0),
                        PointNM(10_000_000, 10_000_000),
                        PointNM(0, 10_000_000),
                    )
                ),
            ),
        ),
        copper_layers=(Layer(id=LAYER, name="F.Cu", index=0, kind="signal"),),
        nets=(Net(id=ROUTE_NET, name="ROUTE"), Net(id=FOREIGN_NET, name="FOREIGN")),
        constraints=ConstraintSet(
            net_classes=(signal,),
            assignments=(
                NetClassAssignment(net_id=ROUTE_NET, net_class_id=signal.id),
                NetClassAssignment(net_id=FOREIGN_NET, net_class_id=signal.id),
            ),
        ),
        footprints=(
            Footprint(
                id="footprint:route",
                origin=left.center,
                rotation_udeg=0,
                side=FootprintSide.FRONT,
                pad_ids=(left.id, right.id),
            ),
        ),
        pads=(left, right),
        segments=segments,
    )
    return make_snapshot(content)


def _request(snapshot) -> RouteRequest:
    return RouteRequest(
        board_revision=snapshot.snapshot_digest,
        net_id=ROUTE_NET,
        layer_id=LAYER,
        seed=7,
        settings=_settings(),
    )


def _point(x_nm: int, y_nm: int) -> dict[str, int]:
    return {"x_nm": x_nm, "y_nm": y_nm}


def _segment(start: tuple[int, int], end: tuple[int, int], **updates: object):
    segment: dict[str, object] = {
        "layer_id": LAYER,
        "width_nm": 200_000,
        "start": _point(*start),
        "end": _point(*end),
    }
    segment.update(updates)
    return segment


def _document(request: RouteRequest) -> dict[str, object]:
    return {
        "schema": EXTERNAL_ROUTE_CANDIDATE_SCHEMA,
        "problem_revision": request.board_revision,
        "start_pad_id": START_PAD,
        "end_pad_id": END_PAD,
        "segments": [
            _segment((1_000_000, 5_000_000), (1_000_000, 1_000_000)),
            _segment((1_000_000, 1_000_000), (9_000_000, 1_000_000)),
            _segment((9_000_000, 1_000_000), (9_000_000, 5_000_000)),
        ],
        "vias": [],
    }


def _verify(snapshot, request: RouteRequest, document: object, **updates: object):
    arguments: dict[str, object] = {
        "start_pad_id": START_PAD,
        "end_pad_id": END_PAD,
        "max_obstacle_checks": 10_000,
        "max_path_edges": 64,
    }
    arguments.update(updates)
    return verify_external_route_candidate(snapshot, request, document, **arguments)


def test_accepts_a_deterministic_route_without_exposing_geometry() -> None:
    snapshot = _snapshot()
    request = _request(snapshot)
    document = _document(request)

    results = tuple(_verify(snapshot, request, document) for _ in range(5))

    assert results == (results[0],) * 5
    assert results[0].accepted
    assert results[0].candidate_id is not None
    assert results[0].physical_validation == "not_run"
    rendered = results[0].to_dict()
    assert rendered["status"] == "accepted"
    assert "code" not in rendered and "diagnostic" not in rendered
    assert not ({"segments", "vias", "geometry", "apply_token"} & rendered.keys())


def test_collinear_reversal_is_not_compressed_out_of_validation() -> None:
    snapshot = _snapshot()
    request = _request(snapshot)
    reversal = _document(request)
    reversal["segments"] = [
        _segment((1_000_000, 5_000_000), (1_000_000, -1_000_000)),
        _segment((1_000_000, -1_000_000), (1_000_000, 1_000_000)),
        _segment((1_000_000, 1_000_000), (9_000_000, 1_000_000)),
        _segment((9_000_000, 1_000_000), (9_000_000, 5_000_000)),
    ]

    result = _verify(snapshot, request, reversal)

    assert result.failure is ExternalCandidateFailure.INVALID_CANDIDATE
    assert result.candidate_id is None


def test_four_foreign_perturbation_classes_have_distinct_refusals() -> None:
    snapshot = _snapshot()
    request = _request(snapshot)

    obstacle = _document(request)
    obstacle["segments"] = [_segment((1_000_000, 5_000_000), (9_000_000, 5_000_000))]
    dropped = _document(request)
    dropped["segments"] = [
        _segment((1_000_000, 5_000_000), (1_000_000, 1_000_000)),
        _segment((2_000_000, 1_000_000), (9_000_000, 1_000_000)),
        _segment((9_000_000, 1_000_000), (9_000_000, 5_000_000)),
    ]
    endpoint = _document(request)
    endpoint["segments"][-1] = _segment((9_000_000, 1_000_000), (9_000_000, 4_000_000))
    via = _document(request)
    via["vias"] = [
        {
            "start_layer_id": LAYER,
            "end_layer_id": "layer:missing",
            "at": _point(5_000_000, 1_000_000),
        }
    ]

    assert (
        _verify(snapshot, request, obstacle).failure is ExternalCandidateFailure.OBSTACLE_VIOLATION
    )
    assert (
        _verify(snapshot, request, dropped).failure is ExternalCandidateFailure.DISCONTINUOUS_PATH
    )
    assert (
        _verify(snapshot, request, endpoint).failure is ExternalCandidateFailure.ENDPOINT_MISMATCH
    )
    assert _verify(snapshot, request, via).failure is ExternalCandidateFailure.UNDECLARED_LAYER


def test_one_nanometre_off_grid_incursion_keeps_its_obstacle_refusal() -> None:
    snapshot = _snapshot()
    request = replace(
        _request(snapshot),
        settings=replace(_settings(), grid_step_nm=100_000, max_grid_nodes=20_000),
    )
    boundary = _document(request)
    boundary["segments"] = [
        _segment((1_000_000, 5_000_000), (1_000_000, 2_700_000)),
        _segment((1_000_000, 2_700_000), (9_000_000, 2_700_000)),
        _segment((9_000_000, 2_700_000), (9_000_000, 5_000_000)),
    ]
    incursion = deepcopy(boundary)
    incursion["segments"] = [
        _segment((1_000_000, 5_000_000), (1_000_000, 2_700_001)),
        _segment((1_000_000, 2_700_001), (9_000_000, 2_700_001)),
        _segment((9_000_000, 2_700_001), (9_000_000, 5_000_000)),
    ]

    assert _verify(snapshot, request, boundary, max_path_edges=256).accepted
    shifted = _verify(snapshot, request, incursion, max_path_edges=256)
    assert shifted.failure is ExternalCandidateFailure.OBSTACLE_VIOLATION
    assert shifted.obstacle_checks <= 10_000


def test_stale_schema_forged_identity_and_unsafe_values_fail_closed() -> None:
    snapshot = _snapshot()
    request = _request(snapshot)

    stale = _document(request)
    stale["problem_revision"] = f"sha256:{'e' * 64}"
    assert _verify(snapshot, request, stale).failure is ExternalCandidateFailure.STALE_REVISION

    for key, value in (
        ("candidate_id", f"sha256:{'0' * 64}"),
        ("net_id", ROUTE_NET),
        ("settings", {}),
        ("cost", 0),
    ):
        forged = _document(request)
        forged[key] = value
        assert (
            _verify(snapshot, request, forged).failure is ExternalCandidateFailure.INVALID_CANDIDATE
        )

    malformed = _document(request)
    malformed["segments"][0]["width_nm"] = True
    assert (
        _verify(snapshot, request, malformed).failure is ExternalCandidateFailure.INVALID_CANDIDATE
    )
    huge = _document(request)
    huge["segments"][0]["start"]["x_nm"] = 1 << 53
    assert _verify(snapshot, request, huge).failure is ExternalCandidateFailure.INVALID_CANDIDATE


@pytest.mark.parametrize("schema", [None, True, 1, [], {}])
def test_schema_discriminator_is_a_strict_scalar(schema: object) -> None:
    snapshot = _snapshot()
    request = _request(snapshot)
    document = _document(request)
    document["schema"] = schema
    assert (
        _verify(snapshot, request, document).failure is ExternalCandidateFailure.INVALID_CANDIDATE
    )


def test_revision_and_endpoint_shapes_are_validated_before_semantic_comparison() -> None:
    snapshot = _snapshot()
    request = _request(snapshot)

    malformed_revision = _document(request)
    malformed_revision["problem_revision"] = "not-a-digest"
    malformed_endpoint = _document(request)
    malformed_endpoint["start_pad_id"] = "missing-prefix"

    assert (
        _verify(snapshot, request, malformed_revision).failure
        is ExternalCandidateFailure.INVALID_CANDIDATE
    )
    assert (
        _verify(snapshot, request, malformed_endpoint).failure
        is ExternalCandidateFailure.INVALID_CANDIDATE
    )


def test_closed_object_checks_reject_oversized_key_sets_without_echo() -> None:
    snapshot = _snapshot()
    request = _request(snapshot)
    document = _document(request)
    document.update({f"hostile_{index}": index for index in range(10_000)})

    result = _verify(snapshot, request, document)

    assert result.failure is ExternalCandidateFailure.INVALID_CANDIDATE
    assert "hostile" not in str(result.to_dict())

    segment_document = _document(request)
    segment_document["segments"][0].update({f"hostile_{index}": index for index in range(10_000)})
    point_document = _document(request)
    point_document["segments"][0]["start"].update(
        {f"hostile_{index}": index for index in range(10_000)}
    )

    assert (
        _verify(snapshot, request, segment_document).failure
        is ExternalCandidateFailure.INVALID_CANDIDATE
    )
    assert (
        _verify(snapshot, request, point_document).failure
        is ExternalCandidateFailure.INVALID_CANDIDATE
    )


def test_type_exact_but_internally_hostile_coordinator_objects_fail_closed() -> None:
    snapshot = _snapshot()
    request = _request(snapshot)
    document = _document(request)
    hostile_content = replace(snapshot.content)
    object.__setattr__(hostile_content, "copper_layers", object())
    hostile_snapshot = replace(snapshot, content=hostile_content)
    hostile_request = replace(request)
    object.__setattr__(hostile_request, "settings", object())

    assert (
        _verify(hostile_snapshot, request, document).failure
        is ExternalCandidateFailure.INVALID_REQUEST
    )
    assert (
        _verify(snapshot, hostile_request, document).failure
        is ExternalCandidateFailure.INVALID_REQUEST
    )


def test_unknown_endpoints_width_layers_and_vias_are_typed() -> None:
    snapshot = _snapshot()
    request = _request(snapshot)
    document = _document(request)

    assert (
        _verify(snapshot, request, document, start_pad_id="pad:missing").failure
        is ExternalCandidateFailure.ENDPOINT_MISMATCH
    )
    unknown_pad = deepcopy(document)
    unknown_pad["start_pad_id"] = "pad:missing"
    assert (
        _verify(snapshot, request, unknown_pad, start_pad_id="pad:missing").failure
        is ExternalCandidateFailure.INVALID_REQUEST
    )
    wrong_width = deepcopy(document)
    for segment in wrong_width["segments"]:
        segment["width_nm"] = 300_000
    assert (
        _verify(snapshot, request, wrong_width).failure
        is ExternalCandidateFailure.INVALID_CANDIDATE
    )
    missing_layer = deepcopy(document)
    missing_layer["segments"][0]["layer_id"] = "layer:missing"
    assert (
        _verify(snapshot, request, missing_layer).failure
        is ExternalCandidateFailure.UNDECLARED_LAYER
    )
    declared_via = deepcopy(document)
    declared_via["vias"] = [
        {"start_layer_id": LAYER, "end_layer_id": LAYER, "at": _point(5_000_000, 1_000_000)}
    ]
    assert (
        _verify(snapshot, request, declared_via).failure
        is ExternalCandidateFailure.UNSUPPORTED_GEOMETRY
    )


def test_resource_cancellation_deadline_and_stale_snapshot_are_redacted() -> None:
    snapshot = _snapshot()
    request = _request(snapshot)
    document = _document(request)

    assert (
        _verify(snapshot, request, document, max_path_edges=2).failure
        is ExternalCandidateFailure.BUDGET_EXCEEDED
    )
    assert (
        _verify(snapshot, request, document, cancelled=lambda: True).failure
        is ExternalCandidateFailure.CANCELLED
    )
    assert (
        _verify(snapshot, request, document, deadline_check=lambda: True).failure
        is ExternalCandidateFailure.DEADLINE_EXCEEDED
    )
    stale_request = replace(request, board_revision=f"sha256:{'e' * 64}")
    stale_document = _document(stale_request)
    refused = _verify(snapshot, stale_request, stale_document)
    assert refused.failure is ExternalCandidateFailure.STALE_REVISION
    rendered = refused.to_dict()
    assert "candidate_id" not in rendered
    assert not ({"segments", "vias", "geometry", "apply_token"} & rendered.keys())


@pytest.mark.parametrize("bad_budget", [True, 0, 1 << 53])
def test_coordinator_budget_is_strictly_bounded(bad_budget: object) -> None:
    snapshot = _snapshot()
    request = _request(snapshot)
    result = _verify(snapshot, request, _document(request), max_path_edges=bad_budget)
    assert result.failure is ExternalCandidateFailure.INVALID_REQUEST


def test_production_verifier_has_no_benchmark_or_private_astar_dependency() -> None:
    source = Path("src/copper_mcp/routing/external_candidate_verifier.py").read_text(
        encoding="utf-8"
    )
    assert "copper_mcp.benchmarks" not in source
    assert "_prepare" not in source
    assert "_edge_is_legal" not in source
    assert "_WorkBudget" not in source


def test_production_verifier_is_exported_from_the_routing_core() -> None:
    from copper_mcp.routing import (
        EXTERNAL_ROUTE_CANDIDATE_SCHEMA as EXPORTED_SCHEMA,
    )
    from copper_mcp.routing import verify_external_route_candidate as exported_verifier

    assert EXPORTED_SCHEMA == EXTERNAL_ROUTE_CANDIDATE_SCHEMA
    assert exported_verifier is verify_external_route_candidate
