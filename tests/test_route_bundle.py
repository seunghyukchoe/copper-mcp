from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from copper_mcp.adapters import KiCadConstraintProfile, net_id_for_name, parse_kicad_bytes
from copper_mcp.adapters.kicad_route_bundle_patch import render_kicad_route_bundle_board
from copper_mcp.board_ir import NetClass
from copper_mcp.config import Settings
from copper_mcp.mcp_contracts import RouteBundleToolResponse
from copper_mcp.request_boundary import MAX_JSON_SAFE_INTEGER
from copper_mcp.route_bundle import (
    RouteBundleError,
    RouteBundleStatus,
    _routes,
    parse_route_bundle_request,
    preview_route_bundle,
)
from copper_mcp.routing import NegotiatedRoutingRequest, NegotiatedRoutingStatus, negotiate_routes

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "benchmarks/audio/fixtures/negotiated-crossing-v1.kicad_pcb"
MAX_BUNDLE_SEED = MAX_JSON_SAFE_INTEGER - 7


def _constraints() -> dict[str, int]:
    return {
        "clearance_nm": 100_000,
        "track_width_nm": 200_000,
        "via_diameter_nm": 600_000,
        "via_drill_nm": 300_000,
    }


def _payload(board: str, source: bytes) -> dict[str, object]:
    net_class = NetClass(id="class:request", name="Request", **_constraints())
    converted = parse_kicad_bytes(
        source,
        KiCadConstraintProfile(net_classes=(net_class,), default_net_class_id=net_class.id),
    )
    assert converted.snapshot is not None
    assert not converted.diagnostics
    return {
        "board": board,
        "layer": "F.Cu",
        "constraints": _constraints(),
        "net_ref_ids": [net_id_for_name("HORIZONTAL"), net_id_for_name("VERTICAL")],
        "expect_board_revision": f"sha256:{hashlib.sha256(source).hexdigest()}",
        "expect_snapshot_digest": converted.snapshot.snapshot_digest,
        "seed": 7,
        "settings": {
            "grid_step_nm": 1_000_000,
            "bend_penalty_nm": 500_000,
            "proximity_penalty_nm": 0,
            "max_grid_nodes": 512,
            "max_expansions": 20_000,
            "max_obstacles": 128,
            "max_obstacle_checks": 200_000,
        },
    }


def test_preview_composes_a_revision_bound_physical_clearance_checked_bundle(
    tmp_path: Path,
) -> None:
    source = FIXTURE.read_bytes()
    board = tmp_path / FIXTURE.name
    board.write_bytes(source)
    before = (board.read_bytes(), board.stat().st_ino, board.stat().st_mtime_ns)

    first = preview_route_bundle(_payload(board.name, source), Settings(workspace=tmp_path))
    second = preview_route_bundle(_payload(board.name, source), Settings(workspace=tmp_path))

    assert first == second
    assert first.status is RouteBundleStatus.ROUTED
    assert first.plan is not None
    assert first.plan.base_revision == first.snapshot_digest
    assert first.plan.net_ref_ids == (
        net_id_for_name("HORIZONTAL"),
        net_id_for_name("VERTICAL"),
    )
    assert len(first.plan.candidates) == 2
    assert first.plan.core_replays == 1
    assert first.plan.physical_pair_checks > 0
    assert first.plan.total_wire_length_nm == 26_000_000
    net_class = NetClass(id="class:request", name="Request", **_constraints())
    converted = parse_kicad_bytes(
        source,
        KiCadConstraintProfile(net_classes=(net_class,), default_net_class_id=net_class.id),
    )
    assert converted.snapshot is not None
    rendered = render_kicad_route_bundle_board(
        source,
        converted.snapshot,
        first.plan,
        KiCadConstraintProfile(net_classes=(net_class,), default_net_class_id=net_class.id),
    )
    assert rendered == render_kicad_route_bundle_board(
        source,
        converted.snapshot,
        first.plan,
        KiCadConstraintProfile(net_classes=(net_class,), default_net_class_id=net_class.id),
    )
    patched = parse_kicad_bytes(
        rendered,
        KiCadConstraintProfile(net_classes=(net_class,), default_net_class_id=net_class.id),
    )
    assert patched.snapshot is not None
    assert len(patched.snapshot.content.segments) == 4
    assert before == (board.read_bytes(), board.stat().st_ino, board.stat().st_mtime_ns)


