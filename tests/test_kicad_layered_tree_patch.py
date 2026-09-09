from __future__ import annotations

import hashlib
from dataclasses import replace

import pytest
from test_layered_tree_router import FIXTURE, _case

from copper_mcp.adapters.kicad_board_ir import parse_kicad_bytes
from copper_mcp.adapters.kicad_layered_tree_patch import (
    KiCadLayeredTreePatchError,
    render_kicad_layered_tree_candidate_board,
)
from copper_mcp.board_ir import ParseLimits
from copper_mcp.routing.layered_tree_router import LayeredTreeRouter
from copper_mcp.routing.layered_tree_verifier import (
    verify_reparsed_layered_tree_connectivity,
)

ORDINARY_FIXTURE = FIXTURE.parent / "layered-tree-ordinary-4layer.kicad_pcb"


def _layer_count_source(count: int) -> bytes:
    source = FIXTURE.read_bytes()
    extra = b"".join(
        f'    ({8 + 2 * offset} "In{3 + offset}.Cu" signal)\n'.encode("ascii")
        for offset in range(count - 4)
    )
    return source.replace(b'    (2 "B.Cu" signal)\n', extra + b'    (2 "B.Cu" signal)\n')


@pytest.mark.parametrize("layer_count", [4, 6, 8])
def test_complete_tree_roundtrips_deterministically_on_supported_stacks(layer_count: int) -> None:
    profile, source, snapshot, request, candidate = _case(_layer_count_source(layer_count))
    source_digest = hashlib.sha256(source).digest()
    first = render_kicad_layered_tree_candidate_board(
        source, snapshot, candidate, profile, request=request
    )
    second = render_kicad_layered_tree_candidate_board(
        source, snapshot, candidate, profile, request=request
    )
    assert first == second and first != source
    assert hashlib.sha256(source).digest() == source_digest
    reparsed = parse_kicad_bytes(first, profile)
    assert reparsed.snapshot is not None and not reparsed.diagnostics
    assert len(reparsed.snapshot.content.copper_layers) == layer_count
    assert verify_reparsed_layered_tree_connectivity(
        reparsed.snapshot,
        candidate.net_id,
        tuple(item.pad_id for item in candidate.terminals),
    ).ok
    assert all(
        {via.start_layer_id, via.end_layer_id} == {"layer:F.Cu", "layer:B.Cu"}
        for via in reparsed.snapshot.content.vias
    )


def test_outer_smd_fixture_forces_inner_layer_without_invalid_inner_only_pad() -> None:
    profile, source, snapshot, request, candidate = _case(ORDINARY_FIXTURE.read_bytes())
    assert all(
        terminal.layer_id in {"layer:F.Cu", "layer:B.Cu"} for terminal in candidate.terminals
    )
    assert any(
        path.layer_id.startswith("layer:In")
        for branch in candidate.branches
        for path in branch.paths
    )
    rendered = render_kicad_layered_tree_candidate_board(
        source, snapshot, candidate, profile, request=request
    )
    reparsed = parse_kicad_bytes(rendered, profile)
    assert reparsed.snapshot is not None and not reparsed.diagnostics


def test_stale_source_and_changed_intermediate_request_are_refused() -> None:
    profile, source, snapshot, request, candidate = _case()
    changed_source = source.replace(b'(generator "copper-mcp")', b'(generator "other")')
    with pytest.raises(KiCadLayeredTreePatchError, match="source and Board IR"):
        render_kicad_layered_tree_candidate_board(
            changed_source, snapshot, candidate, profile, request=request
        )
    with pytest.raises(KiCadLayeredTreePatchError, match="deterministic replay"):
        render_kicad_layered_tree_candidate_board(
            source,
            snapshot,
            candidate,
            profile,
            request=replace(request, seed=request.seed + 1),
        )
    with pytest.raises(KiCadLayeredTreePatchError, match="cancelled"):
        render_kicad_layered_tree_candidate_board(
            source, snapshot, candidate, profile, request=request, cancelled=lambda: True
        )
    with pytest.raises(KiCadLayeredTreePatchError, match="input-byte budget"):
        render_kicad_layered_tree_candidate_board(
            source,
            snapshot,
            candidate,
            profile,
            request=request,
            limits=replace(ParseLimits(), max_input_bytes=len(source) + 1),
        )


def test_branch_geometry_changed_after_search_cannot_serialize() -> None:
    profile, source, snapshot, request, candidate = _case()
    branch = candidate.branches[0]
    path = branch.paths[0]
    detour = replace(
        path,
        vertices=(
            path.vertices[0],
            replace(path.vertices[0], y=path.vertices[0].y + 1_000_000),
            replace(path.vertices[-1], y=path.vertices[0].y + 1_000_000),
            path.vertices[-1],
        ),
    )
    changed_branch = replace(branch, paths=(detour, *branch.paths[1:]))
    branches = (changed_branch, *candidate.branches[1:])
    changed = replace(
        candidate,
        candidate_id=f"sha256:{'0' * 64}",
        branches=branches,
        metrics=replace(
            candidate.metrics,
            wire_length_nm=sum(item.wire_length_nm for item in branches),
            bend_count=sum(item.bend_count for item in branches),
        ),
    )
    from copper_mcp.routing.layered_tree_contracts import with_layered_tree_candidate_id

    changed = with_layered_tree_candidate_id(changed)
    assert LayeredTreeRouter().replay(snapshot, changed, request).candidate is None
    with pytest.raises(KiCadLayeredTreePatchError, match="deterministic replay"):
        render_kicad_layered_tree_candidate_board(
            source, snapshot, changed, profile, request=request
        )
