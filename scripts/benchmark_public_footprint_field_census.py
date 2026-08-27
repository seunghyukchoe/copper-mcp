#!/usr/bin/env python3
"""Measure the closed public KiCad footprint-field surface without accepting it.

This is the sibling of :mod:`scripts.benchmark_public_setup_field_census`, one structural level
down.  B-131 measured all six former `setup_semantics` boards landing on
``footprint contains an unsupported semantic field`` at unchanged depth, so the wall that census
decomposed was one instance of a class -- a container-level allowlist whose refusal names the
container and no field -- and this instrument decomposes the next instance.

**Why a second instrument rather than a parameterized one.**  The setup census's freeze story
rests on its selection rule being a *constant in the file whose digest the artifact binds*:
``_select_setup_terminals`` tests ``blocker == "setup_semantics"`` literally, and
``PREDECLARED_SETUP_SELECTION_COMMITMENT`` is domain-separated to that instrument.  Parameterizing
the rule would make a frozen commitment's meaning depend on a runtime argument -- the constant
would then say only "these six entries" and no longer "these six, selected this way", which is the
half that catches a drifted classifier.  The frozen vocabularies are setup-specific for the same
reason.  So this file re-argues its own commitment, and pays for the duplication by *importing*
the setup census's output plumbing and cohort helpers rather than restating them.

**Continuity is checked rather than assumed.**  The selection here is reached by a different rule
-- B-129's closed blocker vocabulary has no member for the footprint wall and correctly reports
``other`` -- so the instrument recomputes the *setup* census's commitment, with that census's own
function under its own domain, over the boards this one selected, and requires it to equal that
census's frozen constant.  That proves the same six boards without coupling the two rules.

Aggregate counts from predeclared vocabularies only.  No board identity, path, digest, coordinate,
margin value, sheet name or file name is ever committed.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import platform
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
from scripts import benchmark_public_setup_field_census as setup_census

SCHEMA: Final = "copper-mcp/public-footprint-field-census/v1"
EXPECTED_CAPTURED: Final = 13
EXPECTED_PUBLIC: Final = 10
EXPECTED_FOOTPRINT_TERMINALS: Final = 6
OTHER: Final = "other"
PREDECLARED_COHORT_FINGERPRINT: Final = masking.PREDECLARED_COHORT_FINGERPRINT
SELECTION_COMMITMENT_DOMAIN: Final = (
    b"copper-mcp/public-footprint-field-census/selected-manifest-entries/v1\x00"
)
# Assigned once, from the exact B-129 cohort, in the pull request that first ran this instrument.
# A *freeze*, not a prediction, exactly as the setup census's constant is: it binds which six
# entries were aggregated, so a later rerun whose selection differs -- a drifted classifier, a
# re-derived corpus, a swapped manifest row -- fails instead of silently re-aggregating over a
# different population.  `EXPECTED_FOOTPRINT_TERMINALS` alone cannot catch a same-count membership
# swap.
PREDECLARED_FOOTPRINT_SELECTION_COMMITMENT: Final[str | None] = (
    "sha256:8d66a9740bb957eca7d5ed5dff9a7025d6136741fbd8f91c5b1441f1dfe77cae"
)

# The exact terminal this census exists to decompose.  Selection does not merely accept B-129's
# `other` bucket -- `other` is a catch-all and would admit any future unnamed terminal -- it
# re-walks the same mask loop from the same module's primitives and requires the terminal
# diagnostic to be this one, at a footprint locator.
FOOTPRINT_WALL_CODE: Final = "unsupported.construct"
FOOTPRINT_WALL_MESSAGE: Final = "footprint contains an unsupported semantic field"
FOOTPRINT_WALL_LOCATOR_PREFIX: Final = "kicad_pcb.footprint["
FOOTPRINT_WALL_LOCATOR_SUFFIX: Final = "].unsupported"

# The adapter's accepted `footprint` vocabulary **as it stood when B-132 was taken**, frozen so the
# artifact stays replayable.  The drift guard checks *containment*, not equality, for the reason
# the setup census learned: the adapter may widen past this set -- that is what the accompanying
# decision does -- but a head accepted at census time and refused later would invalidate the
# artifact's reading and must fail the run.
ACCEPTED_FOOTPRINT_HEADS: Final = frozenset(
    {
        "at",
        "attr",
        "descr",
        "duplicate_pad_numbers_are_jumpers",
        "embedded_fonts",
        "layer",
        "locked",
        "model",
        "net_tie_pad_groups",
        "pad",
        "path",
        "placed",
        "property",
        "tags",
        "tstamp",
        "uuid",
    }
)
# Heads the adapter routes to its layer-aware path *before* consulting the allowlist, so they never
# reach the container refusal whatever the allowlist says.  Mirrors the `head.startswith("fp_") or
# head in {"property", "point"}` branch in `_validate_footprints`.  `property` is in both this set
# and the accepted set; the layer-aware branch runs first, so this set wins, and the census models
# the adapter's order rather than its tables.
LAYER_ROUTED_EXACT_HEADS: Final = frozenset({"point", "property"})
LAYER_ROUTED_PREFIX: Final = "fp_"
# Refused ahead of the allowlist by its own named sentence, so it is not part of the container
# refusal surface either.
ZONE_HEAD: Final = "zone"

# The closed vocabulary of `footprint` children that reach the container allowlist and are refused
# by it.  It is **the union of the top-level `case T_…` arms of
# `PCB_IO_KICAD_SEXPR_PARSER::parseFOOTPRINT_unchecked` on the KiCad `9.0` and `10.0` release
# branches** (47 arms on 9.0, 54 on 10.0), minus the accepted heads, minus the layer-routed heads,
# minus `zone`.  Deriving it from the grammar rather than from the boards is what makes `other`
# mean something: a head this cohort happens not to write is still in the vocabulary at zero, and a
# head KiCad can write that is missing here shows up as `other` rather than as silence.
#
# Three heads reachable only on `master` (11.0 development: `constraint`, `transform`, and the
# `fp_ellipse*` pair, the last of which is layer-routed anyway) are deliberately **excluded** --
# the target is KiCad 9/10, and `other` is the correct report for a board written by a version this
# vocabulary does not claim to cover.
#
# `generator`, `generator_version` and `version` are written *only* into `.kicad_mod` library files
# -- `CTL_FOR_BOARD` includes `CTL_OMIT_FOOTPRINT_VERSION` -- so they cannot appear inside a
# `.kicad_pcb` footprint written by KiCad.  They are carried anyway because the *parser* accepts
# them and a hand-edited or third-party-generated board can contain one.
REFUSED_FOOTPRINT_HEADS: Final = frozenset(
    {
        "autoplace_cost180",
        "autoplace_cost90",
        "barcode",
        "clearance",
        "component_classes",
        "dimension",
        "embedded_files",
        "generator",
        "generator_version",
        "group",
        "image",
        "jumper_pad_groups",
        "private_layers",
        "sheetfile",
        "sheetname",
        "solder_mask_margin",
        "solder_paste_margin",
        "solder_paste_margin_ratio",
        # KiCad's own `// legacy token`: the 8.0 footprint writer emitted this spelling and the
        # 9.0 writer emits `solder_paste_margin_ratio`.  Both still parse into the same arm, so a
        # reader that knows only the new spelling silently mis-reads every 8.0-written board.
        "solder_paste_ratio",
        "stackup",
        "table",
        "tedit",
        "thermal_gap",
        "thermal_width",
        "units",
        "variant",
        "version",
        "zone_connect",
    }
)
# Children of a footprint-local `(group …)`, the one refused head observed to be a container.  A
# nested vocabulary is declared for the same reason the stackup got one: a container accepted
# without a grammar for its children is an unread container.  `id` is the pre-KiCad-8 spelling of
# `uuid` and still parses; `lib_id` is KiCad 10 only.
GROUP_HEADS: Final = frozenset({"id", "lib_id", "locked", "members", "uuid"})

# The direction-of-error partition, declared before the counts are read.  It is the axis the
# decision table turns on, so the census reports board presence per class rather than leaving a
# reader to re-derive it from head names.
#
# `copper_interacting` is the class a non-claim may not be written for: each member is resolved by
# KiCad into a copper clearance or into pour geometry against copper this adapter does model.
# `own_geometry` members draw their own layer-bearing shapes.  `stencil_mask` members move solder
# paste and solder mask apertures and no copper.  `provenance` is schematic and library
# bookkeeping; `grouping` is editor selection and placement structure.
FIELD_CLASSES: Final[dict[str, frozenset[str]]] = {
    "copper_interacting": frozenset({"clearance", "thermal_gap", "thermal_width", "zone_connect"}),
    "own_geometry": frozenset({"barcode", "dimension", "image", "stackup", "table"}),
    "stencil_mask": frozenset(
        {
            "solder_mask_margin",
            "solder_paste_margin",
            "solder_paste_margin_ratio",
            "solder_paste_ratio",
        }
    ),
    "provenance": frozenset(
        {
            "component_classes",
            "generator",
            "generator_version",
            "sheetfile",
            "sheetname",
            "tedit",
            "units",
            "variant",
            "version",
        }
    ),
    "grouping": frozenset(
        {
            "autoplace_cost180",
            "autoplace_cost90",
            "embedded_files",
            "group",
            "jumper_pad_groups",
            "private_layers",
        }
    ),
}
CLASS_KEYS: Final = (
    "copper_interacting",
    "grouping",
    "own_geometry",
    "provenance",
    "stencil_mask",
    OTHER,
)
# A `copper_interacting` head is only unsafe to ignore when it carries a value KiCad actually
# resolves, so the census measures *which* values this cohort writes -- the question B-130 answered
# for the board-edge attributes by finding them absent.  Each entry is a **predicate over a
# payload, never a payload**: the artifact records a count and no value ever reaches it.
#
# - `clearance_zero`: `FOOTPRINT::GetLocalClearance` reaches `DRC_ENGINE::EvalRules`'
#   local-override block, which returns early only `if( override_val )`, so a clearance of exactly
#   zero falls through to the ordinary rule path and is inert.  A non-zero one *replaces* the
#   resolved rule value and can lower it, which is why the sign analysis is not uniform.
# - `zone_connect_attaching` / `zone_connect_detaching`: `0` (`NONE`) is the only written value
#   that detaches a pad from its pour; `1`, `2` and `3` all attach, and
#   `DRC_ENGINE::EvalZoneConnection` collapses `3` to `1` on a plated through-hole pad and to `2`
#   otherwise.  `-1` (`INHERITED`) is the one value KiCad's writer suppresses.  This is exactly the
#   partition ADR-0091 already draws one level down, on a pad.
PAYLOAD_PREDICATES: Final = (
    "clearance_zero",
    "zone_connect_attaching",
    "zone_connect_detaching",
)
_ATTACHING_ZONE_CONNECTIONS: Final = frozenset({"1", "2", "3"})
_DETACHING_ZONE_CONNECTION: Final = "0"
HEAD_CLASS_BUCKETS: Final = ("accepted", "layer_routed", OTHER, "refused", "zone")
SHAPE_BUCKETS: Final = setup_census.SHAPE_BUCKETS

Converter = Callable[[bytes, Settings], Any]


@dataclass(frozen=True, slots=True)
class BoardObservation:
    footprint_count: int
    head_class_occurrences: Mapping[str, int]
    refused_occurrences: Mapping[str, int]
    refused_presence: frozenset[str]
    refused_shapes: Mapping[str, int]
    refused_class_presence: frozenset[str]
    payload_predicate_occurrences: Mapping[str, int]
    group_count: int
    group_field_occurrences: Mapping[str, int]
    group_field_presence: frozenset[str]
    group_field_shapes: Mapping[str, int]


def _fixed_error(message: str) -> ValueError:
    return ValueError(message)


def _require_symbolic_head(node: SExpr, context: str) -> str:
    head = node.head
    if head is None or is_quoted_atom(head):
        raise _fixed_error(f"{context} must have an unquoted symbolic head")
    return head


def _bucket(head: str, vocabulary: frozenset[str]) -> str:
    return head if head in vocabulary else OTHER


def _classify_head(head: str) -> str:
    """Bucket a footprint child by *which adapter branch it reaches*, in the adapter's order."""

    if head == ZONE_HEAD:
        return "zone"
    if head.startswith(LAYER_ROUTED_PREFIX) or head in LAYER_ROUTED_EXACT_HEADS:
        return "layer_routed"
    if head in ACCEPTED_FOOTPRINT_HEADS:
        return "accepted"
    if head in REFUSED_FOOTPRINT_HEADS:
        return "refused"
    return OTHER