def test_preview_refuses_stale_or_duplicate_reference_bundle_without_a_partial_plan(
    tmp_path: Path,
) -> None:
    source = FIXTURE.read_bytes()
    board = tmp_path / FIXTURE.name
    board.write_bytes(source)
    payload = _payload(board.name, source)

    stale = dict(payload, expect_board_revision=f"sha256:{'0' * 64}")
    stale_result = preview_route_bundle(stale, Settings(workspace=tmp_path))
    assert stale_result.status is RouteBundleStatus.NOT_ROUTED
    assert stale_result.plan is None
    assert stale_result.snapshot_digest is None

    duplicate = dict(payload, net_ref_ids=[net_id_for_name("HORIZONTAL")] * 2)
    with pytest.raises(RouteBundleError, match="distinct"):
        preview_route_bundle(duplicate, Settings(workspace=tmp_path))


def test_oversized_builtin_reference_list_refuses_before_element_validation(
    tmp_path: Path,
) -> None:
    source = FIXTURE.read_bytes()
    board = tmp_path / FIXTURE.name
    board.write_bytes(source)
    payload = _payload(board.name, source)
    payload["net_ref_ids"] = [object()] * 9

    with pytest.raises(RouteBundleError, match="bounded set of net references"):
        parse_route_bundle_request(payload)


def test_list_subclass_refuses_before_len_or_iteration(tmp_path: Path) -> None:
    class ExplosiveLenList(list[object]):
        def __len__(self) -> int:
            raise AssertionError("list subclasses must be rejected before len")

        def __iter__(self):  # type: ignore[override]
            raise AssertionError("list subclasses must be rejected before iteration")

    source = FIXTURE.read_bytes()
    board = tmp_path / FIXTURE.name
    board.write_bytes(source)
    payload = _payload(board.name, source)
    payload["net_ref_ids"] = ExplosiveLenList([object()] * 9)

    with pytest.raises(RouteBundleError, match="net_ref_ids must be an ordered list"):
        parse_route_bundle_request(payload)


def test_preview_preserves_request_order_while_canonicalizing_plan_candidates(
    tmp_path: Path,
) -> None:
    source = FIXTURE.read_bytes()
    board = tmp_path / FIXTURE.name
    board.write_bytes(source)
    payload = _payload(board.name, source)
    requested = [net_id_for_name("VERTICAL"), net_id_for_name("HORIZONTAL")]

    result = preview_route_bundle(
        dict(payload, net_ref_ids=requested), Settings(workspace=tmp_path)
    )

    assert result.status is RouteBundleStatus.ROUTED
    assert result.plan is not None
    assert result.plan.net_ref_ids == tuple(requested)
    assert [candidate.patch.net_id for candidate in result.plan.candidates] == sorted(requested)


def test_boundary_caps_the_seed_so_every_derived_per_net_seed_stays_in_range(
    tmp_path: Path,
) -> None:
    """A schema-valid seed must never reach the core as an out-of-range derived seed.

    Each reference derives ``seed + index``, so the request boundary has to reserve room for the
    largest reachable index.  Without that reservation the maximum JSON-safe seed produced a bare
    ``builtins.ValueError`` from the deterministic core instead of a typed boundary refusal.
    """

    source = FIXTURE.read_bytes()
    board = tmp_path / FIXTURE.name
    board.write_bytes(source)
    payload = _payload(board.name, source)

    for seed in (MAX_JSON_SAFE_INTEGER, MAX_BUNDLE_SEED + 1):
        with pytest.raises(RouteBundleError, match="seed must be between"):
            preview_route_bundle(dict(payload, seed=seed), Settings(workspace=tmp_path))

    accepted = parse_route_bundle_request(dict(payload, seed=MAX_BUNDLE_SEED))
    assert accepted.seed == MAX_BUNDLE_SEED
    assert accepted.seed + 7 == MAX_JSON_SAFE_INTEGER

    result = preview_route_bundle(dict(payload, seed=MAX_BUNDLE_SEED), Settings(workspace=tmp_path))
    assert result.status is RouteBundleStatus.ROUTED


