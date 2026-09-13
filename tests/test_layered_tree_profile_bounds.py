"""Owned ordinary front/back SMD layouts, not held-out quality evidence."""

from dataclasses import replace

import pytest
from test_layered_tree_router import _case, _profile

from copper_mcp.adapters import kicad_layered_tree_patch
from copper_mcp.adapters.kicad_board_ir import parse_kicad_bytes
from copper_mcp.adapters.kicad_layered_tree_patch import (
    KiCadLayeredTreePatchError,
    render_kicad_layered_tree_candidate_board,
)
from copper_mcp.board_ir import PointNM
from copper_mcp.routing import layered_tree_contracts, layered_tree_router
from copper_mcp.routing._layered_tree_cancel import (
    LayeredTreeCancellationError,
    poll_cancelled,
)
from copper_mcp.routing.layered_astar import LayeredAStarSettings
from copper_mcp.routing.layered_contracts import LayeredRouteFailureCode
from copper_mcp.routing.layered_tree_contracts import (
    LayeredTreeTerminal,
    verify_layered_tree_candidate_id,
    with_layered_tree_candidate_id,
)
from copper_mcp.routing.layered_tree_router import LayeredTreeRequest, LayeredTreeRouter
from copper_mcp.routing.layered_tree_verifier import (
    verify_layered_tree_candidate,
    verify_reparsed_layered_tree_connectivity,
)


def _ordinary_source(layer_count: int, pad_count: int) -> bytes:
    layers = ['(0 "F.Cu" signal)']
    layers.extend(f'({2 * index + 2} "In{index}.Cu" signal)' for index in range(1, layer_count - 1))
    layers.extend(['(2 "B.Cu" signal)', '(25 "Edge.Cuts" user)'])
    footprints = []
    for index in range(pad_count):
        side = "F" if index % 2 == 0 else "B"
        footprints.append(
            f'''(footprint "OwnedTreePad"
              (layer "{side}.Cu")
              (uuid "60000000-0000-0000-0000-{2 * index + 1:012x}")
              (at {8 + 4 * index} 10)
              (pad "1" smd rect (at 0 0) (size 1 1)
                (layers "{side}.Cu" "{side}.Mask" "{side}.Paste")
                (net "TREE")
                (uuid "60000000-0000-0000-0000-{2 * index + 2:012x}")))'''
        )
    return (
        '(kicad_pcb (version 20260206) (generator "copper-mcp") '
        '(generator_version "0.1.0") (layers '
        + " ".join(layers)
        + ") "
        + " ".join(footprints)
        + f""" (gr_rect (start 0 0) (end {16 + 4 * pad_count} 20)
            (stroke (width 0.1) (type default)) (fill no) (layer "Edge.Cuts")
            (uuid "60000000-0000-0000-0000-000000000100")))"""
    ).encode("ascii")


@pytest.mark.parametrize("layer_count", (2, 4, 6, 8))
@pytest.mark.parametrize("pad_count", (3, 4, 8, 32))
def test_full_pad_range_on_ordinary_smd_stacks(layer_count: int, pad_count: int) -> None:
    source = _ordinary_source(layer_count, pad_count)
    profile = _profile()
    parsed = parse_kicad_bytes(source, profile)
    assert parsed.snapshot is not None and not parsed.diagnostics
    snapshot = parsed.snapshot
    pads = tuple(sorted(snapshot.content.pads, key=lambda pad: pad.id))
    assert len(pads) == pad_count
    request = LayeredTreeRequest(
        snapshot.snapshot_digest,
        pads[0].net_id,
        tuple(LayeredTreeTerminal(pad.id, pad.layer_ids[0]) for pad in pads),
        grid_step_nm=1_000_000,
        settings=LayeredAStarSettings(),
    )
    result = LayeredTreeRouter().propose(snapshot, request)
    assert result.candidate is not None, result.diagnostic
    rendered = render_kicad_layered_tree_candidate_board(
        source, snapshot, result.candidate, profile, request=request
    )
    reparsed = parse_kicad_bytes(rendered, profile)
    assert reparsed.snapshot is not None and not reparsed.diagnostics
    assert len(reparsed.snapshot.content.pads) == pad_count
    assert verify_reparsed_layered_tree_connectivity(
        reparsed.snapshot, request.net_id, tuple(pad.id for pad in pads)
    ).ok