def _field_class(head: str) -> str:
    for name, members in FIELD_CLASSES.items():
        if head in members:
            return name
    return OTHER


def _sole_bare_atom(node: SExpr) -> str | None:
    """Return a field's single unquoted payload atom, or None if it has no such shape."""

    payload = node.items[1:]
    if len(payload) != 1:
        return None
    atom = payload[0]
    if not isinstance(atom, str) or is_quoted_atom(atom):
        return None
    return atom


def _payload_predicates(head: str, node: SExpr) -> tuple[str, ...]:
    """Classify a copper-interacting payload into closed predicate buckets.

    Predicates, never disclosures: the caller increments counters and no value is stored or
    published.
    """

    atom = _sole_bare_atom(node)
    if atom is None:
        return ()
    if head == "clearance":
        try:
            return ("clearance_zero",) if float(atom) == 0.0 else ()
        except ValueError:
            return ()
    if head == "zone_connect":
        if atom in _ATTACHING_ZONE_CONNECTIONS:
            return ("zone_connect_attaching",)
        if atom == _DETACHING_ZONE_CONNECTION:
            return ("zone_connect_detaching",)
    return ()


def _footprints(source: bytes, settings: Settings) -> tuple[SExpr, ...]:
    root = parse_sexpr(source, masking.parse_limits_for(settings))
    if _require_symbolic_head(root, "source root") != "kicad_pcb":
        raise _fixed_error("source root must be kicad_pcb")
    payload = root.items[1:]
    if any(not isinstance(item, SExpr) for item in payload):
        raise _fixed_error("source root must contain only child expressions")
    children = tuple(item for item in payload if isinstance(item, SExpr))
    for child in children:
        _require_symbolic_head(child, "source root child")
    footprints = tuple(child for child in children if child.head == "footprint")
    if not footprints:
        raise _fixed_error("each selected public source must contain at least one footprint")
    return footprints