class _FakeClock:
    """A monotonic clock the test advances explicitly, so no test depends on wall time."""

    def __init__(self) -> None:
        self.now = 1_000.0

    def monotonic(self) -> float:
        return self.now


def test_an_exhausted_budget_is_reported_as_exhaustion_not_a_replay_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A deadline shared by both runs must never be published as a determinism failure."""

    source = FIXTURE.read_bytes()
    board = tmp_path / FIXTURE.name
    board.write_bytes(source)
    from copper_mcp import route_bundle

    clock = _FakeClock()
    real = route_bundle.negotiate_routes
    calls: list[int] = []

    def expire_after_the_first_run(snapshot: Any, envelope: Any, **kwargs: Any) -> Any:
        calls.append(1)
        result = real(snapshot, envelope, **kwargs)
        if len(calls) == 1:
            clock.now += 3_600.0
        return result

    monkeypatch.setattr(route_bundle, "time", clock)
    monkeypatch.setattr(route_bundle, "negotiate_routes", expire_after_the_first_run)

    result = preview_route_bundle(_payload(board.name, source), Settings(workspace=tmp_path))

    assert len(calls) == 2
    assert result.status is RouteBundleStatus.NOT_ROUTED
    assert result.plan is None
    assert result.diagnostic == "the route-bundle composition exhausted its bounded time budget"


def test_a_genuine_structural_difference_still_reports_the_replay_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = FIXTURE.read_bytes()
    board = tmp_path / FIXTURE.name
    board.write_bytes(source)
    from copper_mcp import route_bundle

    real = route_bundle.negotiate_routes
    calls: list[int] = []

    def perturb_the_replay(snapshot: Any, envelope: Any, **kwargs: Any) -> Any:
        calls.append(1)
        result = real(snapshot, envelope, **kwargs)
        if len(calls) == 2:
            return replace(result, ripups=result.ripups + 1)
        return result

    monkeypatch.setattr(route_bundle, "negotiate_routes", perturb_the_replay)

    result = preview_route_bundle(_payload(board.name, source), Settings(workspace=tmp_path))

    assert result.status is RouteBundleStatus.NOT_ROUTED
    assert result.plan is None
    assert result.diagnostic == (
        "the deterministic route-bundle replay did not reproduce the allocation"
    )


def test_bundle_identity_binds_the_coordinator_policy_envelope_digest(tmp_path: Path) -> None:
    """The plan digest must change when the coordinator's policy envelope changes."""

    source = FIXTURE.read_bytes()
    board = tmp_path / FIXTURE.name
    board.write_bytes(source)
    net_class = NetClass(id="class:request", name="Request", **_constraints())
    profile = KiCadConstraintProfile(net_classes=(net_class,), default_net_class_id=net_class.id)
    converted = parse_kicad_bytes(source, profile)
    assert converted.snapshot is not None

    result = preview_route_bundle(_payload(board.name, source), Settings(workspace=tmp_path))

    assert result.status is RouteBundleStatus.ROUTED
    assert result.plan is not None
    envelope = NegotiatedRoutingRequest(
        board_revision=converted.snapshot.snapshot_digest,
        requests=_routes(converted.snapshot.snapshot_digest, result.request),
        max_iterations=8,
    )
    assert result.plan.policy_digest == envelope.policy_digest
    document = result.to_dict()["plan"]
    assert isinstance(document, dict)
    assert document["policy_digest"] == envelope.policy_digest

    # A different iteration ceiling is a different policy envelope and must not collide.
    other = NegotiatedRoutingRequest(
        board_revision=converted.snapshot.snapshot_digest,
        requests=envelope.requests,
        max_iterations=7,
    )
    negotiated = negotiate_routes(converted.snapshot, other)
    assert negotiated.status is NegotiatedRoutingStatus.COMPLETED
    assert negotiated.policy_digest != result.plan.policy_digest
    # The identity is bound to the envelope, so the same content under another policy is refused.
    with pytest.raises(RouteBundleError, match="does not match its immutable content"):
        replace(result.plan, policy_digest=negotiated.policy_digest)


