"""Private immutable contracts for one complete branch-aware layered net tree."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from itertools import pairwise

from copper_mcp.board_ir import PointNM
from copper_mcp.routing.layered_astar import LayeredAStarSettings
from copper_mcp.routing.layered_contracts import (
    LayeredRouteDiagnostic,
    LayeredRoutePath,
    LayeredRouteVia,
)

_MAX_SAFE_INT = (1 << 53) - 1
_EMPTY_DIGEST = f"sha256:{'0' * 64}"
LAYERED_TREE_ROUTER_VERSION = "layered-tree-a-star/0.1.0"
LAYERED_TREE_POLICY = "private-branch-aware-layered-tree-v1"


def _integer(name: str, value: object, *, minimum: int = 0) -> None:
    if type(value) is not int or not minimum <= value <= _MAX_SAFE_INT:
        raise ValueError(f"{name} is outside the supported integer range")


def _typed_id(name: str, value: object, prefix: str) -> None:
    if (
        type(value) is not str
        or not value.startswith(prefix)
        or not 1 <= len(value.removeprefix(prefix)) <= 160
        or not all(
            character.isascii() and (character.isalnum() or character in "_.:-")
            for character in value
        )
    ):
        raise ValueError(f"{name} is malformed")


def _digest(name: str, value: object) -> None:
    if (
        type(value) is not str
        or len(value) != 71
        or not value.startswith("sha256:")
        or any(character not in "0123456789abcdef" for character in value[7:])
    ):
        raise ValueError(f"{name} must be content-addressed with sha256")


@dataclass(frozen=True, slots=True)
class LayeredTreeTerminal:
    pad_id: str
    layer_id: str

    def __post_init__(self) -> None:
        _typed_id("terminal pad ID", self.pad_id, "pad:")
        _typed_id("terminal layer ID", self.layer_id, "layer:")


@dataclass(frozen=True, slots=True)
class LayeredTreeBranch:
    branch_id: str
    start_pad_id: str
    end_pad_id: str
    attachment_point: PointNM
    attachment_layer_id: str
    paths: tuple[LayeredRoutePath, ...]
    vias: tuple[LayeredRouteVia, ...] = ()
    attachment_only: bool = False

    def __post_init__(self) -> None:
        _typed_id("branch ID", self.branch_id, "branch:")
        _typed_id("branch start pad ID", self.start_pad_id, "pad:")
        _typed_id("branch end pad ID", self.end_pad_id, "pad:")
        if self.start_pad_id == self.end_pad_id:
            raise ValueError("tree branch endpoints must be distinct")
        if type(self.attachment_point) is not PointNM:
            raise ValueError("tree branch attachment point is malformed")
        _typed_id("tree branch attachment layer", self.attachment_layer_id, "layer:")
        if type(self.attachment_only) is not bool:
            raise ValueError("tree branch attachment-only flag is malformed")
        if type(self.paths) is not tuple or any(
            type(path) is not LayeredRoutePath for path in self.paths
        ):
            raise ValueError("tree branch paths are malformed")
        if type(self.vias) is not tuple or any(
            type(via) is not LayeredRouteVia for via in self.vias
        ):
            raise ValueError("tree branch vias are malformed")
        if (
            len(self.paths) > 256
            or len(self.vias) > 256
            or sum(len(path.vertices) for path in self.paths) > 16_384
        ):
            raise ValueError("tree branch geometry exceeds its structural budget")
        if self.attachment_only and (self.paths or self.vias):
            raise ValueError("attachment-only tree branch cannot add geometry")
        if not self.attachment_only and (not self.paths or len(self.paths) != len(self.vias) + 1):
            raise ValueError("tree branch must be one path/via chain")

    @property
    def wire_length_nm(self) -> int:
        return sum(path.length_nm for path in self.paths)

    @property
    def bend_count(self) -> int:
        return sum(path.bend_count for path in self.paths)


@dataclass(frozen=True, slots=True)
class LayeredTreeMetrics:
    branch_count: int
    obstacles: int
    attachment_nodes: int
    expanded_states: int
    discovered_states: int
    peak_frontier_states: int
    obstacle_checks: int
    move_steps: int
    vias: int
    wire_length_nm: int
    bend_count: int

    def __post_init__(self) -> None:
        for name, value in (
            ("branch count", self.branch_count),
            ("obstacle count", self.obstacles),
            ("attachment node count", self.attachment_nodes),
            ("expanded states", self.expanded_states),
            ("discovered states", self.discovered_states),
            ("peak frontier states", self.peak_frontier_states),
            ("obstacle checks", self.obstacle_checks),
            ("move steps", self.move_steps),
            ("via count", self.vias),
            ("wire length", self.wire_length_nm),
            ("bend count", self.bend_count),
        ):
            _integer(name, value)


def layered_tree_physical_edges(
    branches: tuple[LayeredTreeBranch, ...],
) -> tuple[tuple[str, PointNM, PointNM], ...]:
    seen: set[tuple[str, PointNM, PointNM]] = set()
    result: list[tuple[str, PointNM, PointNM]] = []
    for branch in branches:
        for path in branch.paths:
            for start, end in pairwise(path.vertices):
                key = (path.layer_id, min(start, end), max(start, end))
                if key in seen:
                    continue
                seen.add(key)
                result.append((path.layer_id, start, end))
    return tuple(result)


def layered_tree_physical_vias(
    branches: tuple[LayeredTreeBranch, ...],
) -> tuple[LayeredRouteVia, ...]:
    by_center: dict[PointNM, LayeredRouteVia] = {}
    by_id: dict[str, LayeredRouteVia] = {}
    result: list[LayeredRouteVia] = []
    for branch in branches:
        for via in branch.vias:
            at_center = by_center.get(via.center)
            at_id = by_id.get(via.id)
            if (at_center is not None and at_center != via) or (at_id is not None and at_id != via):
                raise ValueError("shared tree via identity or geometry conflicts")
            if at_center is None:
                by_center[via.center] = via
                by_id[via.id] = via
                result.append(via)
    return tuple(result)


@dataclass(frozen=True, slots=True)
class LayeredTreeCandidate:
    candidate_id: str
    base_revision: str
    net_id: str
    terminals: tuple[LayeredTreeTerminal, ...]
    grid_step_nm: int
    width_nm: int
    via_diameter_nm: int
    via_drill_nm: int
    branches: tuple[LayeredTreeBranch, ...]
    metrics: LayeredTreeMetrics
    settings: LayeredAStarSettings
    router_version: str
    policy: str
    seed: int
    fill_binding: str | None = None

    def __post_init__(self) -> None:
        _digest("tree candidate ID", self.candidate_id)
        _digest("tree base revision", self.base_revision)
        if self.fill_binding is not None:
            _digest("tree fill binding", self.fill_binding)
        _typed_id("tree net ID", self.net_id, "net:")
        if (
            type(self.terminals) is not tuple
            or not 2 <= len(self.terminals) <= 32
            or any(type(item) is not LayeredTreeTerminal for item in self.terminals)
        ):
            raise ValueError("tree terminals must contain two through thirty-two pads")
        terminal_ids = tuple(item.pad_id for item in self.terminals)
        if terminal_ids != tuple(sorted(set(terminal_ids))):
            raise ValueError("tree terminal pad IDs must be unique and canonical")
        _integer("tree grid step", self.grid_step_nm, minimum=1)
        _integer("tree route width", self.width_nm, minimum=1)
        _integer("tree via diameter", self.via_diameter_nm, minimum=1)
        _integer("tree via drill", self.via_drill_nm, minimum=1)
        if self.via_drill_nm >= self.via_diameter_nm:
            raise ValueError("tree via drill must be smaller than its diameter")
        if type(self.branches) is not tuple or any(
            type(item) is not LayeredTreeBranch for item in self.branches
        ):
            raise ValueError("tree branches are malformed")
        if len(self.branches) != len(self.terminals) - 1:
            raise ValueError("tree branches must cover every non-root terminal")
        path_count = vertex_count = segment_count = via_references = 0
        for branch in self.branches:
            path_count += len(branch.paths)
            via_references += len(branch.vias)
            for path in branch.paths:
                vertex_count += len(path.vertices)
                segment_count += len(path.vertices) - 1
            if (
                path_count > 256
                or vertex_count > 16_384
                or segment_count > 16_384
                or via_references > 256
            ):
                raise ValueError("tree candidate geometry exceeds its structural budget")
        connected = {self.terminals[0].pad_id}
        for terminal, branch in zip(self.terminals[1:], self.branches, strict=True):
            if branch.start_pad_id != terminal.pad_id or branch.end_pad_id not in connected:
                raise ValueError("tree branches must add each terminal to the prior component")
            connected.add(terminal.pad_id)
        branch_ids = tuple(item.branch_id for item in self.branches)
        if len(set(branch_ids)) != len(branch_ids):
            raise ValueError("tree branch IDs must be unique")
        vias = layered_tree_physical_vias(self.branches)
        if any(
            via.diameter_nm != self.via_diameter_nm or via.drill_nm != self.via_drill_nm
            for via in vias
        ):
            raise ValueError("tree via dimensions are inconsistent")
        if type(self.metrics) is not LayeredTreeMetrics:
            raise ValueError("tree metrics are malformed")
        if type(self.settings) is not LayeredAStarSettings:
            raise ValueError("tree settings are malformed")
        if self.router_version != LAYERED_TREE_ROUTER_VERSION or self.policy != LAYERED_TREE_POLICY:
            raise ValueError("tree provenance is unsupported")
        _integer("tree seed", self.seed)
        if self.metrics.branch_count != len(self.branches):
            raise ValueError("tree branch accounting is inconsistent")
        if self.metrics.obstacles > self.settings.max_obstacles:
            raise ValueError("tree obstacle accounting exceeds its budget")
        if self.metrics.vias != len(vias):
            raise ValueError("tree via accounting is inconsistent")
        physical_length = sum(
            abs(start.x - end.x) + abs(start.y - end.y)
            for _layer, start, end in layered_tree_physical_edges(self.branches)
        )
        if self.metrics.wire_length_nm != physical_length:
            raise ValueError("tree wire-length accounting is inconsistent")
        if self.metrics.bend_count != sum(item.bend_count for item in self.branches):
            raise ValueError("tree bend accounting is inconsistent")
        if self.metrics.expanded_states > self.settings.max_expansions:
            raise ValueError("tree expansion accounting exceeds its budget")
        if self.metrics.discovered_states + self.metrics.attachment_nodes > self.settings.max_nodes:
            raise ValueError("tree node accounting exceeds its budget")
        if self.metrics.obstacle_checks > self.settings.max_obstacle_checks:
            raise ValueError("tree obstacle-check accounting exceeds its budget")


def canonical_layered_tree_candidate_bytes(
    candidate: LayeredTreeCandidate,
    *,
    checkpoint: Callable[[], None] | None = None,
) -> bytes:
    _admit_layered_tree_candidate(candidate)
    payload = {
        **({"fill_binding": candidate.fill_binding} if candidate.fill_binding is not None else {}),
        "base_revision": candidate.base_revision,
        "branches": [
            {
                "branch_id": branch.branch_id,
                "attachment_layer_id": branch.attachment_layer_id,
                "attachment_point": {
                    "x_nm": branch.attachment_point.x,
                    "y_nm": branch.attachment_point.y,
                },
                "attachment_only": branch.attachment_only,
                "end_pad_id": branch.end_pad_id,
                "paths": [
                    {
                        "layer_id": path.layer_id,
                        "vertices": [
                            _canonical_point(point, checkpoint) for point in path.vertices
                        ],
                    }
                    for path in branch.paths
                ],
                "start_pad_id": branch.start_pad_id,
                "vias": [
                    {
                        "center": {"x_nm": via.center.x, "y_nm": via.center.y},
                        "diameter_nm": via.diameter_nm,
                        "drill_nm": via.drill_nm,
                        "end_layer_id": via.end_layer_id,
                        "id": via.id,
                        "start_layer_id": via.start_layer_id,
                    }
                    for via in branch.vias
                ],
            }
            for branch in candidate.branches
        ],
        "metrics": {
            name: getattr(candidate.metrics, name)
            for name in (
                "bend_count",
                "attachment_nodes",
                "branch_count",
                "discovered_states",
                "expanded_states",
                "move_steps",
                "obstacles",
                "obstacle_checks",
                "peak_frontier_states",
                "vias",
                "wire_length_nm",
            )
        },
        "net_id": candidate.net_id,
        "policy": candidate.policy,
        "router_version": candidate.router_version,
        "seed": candidate.seed,
        "settings": {
            "max_expansions": candidate.settings.max_expansions,
            "max_nodes": candidate.settings.max_nodes,
            "max_obstacle_checks": candidate.settings.max_obstacle_checks,
            "max_obstacles": candidate.settings.max_obstacles,
            "max_vias": candidate.settings.max_vias,
            "move_cost": candidate.settings.move_cost,
            "via_cost": candidate.settings.via_cost,
        },
        "terminals": [
            {"layer_id": terminal.layer_id, "pad_id": terminal.pad_id}
            for terminal in candidate.terminals
        ],
        "grid_step_nm": candidate.grid_step_nm,
        "via_diameter_nm": candidate.via_diameter_nm,
        "via_drill_nm": candidate.via_drill_nm,
        "width_nm": candidate.width_nm,
    }
    return (
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
            "ascii"
        )
        + b"\n"
    )


def _admit_layered_tree_candidate(candidate: object) -> tuple[int, int]:
    """Validate bounded nested shape before any candidate iteration or hashing."""

    if type(candidate) is not LayeredTreeCandidate:
        raise ValueError("tree candidate is malformed")
    terminals: object = candidate.terminals
    branches: object = candidate.branches
    if (
        type(terminals) is not tuple
        or not 2 <= len(terminals) <= 32
        or type(branches) is not tuple
        or len(branches) != len(terminals) - 1
        or len(branches) > 31
    ):
        raise ValueError("tree candidate shape is malformed")
    if any(type(item) is not LayeredTreeTerminal for item in terminals):
        raise ValueError("tree candidate terminals are malformed")
    for terminal in terminals:
        terminal.__post_init__()
    path_count = vertex_count = segment_count = via_count = 0
    for branch in branches:
        if type(branch) is not LayeredTreeBranch:
            raise ValueError("tree candidate branch is malformed")
        paths: object = branch.paths
        vias: object = branch.vias
        if type(paths) is not tuple or type(vias) is not tuple:
            raise ValueError("tree candidate branch geometry is malformed")
        if path_count + len(paths) > 256 or via_count + len(vias) > 256:
            raise ValueError("tree candidate geometry exceeds its structural budget")
        path_count += len(paths)
        via_count += len(vias)
        for path in paths:
            if type(path) is not LayeredRoutePath or type(path.vertices) is not tuple:
                raise ValueError("tree candidate path is malformed")
            path_vertices = len(path.vertices)
            if (
                vertex_count + path_vertices > 16_384
                or segment_count + max(0, path_vertices - 1) > 16_384
            ):
                raise ValueError("tree candidate geometry exceeds its structural budget")
            vertex_count += path_vertices
            segment_count += max(0, path_vertices - 1)
            if any(type(point) is not PointNM for point in path.vertices):
                raise ValueError("tree candidate path point is malformed")
            path.__post_init__()
        if any(type(via) is not LayeredRouteVia for via in vias):
            raise ValueError("tree candidate via is malformed")
        for via in vias:
            via.__post_init__()
        branch.__post_init__()
    candidate.__post_init__()
    return segment_count, via_count


def _canonical_point(point: PointNM, checkpoint: Callable[[], None] | None) -> dict[str, int]:
    if checkpoint is not None:
        checkpoint()
    return {"x_nm": point.x, "y_nm": point.y}


def with_layered_tree_candidate_id(
    candidate: LayeredTreeCandidate,
    *,
    checkpoint: Callable[[], None] | None = None,
) -> LayeredTreeCandidate:
    from dataclasses import replace

    return replace(
        candidate,
        candidate_id="sha256:"
        + hashlib.sha256(
            canonical_layered_tree_candidate_bytes(candidate, checkpoint=checkpoint)
        ).hexdigest(),
    )


def verify_layered_tree_candidate_id(
    candidate: LayeredTreeCandidate,
    *,
    checkpoint: Callable[[], None] | None = None,
) -> bool:
    expected = (
        "sha256:"
        + hashlib.sha256(
            canonical_layered_tree_candidate_bytes(candidate, checkpoint=checkpoint)
        ).hexdigest()
    )
    if candidate.candidate_id != expected:
        raise ValueError("tree candidate ID does not match canonical content")
    return True


@dataclass(frozen=True, slots=True)
class LayeredTreeResult:
    candidate: LayeredTreeCandidate | None = None
    diagnostic: LayeredRouteDiagnostic | None = None

    def __post_init__(self) -> None:
        if (self.candidate is None) == (self.diagnostic is None):
            raise ValueError("tree result must contain exactly one outcome")


__all__: list[str] = []