@pytest.mark.parametrize("limit", ("max_segments", "max_vias", "max_pair_checks"))
def test_nonfinite_reparse_budget_cannot_disable_its_ceiling(limit: str) -> None:
    profile, source, snapshot, request, candidate = _case(_ordinary_source(2, 3))
    rendered = render_kicad_layered_tree_candidate_board(
        source, snapshot, candidate, profile, request=request
    )
    reparsed = parse_kicad_bytes(rendered, profile)
    assert reparsed.snapshot is not None
    result = verify_reparsed_layered_tree_connectivity(
        reparsed.snapshot,
        candidate.net_id,
        tuple(item.pad_id for item in candidate.terminals),
        **{limit: float("nan")},
    )
    assert not result.ok and result.code == "invalid_limits"


def test_reparse_cannot_call_a_connected_subset_the_complete_net() -> None:
    source = _ordinary_source(2, 3)
    segment = b"""(segment (start 8 10) (end 16 10) (width 0.25)
        (layer "F.Cu") (net "TREE")
        (uuid "60000000-0000-0000-0000-000000000101"))"""
    parsed = parse_kicad_bytes(source[:-1] + segment + b")", _profile())
    assert parsed.snapshot is not None and not parsed.diagnostics
    pads = tuple(sorted(parsed.snapshot.content.pads, key=lambda pad: pad.id))
    selected = tuple(pad.id for pad in pads if "layer:F.Cu" in pad.layer_ids)
    assert len(pads) == 3 and len(selected) == 2
    result = verify_reparsed_layered_tree_connectivity(parsed.snapshot, pads[0].net_id, selected)
    assert not result.ok and result.code == "terminal_mismatch"


def test_candidate_terminal_layer_must_be_exposed_by_the_actual_pad() -> None:
    _profile_value, _source, snapshot, _request, candidate = _case(_ordinary_source(2, 3))
    relabelled = tuple(
        replace(item, layer_id="layer:F.Cu") if item.layer_id == "layer:B.Cu" else item
        for item in candidate.terminals
    )
    altered = with_layered_tree_candidate_id(
        replace(
            candidate,
            candidate_id=f"sha256:{'0' * 64}",
            terminals=relabelled,
        )
    )
    result = verify_layered_tree_candidate(altered, snapshot)
    assert not result.ok and result.code == "terminal_layer_mismatch"


def test_terminal_already_on_prior_copper_needs_no_fake_geometry() -> None:
    source = (
        _ordinary_source(2, 3)
        .replace(b"(at 12 10)", b"(at 30 10)")
        .replace(b"(at 16 10)", b"(at 9 10)")
        .replace(b"(end 28 20)", b"(end 40 20)")
    )
    profile, source, snapshot, request, candidate = _case(source)
    attached = candidate.branches[-1]
    assert attached.attachment_only
    assert attached.paths == attached.vias == ()
    assert candidate.metrics.branch_count == 2
    assert verify_layered_tree_candidate(candidate, snapshot).ok
    rendered = render_kicad_layered_tree_candidate_board(
        source, snapshot, candidate, profile, request=request
    )
    reparsed = parse_kicad_bytes(rendered, profile)
    assert reparsed.snapshot is not None and not reparsed.diagnostics
    assert verify_reparsed_layered_tree_connectivity(
        reparsed.snapshot,
        candidate.net_id,
        tuple(item.pad_id for item in candidate.terminals),
    ).ok


def test_later_searches_receive_only_remaining_unique_via_allowance(monkeypatch) -> None:
    source = _ordinary_source(2, 4)
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
        settings=LayeredAStarSettings(max_vias=1),
    )
    allowances: list[int | None] = []
    actual_search = layered_tree_router.route_layered

    def record_allowance(search_request, *, cancelled=None):
        allowances.append(search_request.settings.max_vias)
        return actual_search(search_request, cancelled=cancelled)

    monkeypatch.setattr(layered_tree_router, "route_layered", record_allowance)
    routed = LayeredTreeRouter().propose(snapshot, request)
    assert routed.candidate is not None
    assert routed.candidate.metrics.vias == 1
    assert allowances == [1, 0, 0]


@pytest.mark.parametrize("shape", ("paths", "vertices", "vias"))
def test_oversized_nested_shape_is_refused_before_hash_or_replay(monkeypatch, shape: str) -> None:
    profile, source, snapshot, request, candidate = _case(_ordinary_source(2, 3))
    branch = candidate.branches[0]
    if shape == "paths":
        object.__setattr__(branch, "paths", (branch.paths[0],) * 257)
    elif shape == "vertices":
        object.__setattr__(
            branch.paths[0],
            "vertices",
            tuple(PointNM(index, 0) for index in range(16_385)),
        )
    else:
        object.__setattr__(branch, "vias", (branch.vias[0],) * 257)

    def forbidden_hash(_value=b""):
        pytest.fail("oversized nested candidate reached hashing")

    monkeypatch.setattr(layered_tree_contracts.hashlib, "sha256", forbidden_hash)
    result = verify_layered_tree_candidate(candidate, snapshot)
    assert not result.ok and result.code == "budget_exhausted"
    replay = LayeredTreeRouter().replay(snapshot, candidate, request)
    assert replay.candidate is None
    with pytest.raises(KiCadLayeredTreePatchError, match="structural budget"):
        render_kicad_layered_tree_candidate_board(
            source, snapshot, candidate, profile, request=request
        )
    checkpoints = 0

    def count_checkpoint() -> None:
        nonlocal checkpoints
        checkpoints += 1

    with pytest.raises(ValueError, match="structural budget"):
        verify_layered_tree_candidate_id(candidate, checkpoint=count_checkpoint)
    assert checkpoints == 0