def test_preview_refuses_a_stale_snapshot_digest_without_a_plan(tmp_path: Path) -> None:
    """A current board whose Board IR digest moved is refused after the source check."""

    source = FIXTURE.read_bytes()
    board = tmp_path / FIXTURE.name
    board.write_bytes(source)
    payload = dict(_payload(board.name, source), expect_snapshot_digest=f"sha256:{'1' * 64}")

    result = preview_route_bundle(payload, Settings(workspace=tmp_path))

    assert result.status is RouteBundleStatus.NOT_ROUTED
    assert result.plan is None
    assert result.board_revision == payload["expect_board_revision"]
    assert result.snapshot_digest is not None
    assert result.snapshot_digest != payload["expect_snapshot_digest"]
    assert result.diagnostic == "the observed scene no longer matches the current routing snapshot"
    assert not result.conversion_diagnostic_counts


def test_a_board_outside_the_ir_subset_reports_counts_without_a_plan_or_digest(
    tmp_path: Path,
) -> None:
    source = FIXTURE.read_bytes()
    unsupported = source.replace(b'(layer "Edge.Cuts")', b'(layer "F.SilkS")')
    assert unsupported != source
    board = tmp_path / FIXTURE.name
    board.write_bytes(unsupported)
    payload = dict(
        _payload(board.name, source),
        expect_board_revision=f"sha256:{hashlib.sha256(unsupported).hexdigest()}",
    )

    result = preview_route_bundle(payload, Settings(workspace=tmp_path))

    assert result.status is RouteBundleStatus.UNSUPPORTED_BOARD
    assert result.plan is None
    assert result.snapshot_digest is None
    assert result.diagnostic is None
    assert dict(result.conversion_diagnostic_counts) == {"geometry.missing": 1}


def test_every_real_service_response_validates_against_the_published_tool_contract(
    tmp_path: Path,
) -> None:
    """Pin the actual service-to-MCP seam, not only the advertised schema shape."""

    source = FIXTURE.read_bytes()
    unsupported = source.replace(b'(layer "Edge.Cuts")', b'(layer "F.SilkS")')
    board = tmp_path / FIXTURE.name
    board.write_bytes(source)
    unsupported_board = tmp_path / "unsupported.kicad_pcb"
    unsupported_board.write_bytes(unsupported)
    settings = Settings(workspace=tmp_path)
    payload = _payload(board.name, source)

    documents = [
        preview_route_bundle(payload, settings).to_dict(),
        preview_route_bundle(
            dict(payload, expect_board_revision=f"sha256:{'0' * 64}"), settings
        ).to_dict(),
        preview_route_bundle(
            dict(payload, expect_snapshot_digest=f"sha256:{'1' * 64}"), settings
        ).to_dict(),
        preview_route_bundle(
            dict(
                payload,
                board=unsupported_board.name,
                expect_board_revision=f"sha256:{hashlib.sha256(unsupported).hexdigest()}",
            ),
            settings,
        ).to_dict(),
    ]

    assert [document["status"] for document in documents] == [
        "routed",
        "not_routed",
        "not_routed",
        "unsupported_board",
    ]
    for document in documents:
        validated = RouteBundleToolResponse.model_validate(document)
        assert validated.model_dump(mode="json", exclude_none=False) == document
