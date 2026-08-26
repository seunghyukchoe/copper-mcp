#!/usr/bin/env python3
"""Measure the closed public KiCad setup-field surface without accepting it.

The exact B-129 manifest remains the cohort authority. Board bytes are captured once,
the fixed-point classifier is independently rerun to select the six public setup-terminal
cases, and only aggregate counts from predeclared vocabularies are emitted.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from copper_mcp.adapters import kicad_board_ir
from copper_mcp.adapters.sexpr import SExpr, parse_sexpr
from copper_mcp.config import Settings
from copper_mcp.security import read_workspace_file
from scripts import benchmark_fixed_point_masking_census as masking

SCHEMA: Final = "copper-mcp/public-setup-field-census/v1"
EXPECTED_CAPTURED: Final = 13
EXPECTED_PUBLIC: Final = 10
EXPECTED_SETUP_TERMINALS: Final = 6
OTHER: Final = "other"

ACCEPTED_SETUP_HEADS: Final = frozenset(
    {
        "allow_soldermask_bridges_in_footprints",
        "capping",
        "covering",
        "filling",
        "pad_to_mask_clearance",
        "pcbplotparams",
        "plugging",
        "solder_mask_min_width",
        "tenting",
    }
)
DIRECT_SETUP_HEADS: Final = frozenset(
    {
        *ACCEPTED_SETUP_HEADS,
        "aux_axis_origin",
        "grid_origin",
        "pad_to_paste_clearance",
        "pad_to_paste_clearance_ratio",
        "stackup",
    }
)
STACKUP_HEADS: Final = frozenset(
    {
        "castellated_pads",
        "copper_finish",
        "dielectric_constraints",
        "edge_connector",
        "edge_plating",
        "layer",
    }
)
STACKUP_LAYER_HEADS: Final = frozenset(
    {
        "color",
        "epsilon_r",
        "loss_tangent",
        "material",
        "thickness",
        "type",
    }
)
SHAPE_BUCKETS: Final = (
    "empty",
    "one_atom",
    "many_atoms",
    "one_child",
    "many_children",
    "mixed",
)
UNSUPPORTED_SET_BUCKETS: Final = (
    "none",
    "stackup_only",
    "stackup_plus_other",
    "other_only",
)

Converter = Callable[[bytes, Settings], Any]


@dataclass(frozen=True, slots=True)
class BoardObservation:
    direct_occurrences: Mapping[str, int]
    direct_presence: frozenset[str]
    direct_shapes: Mapping[str, int]
    unsupported_set: str
    stackup_occurrences: Mapping[str, int]
    stackup_presence: frozenset[str]
    stackup_shapes: Mapping[str, int]
    layer_count: int
    layer_field_occurrences: Mapping[str, int]
    layer_field_presence: frozenset[str]
    layer_field_shapes: Mapping[str, int]


def _fixed_error(message: str) -> ValueError:
    return ValueError(message)


def _bucket(head: str | None, vocabulary: frozenset[str]) -> str:
    return head if head in vocabulary else OTHER


def _shape(node: SExpr) -> str:
    payload = node.items[1:]
    if not payload:
        return "empty"
    atoms = sum(isinstance(item, str) for item in payload)
    children = len(payload) - atoms
    if children == 0:
        return "one_atom" if atoms == 1 else "many_atoms"
    if atoms == 0:
        return "one_child" if children == 1 else "many_children"
    return "mixed"


def _children(node: SExpr) -> tuple[SExpr, ...]:
    return tuple(item for item in node.items[1:] if isinstance(item, SExpr))


def _require_child_expressions(node: SExpr, context: str) -> tuple[SExpr, ...]:
    payload = node.items[1:]
    if any(not isinstance(item, SExpr) for item in payload):
        raise _fixed_error(f"{context} must contain only child expressions")
    return tuple(item for item in payload if isinstance(item, SExpr))


def _single_setup(source: bytes, settings: Settings) -> SExpr:
    root = parse_sexpr(source, masking.parse_limits_for(settings))
    if root.head != "kicad_pcb":
        raise _fixed_error("source root must be kicad_pcb")
    setups = tuple(child for child in _children(root) if child.head == "setup")
    if len(setups) != 1:
        raise _fixed_error("each selected public source must contain exactly one direct setup")
    _require_child_expressions(setups[0], "setup")
    return setups[0]


def _unsupported_set(fields: Sequence[SExpr]) -> str:
    buckets = {
        _bucket(field.head, DIRECT_SETUP_HEADS)
        for field in fields
        if field.head not in ACCEPTED_SETUP_HEADS
    }
    if not buckets:
        return "none"
    if buckets == {"stackup"}:
        return "stackup_only"
    if "stackup" in buckets:
        return "stackup_plus_other"
    return "other_only"


def _observe_setup(setup: SExpr) -> BoardObservation:
    direct_occurrences: Counter[str] = Counter()
    direct_shapes: Counter[str] = Counter()
    direct_presence: set[str] = set()
    stackup_occurrences: Counter[str] = Counter()
    stackup_shapes: Counter[str] = Counter()
    stackup_presence: set[str] = set()
    layer_field_occurrences: Counter[str] = Counter()
    layer_field_shapes: Counter[str] = Counter()
    layer_field_presence: set[str] = set()
    layer_count = 0

    fields = _require_child_expressions(setup, "setup")
    for field in fields:
        direct_bucket = _bucket(field.head, DIRECT_SETUP_HEADS)
        direct_occurrences[direct_bucket] += 1
        direct_presence.add(direct_bucket)
        direct_shapes[f"{direct_bucket}:{_shape(field)}"] += 1
        if direct_bucket != "stackup":
            continue

        stackup_fields = _require_child_expressions(field, "stackup")
        for stackup_field in stackup_fields:
            stackup_bucket = _bucket(stackup_field.head, STACKUP_HEADS)
            stackup_occurrences[stackup_bucket] += 1
            stackup_presence.add(stackup_bucket)
            stackup_shapes[f"{stackup_bucket}:{_shape(stackup_field)}"] += 1
            if stackup_bucket != "layer":
                continue

            layer_payload = stackup_field.items[1:]
            if (
                not layer_payload
                or not isinstance(layer_payload[0], str)
                or any(not isinstance(item, SExpr) for item in layer_payload[1:])
            ):
                raise _fixed_error("stackup layer must have one positional atom then fields")
            layer_count += 1
            for layer_field in layer_payload[1:]:
                assert isinstance(layer_field, SExpr)
                layer_bucket = _bucket(layer_field.head, STACKUP_LAYER_HEADS)
                layer_field_occurrences[layer_bucket] += 1
                layer_field_presence.add(layer_bucket)
                layer_field_shapes[f"{layer_bucket}:{_shape(layer_field)}"] += 1

    return BoardObservation(
        direct_occurrences=dict(direct_occurrences),
        direct_presence=frozenset(direct_presence),
        direct_shapes=dict(direct_shapes),
        unsupported_set=_unsupported_set(fields),
        stackup_occurrences=dict(stackup_occurrences),
        stackup_presence=frozenset(stackup_presence),
        stackup_shapes=dict(stackup_shapes),
        layer_count=layer_count,
        layer_field_occurrences=dict(layer_field_occurrences),
        layer_field_presence=frozenset(layer_field_presence),
        layer_field_shapes=dict(layer_field_shapes),
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


def _select_setup_terminals(
    snapshots: Sequence[masking.Snapshot],
    *,
    settings: Settings,
    converter: Converter | None,
) -> tuple[masking.Snapshot, ...]:
    public = tuple(snapshot for snapshot in snapshots if snapshot.entry.visibility == "public")
    if len(public) != EXPECTED_PUBLIC:
        raise _fixed_error(f"expected {EXPECTED_PUBLIC} public entries, got {len(public)}")

    selected: list[masking.Snapshot] = []
    for snapshot in public:
        _, terminal, blocker = masking._classify_source_detail(
            snapshot.source,
            settings,
            converter=converter,
        )
        if terminal == "unmaskable" and blocker == "setup_semantics":
            selected.append(snapshot)
    if len(selected) != EXPECTED_SETUP_TERMINALS:
        raise _fixed_error(
            "fixed-point setup-terminal population drifted: "
            f"expected {EXPECTED_SETUP_TERMINALS}, got {len(selected)}"
        )
    return tuple(selected)


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


def measure(
    corpus: Path,
    manifest: Path,
    settings: Settings,
    *,
    expected_fingerprint: str | None = None,
    converter: Converter | None = None,
) -> dict[str, Any]:
    if settings.allow_apply or settings.allow_live_ipc or settings.allow_live_apply:
        raise _fixed_error("setup-field census is read-only")
    if ACCEPTED_SETUP_HEADS != kicad_board_ir._SETUP_METADATA_HEADS:
        raise _fixed_error("adapter accepted setup vocabulary drifted")

    entries, fingerprint = masking.load_manifest(manifest)
    expected = (
        masking.PREDECLARED_COHORT_FINGERPRINT
        if expected_fingerprint is None
        else expected_fingerprint
    )
    if not isinstance(expected, str) or expected != fingerprint or len(expected) != 39:
        raise _fixed_error("predeclared cohort fingerprint does not match")

    snapshots = masking.capture_snapshots(
        corpus,
        entries,
        max_bytes=settings.max_board_bytes,
    )
    if len(snapshots) != EXPECTED_CAPTURED:
        raise _fixed_error(
            f"expected {EXPECTED_CAPTURED} captured entries, got {len(snapshots)}"
        )
    selected = _select_setup_terminals(
        snapshots,
        settings=settings,
        converter=converter,
    )
    observations = tuple(
        _observe_setup(_single_setup(snapshot.source, settings)) for snapshot in selected
    )

    direct_keys = tuple(sorted(DIRECT_SETUP_HEADS)) + (OTHER,)
    stackup_keys = tuple(sorted(STACKUP_HEADS)) + (OTHER,)
    layer_keys = tuple(sorted(STACKUP_LAYER_HEADS)) + (OTHER,)

    direct_occurrences = _merge_mapping(observations, "direct_occurrences")
    direct_presence = _merge_presence(observations, "direct_presence")
    direct_shapes = _merge_mapping(observations, "direct_shapes")
    stackup_occurrences = _merge_mapping(observations, "stackup_occurrences")
    stackup_presence = _merge_presence(observations, "stackup_presence")
    stackup_shapes = _merge_mapping(observations, "stackup_shapes")
    layer_occurrences = _merge_mapping(observations, "layer_field_occurrences")
    layer_presence = _merge_presence(observations, "layer_field_presence")
    layer_shapes = _merge_mapping(observations, "layer_field_shapes")
    unsupported_sets = Counter(observation.unsupported_set for observation in observations)

    _verify_sources_unchanged(corpus, snapshots, settings)

    return {
        "schema": SCHEMA,
        "source_census": {
            "source_schema": masking.SCHEMA,
            "cohort_fingerprint": fingerprint,
            "captured_entries": len(snapshots),
            "public_entries": EXPECTED_PUBLIC,
            "setup_terminal_entries": len(selected),
            "selection_rule": "fixed_point_terminal_setup_semantics",
        },
        "closed_vocabularies": {
            "direct_setup": list(direct_keys),
            "stackup": list(stackup_keys),
            "stackup_layer": list(layer_keys),
            "shape": list(SHAPE_BUCKETS),
            "unsupported_set": list(UNSUPPORTED_SET_BUCKETS),
        },
        "aggregates": {
            "boards": len(observations),
            "direct_setup": {
                "occurrences": _closed_counts(direct_occurrences, direct_keys),
                "board_presence": _closed_counts(direct_presence, direct_keys),
                "shape_occurrences": _closed_shape_counts(direct_shapes, direct_keys),
            },
            "unsupported_head_sets": _closed_counts(
                unsupported_sets,
                UNSUPPORTED_SET_BUCKETS,
            ),
            "stackup": {
                "node_count": int(direct_occurrences.get("stackup", 0)),
                "field_occurrences": _closed_counts(stackup_occurrences, stackup_keys),
                "field_board_presence": _closed_counts(stackup_presence, stackup_keys),
                "field_shape_occurrences": _closed_shape_counts(
                    stackup_shapes,
                    stackup_keys,
                ),
                "layer_count": sum(observation.layer_count for observation in observations),
                "layer_field_occurrences": _closed_counts(layer_occurrences, layer_keys),
                "layer_field_board_presence": _closed_counts(layer_presence, layer_keys),
                "layer_field_shape_occurrences": _closed_shape_counts(
                    layer_shapes,
                    layer_keys,
                ),
            },
        },
        "source_hashes_unchanged": True,
        "privacy": {
            "aggregate_only": True,
            "atom_values_committed": 0,
            "board_identities_committed": 0,
            "board_paths_committed": 0,
            "board_digests_committed": 0,
            "board_bytes_committed": 0,
        },
        "claim_scope": {
            "measurement_only": True,
            "setup_acceptance": False,
            "stackup_acceptance": False,
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
    parser.add_argument(
        "--expected-fingerprint",
        default=masking.PREDECLARED_COHORT_FINGERPRINT,
    )
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    corpus = args.corpus.expanduser().resolve(strict=True)
    manifest = args.manifest.expanduser()
    if not manifest.is_file() or manifest.is_symlink():
        raise SystemExit("manifest must be a regular file")
    output = args.output.expanduser().resolve()
    if output == corpus or corpus in output.parents:
        raise SystemExit("output must be outside corpus")

    root = Path(__file__).resolve().parents[1]
    runner = Path(__file__).resolve()
    commit, dirty = masking._git_state(root)
    if dirty:
        raise SystemExit("measurement worktree must start clean")
    runner_bytes = runner.read_bytes()

    settings = Settings(workspace=corpus)
    result = measure(
        corpus,
        manifest,
        settings,
        expected_fingerprint=args.expected_fingerprint,
    )

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
                "operation": "read_only_closed_setup_field_census",
            },
            "committed_board_bytes": 0,
            "not_claimed": [
                "no setup or stackup product support",
                "no converted board, route, DRC, fabrication, or hardware result",
                "no board write, apply authority, editor mutation, or committed source input",
            ],
        }
    )
    canonical = json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False)
    result["run_id"] = "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