def _observe_footprints(footprints: Sequence[SExpr]) -> BoardObservation:
    head_classes: Counter[str] = Counter()
    refused_occurrences: Counter[str] = Counter()
    refused_shapes: Counter[str] = Counter()
    refused_presence: set[str] = set()
    refused_class_presence: set[str] = set()
    neutral_payloads: Counter[str] = Counter()
    group_field_occurrences: Counter[str] = Counter()
    group_field_shapes: Counter[str] = Counter()
    group_field_presence: set[str] = set()
    group_count = 0

    for footprint in footprints:
        if _require_symbolic_head(footprint, "footprint") != "footprint":
            raise _fixed_error("footprint expression must be footprint")
        for item in footprint.items[1:]:
            if not isinstance(item, SExpr):
                continue
            head = _require_symbolic_head(item, "footprint field")
            classification = _classify_head(head)
            head_classes[classification] += 1
            if classification not in {"refused", OTHER}:
                continue

            bucket = _bucket(head, REFUSED_FOOTPRINT_HEADS)
            refused_occurrences[bucket] += 1
            refused_presence.add(bucket)
            refused_shapes[f"{bucket}:{setup_census._shape(item)}"] += 1
            refused_class_presence.add(_field_class(bucket))
            for predicate in _payload_predicates(bucket, item):
                neutral_payloads[predicate] += 1
            if bucket != "group":
                continue

            group_count += 1
            for group_field in item.items[1:]:
                if not isinstance(group_field, SExpr):
                    continue
                group_head = _require_symbolic_head(group_field, "footprint group field")
                group_bucket = _bucket(group_head, GROUP_HEADS)
                group_field_occurrences[group_bucket] += 1
                group_field_presence.add(group_bucket)
                group_field_shapes[f"{group_bucket}:{setup_census._shape(group_field)}"] += 1

    return BoardObservation(
        footprint_count=len(footprints),
        head_class_occurrences=dict(head_classes),
        refused_occurrences=dict(refused_occurrences),
        refused_presence=frozenset(refused_presence),
        refused_shapes=dict(refused_shapes),
        refused_class_presence=frozenset(refused_class_presence),
        payload_predicate_occurrences=dict(neutral_payloads),
        group_count=group_count,
        group_field_occurrences=dict(group_field_occurrences),
        group_field_presence=frozenset(group_field_presence),
        group_field_shapes=dict(group_field_shapes),
    )


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
    expected = PREDECLARED_FOOTPRINT_SELECTION_COMMITMENT
    if (
        not isinstance(expected, str)
        or len(expected) != 71
        or not expected.startswith("sha256:")
        or any(character not in "0123456789abcdef" for character in expected[7:])
    ):
        if expected is None:
            raise _fixed_error("predeclared footprint selection commitment is unassigned")
        raise _fixed_error("predeclared footprint selection commitment is malformed")
    return expected