@pytest.mark.parametrize(
    "value",
    (float("nan"), float("inf"), float("-inf"), True, 0, 2_000_001),
)
@pytest.mark.parametrize("limit", ("max_segments", "max_vias", "max_pair_checks"))
def test_reparse_limits_require_exact_bounded_integers(limit: str, value: object) -> None:
    profile, source, snapshot, request, candidate = _case(_ordinary_source(2, 3))
    rendered = render_kicad_layered_tree_candidate_board(
        source, snapshot, candidate, profile, request=request
    )
    reparsed = parse_kicad_bytes(rendered, profile)
    assert reparsed.snapshot is not None
    result = verify_reparsed_layered_tree_connectivity(
        reparsed.snapshot,
        candidate.net_id,
        tuple(item.pad_id for item in candidate.terminals),
        **{limit: value},
    )
    assert not result.ok and result.code == "invalid_limits"


@pytest.mark.parametrize("outcome", (1, "yes", None))
def test_faulting_or_nonboolean_cancellation_is_a_fixed_refusal(outcome: object) -> None:
    profile, source, snapshot, request, candidate = _case(_ordinary_source(2, 3))

    def callback():
        if outcome is None:
            raise RuntimeError("PRIVATE-CANCELLATION-CONTEXT")
        return outcome

    if outcome is None:
        with pytest.raises(LayeredTreeCancellationError) as cancellation_error:
            poll_cancelled(callback)
        assert cancellation_error.value.__cause__ is None
        assert cancellation_error.value.__context__ is None

    proposed = LayeredTreeRouter().propose(snapshot, request, cancelled=callback)
    assert proposed.candidate is None
    assert proposed.diagnostic is not None
    assert proposed.diagnostic.code is LayeredRouteFailureCode.INVALID_REQUEST
    assert "PRIVATE" not in proposed.diagnostic.message
    verified = verify_layered_tree_candidate(candidate, snapshot, cancelled=callback)
    assert not verified.ok and verified.code == "cancellation_failed"
    with pytest.raises(KiCadLayeredTreePatchError, match="cancellation check failed") as error:
        render_kicad_layered_tree_candidate_board(
            source, snapshot, candidate, profile, request=request, cancelled=callback
        )
    assert "PRIVATE" not in str(error.value)
    assert error.value.__cause__ is None
    assert error.value.__context__ is None


def test_late_cancellation_cannot_return_candidate_or_serialized_board(monkeypatch) -> None:
    profile, source, snapshot, request, candidate = _case(_ordinary_source(2, 3))
    armed = False
    real_candidate_id = layered_tree_router.with_layered_tree_candidate_id

    def arm_after_hash(value, *, checkpoint=None):
        nonlocal armed
        result = real_candidate_id(value, checkpoint=checkpoint)
        armed = True
        return result

    monkeypatch.setattr(layered_tree_router, "with_layered_tree_candidate_id", arm_after_hash)
    routed = LayeredTreeRouter().propose(snapshot, request, cancelled=lambda: armed)
    assert routed.candidate is None
    assert routed.diagnostic is not None
    assert routed.diagnostic.code is LayeredRouteFailureCode.CANCELLED
    monkeypatch.setattr(layered_tree_router, "with_layered_tree_candidate_id", real_candidate_id)

    armed = False
    connectivity_calls = 0
    real_connectivity = kicad_layered_tree_patch.verify_reparsed_layered_tree_connectivity

    def arm_after_connectivity(*args, **kwargs):
        nonlocal armed, connectivity_calls
        result = real_connectivity(*args, **kwargs)
        connectivity_calls += 1
        armed = True
        return result

    monkeypatch.setattr(
        kicad_layered_tree_patch,
        "verify_reparsed_layered_tree_connectivity",
        arm_after_connectivity,
    )
    with pytest.raises(KiCadLayeredTreePatchError, match="cancelled"):
        render_kicad_layered_tree_candidate_board(
            source, snapshot, candidate, profile, request=request, cancelled=lambda: armed
        )
    assert connectivity_calls == 1
