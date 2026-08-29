#!/usr/bin/env python3
"""Measure the closed root-zone field surface behind B-137's two public terminals.

This is a measurement-only sibling of the setup, footprint, and copper-graphic censuses.  It
replays the exact B-129 cohort, re-walks the fixed-point masking instrument, requires B-137's
current successor partition, and proves that the two selected entries are B-136's two entries.
It then counts every root ``zone`` and the nested ``fill`` grammar on those entries.

Only aggregate counts from predeclared vocabularies are returned.  Board bytes, identities,
paths, per-board/source digests, atom values, coordinates, and geometry never enter the artifact.
The aggregate cohort fingerprint and artifact self-identities remain part of the sibling evidence
contract.  The new selection commitment deliberately starts unassigned, so the exact-corpus run
refuses until a later evidence commit freezes the observed membership.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import platform
import re
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from copper_mcp.adapters.sexpr import SExpr, is_quoted_atom, parse_sexpr
from copper_mcp.config import Settings
from copper_mcp.parse_budgets import parse_limits_for
from copper_mcp.security import read_workspace_file
from scripts import benchmark_fixed_point_masking_census as masking
from scripts import benchmark_public_copper_graphic_census as copper_graphic_census
from scripts import benchmark_public_setup_field_census as setup_census

SCHEMA: Final = "copper-mcp/public-root-zone-field-census/v1"
EXPECTED_CAPTURED: Final = 13
EXPECTED_PUBLIC: Final = 10
EXPECTED_ROOT_ZONE_TERMINALS: Final = 2
OTHER: Final = "other"
PREDECLARED_COHORT_FINGERPRINT: Final = masking.PREDECLARED_COHORT_FINGERPRINT

SELECTION_COMMITMENT_DOMAIN: Final = (
    b"copper-mcp/public-root-zone-field-census/selected-manifest-entries/v1\x00"
)
# Assigned only after the exact digest-bound B-129 corpus has selected the two B-137 successors.
# ``None`` is load-bearing: the first exact-corpus execution must refuse rather than measure before
# the membership freeze has its own evidence commit.
PREDECLARED_ROOT_ZONE_SELECTION_COMMITMENT: Final[str | None] = None

WALL_CODE: Final = "unsupported.construct"
ROOT_ZONE_WALL_MESSAGE: Final = "expression contains an unsupported semantic field"
FOOTPRINT_ZONE_MESSAGE: Final = "footprint-local zones are unsupported"
EDGE_CUTS_GRAPHIC_MESSAGE: Final = "footprint graphic on Edge.Cuts is unsupported"

_ZONE_CONTAINER_LOCATOR = re.compile(r"\Akicad_pcb\.zone\[[0-9]+\]\Z")
_ZONE_FILL_LOCATOR = re.compile(r"\Akicad_pcb\.zone\[[0-9]+\]\.fill\Z")
_FOOTPRINT_ZONE_LOCATOR = re.compile(r"\Akicad_pcb\.footprint\[[0-9]+\]\.zone\Z")
_FOOTPRINT_GRAPHIC_LOCATOR = re.compile(r"\Akicad_pcb\.footprint\[[0-9]+\]\.graphic\Z")

SUCCESSOR_KEYS: Final = (
    "zone_container",
    "zone_fill",
    "footprint_zone",
    "edge_cuts_graphic",
    OTHER,
)
PREDECLARED_SUCCESSOR_PARTITION: Final[dict[str, int]] = {
    "zone_container": 1,
    "zone_fill": 1,
    "footprint_zone": 3,
    "edge_cuts_graphic": 1,
    OTHER: 4,
}

# Frozen union of the top-level ``case T_*`` arms in KiCad 9.0 and 10.0 ``parseZONE``.
# Official sources:
# https://gitlab.com/kicad/code/kicad/-/blob/9.0/pcbnew/pcb_io/kicad_sexpr/pcb_io_kicad_sexpr_parser.cpp
# https://gitlab.com/kicad/code/kicad/-/blob/10.0/pcbnew/pcb_io/kicad_sexpr/pcb_io_kicad_sexpr_parser.cpp
# ``other`` remains outside this set so a KiCad 11 or third-party head cannot disappear.
ROOT_ZONE_HEADS: Final = frozenset(
    {
        "attr",
        "connect_pads",
        "fill",
        "fill_segments",
        "filled_areas_thickness",
        "filled_polygon",
        "hatch",
        "keepout",
        "layer",
        "layers",
        "locked",
        "min_thickness",
        "name",
        "net",
        "net_name",
        "placement",
        "polygon",
        "priority",
        "property",
        "tstamp",
        "uuid",
    }
)

# Frozen union of the nested child arms under ``case T_fill`` in the same KiCad 9/10 sources.
# The bare ``yes`` marker is a direct atom and therefore belongs to ``FILL_MARKER_BUCKETS`` rather
# than this child-expression vocabulary.
FILL_CHILD_HEADS: Final = frozenset(
    {
        "arc_segments",
        "hatch_border_algorithm",
        "hatch_gap",
        "hatch_min_hole_area",
        "hatch_orientation",
        "hatch_smoothing_level",
        "hatch_smoothing_value",
        "hatch_thickness",
        "island_area_min",
        "island_removal_mode",
        "mode",
        "radius",
        "smoothing",
        "thermal_bridge_width",
        "thermal_gap",
    }
)

ZONE_KIND_BUCKETS: Final = (
    "solid_candidate",
    "keepout_only",
    "placement_only",
    "keepout_and_placement",
    "duplicate_or_malformed",
)
DIRECT_ATOM_BUCKETS: Final = (
    "none",
    "locked_once",
    "locked_repeated",
    "other_or_quoted",
)
FILL_MARKER_BUCKETS: Final = (
    "fill_absent",
    "marker_absent",
    "yes_once",
    "yes_repeated",
    "other_or_quoted",
    "duplicate_fill",
)
MODE_BUCKETS: Final = ("absent", "polygon", "hatch", "segment", "invalid")
SMOOTHING_BUCKETS: Final = ("absent", "none", "chamfer", "fillet", "invalid")
ISLAND_REMOVAL_BUCKETS: Final = (
    "absent",
    "always_0",
    "never_1",
    "minimum_area_2",
    "invalid",
)
FILLED_AREA_THICKNESS_BUCKETS: Final = ("absent", "yes", "no", "invalid")
SHAPE_BUCKETS: Final = (
    "empty",
    "one_atom",
    "many_atoms",
    "one_child",
    "many_children",
    "mixed",
)

Converter = Callable[[bytes, Settings], Any]


@dataclass(frozen=True, slots=True)
class BoardObservation:
    zone_count: int
    root_occurrences: Mapping[str, int]
    root_presence: frozenset[str]
    root_shapes: Mapping[str, int]
    fill_occurrences: Mapping[str, int]
    fill_presence: frozenset[str]
    fill_shapes: Mapping[str, int]
    zone_kinds: Mapping[str, int]
    direct_atoms: Mapping[str, int]
    fill_markers: Mapping[str, int]
    modes: Mapping[str, int]
    smoothing: Mapping[str, int]
    island_removal: Mapping[str, int]
    filled_area_thickness: Mapping[str, int]


def _fixed_error(message: str) -> ValueError:
    return ValueError(message)


def _require_symbolic_head(node: SExpr, context: str) -> str:
    head = node.head
    if head is None or is_quoted_atom(head):
        raise _fixed_error(f"{context} must have an unquoted symbolic head")
    return head


def _bucket(head: str, vocabulary: frozenset[str]) -> str:
    return head if head in vocabulary else OTHER


def _children(node: SExpr, head: str) -> tuple[SExpr, ...]:
    return tuple(item for item in node.items[1:] if isinstance(item, SExpr) and item.head == head)


def _zone_kind(zone: SExpr) -> str:
    keepouts = len(_children(zone, "keepout"))
    placements = len(_children(zone, "placement"))
    if keepouts > 1 or placements > 1:
        return "duplicate_or_malformed"
    if keepouts == 0 and placements == 0:
        return "solid_candidate"
    if keepouts == 1 and placements == 0:
        return "keepout_only"
    if keepouts == 0 and placements == 1:
        return "placement_only"
    return "keepout_and_placement"


def _direct_atom_class(zone: SExpr) -> str:
    values = tuple(item for item in zone.items[1:] if isinstance(item, str))
    if not values:
        return "none"
    if any(is_quoted_atom(value) or value != "locked" for value in values):
        return "other_or_quoted"
    return "locked_once" if len(values) == 1 else "locked_repeated"


def _sole_unquoted_atom(nodes: Sequence[SExpr]) -> str | None:
    if len(nodes) != 1:
        return None
    payload = nodes[0].items[1:]
    if len(payload) != 1 or not isinstance(payload[0], str) or is_quoted_atom(payload[0]):
        return None
    return payload[0]


def _fill_marker_class(zone: SExpr) -> str:
    fills = _children(zone, "fill")
    if not fills:
        return "fill_absent"
    if len(fills) != 1:
        return "duplicate_fill"
    values = tuple(item for item in fills[0].items[1:] if isinstance(item, str))
    if not values:
        return "marker_absent"
    if any(is_quoted_atom(value) or value != "yes" for value in values):
        return "other_or_quoted"
    return "yes_once" if len(values) == 1 else "yes_repeated"


def _fill_leaf_class(
    zone: SExpr,
    head: str,
    accepted: Mapping[str, str],
) -> str:
    fills = _children(zone, "fill")
    if not fills:
        return "absent"
    if len(fills) != 1:
        return "invalid"
    leaves = _children(fills[0], head)
    if not leaves:
        return "absent"
    token = _sole_unquoted_atom(leaves)
    return accepted.get(token, "invalid") if token is not None else "invalid"


def _filled_area_thickness_class(zone: SExpr) -> str:
    fields = _children(zone, "filled_areas_thickness")
    if not fields:
        return "absent"
    token = _sole_unquoted_atom(fields)
    return token if token in {"yes", "no"} else "invalid"


def _root(source: bytes, settings: Settings) -> SExpr:
    root = parse_sexpr(source, parse_limits_for(settings))
    if _require_symbolic_head(root, "source root") != "kicad_pcb":
        raise _fixed_error("source root must be kicad_pcb")
    payload = root.items[1:]
    if any(not isinstance(item, SExpr) for item in payload):
        raise _fixed_error("source root must contain only child expressions")
    for item in payload:
        assert isinstance(item, SExpr)
        _require_symbolic_head(item, "source root child")
    if not _children(root, "zone"):
        raise _fixed_error("each selected public source must contain at least one root zone")
    return root


def _observe(root: SExpr) -> BoardObservation:
    if _require_symbolic_head(root, "source root") != "kicad_pcb":
        raise _fixed_error("source root must be kicad_pcb")
    zones = _children(root, "zone")
    if not zones:
        raise _fixed_error("each selected public source must contain at least one root zone")

    root_occurrences: Counter[str] = Counter()
    root_presence: set[str] = set()
    root_shapes: Counter[str] = Counter()
    fill_occurrences: Counter[str] = Counter()
    fill_presence: set[str] = set()
    fill_shapes: Counter[str] = Counter()
    zone_kinds: Counter[str] = Counter()
    direct_atoms: Counter[str] = Counter()
    fill_markers: Counter[str] = Counter()
    modes: Counter[str] = Counter()
    smoothing: Counter[str] = Counter()
    island_removal: Counter[str] = Counter()
    filled_area_thickness: Counter[str] = Counter()

    for zone in zones:
        _require_symbolic_head(zone, "root zone")
        zone_kinds[_zone_kind(zone)] += 1
        direct_atoms[_direct_atom_class(zone)] += 1
        fill_markers[_fill_marker_class(zone)] += 1
        modes[
            _fill_leaf_class(
                zone,
                "mode",
                {"polygon": "polygon", "hatch": "hatch", "segment": "segment"},
            )
        ] += 1
        smoothing[
            _fill_leaf_class(
                zone,
                "smoothing",
                {"none": "none", "chamfer": "chamfer", "fillet": "fillet"},
            )
        ] += 1
        island_removal[
            _fill_leaf_class(
                zone,
                "island_removal_mode",
                {"0": "always_0", "1": "never_1", "2": "minimum_area_2"},
            )
        ] += 1
        filled_area_thickness[_filled_area_thickness_class(zone)] += 1

        for field in (item for item in zone.items[1:] if isinstance(item, SExpr)):
            head = _require_symbolic_head(field, "root zone field")
            field_bucket = _bucket(head, ROOT_ZONE_HEADS)
            root_occurrences[field_bucket] += 1
            root_presence.add(field_bucket)
            root_shapes[f"{field_bucket}:{setup_census._shape(field)}"] += 1
            if head != "fill":
                continue
            for fill_field in (item for item in field.items[1:] if isinstance(item, SExpr)):
                fill_head = _require_symbolic_head(fill_field, "root zone fill field")
                fill_bucket = _bucket(fill_head, FILL_CHILD_HEADS)
                fill_occurrences[fill_bucket] += 1
                fill_presence.add(fill_bucket)
                fill_shapes[f"{fill_bucket}:{setup_census._shape(fill_field)}"] += 1

    return BoardObservation(
        zone_count=len(zones),
        root_occurrences=dict(root_occurrences),
        root_presence=frozenset(root_presence),
        root_shapes=dict(root_shapes),
        fill_occurrences=dict(fill_occurrences),
        fill_presence=frozenset(fill_presence),
        fill_shapes=dict(fill_shapes),
        zone_kinds=dict(zone_kinds),
        direct_atoms=dict(direct_atoms),
        fill_markers=dict(fill_markers),
        modes=dict(modes),
        smoothing=dict(smoothing),
        island_removal=dict(island_removal),
        filled_area_thickness=dict(filled_area_thickness),
    )


def _closed_counts(counter: Mapping[str, int], keys: Sequence[str]) -> dict[str, int]:
    return {key: int(counter.get(key, 0)) for key in keys}


def _closed_shape_counts(
    counter: Mapping[str, int],
    heads: Sequence[str],
) -> dict[str, int]:
    keys = tuple(f"{head}:{shape}" for head in heads for shape in SHAPE_BUCKETS)
    return _closed_counts(counter, keys)


def _merge_mapping(
    observations: Sequence[BoardObservation],
    attribute: str,
) -> Counter[str]:
    merged: Counter[str] = Counter()
    for observation in observations:
        value = getattr(observation, attribute)
        if not isinstance(value, Mapping):
            raise _fixed_error("internal aggregate mapping is invalid")
        merged.update(value)
    return merged


def _merge_presence(
    observations: Sequence[BoardObservation],
    attribute: str,
) -> Counter[str]:
    merged: Counter[str] = Counter()
    for observation in observations:
        value = getattr(observation, attribute)
        if not isinstance(value, frozenset):
            raise _fixed_error("internal aggregate presence set is invalid")
        merged.update(value)
    return merged


def _selection_commitment(snapshots: Sequence[masking.Snapshot]) -> str:
    digest = hashlib.sha256()
    digest.update(SELECTION_COMMITMENT_DOMAIN)
    digest.update(len(snapshots).to_bytes(4, "big"))
    for snapshot in snapshots:
        entry = snapshot.entry
        for value in (entry.identity, entry.visibility, entry.relative, entry.digest):
            encoded = value.encode("utf-8")
            digest.update(len(encoded).to_bytes(8, "big"))
            digest.update(encoded)
    return "sha256:" + digest.hexdigest()


def _required_digest(value: object, *, label: str) -> str:
    if value is None:
        raise _fixed_error(f"predeclared {label} selection commitment is unassigned")
    if (
        not isinstance(value, str)
        or len(value) != 71
        or not value.startswith("sha256:")
        or any(character not in "0123456789abcdef" for character in value[7:])
    ):
        raise _fixed_error(f"predeclared {label} selection commitment is malformed")
    return value


def _expected_selection_commitment() -> str:
    return _required_digest(
        PREDECLARED_ROOT_ZONE_SELECTION_COMMITMENT,
        label="root-zone",
    )


def _successor_class(result: Any) -> str:
    diagnostics = getattr(result, "diagnostics", ())
    if not diagnostics:
        return OTHER
    diagnostic = diagnostics[0]
    code = getattr(diagnostic, "code", None)
    message = getattr(diagnostic, "message", None)
    locator = getattr(diagnostic, "source_locator", None)
    if code != WALL_CODE or not isinstance(locator, str):
        return OTHER
    if message == ROOT_ZONE_WALL_MESSAGE:
        if _ZONE_CONTAINER_LOCATOR.fullmatch(locator):
            return "zone_container"
        if _ZONE_FILL_LOCATOR.fullmatch(locator):
            return "zone_fill"
        return OTHER
    if message == FOOTPRINT_ZONE_MESSAGE and _FOOTPRINT_ZONE_LOCATOR.fullmatch(locator):
        return "footprint_zone"
    if message == EDGE_CUTS_GRAPHIC_MESSAGE and _FOOTPRINT_GRAPHIC_LOCATOR.fullmatch(locator):
        return "edge_cuts_graphic"
    return OTHER


def _terminal_depth_and_class(
    source: bytes,
    settings: Settings,
    *,
    converter: Converter | None,
) -> tuple[int, str]:
    convert = converter or (lambda data, opts: masking._convert(data, "frozen-board", opts))
    current = source
    seen = {source}
    for depth in range(masking.MAX_MASK_PASSES + 1):
        try:
            result = convert(current, settings)
        except Exception:
            return depth, OTHER
        if getattr(result, "snapshot", None) is not None and not getattr(result, "diagnostics", ()):
            return depth, OTHER
        if depth == masking.MAX_MASK_PASSES:
            return depth, OTHER
        replacement = masking._mask_first_blocker(current, result, settings)
        if replacement is None:
            return depth, _successor_class(result)
        if len(replacement) >= len(current) or replacement in seen:
            return depth, _successor_class(result)
        seen.add(replacement)
        current = replacement
    return masking.MAX_MASK_PASSES, OTHER


def _select_root_zone_terminals(
    snapshots: Sequence[masking.Snapshot],
    *,
    settings: Settings,
    converter: Converter | None,
) -> tuple[tuple[masking.Snapshot, ...], dict[str, int]]:
    public = tuple(snapshot for snapshot in snapshots if snapshot.entry.visibility == "public")
    if len(public) != EXPECTED_PUBLIC:
        raise _fixed_error(f"expected {EXPECTED_PUBLIC} public entries, got {len(public)}")

    partition: Counter[str] = Counter()
    selected: list[masking.Snapshot] = []
    for snapshot in public:
        depth, terminal, blocker = masking._classify_source_detail(
            snapshot.source,
            settings,
            converter=converter,
        )
        walk_depth, successor = _terminal_depth_and_class(
            snapshot.source,
            settings,
            converter=converter,
        )
        if walk_depth != depth:
            raise _fixed_error("terminal walk disagrees with the fixed-point classifier")
        if terminal != "unmaskable" or blocker != OTHER:
            successor = OTHER
        partition[successor] += 1
        if successor in {"zone_container", "zone_fill"}:
            selected.append(snapshot)

    closed_partition = _closed_counts(partition, SUCCESSOR_KEYS)
    if closed_partition != PREDECLARED_SUCCESSOR_PARTITION:
        raise _fixed_error("B-137 successor partition drifted")
    if sum(partition.values()) != sum(closed_partition.values()):
        raise _fixed_error("B-137 successor partition contains an undeclared bucket")
    if len(selected) != EXPECTED_ROOT_ZONE_TERMINALS:
        raise _fixed_error(
            "fixed-point root-zone terminal population drifted: "
            f"expected {EXPECTED_ROOT_ZONE_TERMINALS}, got {len(selected)}"
        )
    return tuple(selected), closed_partition


def _verify_sources_unchanged(
    corpus: Path,
    snapshots: Sequence[masking.Snapshot],
    settings: Settings,
) -> None:
    for snapshot in snapshots:
        try:
            current = read_workspace_file(
                corpus,
                snapshot.entry.relative,
                allowed_suffixes={".kicad_pcb"},
                max_bytes=settings.max_board_bytes,
            ).content
        except Exception as error:
            raise _fixed_error("source changed or became unavailable") from error
        if current != snapshot.source:
            raise _fixed_error("source changed during measurement")


def _reconcile_projection(
    name: str,
    raw: Mapping[str, int],
    projected: Mapping[str, int],
) -> None:
    if sum(raw.values()) != sum(projected.values()):
        raise _fixed_error(f"{name} buckets do not partition their population")


def measure(
    corpus: Path,
    manifest: Path,
    settings: Settings,
    *,
    converter: Converter | None = None,
) -> dict[str, Any]:
    if settings.allow_apply or settings.allow_live_ipc or settings.allow_live_apply:
        raise _fixed_error("root-zone field census is read-only")
    expected_selection = _expected_selection_commitment()

    entries, fingerprint = masking.load_manifest(manifest)
    expected_fingerprint = PREDECLARED_COHORT_FINGERPRINT
    if (
        not isinstance(expected_fingerprint, str)
        or expected_fingerprint != fingerprint
        or len(expected_fingerprint) != 39
    ):
        raise _fixed_error("predeclared cohort fingerprint does not match")

    snapshots = masking.capture_snapshots(
        corpus,
        entries,
        max_bytes=settings.max_board_bytes,
    )
    if len(snapshots) != EXPECTED_CAPTURED:
        raise _fixed_error(f"expected {EXPECTED_CAPTURED} captured entries, got {len(snapshots)}")
    selected, successor_partition = _select_root_zone_terminals(
        snapshots,
        settings=settings,
        converter=converter,
    )

    predecessor_expected = _required_digest(
        copper_graphic_census.PREDECLARED_COPPER_GRAPHIC_SELECTION_COMMITMENT,
        label="B-136 copper-graphic",
    )
    predecessor_observed = copper_graphic_census._selection_commitment(selected)
    same_cohort_as_b136 = hmac.compare_digest(predecessor_expected, predecessor_observed)
    if not same_cohort_as_b136:
        raise _fixed_error("root-zone terminals are not B-136's two boards")

    observed_selection = _selection_commitment(selected)
    if not hmac.compare_digest(expected_selection, observed_selection):
        raise _fixed_error("fixed-point root-zone terminal membership drifted")

    observations = tuple(_observe(_root(snapshot.source, settings)) for snapshot in selected)
    root_keys = (*sorted(ROOT_ZONE_HEADS), OTHER)
    fill_keys = (*sorted(FILL_CHILD_HEADS), OTHER)

    root_occurrences = _merge_mapping(observations, "root_occurrences")
    root_presence = _merge_presence(observations, "root_presence")
    root_shapes = _merge_mapping(observations, "root_shapes")
    fill_occurrences = _merge_mapping(observations, "fill_occurrences")
    fill_presence = _merge_presence(observations, "fill_presence")
    fill_shapes = _merge_mapping(observations, "fill_shapes")
    zone_kinds = _merge_mapping(observations, "zone_kinds")
    direct_atoms = _merge_mapping(observations, "direct_atoms")
    fill_markers = _merge_mapping(observations, "fill_markers")
    modes = _merge_mapping(observations, "modes")
    smoothing = _merge_mapping(observations, "smoothing")
    island_removal = _merge_mapping(observations, "island_removal")
    filled_area_thickness = _merge_mapping(observations, "filled_area_thickness")

    root_counts = _closed_counts(root_occurrences, root_keys)
    root_presence_counts = _closed_counts(root_presence, root_keys)
    root_shape_counts = _closed_shape_counts(root_shapes, root_keys)
    fill_counts = _closed_counts(fill_occurrences, fill_keys)
    fill_presence_counts = _closed_counts(fill_presence, fill_keys)
    fill_shape_counts = _closed_shape_counts(fill_shapes, fill_keys)
    zone_kind_counts = _closed_counts(zone_kinds, ZONE_KIND_BUCKETS)
    direct_atom_counts = _closed_counts(direct_atoms, DIRECT_ATOM_BUCKETS)
    fill_marker_counts = _closed_counts(fill_markers, FILL_MARKER_BUCKETS)
    mode_counts = _closed_counts(modes, MODE_BUCKETS)
    smoothing_counts = _closed_counts(smoothing, SMOOTHING_BUCKETS)
    island_counts = _closed_counts(island_removal, ISLAND_REMOVAL_BUCKETS)
    filled_area_counts = _closed_counts(
        filled_area_thickness,
        FILLED_AREA_THICKNESS_BUCKETS,
    )

    for name, raw, projected in (
        ("root-zone field", root_occurrences, root_counts),
        ("root-zone field presence", root_presence, root_presence_counts),
        ("root-zone field shape", root_shapes, root_shape_counts),
        ("fill field", fill_occurrences, fill_counts),
        ("fill field presence", fill_presence, fill_presence_counts),
        ("fill field shape", fill_shapes, fill_shape_counts),
        ("zone kind", zone_kinds, zone_kind_counts),
        ("direct atom", direct_atoms, direct_atom_counts),
        ("fill marker", fill_markers, fill_marker_counts),
        ("fill mode", modes, mode_counts),
        ("fill smoothing", smoothing, smoothing_counts),
        ("island removal", island_removal, island_counts),
        ("filled-area thickness", filled_area_thickness, filled_area_counts),
    ):
        _reconcile_projection(name, raw, projected)

    zone_total = sum(observation.zone_count for observation in observations)
    if sum(root_shape_counts.values()) != sum(root_counts.values()):
        raise _fixed_error("root-zone field shapes do not partition field occurrences")
    if sum(fill_shape_counts.values()) != sum(fill_counts.values()):
        raise _fixed_error("fill field shapes do not partition field occurrences")
    for name, partition in (
        ("zone kind", zone_kind_counts),
        ("direct atom", direct_atom_counts),
        ("fill marker", fill_marker_counts),
        ("fill mode", mode_counts),
        ("fill smoothing", smoothing_counts),
        ("island removal", island_counts),
        ("filled-area thickness", filled_area_counts),
    ):
        if sum(partition.values()) != zone_total:
            raise _fixed_error(f"{name} buckets do not partition root zones")

    _verify_sources_unchanged(corpus, snapshots, settings)

    return {
        "schema": SCHEMA,
        "source_census": {
            "source_schema": masking.SCHEMA,
            "sibling_schema": copper_graphic_census.SCHEMA,
            "cohort_fingerprint": fingerprint,
            "captured_entries": len(snapshots),
            "public_entries": EXPECTED_PUBLIC,
            "root_zone_terminal_entries": len(selected),
            "selection_rule": "fixed_point_terminal_root_zone_container_allowlist",
            "b137_successor_partition": successor_partition,
            "b137_successor_partition_matches": True,
            "same_cohort_as_b136": same_cohort_as_b136,
        },
        "closed_vocabularies": {
            "root_zone_child": list(root_keys),
            "fill_child": list(fill_keys),
            "shape": list(SHAPE_BUCKETS),
            "successor": list(SUCCESSOR_KEYS),
            "zone_kind": list(ZONE_KIND_BUCKETS),
            "direct_atom": list(DIRECT_ATOM_BUCKETS),
            "fill_marker": list(FILL_MARKER_BUCKETS),
            "mode": list(MODE_BUCKETS),
            "smoothing": list(SMOOTHING_BUCKETS),
            "island_removal": list(ISLAND_REMOVAL_BUCKETS),
            "filled_area_thickness": list(FILLED_AREA_THICKNESS_BUCKETS),
        },
        "aggregates": {
            "boards": len(observations),
            "root_zone_count": zone_total,
            "root_zone_children": {
                "occurrences": root_counts,
                "board_presence": root_presence_counts,
                "shape_occurrences": root_shape_counts,
            },
            "fill_children": {
                "occurrences": fill_counts,
                "board_presence": fill_presence_counts,
                "shape_occurrences": fill_shape_counts,
            },
            "zone_kind": zone_kind_counts,
            "direct_atoms": direct_atom_counts,
            "fill_marker": fill_marker_counts,
            "mode": mode_counts,
            "smoothing": smoothing_counts,
            "island_removal": island_counts,
            "filled_area_thickness": filled_area_counts,
        },
        "source_hashes_unchanged": True,
        "privacy": {
            "aggregate_only": True,
            "atom_values_committed": 0,
            "coordinates_committed": 0,
            "geometry_values_committed": 0,
            "board_identities_committed": 0,
            "board_paths_committed": 0,
            "board_digests_committed": 0,
            "board_bytes_committed": 0,
        },
        "claim_scope": {
            "measurement_only": True,
            "root_zone_acceptance": False,
            "cached_fill_validation": False,
            "conversion_success": False,
            "board_ir_schema_change": False,
            "production_behavior_change": False,
        },
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    corpus, manifest, output_target, runner = setup_census._resolve_cli_paths(
        args.corpus,
        args.manifest,
        args.output,
        Path(__file__),
    )
    try:
        root = runner.parents[1]
        commit, dirty = masking._git_state(root)
        if dirty:
            raise SystemExit("measurement worktree must start clean")
        runner_bytes = runner.read_bytes()

        settings = Settings(workspace=corpus)
        result = measure(corpus, manifest, settings)

        final_commit, final_dirty = masking._git_state(root)
        if final_commit != commit or final_dirty or runner.read_bytes() != runner_bytes:
            raise SystemExit("measurement inputs changed during run")
        result.update(
            {
                "commit": commit,
                "dirty": False,
                "recorded_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
                "runner_digest": "sha256:" + hashlib.sha256(runner_bytes).hexdigest(),
                "environment": {
                    "platform": platform.platform(),
                    "python": platform.python_version(),
                },
                "configuration": {
                    "max_manifest_bytes": masking.MAX_MANIFEST_BYTES,
                    "max_source_bytes": settings.max_board_bytes,
                    "operation": "read_only_closed_root_zone_field_census",
                },
                "committed_board_bytes": 0,
                "not_claimed": [
                    "no root-zone or nested-fill product support",
                    "no cached-fill validation or geometry interpretation",
                    "no converted board, route, DRC, fabrication, or hardware result",
                    "no board write, apply authority, editor mutation, or committed source input",
                ],
            }
        )
        canonical = json.dumps(
            result,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        result["run_id"] = "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()
        setup_census._write_output(
            output_target,
            json.dumps(result, sort_keys=True, indent=2) + "\n",
        )
        return 0
    finally:
        output_target.close()


if __name__ == "__main__":
    raise SystemExit(main())