def _is_footprint_wall(result: Any) -> bool:
    """Report whether a terminal conversion stopped at the container-allowlist refusal."""

    diagnostics = getattr(result, "diagnostics", ())
    if not diagnostics:
        return False
    diagnostic = diagnostics[0]
    locator = getattr(diagnostic, "source_locator", None)
    return (
        getattr(diagnostic, "code", None) == FOOTPRINT_WALL_CODE
        and getattr(diagnostic, "message", None) == FOOTPRINT_WALL_MESSAGE
        and isinstance(locator, str)
        and locator.startswith(FOOTPRINT_WALL_LOCATOR_PREFIX)
        and locator.endswith(FOOTPRINT_WALL_LOCATOR_SUFFIX)
    )


def _terminal_depth_and_wall(
    source: bytes,
    settings: Settings,
    *,
    converter: Converter | None,
) -> tuple[int, bool]:
    """Re-walk B-129's mask loop from its own primitives and report the terminal diagnostic.

    `_classify_source_detail` returns a closed blocker class and deliberately never returns
    diagnostic text, so it cannot distinguish this wall from any other terminal its vocabulary has
    no member for.  Rather than widen that vocabulary -- which would change the instrument B-133's
    differential must replay byte-for-byte -- this composes the same module's `_convert` and
    `_mask_first_blocker`, and the caller requires the depth to agree with the classifier's.
    """

    convert = converter or (lambda data, opts: masking._convert(data, "frozen-board", opts))
    current = source
    seen = {source}
    for depth in range(masking.MAX_MASK_PASSES + 1):
        try:
            result = convert(current, settings)
        except Exception:
            return depth, False
        if getattr(result, "snapshot", None) is not None and not getattr(result, "diagnostics", ()):
            return depth, False
        if depth == masking.MAX_MASK_PASSES:
            return depth, False
        replacement = masking._mask_first_blocker(current, result, settings)
        if replacement is None:
            return depth, _is_footprint_wall(result)
        if len(replacement) >= len(current) or replacement in seen:
            return depth, _is_footprint_wall(result)
        seen.add(replacement)
        current = replacement
    return masking.MAX_MASK_PASSES, False


