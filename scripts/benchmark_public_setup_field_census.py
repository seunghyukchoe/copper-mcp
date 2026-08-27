#!/usr/bin/env python3
"""Measure the closed public KiCad setup-field surface without accepting it.

The exact B-129 manifest remains the cohort authority. Board bytes are captured once,
the fixed-point classifier is independently rerun to select the six public setup-terminal
cases, and only aggregate counts from predeclared vocabularies are emitted.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import platform
import stat
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from copper_mcp.adapters import kicad_board_ir
from copper_mcp.adapters.sexpr import SExpr, is_quoted_atom, parse_sexpr
from copper_mcp.config import Settings
from copper_mcp.security import read_workspace_file
from scripts import benchmark_fixed_point_masking_census as masking

SCHEMA: Final = "copper-mcp/public-setup-field-census/v1"
EXPECTED_CAPTURED: Final = 13
EXPECTED_PUBLIC: Final = 10
EXPECTED_SETUP_TERMINALS: Final = 6
OTHER: Final = "other"
PREDECLARED_COHORT_FINGERPRINT: Final = masking.PREDECLARED_COHORT_FINGERPRINT
SELECTION_COMMITMENT_DOMAIN: Final = (
    b"copper-mcp/public-setup-field-census/selected-manifest-entries/v1\x00"
)
# Assigned once, from the exact B-129 cohort, in the pull request that first ran this instrument.
# It is a *freeze*, not a prediction: the six entries it binds were selected by rerunning the
# fixed-point classifier, and the constant records which six, so that any later rerun whose
# selection differs -- a drifted classifier, a re-derived corpus, a swapped manifest row -- fails
# instead of silently re-aggregating over a different population. `EXPECTED_SETUP_TERMINALS` alone
# cannot catch that: a same-count membership swap keeps the count and changes the answer, which is
# exactly what `test_measure_rejects_same_count_selection_membership_drift` exercises.
PREDECLARED_SETUP_SELECTION_COMMITMENT: Final[str | None] = (
    "sha256:bda70bb147c572f316f0ae218a8a0daed225e392f4c315b71947c5a88083e9e1"
)

# The adapter's accepted `setup` vocabulary **as it stood when B-130 was taken**, frozen here so
# the artifact stays replayable.  It was a live mirror of `kicad_board_ir._SETUP_METADATA_HEADS`
# until D-227 accepted five of the heads this census had just measured as unsupported; leaving it a
# mirror would have made a rerun silently answer a different question -- `unsupported_head_sets`
# would collapse to `none` and the recorded aggregate would no longer be reproducible from the same
# cohort.  The drift guard in `measure` therefore checks *containment* rather than equality: the
# adapter may widen past this set, which is what D-227 did, but a head accepted at B-130 and
# refused later would invalidate the artifact's reading and fails the run.
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


@dataclass(slots=True)
class OutputTarget:
    path: Path
    parent_fd: int

    def close(self) -> None:
        if self.parent_fd < 0:
            return
        descriptor = self.parent_fd
        self.parent_fd = -1
        os.close(descriptor)


def _fixed_error(message: str) -> ValueError:
    return ValueError(message)


def _require_symbolic_head(node: SExpr, context: str) -> str:
    head = node.head
    if head is None or is_quoted_atom(head):
        raise _fixed_error(f"{context} must have an unquoted symbolic head")
    return head


def _bucket(head: str, vocabulary: frozenset[str]) -> str:
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


def _require_child_expressions(node: SExpr, context: str) -> tuple[SExpr, ...]:
    _require_symbolic_head(node, context)
    payload = node.items[1:]
    if any(not isinstance(item, SExpr) for item in payload):
        raise _fixed_error(f"{context} must contain only child expressions")
    children = tuple(item for item in payload if isinstance(item, SExpr))
    for child in children:
        _require_symbolic_head(child, f"{context} child")
    return children


def _single_setup(source: bytes, settings: Settings) -> SExpr:
    root = parse_sexpr(source, masking.parse_limits_for(settings))
    if _require_symbolic_head(root, "source root") != "kicad_pcb":
        raise _fixed_error("source root must be kicad_pcb")
    root_children = _require_child_expressions(root, "source root")
    setups = tuple(
        child
        for child in root_children
        if _require_symbolic_head(child, "source root child") == "setup"
    )
    if len(setups) != 1:
        raise _fixed_error("each selected public source must contain exactly one direct setup")
    _require_child_expressions(setups[0], "setup")
    return setups[0]


def _unsupported_set(fields: Sequence[SExpr]) -> str:
    buckets: set[str] = set()
    for field in fields:
        head = _require_symbolic_head(field, "setup field")
        if head not in ACCEPTED_SETUP_HEADS:
            buckets.add(_bucket(head, DIRECT_SETUP_HEADS))
    if not buckets:
        return "none"
    if buckets == {"stackup"}:
        return "stackup_only"
    if "stackup" in buckets:
        return "stackup_plus_other"
    return "other_only"


def _observe_setup(setup: SExpr) -> BoardObservation:
    if _require_symbolic_head(setup, "setup") != "setup":
        raise _fixed_error("setup expression must be setup")

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
        field_head = _require_symbolic_head(field, "setup field")
        direct_bucket = _bucket(field_head, DIRECT_SETUP_HEADS)
        direct_occurrences[direct_bucket] += 1
        direct_presence.add(direct_bucket)
        direct_shapes[f"{direct_bucket}:{_shape(field)}"] += 1
        if direct_bucket != "stackup":
            continue

        stackup_fields = _require_child_expressions(field, "stackup")
        for stackup_field in stackup_fields:
            stackup_head = _require_symbolic_head(stackup_field, "stackup field")
            stackup_bucket = _bucket(stackup_head, STACKUP_HEADS)
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
                layer_head = _require_symbolic_head(layer_field, "stackup layer field")
                layer_bucket = _bucket(layer_head, STACKUP_LAYER_HEADS)
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


def _expected_selection_commitment() -> str:
    expected = PREDECLARED_SETUP_SELECTION_COMMITMENT
    if (
        not isinstance(expected, str)
        or len(expected) != 71
        or not expected.startswith("sha256:")
        or any(character not in "0123456789abcdef" for character in expected[7:])
    ):
        if expected is None:
            raise _fixed_error("predeclared setup selection commitment is unassigned")
        raise _fixed_error("predeclared setup selection commitment is malformed")
    return expected


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
    converter: Converter | None = None,
) -> dict[str, Any]:
    if settings.allow_apply or settings.allow_live_ipc or settings.allow_live_apply:
        raise _fixed_error("setup-field census is read-only")
    if not ACCEPTED_SETUP_HEADS <= kicad_board_ir._SETUP_METADATA_HEADS:
        raise _fixed_error("adapter accepted setup vocabulary drifted")
    expected_selection = _expected_selection_commitment()

    entries, fingerprint = masking.load_manifest(manifest)
    expected = PREDECLARED_COHORT_FINGERPRINT
    if not isinstance(expected, str) or expected != fingerprint or len(expected) != 39:
        raise _fixed_error("predeclared cohort fingerprint does not match")

    snapshots = masking.capture_snapshots(
        corpus,
        entries,
        max_bytes=settings.max_board_bytes,
    )
    if len(snapshots) != EXPECTED_CAPTURED:
        raise _fixed_error(f"expected {EXPECTED_CAPTURED} captured entries, got {len(snapshots)}")
    selected = _select_setup_terminals(
        snapshots,
        settings=settings,
        converter=converter,
    )
    observed_selection = _selection_commitment(selected)
    if not hmac.compare_digest(expected_selection, observed_selection):
        raise _fixed_error("fixed-point setup-terminal membership drifted")

    observations = tuple(
        _observe_setup(_single_setup(snapshot.source, settings)) for snapshot in selected
    )

    direct_keys = (*sorted(DIRECT_SETUP_HEADS), OTHER)
    stackup_keys = (*sorted(STACKUP_HEADS), OTHER)
    layer_keys = (*sorted(STACKUP_LAYER_HEADS), OTHER)

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
    return parser


def _open_output_parent(parent: Path) -> int:
    if os.open not in os.supports_dir_fd:
        raise SystemExit("platform must support anchored output creation")
    if not hasattr(os, "O_DIRECTORY") or not hasattr(os, "O_NOFOLLOW"):
        raise SystemExit("platform must support anchored output creation")

    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    try:
        expected = parent.stat(follow_symlinks=False)
    except OSError as error:
        raise SystemExit("output parent must be an existing directory") from error
    if not stat.S_ISDIR(expected.st_mode):
        raise SystemExit("output parent must be an existing directory")

    descriptor = -1
    try:
        descriptor = os.open(parent.anchor, flags)
        for component in parent.parts[1:]:
            next_descriptor = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
    except (NotImplementedError, OSError) as error:
        if descriptor >= 0:
            os.close(descriptor)
        raise SystemExit("output parent must be an anchored no-follow directory") from error

    actual = os.fstat(descriptor)
    if (actual.st_dev, actual.st_ino) != (expected.st_dev, expected.st_ino):
        os.close(descriptor)
        raise SystemExit("output parent changed during validation")
    return descriptor


def _resolve_cli_paths(
    corpus_argument: Path,
    manifest_argument: Path,
    output_argument: Path,
    runner_argument: Path,
) -> tuple[Path, Path, OutputTarget, Path]:
    try:
        corpus = corpus_argument.expanduser().resolve(strict=True)
    except OSError as error:
        raise SystemExit("corpus must be an existing directory") from error
    if not corpus.is_dir():
        raise SystemExit("corpus must be an existing directory")

    manifest_input = manifest_argument.expanduser()
    if manifest_input.is_symlink():
        raise SystemExit("manifest must be a regular file")
    try:
        manifest = manifest_input.resolve(strict=True)
    except OSError as error:
        raise SystemExit("manifest must be a regular file") from error
    if not manifest.is_file():
        raise SystemExit("manifest must be a regular file")

    output_input = output_argument.expanduser()
    if output_input.suffix != ".json":
        raise SystemExit("output must use a .json suffix")
    if output_input.is_symlink():
        raise SystemExit("output must not be a symlink")
    try:
        output_parent = output_input.parent.resolve(strict=True)
    except OSError as error:
        raise SystemExit("output parent must be an existing directory") from error
    if not output_parent.is_dir():
        raise SystemExit("output parent must be an existing directory")
    output = output_parent / output_input.name
    if output.exists() or output.is_symlink():
        raise SystemExit("output must be a new path")
    if output == corpus or corpus in output.parents:
        raise SystemExit("output must be outside corpus")

    try:
        runner = runner_argument.expanduser().resolve(strict=True)
    except OSError as error:
        raise SystemExit("runner must be an existing regular file") from error
    if not runner.is_file():
        raise SystemExit("runner must be an existing regular file")

    parent_fd = _open_output_parent(output_parent)
    return corpus, manifest, OutputTarget(path=output, parent_fd=parent_fd), runner


def _write_output(target: OutputTarget, payload: str) -> None:
    """Publish a complete artifact or none at all.

    The payload is staged in a private sibling inside the already-anchored parent, flushed and
    fsynced, and only then linked to the final name. Writing straight into the create-exclusive
    destination would leave a truncated JSON body behind on a full disk, a quota refusal or an
    interruption -- and because `_resolve_cli_paths` requires a previously nonexistent output,
    that stub would also block the retry until someone deleted it by hand. `os.link` keeps the
    publish create-only, so the destination is still never overwritten and still never followed.
    """

    if target.parent_fd < 0:
        raise SystemExit("output parent descriptor is closed")
    if os.link not in os.supports_dir_fd:
        raise SystemExit("platform must support anchored output creation")

    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    staged = f".{target.path.name}.{os.getpid()}.{os.urandom(8).hex()}.tmp"
    try:
        descriptor = os.open(staged, flags, 0o600, dir_fd=target.parent_fd)
    except (NotImplementedError, OSError) as error:
        raise SystemExit("output could not be created safely") from error

    try:
        try:
            stream = os.fdopen(descriptor, "w", encoding="utf-8")
        except BaseException:
            os.close(descriptor)
            raise
        with stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(
                staged,
                target.path.name,
                src_dir_fd=target.parent_fd,
                dst_dir_fd=target.parent_fd,
            )
        except FileExistsError as error:
            raise SystemExit("output must remain a new path") from error
        except (NotImplementedError, OSError) as error:
            raise SystemExit("output could not be published safely") from error
    finally:
        try:
            os.unlink(staged, dir_fd=target.parent_fd)
        except OSError:
            pass


def main() -> int:
    args = _build_parser().parse_args()
    corpus, manifest, output_target, runner = _resolve_cli_paths(
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
        canonical = json.dumps(
            result,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        result["run_id"] = "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()
        _write_output(
            output_target,
            json.dumps(result, sort_keys=True, indent=2) + "\n",
        )
        return 0
    finally:
        output_target.close()


if __name__ == "__main__":
    raise SystemExit(main())