def _select_footprint_terminals(
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
        depth, terminal, blocker = masking._classify_source_detail(
            snapshot.source,
            settings,
            converter=converter,
        )
        if terminal != "unmaskable" or blocker != OTHER:
            continue
        walk_depth, at_wall = _terminal_depth_and_wall(
            snapshot.source,
            settings,
            converter=converter,
        )
        if walk_depth != depth:
            raise _fixed_error("terminal walk disagrees with the fixed-point classifier")
        if at_wall:
            selected.append(snapshot)
    if len(selected) != EXPECTED_FOOTPRINT_TERMINALS:
        raise _fixed_error(
            "fixed-point footprint-terminal population drifted: "
            f"expected {EXPECTED_FOOTPRINT_TERMINALS}, got {len(selected)}"
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


def _closed_counts(counter: Mapping[str, int], keys: Sequence[str]) -> dict[str, int]:
    return {key: int(counter.get(key, 0)) for key in keys}


def _closed_shape_counts(counter: Mapping[str, int], heads: Sequence[str]) -> dict[str, int]:
    keys = tuple(f"{head}:{shape}" for head in heads for shape in SHAPE_BUCKETS)
    return _closed_counts(counter, keys)


def _merge_mapping(observations: Sequence[BoardObservation], attribute: str) -> Counter[str]:
    merged: Counter[str] = Counter()
    for observation in observations:
        value = getattr(observation, attribute)
        if not isinstance(value, Mapping):
            raise _fixed_error("internal aggregate mapping is invalid")
        merged.update(value)
    return merged


def _merge_presence(observations: Sequence[BoardObservation], attribute: str) -> Counter[str]:
    merged: Counter[str] = Counter()
    for observation in observations:
        value = getattr(observation, attribute)
        if not isinstance(value, frozenset):
            raise _fixed_error("internal aggregate presence set is invalid")
        merged.update(value)
    return merged


def measure(
    corpus: Path,
    manifest: Path,
    settings: Settings,
    *,
    converter: Converter | None = None,
) -> dict[str, Any]:
    if settings.allow_apply or settings.allow_live_ipc or settings.allow_live_apply:
        raise _fixed_error("footprint-field census is read-only")
    if not ACCEPTED_FOOTPRINT_HEADS <= kicad_board_ir._FOOTPRINT_METADATA_HEADS:
        raise _fixed_error("adapter accepted footprint vocabulary drifted")
    if REFUSED_FOOTPRINT_HEADS & ACCEPTED_FOOTPRINT_HEADS:
        raise _fixed_error("predeclared refused and accepted vocabularies overlap")
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
    selected = _select_footprint_terminals(
        snapshots,
        settings=settings,
        converter=converter,
    )
    observed_selection = _selection_commitment(selected)
    if not hmac.compare_digest(expected_selection, observed_selection):
        raise _fixed_error("fixed-point footprint-terminal membership drifted")

    # Continuity, checked rather than asserted in prose: the setup census's own commitment
    # function, under its own domain, over the boards this instrument selected by a different rule.
    setup_selection = setup_census._selection_commitment(selected)
    setup_expected = setup_census.PREDECLARED_SETUP_SELECTION_COMMITMENT
    same_cohort_as_setup_census = isinstance(setup_expected, str) and hmac.compare_digest(
        setup_expected, setup_selection
    )
    if not same_cohort_as_setup_census:
        raise _fixed_error("footprint terminals are not the setup census's six boards")

    observations = tuple(
        _observe_footprints(_footprints(snapshot.source, settings)) for snapshot in selected
    )

    refused_keys = (*sorted(REFUSED_FOOTPRINT_HEADS), OTHER)
    group_keys = (*sorted(GROUP_HEADS), OTHER)

    refused_occurrences = _merge_mapping(observations, "refused_occurrences")
    refused_presence = _merge_presence(observations, "refused_presence")
    refused_shapes = _merge_mapping(observations, "refused_shapes")
    refused_class_presence = _merge_presence(observations, "refused_class_presence")
    neutral_payloads = _merge_mapping(observations, "payload_predicate_occurrences")
    head_classes = _merge_mapping(observations, "head_class_occurrences")
    group_occurrences = _merge_mapping(observations, "group_field_occurrences")
    group_presence = _merge_presence(observations, "group_field_presence")
    group_shapes = _merge_mapping(observations, "group_field_shapes")

    _verify_sources_unchanged(corpus, snapshots, settings)

    return {
        "schema": SCHEMA,
        "source_census": {
            "source_schema": masking.SCHEMA,
            "sibling_schema": setup_census.SCHEMA,
            "cohort_fingerprint": fingerprint,
            "captured_entries": len(snapshots),
            "public_entries": EXPECTED_PUBLIC,
            "footprint_terminal_entries": len(selected),
            "selection_rule": "fixed_point_terminal_footprint_container_allowlist",
            "same_cohort_as_setup_census": same_cohort_as_setup_census,
        },
        "closed_vocabularies": {
            "head_class": list(HEAD_CLASS_BUCKETS),
            "refused_footprint": list(refused_keys),
            "footprint_group": list(group_keys),
            "field_class": list(CLASS_KEYS),
            "payload_predicate": list(PAYLOAD_PREDICATES),
            "shape": list(SHAPE_BUCKETS),
        },
        "aggregates": {
            "boards": len(observations),
            "footprint_count": sum(observation.footprint_count for observation in observations),
            "head_classes": _closed_counts(head_classes, HEAD_CLASS_BUCKETS),
            "refused_fields": {
                "occurrences": _closed_counts(refused_occurrences, refused_keys),
                "board_presence": _closed_counts(refused_presence, refused_keys),
                "shape_occurrences": _closed_shape_counts(refused_shapes, refused_keys),
            },
            "refused_field_classes": {
                "board_presence": _closed_counts(refused_class_presence, CLASS_KEYS),
            },
            "payload_predicate_occurrences": _closed_counts(neutral_payloads, PAYLOAD_PREDICATES),
            "footprint_group": {
                "node_count": sum(observation.group_count for observation in observations),
                "field_occurrences": _closed_counts(group_occurrences, group_keys),
                "field_board_presence": _closed_counts(group_presence, group_keys),
                "field_shape_occurrences": _closed_shape_counts(group_shapes, group_keys),
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
            "footprint_field_acceptance": False,
            "pad_field_surface_measured": False,
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
                    "operation": "read_only_closed_footprint_field_census",
                },
                "committed_board_bytes": 0,
                "not_claimed": [
                    "no footprint-field product support",
                    "no measurement of the pad-level allowlist one level further down",
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
