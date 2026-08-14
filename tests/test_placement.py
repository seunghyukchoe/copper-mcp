"""Placement intent, the deterministic legalizer, and immutable placement candidates.

Two properties carry most of the weight here.

*Direction of error.* Pad bounds over-approximate and pad cores under-approximate, so a clear
verdict and a violation verdict are each proofs while everything between is explicitly
inconclusive. A test that accepted "inconclusive" as either would erase the distinction the
whole contract is built on.

*Infeasible is not the same as exhausted.* A rule set that provably cannot hold and a run that
ran out of budget produce different codes, and a test asserts they never collapse into one.
"""

from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path
from typing import Any

import pytest

from copper_mcp.adapters import KiCadConstraintProfile, parse_kicad_bytes
from copper_mcp.board_ir import NetClass, ParseLimits, PointNM, Ring, make_snapshot
from copper_mcp.placement import (
    ORDERING_POLICY,
    PLACEMENT_VERSION,
    PlacementError,
    PlacementFailureCode,
    PlacementViewError,
    build_placement_view,
    evaluate_placement,
    parse_placement_intent,
    verify_placement_id,
)
from copper_mcp.placement.contracts import PlacementLegality, canonical_candidate_bytes
from copper_mcp.placement.geometry import (
    COURTYARD_CACHE_INSET_NM,
    COURTYARD_COLLISION_THRESHOLD_NM,
    orthogonal_courtyard_region_overlap,
    orthogonal_rings_overlap_open,
    pad_bounds,
    pad_core,
    rects_overlap,
)
from copper_mcp.placement.legalizer import (
    _PlacedFootprint,
    _PlacedPad,
    _resolve_centre,
    snap,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "placement-v0.1"
ROTATION_BOARD = ROOT / "tests" / "fixtures" / "board-ir-v0.1" / "footprint-rotation.kicad_pcb"
FOOTPRINT_V02_BOARD = (
    ROOT / "tests" / "fixtures" / "board-ir-v0.2" / "footprint-pose-courtyard.kicad_pcb"
)
FRONT_BACK_FOOTPRINT_V02_BOARD = (
    ROOT / "tests" / "fixtures" / "board-ir-v0.2" / "footprint-front-back-pose.kicad_pcb"
)
PADLESS_BOARD = ROOT / "tests" / "fixtures" / "board-ir-v0.2" / "padless-footprint.kicad_pcb"
ORTHOGONAL_COURTYARD_BOARD = (
    ROOT / "tests" / "fixtures" / "board-ir-v0.2" / "courtyard-orthogonal-chains.kicad_pcb"
)
#: A socket whose courtyard is a ring: an outer wall with an inner hole the centre part occupies.
#: Real KiCad 10.0.5 reports no violation on it, which is the whole point of the fixture.
DONUT_COURTYARD_BOARD = ROOT / "tests" / "fixtures" / "board-ir-v0.2" / "courtyard-donut.kicad_pcb"
#: The graphics-only footprint in ``PADLESS_BOARD``: real in Board IR, reported by the scene,
#: but owning no copper pad.
PADLESS_REF = "footprint:kicad:93000000-0000-0000-0000-000000000011"
COPPERTONE = ROOT / "hardware" / "coppertone-buffer" / "coppertone-buffer.kicad_pcb"
REAL_KICAD_CLI = Path("/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli")

CONSTRAINTS = {
    "clearance_nm": 200_000,
    "track_width_nm": 250_000,
    "via_diameter_nm": 600_000,
    "via_drill_nm": 300_000,
}


def _profile() -> KiCadConstraintProfile:
    net_class = NetClass(id="class:request", name="Request", **CONSTRAINTS)
    return KiCadConstraintProfile(net_classes=(net_class,), default_net_class_id=net_class.id)


def _board(path: Path) -> tuple[bytes, Any, Any]:
    source = path.read_bytes()
    result = parse_kicad_bytes(source, _profile(), ParseLimits())
    assert result.snapshot is not None, f"{path.name} must be a supported board"
    return source, result.snapshot, build_placement_view(source, result.snapshot)


def _intent(view: Any, board: str, **overrides: Any) -> Any:
    payload: dict[str, Any] = {
        "board": board,
        "constraints": dict(CONSTRAINTS),
        "subjects": sorted(view.footprints),
    }
    payload.update(overrides)
    return parse_placement_intent(payload)


def _evaluate(path: Path, **overrides: Any) -> Any:
    _, snapshot, view = _board(path)
    return evaluate_placement(_intent(view, path.name, **overrides), snapshot, view)


def _skewed_saved_pose(path: Path, angle_udeg: int = 45_000_000) -> tuple[Any, Any, str]:
    """Re-bind a board whose first movable footprint carries a non-orthogonal saved pose.

    The KiCad adapter fails closed on an oblique footprint angle, so this reaches the state a
    directly constructed snapshot or a decoded Board IR envelope can still present. The digest is
    recomputed rather than forged, because the point is a *valid* snapshot the placement boundary
    must refuse in words, not a tampered one it refuses on binding.
    """

    from dataclasses import replace

    source, snapshot, _ = _board(path)
    footprints = snapshot.content.footprints
    target = next(item for item in footprints if not item.locked and item.pad_ids)
    skewed = make_snapshot(
        replace(
            snapshot.content,
            footprints=tuple(
                replace(item, rotation_udeg=angle_udeg) if item is target else item
                for item in footprints
            ),
        )
    )
    return skewed, build_placement_view(source, skewed), target.id


class PlacementViewTests(unittest.TestCase):
    def test_the_footprint_join_is_total_on_every_supported_board(self) -> None:
        """Every Board IR pad must be attributable, or the grouping is not trustworthy."""

        for board in (FIXTURES / "placement-legal.kicad_pcb", ROTATION_BOARD, COPPERTONE):
            if not board.exists():
                continue
            with self.subTest(board=board.name):
                _, snapshot, view = _board(board)
                self.assertEqual(len(view.owner_by_pad), len(snapshot.content.pads))
                owned = {pad for item in view.footprints.values() for pad in item.pad_ids}
                self.assertEqual(owned, {pad.id for pad in snapshot.content.pads})

    def test_a_view_binds_to_both_the_source_bytes_and_the_snapshot(self) -> None:
        _, snapshot, view = _board(FIXTURES / "placement-legal.kicad_pcb")
        self.assertRegex(view.board_revision, r"^sha256:[0-9a-f]{64}$")
        self.assertEqual(view.snapshot_digest, snapshot.snapshot_digest)

    def test_a_view_built_from_different_bytes_is_refused(self) -> None:
        """The join is total by construction, so a mismatch means two different boards.

        The two boards must have genuinely different pad identities for this to test
        anything: a fixture derived from another by editing coordinates keeps its UUIDs, and
        would join perfectly well.
        """

        _, snapshot, _ = _board(FIXTURES / "placement-legal.kicad_pcb")
        other = ROTATION_BOARD.read_bytes()
        with self.assertRaises(PlacementViewError):
            build_placement_view(other, snapshot)

    def test_a_view_refuses_footprint_content_not_bound_to_its_snapshot_digest(self) -> None:
        from dataclasses import replace

        source, snapshot, _ = _board(FOOTPRINT_V02_BOARD)
        first, *remaining = snapshot.content.footprints
        forged_footprint = replace(
            first,
            origin=replace(first.origin, x=first.origin.x + 1),
        )
        forged = replace(
            snapshot,
            content=replace(
                snapshot.content,
                footprints=(forged_footprint, *remaining),
            ),
        )

        with self.assertRaisesRegex(
            PlacementViewError,
            "Board IR snapshot failed placement-view validation",
        ):
            build_placement_view(source, forged)

    def test_a_view_enforces_caller_tightened_board_ir_limits(self) -> None:
        source, snapshot, _ = _board(FOOTPRINT_V02_BOARD)

        with self.assertRaisesRegex(
            PlacementViewError,
            "Board IR snapshot failed placement-view validation",
        ):
            build_placement_view(source, snapshot, limits=ParseLimits(max_objects=1))

    def test_a_pad_reference_resolves_to_the_footprint_that_owns_it(self) -> None:
        _, _, view = _board(FIXTURES / "placement-legal.kicad_pcb")
        footprint = sorted(view.footprints.values(), key=lambda item: item.ref_id)[0]
        resolved = view.resolve(footprint.pad_ids[0])
        self.assertIsNotNone(resolved)
        assert resolved is not None
        self.assertEqual(resolved.ref_id, footprint.ref_id)

    def test_derived_pad_identities_are_reproduced_rather_than_guessed(self) -> None:
        """A board whose pads carry no UUID still joins, via the adapter's own hash rule."""

        source = (FIXTURES / "placement-legal.kicad_pcb").read_bytes()
        stripped = source.replace(b'      (uuid "90000000-0000-0000-0000-000000000002")\n', b"")
        result = parse_kicad_bytes(stripped, _profile(), ParseLimits())
        assert result.snapshot is not None
        self.assertTrue(
            any(pad.id.startswith("pad:derived:") for pad in result.snapshot.content.pads),
            "the fixture must actually exercise a derived identity",
        )
        view = build_placement_view(stripped, result.snapshot)
        self.assertEqual(len(view.owner_by_pad), len(result.snapshot.content.pads))


class FootprintV02PlacementRegressionTests(unittest.TestCase):
    def test_source_revision_mismatch_is_rejected_before_the_join(self) -> None:
        source, snapshot, _ = _board(FOOTPRINT_V02_BOARD)
        changed_source = source.replace(b"(at 45 15 90)", b"(at 45 16 90)", 1)
        self.assertNotEqual(changed_source, source)

        with self.assertRaisesRegex(
            PlacementViewError,
            "board source and Board IR snapshot revisions disagree",
        ):
            build_placement_view(changed_source, snapshot)

    def test_a_locked_footprint_move_is_refused_without_a_candidate(self) -> None:
        _, snapshot, view = _board(FOOTPRINT_V02_BOARD)
        locked = [footprint for footprint in view.footprints.values() if footprint.locked]
        self.assertEqual(len(locked), 1)

        result = evaluate_placement(
            _intent(
                view,
                FOOTPRINT_V02_BOARD.name,
                proposals=[{"subject": locked[0].ref_id, "offset_x_nm": 1_000_000}],
            ),
            snapshot,
            view,
        )

        self.assertEqual(result.status, "refused")
        self.assertIsNone(result.candidate)
        assert result.diagnostic is not None
        self.assertEqual(result.diagnostic.code, PlacementFailureCode.UNSUPPORTED_GEOMETRY)
        self.assertEqual(result.diagnostic.message, "moving a locked footprint is not authorized")


class PadlessFootprintTests(unittest.TestCase):
    """A footprint with no copper pads exists; refusing it must not deny that.

    Board IR v0.2 keeps graphics-only footprints and the scene reports them, so answering
    ``unresolved_ref`` - "does not exist on this board" - would be a false statement about the
    caller's board. The honest answer is that it exists and this version cannot place it.
    """

    def test_the_view_records_a_padless_footprint_instead_of_forgetting_it(self) -> None:
        _, snapshot, view = _board(PADLESS_BOARD)

        board_ir_refs = {footprint.id for footprint in snapshot.content.footprints}
        self.assertIn(PADLESS_REF, board_ir_refs, "the fixture must carry a padless footprint")
        # It is not placeable, so it is deliberately absent from the placeable mapping...
        self.assertNotIn(PADLESS_REF, view.footprints)
        self.assertIsNone(view.resolve(PADLESS_REF))
        # ...but its identity survives, which is what lets the refusal be truthful.
        self.assertTrue(view.is_padless(PADLESS_REF))
        self.assertFalse(view.is_padless("footprint:kicad:not-on-this-board"))

    def test_naming_a_padless_subject_is_refused_as_unplaceable_not_as_unknown(self) -> None:
        _, snapshot, view = _board(PADLESS_BOARD)

        result = evaluate_placement(
            _intent(view, PADLESS_BOARD.name, subjects=[PADLESS_REF]), snapshot, view
        )

        self.assertEqual(result.status, "refused")
        assert result.diagnostic is not None
        self.assertEqual(result.diagnostic.code, PlacementFailureCode.UNSUPPORTED_GEOMETRY)
        self.assertIn("no copper pad", result.diagnostic.message)
        self.assertNotIn("does not exist", result.diagnostic.message)

    def test_padless_subject_precedes_an_unrelated_front_back_contradiction(self) -> None:
        """Subjects are preflighted before syntactic infeasibility is claimed."""

        _, snapshot, view = _board(PADLESS_BOARD)
        placeable = sorted(view.footprints)[0]

        result = evaluate_placement(
            _intent(
                view,
                PADLESS_BOARD.name,
                subjects=[PADLESS_REF, placeable],
                rules=[
                    {"kind": "side", "subject": placeable, "side": "front"},
                    {"kind": "side", "subject": placeable, "side": "back"},
                ],
            ),
            snapshot,
            view,
        )

        self.assertEqual(result.status, "refused")
        self.assertIsNone(result.candidate)
        assert result.diagnostic is not None
        self.assertEqual(result.diagnostic.code, PlacementFailureCode.UNSUPPORTED_GEOMETRY)
        self.assertEqual(
            result.diagnostic.message,
            "a placement subject owns no copper pad, so it cannot be placed in v0.1",
        )

    def test_subject_preflight_stops_at_the_first_unknown_reference(self) -> None:
        """A later padless ref cannot overwrite an earlier unknown-subject refusal."""

        _, snapshot, view = _board(PADLESS_BOARD)
        placeable = sorted(view.footprints)[0]
        unknown_ref = "footprint:kicad:00000000-0000-0000-0000-00000000ffff"

        result = evaluate_placement(
            _intent(
                view,
                PADLESS_BOARD.name,
                subjects=[unknown_ref, PADLESS_REF, placeable],
                rules=[
                    {"kind": "side", "subject": placeable, "side": "front"},
                    {"kind": "side", "subject": placeable, "side": "back"},
                ],
            ),
            snapshot,
            view,
        )

        self.assertEqual(result.status, "refused")
        self.assertIsNone(result.candidate)
        assert result.diagnostic is not None
        self.assertEqual(result.diagnostic.code, PlacementFailureCode.UNRESOLVED_REF)
        self.assertEqual(
            result.diagnostic.message,
            "a placement subject does not exist on this board",
        )

    def test_subject_preflight_keeps_an_earlier_padless_reference_precedence(self) -> None:
        _, snapshot, view = _board(PADLESS_BOARD)
        placeable = sorted(view.footprints)[0]
        unknown_ref = "footprint:kicad:00000000-0000-0000-0000-00000000ffff"

        result = evaluate_placement(
            _intent(
                view,
                PADLESS_BOARD.name,
                subjects=[PADLESS_REF, unknown_ref, placeable],
                rules=[
                    {"kind": "side", "subject": placeable, "side": "front"},
                    {"kind": "side", "subject": placeable, "side": "back"},
                ],
            ),
            snapshot,
            view,
        )

        self.assertEqual(result.status, "refused")
        self.assertIsNone(result.candidate)
        assert result.diagnostic is not None
        self.assertEqual(result.diagnostic.code, PlacementFailureCode.UNSUPPORTED_GEOMETRY)
        self.assertEqual(
            result.diagnostic.message,
            "a placement subject owns no copper pad, so it cannot be placed in v0.1",
        )

    def test_a_padless_anchor_and_rule_subject_are_refused_the_same_way(self) -> None:
        _, snapshot, view = _board(PADLESS_BOARD)
        placeable = sorted(view.footprints)[0]

        anchored = evaluate_placement(
            _intent(
                view,
                PADLESS_BOARD.name,
                subjects=[placeable],
                proposals=[{"subject": placeable, "anchor": PADLESS_REF, "offset_x_nm": 0}],
            ),
            snapshot,
            view,
        )
        assert anchored.diagnostic is not None
        self.assertEqual(anchored.diagnostic.code, PlacementFailureCode.UNSUPPORTED_GEOMETRY)
        self.assertIn("no copper pad", anchored.diagnostic.message)

        ruled = evaluate_placement(
            _intent(
                view,
                PADLESS_BOARD.name,
                subjects=[placeable],
                rules=[
                    {
                        "kind": "alignment",
                        "axis": "x",
                        "members": [placeable, PADLESS_REF],
                    }
                ],
            ),
            snapshot,
            view,
        )
        assert ruled.diagnostic is not None
        self.assertEqual(ruled.diagnostic.code, PlacementFailureCode.UNSUPPORTED_GEOMETRY)
        self.assertIn("no copper pad", ruled.diagnostic.message)

    def test_padless_rule_ref_is_rejected_before_infeasible_rules(self) -> None:
        _, snapshot, view = _board(PADLESS_BOARD)
        placeable = sorted(view.footprints)[0]

        result = evaluate_placement(
            _intent(
                view,
                PADLESS_BOARD.name,
                subjects=[placeable],
                rules=[
                    {"kind": "side", "subject": PADLESS_REF, "side": "front"},
                    {"kind": "side", "subject": PADLESS_REF, "side": "back"},
                ],
            ),
            snapshot,
            view,
        )

        assert result.diagnostic is not None
        self.assertEqual(result.diagnostic.code, PlacementFailureCode.UNSUPPORTED_GEOMETRY)
        self.assertIn("no copper pad", result.diagnostic.message)

    def test_padless_proximity_target_precedes_an_unrelated_contradiction(self) -> None:
        """Both operands of a proximity rule are unavailable when they are padless."""

        _, snapshot, view = _board(PADLESS_BOARD)
        placeable = sorted(view.footprints)[0]

        result = evaluate_placement(
            _intent(
                view,
                PADLESS_BOARD.name,
                subjects=[placeable],
                rules=[
                    {
                        "kind": "proximity",
                        "subject": placeable,
                        "target": PADLESS_REF,
                        "max_distance_nm": 0,
                    },
                    {"kind": "side", "subject": placeable, "side": "front"},
                    {"kind": "side", "subject": placeable, "side": "back"},
                ],
            ),
            snapshot,
            view,
        )

        self.assertEqual(result.status, "refused")
        self.assertIsNone(result.candidate)
        assert result.diagnostic is not None
        self.assertEqual(result.diagnostic.code, PlacementFailureCode.UNSUPPORTED_GEOMETRY)
        self.assertEqual(
            result.diagnostic.message,
            "a placement subject owns no copper pad, so it cannot be placed in v0.1",
        )

    def test_proximity_target_preflight_stops_at_the_first_unknown_reference(self) -> None:
        """A later padless target cannot overwrite an earlier unknown-target refusal."""

        _, snapshot, view = _board(PADLESS_BOARD)
        placeable = sorted(view.footprints)[0]
        unknown_ref = "footprint:kicad:00000000-0000-0000-0000-00000000ffff"

        for first, second, expected in (
            (unknown_ref, PADLESS_REF, PlacementFailureCode.UNRESOLVED_REF),
            (PADLESS_REF, unknown_ref, PlacementFailureCode.UNSUPPORTED_GEOMETRY),
        ):
            with self.subTest(first=first, second=second):
                result = evaluate_placement(
                    _intent(
                        view,
                        PADLESS_BOARD.name,
                        subjects=[placeable],
                        rules=[
                            {
                                "kind": "proximity",
                                "subject": placeable,
                                "target": first,
                                "max_distance_nm": 0,
                            },
                            {
                                "kind": "proximity",
                                "subject": placeable,
                                "target": second,
                                "max_distance_nm": 0,
                            },
                        ],
                    ),
                    snapshot,
                    view,
                )

                assert result.diagnostic is not None
                self.assertEqual(result.diagnostic.code, expected)

    def test_every_padless_proposal_anchor_precedes_an_unrelated_contradiction(self) -> None:
        """Unsupported proposal anchors must not be masked by syntactic rule analysis."""

        _, snapshot, view = _board(PADLESS_BOARD)
        placeable = sorted(view.footprints)[0]
        expected_message = "a placement subject owns no copper pad, so it cannot be placed in v0.1"
        for anchor_point in ("center", "north", "south", "east", "west"):
            with self.subTest(anchor_point=anchor_point):
                result = evaluate_placement(
                    _intent(
                        view,
                        PADLESS_BOARD.name,
                        subjects=[placeable],
                        proposals=[
                            {
                                "subject": placeable,
                                "anchor": PADLESS_REF,
                                "anchor_point": anchor_point,
                                "offset_x_nm": 0,
                            }
                        ],
                        rules=[
                            {"kind": "side", "subject": placeable, "side": "front"},
                            {"kind": "side", "subject": placeable, "side": "back"},
                        ],
                    ),
                    snapshot,
                    view,
                )

                self.assertEqual(result.status, "refused")
                self.assertIsNone(result.candidate)
                assert result.diagnostic is not None
                self.assertEqual(result.diagnostic.code, PlacementFailureCode.UNSUPPORTED_GEOMETRY)
                self.assertEqual(result.diagnostic.message, expected_message)

    def test_a_genuinely_absent_reference_is_still_unresolved(self) -> None:
        """Guard the guard: the honest refusal must not swallow the real unknown-ref case."""

        _, snapshot, view = _board(PADLESS_BOARD)

        result = evaluate_placement(
            _intent(
                view,
                PADLESS_BOARD.name,
                subjects=["footprint:kicad:00000000-0000-0000-0000-00000000ffff"],
            ),
            snapshot,
            view,
        )

        assert result.diagnostic is not None
        self.assertEqual(result.diagnostic.code, PlacementFailureCode.UNRESOLVED_REF)
        self.assertIn("does not exist", result.diagnostic.message)

    def test_the_padless_board_still_places_its_real_footprint(self) -> None:
        """The padless footprint must not block placement of the rest of the board."""

        _, snapshot, view = _board(PADLESS_BOARD)
        placeable = sorted(view.footprints)[0]

        result = evaluate_placement(
            _intent(view, PADLESS_BOARD.name, subjects=[placeable]), snapshot, view
        )

        self.assertEqual(result.status, "previewed")
        assert result.candidate is not None
        self.assertNotIn(PADLESS_REF, {item.ref_id for item in result.candidate.placements})

    def test_a_padless_courtyard_is_a_stationary_collision_envelope(self) -> None:
        _, snapshot, view = _board(PADLESS_BOARD)
        placeable = sorted(view.footprints)[0]

        result = evaluate_placement(
            _intent(
                view,
                PADLESS_BOARD.name,
                subjects=[placeable],
                proposals=[{"subject": placeable, "offset_x_nm": 30_000_000}],
            ),
            snapshot,
            view,
        )

        self.assertEqual(result.status, "refused")
        assert result.diagnostic is not None
        self.assertEqual(result.diagnostic.code, PlacementFailureCode.ILLEGAL_PLACEMENT)
        assert result.diagnostic.legality is not None
        self.assertEqual(result.diagnostic.legality.courtyard_overlap, "violated")


class RequestBoundaryTests(unittest.TestCase):
    def test_every_supported_rule_kind_parses(self) -> None:
        rules = [
            {"kind": "proximity", "subject": "a", "target": "b", "max_distance_nm": 1000},
            {"kind": "alignment", "axis": "x", "members": ["a", "b"]},
            {"kind": "symmetry", "axis": "x", "about": "c", "pairs": [["a", "b"]]},
            {"kind": "edge", "subject": "a", "edge": "north", "offset_nm": 500},
            {"kind": "region", "subject": "a", "mode": "keep_in", "boundary_ref": "k"},
            {"kind": "orientation", "subject": "a", "allowed": [0, 180_000_000]},
            {"kind": "side", "subject": "a", "side": "front"},
        ]
        intent = parse_placement_intent(
            {
                "board": "b.kicad_pcb",
                "constraints": dict(CONSTRAINTS),
                "subjects": ["a", "b", "c"],
                "rules": rules,
            }
        )
        self.assertEqual(len(intent.rules), 7)
        self.assertEqual(
            [rule.kind for rule in intent.rules],
            ["proximity", "alignment", "symmetry", "edge", "region", "orientation", "side"],
        )

    def test_hostile_and_malformed_requests_are_refused(self) -> None:
        base: dict[str, Any] = {
            "board": "b.kicad_pcb",
            "constraints": dict(CONSTRAINTS),
            "subjects": ["a"],
        }
        cases: list[tuple[dict[str, Any], str]] = [
            ({**base, "surprise": 1}, "unknown field"),
            ({**base, "subjects": []}, "no subjects"),
            ({**base, "subjects": ["a", "a"]}, "duplicate subjects"),
            ({**base, "placement_grid_nm": 0}, "non-positive grid"),
            ({**base, "rules": [{"kind": "nope"}]}, "unknown rule kind"),
            (
                {
                    **base,
                    "rules": [{"kind": "orientation", "subject": "a", "allowed": [45_000_000]}],
                },
                "non-orthogonal orientation",
            ),
            (
                {**base, "rules": [{"kind": "alignment", "axis": "z", "members": ["a", "b"]}]},
                "bad axis",
            ),
            (
                {**base, "rules": [{"kind": "alignment", "axis": "x", "members": ["a"]}]},
                "single-member alignment",
            ),
            (
                {**base, "rules": [{"kind": "side", "subject": "a", "side": "middle"}]},
                "bad side",
            ),
            (
                {
                    **base,
                    "rules": [
                        {
                            "kind": "proximity",
                            "subject": "a",
                            "target": "b",
                            "max_distance_nm": -1,
                        }
                    ],
                },
                "negative distance",
            ),
            (
                {**base, "rules": [{"kind": "edge", "subject": "a", "edge": "up", "offset_nm": 0}]},
                "bad edge",
            ),
            ({**base, "proposals": [{"subject": "zzz"}]}, "proposal outside the subjects"),
            ({**base, "proposals": [{"subject": "a"}, {"subject": "a"}]}, "duplicate proposal"),
            (
                {**base, "proposals": [{"subject": "a", "orientation_udeg": 45_000_000}]},
                "oblique proposal",
            ),
            (
                {**base, "proposals": [{"subject": "a", "offset_x_nm": 10**12}]},
                "offset out of range",
            ),
            ({**base, "constraints": {}}, "empty constraints"),
            ({**base, "board": "b.kicad_pcb\x00"}, "control character in a path"),
        ]
        for payload, reason in cases:
            with self.subTest(reason=reason), self.assertRaises((PlacementError, ValueError)):
                parse_placement_intent(payload)

    def test_a_refusal_does_not_echo_the_rejected_value(self) -> None:
        secret = "SUBJECT-NAMING-A-PRIVATE-PROJECT"
        with self.assertRaises(PlacementError) as caught:
            parse_placement_intent(
                {
                    "board": "b.kicad_pcb",
                    "constraints": dict(CONSTRAINTS),
                    "subjects": ["a"],
                    "proposals": [{"subject": secret}],
                }
            )
        self.assertNotIn(secret, str(caught.exception))

    def test_the_language_cannot_express_an_absolute_position(self) -> None:
        """The design invariant: a rule may not place anything at a coordinate.

        If a future field lets a caller supply an absolute position, the legalizer stops being
        the only thing that decides where copper goes.
        """

        for field_name in ("x_nm", "y_nm", "position_nm", "origin_nm", "at"):
            with self.subTest(field=field_name), self.assertRaises((PlacementError, ValueError)):
                parse_placement_intent(
                    {
                        "board": "b.kicad_pcb",
                        "constraints": dict(CONSTRAINTS),
                        "subjects": ["a"],
                        "proposals": [{"subject": "a", field_name: 1_000_000}],
                    }
                )


class LegalityTests(unittest.TestCase):
    def test_a_legal_board_is_previewed_with_every_check_proven(self) -> None:
        result = _evaluate(FIXTURES / "placement-legal.kicad_pcb")
        self.assertEqual(result.status, "previewed")
        assert result.candidate is not None
        legality = result.candidate.evidence.legality
        self.assertEqual(legality.pad_overlap, "proven_clear")
        self.assertEqual(legality.outline_containment, "proven_inside")
        self.assertEqual(legality.keepout_respect, "proven_clear")
        self.assertEqual(legality.courtyard_overlap, "proven_clear")
        self.assertTrue(legality.legal)

    def test_same_side_rectangular_courtyards_are_exactly_checked(self) -> None:
        """Courtyard overlap is independent from pad overlap on the bounded Board IR subset."""

        source = FRONT_BACK_FOOTPRINT_V02_BOARD.read_bytes()
        source = source.replace(b'(layer "B.Cu")', b'(layer "F.Cu")', 1)
        source = source.replace(b'(layer "B.CrtYd")', b'(layer "F.CrtYd")', 1)
        source = source.replace(b"(at 60 20 0)", b"(at 20 20 0)", 1)
        parsed = parse_kicad_bytes(source, _profile(), ParseLimits())
        assert parsed.snapshot is not None
        view = build_placement_view(source, parsed.snapshot)
        result = evaluate_placement(
            _intent(view, "front-back-overlap.kicad_pcb"), parsed.snapshot, view
        )

        self.assertEqual(result.status, "refused")
        assert result.diagnostic is not None
        assert result.diagnostic.legality is not None
        self.assertEqual(result.diagnostic.code, PlacementFailureCode.ILLEGAL_PLACEMENT)
        self.assertEqual(result.diagnostic.legality.courtyard_overlap, "violated")
        self.assertEqual(result.diagnostic.legality.pad_overlap, "proven_clear")

    def test_front_and_back_courtyards_do_not_collide_across_sides(self) -> None:
        source = FRONT_BACK_FOOTPRINT_V02_BOARD.read_bytes().replace(
            b"(at 60 20 0)", b"(at 20 20 0)", 1
        )
        parsed = parse_kicad_bytes(source, _profile(), ParseLimits())
        assert parsed.snapshot is not None
        view = build_placement_view(source, parsed.snapshot)
        result = evaluate_placement(
            _intent(view, "front-back-cross-side.kicad_pcb"), parsed.snapshot, view
        )

        self.assertEqual(result.status, "previewed")
        assert result.candidate is not None
        self.assertEqual(result.candidate.evidence.legality.courtyard_overlap, "proven_clear")

    def test_closed_line_chain_and_concave_polygon_courtyards_have_exact_legality(self) -> None:
        """The held-out KiCad fixture moves from adapter refusal to exact clear/violation proof."""

        _, snapshot, view = _board(ORTHOGONAL_COURTYARD_BOARD)
        _polygon, line_chain = sorted(view.footprints)
        clear = evaluate_placement(_intent(view, ORTHOGONAL_COURTYARD_BOARD.name), snapshot, view)
        self.assertEqual(clear.status, "previewed")
        assert clear.candidate is not None
        self.assertEqual(clear.candidate.evidence.legality.courtyard_overlap, "proven_clear")

        overlap = evaluate_placement(
            _intent(
                view,
                ORTHOGONAL_COURTYARD_BOARD.name,
                proposals=[{"subject": line_chain, "offset_x_nm": -20_000_000}],
            ),
            snapshot,
            view,
        )
        self.assertEqual(overlap.status, "refused")
        assert overlap.diagnostic is not None
        assert overlap.diagnostic.legality is not None
        self.assertEqual(overlap.diagnostic.code, PlacementFailureCode.ILLEGAL_PLACEMENT)
        self.assertEqual(overlap.diagnostic.legality.courtyard_overlap, "violated")

    def test_each_illegality_is_reported_by_its_own_check(self) -> None:
        """Guard the guard: the three checks must be independent, not one flag in disguise."""

        expected = {
            "placement-overlap.kicad_pcb": ("pad_overlap", "violated"),
            "placement-keepout.kicad_pcb": ("keepout_respect", "violated"),
            "placement-outside-outline.kicad_pcb": ("outline_containment", "violated"),
        }
        for name, (field_name, value) in expected.items():
            with self.subTest(fixture=name):
                result = _evaluate(FIXTURES / name)
                self.assertEqual(result.status, "refused")
                assert result.diagnostic is not None
                self.assertEqual(result.diagnostic.code, PlacementFailureCode.ILLEGAL_PLACEMENT)
                legality = result.diagnostic.legality
                assert legality is not None
                self.assertEqual(getattr(legality, field_name), value)
                # Only the named check fires; the others still prove what they can.
                others = {"pad_overlap", "outline_containment", "keepout_respect"} - {field_name}
                for other in others:
                    self.assertNotEqual(getattr(legality, other), "violated")

    def test_inconclusive_is_neither_clear_nor_a_violation(self) -> None:
        """CopperTone's one clipping pad pair must not be reported as either extreme."""

        if not COPPERTONE.exists():
            self.skipTest("CopperTone board is not present")
        result = _evaluate(COPPERTONE)
        self.assertEqual(result.status, "previewed")
        assert result.candidate is not None
        evidence = result.candidate.evidence
        self.assertEqual(evidence.legality.pad_overlap, "inconclusive")
        self.assertGreater(evidence.inconclusive_pairs, 0)
        # Inconclusive is not a violation, so a candidate is still produced.
        self.assertTrue(evidence.legality.legal)

    def test_outline_containment_brackets_a_circle_at_a_diagonal_edge(self) -> None:
        """A box corner outside is not proof that the circular pad leaves the board."""

        result = _evaluate(FIXTURES / "placement-circle-outline-bracket.kicad_pcb")

        self.assertEqual(result.status, "previewed")
        assert result.candidate is not None
        legality = result.candidate.evidence.legality
        self.assertEqual(legality.outline_containment, "inconclusive")
        self.assertTrue(legality.legal)

    def test_keepout_respect_brackets_a_circle_at_a_diagonal_boundary(self) -> None:
        """An envelope touching a keepout is not proof that circular copper intrudes."""

        result = _evaluate(FIXTURES / "placement-circle-keepout-bracket.kicad_pcb")

        self.assertEqual(result.status, "previewed")
        assert result.candidate is not None
        legality = result.candidate.evidence.legality
        self.assertEqual(legality.keepout_respect, "inconclusive")
        self.assertTrue(legality.legal)

    def test_bounds_and_cores_bracket_every_pad(self) -> None:
        """The direction-of-error invariant the three-valued verdict rests on."""

        _, snapshot, _ = _board(COPPERTONE if COPPERTONE.exists() else ROTATION_BOARD)
        for pad in snapshot.content.pads:
            bounds = pad_bounds(pad)
            core = pad_core(pad)
            if core is None:
                continue
            with self.subTest(pad=pad.id):
                self.assertGreaterEqual(core[0], bounds[0])
                self.assertGreaterEqual(core[1], bounds[1])
                self.assertLessEqual(core[2], bounds[2])
                self.assertLessEqual(core[3], bounds[3])
                self.assertTrue(rects_overlap(bounds, core))


def _square(x0: int, y0: int, x1: int, y1: int) -> Ring:
    """An axis-aligned courtyard ring in nanometres."""

    return Ring(
        points=(PointNM(x0, y0), PointNM(x1, y0), PointNM(x1, y1), PointNM(x0, y1)),
    )


class CourtyardCacheInsetTests(unittest.TestCase):
    """Pin the KiCad 10.0.5 cached-courtyard inset (ADR-0075, issue #72).

    ``FOOTPRINT::BuildCourtyardCaches`` contracts each cached outline by ``maxError = 0.005 mm``
    (``pcbnew/footprint.cpp:3701``/``:3712``), so a collision needs twice that in penetration.
    ``KiCadCourtyardOracleTests`` below re-measures the same offsets against the real tool; these
    cases are the deterministic half of the same claim and run without KiCad installed.
    """

    def test_the_inset_constants_are_the_kicad_10_0_5_values(self) -> None:
        self.assertEqual(COURTYARD_CACHE_INSET_NM, 5_000)
        self.assertEqual(COURTYARD_COLLISION_THRESHOLD_NM, 10_000)

    def _verdict(self, penetration_nm: int, *, height_nm: int = 10_000_000) -> str:
        left = _square(0, 0, 10_000_000, height_nm)
        right = _square(10_000_000 - penetration_nm, 0, 20_000_000, height_nm)
        return orthogonal_courtyard_region_overlap((left,), (right,))

    def test_separated_and_edge_touching_courtyards_are_proven_clear(self) -> None:
        for penetration in (-1_000_000, -1, 0):
            with self.subTest(penetration_nm=penetration):
                self.assertEqual(self._verdict(penetration), "proven_clear")

    def test_penetration_one_nm_inside_the_inset_band_is_inconclusive(self) -> None:
        """The band where raw geometry and KiCad's contracted cache genuinely disagree."""

        for penetration in (1, 5_000, 9_998, 9_999):
            with self.subTest(penetration_nm=penetration):
                self.assertEqual(self._verdict(penetration), "inconclusive")

    def test_penetration_exactly_at_the_inset_threshold_is_violated(self) -> None:
        """Contracted caches that merely touch are a collision, so the bound is inclusive."""

        self.assertEqual(self._verdict(10_000), "violated")

    def test_penetration_one_nm_past_the_threshold_is_violated(self) -> None:
        self.assertEqual(self._verdict(10_001), "violated")

    def test_a_corner_only_overlap_needs_the_threshold_on_both_axes(self) -> None:
        """A shared strip narrower than the threshold in *either* axis cannot be certified."""

        self.assertEqual(self._verdict(10_000, height_nm=9_999), "inconclusive")
        self.assertEqual(self._verdict(9_999, height_nm=10_000), "inconclusive")
        self.assertEqual(self._verdict(10_000, height_nm=10_000), "violated")

    def test_a_courtyard_thinner_than_the_threshold_is_never_certified(self) -> None:
        """KiCad contracts such a shape out of existence; its band was measured as unstable."""

        thin = _square(0, 0, 9_000, 10_000_000)
        overlapping = _square(0, 0, 9_000, 10_000_000)
        self.assertEqual(
            orthogonal_courtyard_region_overlap((thin,), (overlapping,)), "inconclusive"
        )

    def test_a_negative_inset_is_refused_rather_than_silently_accepted(self) -> None:
        left = _square(0, 0, 1_000_000, 1_000_000)
        with self.assertRaises(ValueError):
            orthogonal_courtyard_region_overlap((left,), (left,), inset_nm=-1)


class CourtyardRingNestingTests(unittest.TestCase):
    """A donut courtyard is an outline plus a hole, not two solids (ADR-0075, issue #74).

    KiCad's ``buildContourHierarchy`` counts containing contours and makes an odd count a hole
    (``pcbnew/convert_shape_list_to_polygon.cpp:373-400``, ``:446``).  Pooling every ring's
    crossings before pairing them reproduces exactly that even-odd fill.
    """

    #: Outer 0..30 mm with a 10..20 mm hole.
    DONUT = (
        _square(0, 0, 30_000_000, 30_000_000),
        _square(10_000_000, 10_000_000, 20_000_000, 20_000_000),
    )

    def test_a_footprint_inside_the_hole_is_proven_clear(self) -> None:
        inside = _square(12_000_000, 12_000_000, 18_000_000, 18_000_000)
        self.assertEqual(orthogonal_courtyard_region_overlap(self.DONUT, (inside,)), "proven_clear")

    def test_a_footprint_on_the_annulus_is_violated(self) -> None:
        on_ring = _square(2_000_000, 2_000_000, 8_000_000, 8_000_000)
        self.assertEqual(orthogonal_courtyard_region_overlap(self.DONUT, (on_ring,)), "violated")

    def test_a_footprint_straddling_the_hole_edge_is_violated(self) -> None:
        straddle = _square(5_000_000, 12_000_000, 15_000_000, 18_000_000)
        self.assertEqual(orthogonal_courtyard_region_overlap(self.DONUT, (straddle,)), "violated")

    def test_treating_the_rings_as_two_solids_would_give_the_wrong_answer(self) -> None:
        """Guard the guard: the fix must not be indistinguishable from the bug it replaces."""

        inside = _square(12_000_000, 12_000_000, 18_000_000, 18_000_000)
        outer, hole = self.DONUT
        self.assertTrue(orthogonal_rings_overlap_open(outer, inside))
        self.assertTrue(orthogonal_rings_overlap_open(hole, inside))
        self.assertEqual(orthogonal_courtyard_region_overlap(self.DONUT, (inside,)), "proven_clear")

    def test_two_disjoint_rings_on_one_footprint_stay_two_solids(self) -> None:
        """Even-odd must not turn side-by-side shapes into a hole."""

        pair = (
            _square(0, 0, 10_000_000, 10_000_000),
            _square(20_000_000, 0, 30_000_000, 10_000_000),
        )
        on_second = _square(22_000_000, 2_000_000, 28_000_000, 8_000_000)
        between = _square(12_000_000, 2_000_000, 18_000_000, 8_000_000)
        self.assertEqual(orthogonal_courtyard_region_overlap(pair, (on_second,)), "violated")
        self.assertEqual(orthogonal_courtyard_region_overlap(pair, (between,)), "proven_clear")

    def test_the_donut_fixture_is_previewed_rather_than_refused(self) -> None:
        _, snapshot, view = _board(DONUT_COURTYARD_BOARD)
        result = evaluate_placement(_intent(view, DONUT_COURTYARD_BOARD.name), snapshot, view)

        self.assertEqual(result.status, "previewed")
        assert result.candidate is not None
        self.assertEqual(result.candidate.evidence.legality.courtyard_overlap, "proven_clear")

    def test_moving_the_insert_onto_the_socket_wall_is_refused(self) -> None:
        """The same fixture must still prove a real collision, or the hole fix proves nothing."""

        _, snapshot, view = _board(DONUT_COURTYARD_BOARD)
        insert = next(ref for ref in sorted(view.footprints) if ref.endswith("011"))
        result = evaluate_placement(
            _intent(
                view,
                DONUT_COURTYARD_BOARD.name,
                proposals=[{"subject": insert, "offset_x_nm": -9_000_000}],
            ),
            snapshot,
            view,
        )

        self.assertEqual(result.status, "refused")
        assert result.diagnostic is not None
        assert result.diagnostic.legality is not None
        self.assertEqual(result.diagnostic.legality.courtyard_overlap, "violated")


class CourtyardInconclusiveContractTests(unittest.TestCase):
    """``inconclusive`` must reach the published contract intact, never as a silent pass."""

    def test_the_legality_contract_accepts_the_third_value(self) -> None:
        legality = PlacementLegality(
            pad_overlap="proven_clear",
            outline_containment="proven_inside",
            keepout_respect="proven_clear",
            courtyard_overlap="inconclusive",
        )
        self.assertEqual(legality.to_dict()["courtyard_overlap"], "inconclusive")

    def test_an_inconclusive_courtyard_is_not_a_violation_but_is_not_proven_clear(self) -> None:
        inconclusive = PlacementLegality(
            pad_overlap="proven_clear",
            outline_containment="proven_inside",
            keepout_respect="proven_clear",
            courtyard_overlap="inconclusive",
        )
        self.assertTrue(inconclusive.legal)
        self.assertNotEqual(inconclusive.to_dict()["courtyard_overlap"], "proven_clear")

    def test_a_violated_courtyard_is_still_illegal(self) -> None:
        violated = PlacementLegality(
            pad_overlap="proven_clear",
            outline_containment="proven_inside",
            keepout_respect="proven_clear",
            courtyard_overlap="violated",
        )
        self.assertFalse(violated.legal)

    def test_a_fourth_value_is_refused(self) -> None:
        with self.assertRaises(PlacementError):
            PlacementLegality(
                pad_overlap="proven_clear",
                outline_containment="proven_inside",
                keepout_respect="proven_clear",
                courtyard_overlap="not_modelled",
            )


class PadBoundaryInconclusiveContractTests(unittest.TestCase):
    """Outline and keepout evidence preserve the gap between cores and envelopes."""

    def test_outline_and_keepout_accept_inconclusive_without_calling_it_clear(self) -> None:
        legality = PlacementLegality(
            pad_overlap="proven_clear",
            outline_containment="inconclusive",
            keepout_respect="inconclusive",
            courtyard_overlap="proven_clear",
        )

        self.assertTrue(legality.legal)
        self.assertEqual(legality.to_dict()["outline_containment"], "inconclusive")
        self.assertEqual(legality.to_dict()["keepout_respect"], "inconclusive")

    def test_a_fourth_boundary_value_is_refused(self) -> None:
        with self.subTest(field="outline_containment"), self.assertRaises(PlacementError):
            PlacementLegality(
                pad_overlap="proven_clear",
                outline_containment="not_modelled",
                keepout_respect="proven_clear",
                courtyard_overlap="proven_clear",
            )
        with self.subTest(field="keepout_respect"), self.assertRaises(PlacementError):
            PlacementLegality(
                pad_overlap="proven_clear",
                outline_containment="proven_inside",
                keepout_respect="not_modelled",
                courtyard_overlap="proven_clear",
            )


class FailureTaxonomyTests(unittest.TestCase):
    def test_an_unresolved_reference_is_its_own_code(self) -> None:
        _, snapshot, view = _board(FIXTURES / "placement-legal.kicad_pcb")
        intent = _intent(
            view, "placement-legal.kicad_pcb", subjects=["footprint:kicad:does-not-exist"]
        )
        result = evaluate_placement(intent, snapshot, view)
        self.assertEqual(result.status, "refused")
        assert result.diagnostic is not None
        self.assertEqual(result.diagnostic.code, PlacementFailureCode.UNRESOLVED_REF)

    def test_provably_contradictory_rules_are_infeasible_not_exhausted(self) -> None:
        _, snapshot, view = _board(FIXTURES / "placement-legal.kicad_pcb")
        subject = sorted(view.footprints)[0]
        contradictions = (
            [
                {"kind": "side", "subject": subject, "side": "front"},
                {"kind": "side", "subject": subject, "side": "back"},
            ],
            [
                {"kind": "orientation", "subject": subject, "allowed": [0]},
                {"kind": "orientation", "subject": subject, "allowed": [180_000_000]},
            ],
            [
                {"kind": "edge", "subject": subject, "edge": "north", "offset_nm": 0},
                {"kind": "edge", "subject": subject, "edge": "south", "offset_nm": 0},
            ],
        )
        for rules in contradictions:
            with self.subTest(rules=rules[0]["kind"]):
                result = evaluate_placement(
                    _intent(view, "placement-legal.kicad_pcb", rules=rules), snapshot, view
                )
                assert result.diagnostic is not None
                self.assertEqual(
                    result.diagnostic.code, PlacementFailureCode.INFEASIBLE_CONSTRAINTS
                )

    def test_running_out_of_budget_is_never_reported_as_infeasible(self) -> None:
        """The distinction the taxonomy exists to protect."""

        _, snapshot, view = _board(FIXTURES / "placement-legal.kicad_pcb")
        result = evaluate_placement(
            _intent(view, "placement-legal.kicad_pcb"), snapshot, view, max_checks=1
        )
        assert result.diagnostic is not None
        self.assertEqual(result.diagnostic.code, PlacementFailureCode.BUDGET_EXHAUSTED)
        self.assertNotEqual(result.diagnostic.code, PlacementFailureCode.INFEASIBLE_CONSTRAINTS)

    def test_a_stale_view_is_refused_rather_than_evaluated(self) -> None:
        _, snapshot, _ = _board(FIXTURES / "placement-legal.kicad_pcb")
        _, _, other_view = _board(FIXTURES / "placement-overlap.kicad_pcb")
        result = evaluate_placement(
            _intent(other_view, "placement-overlap.kicad_pcb"), snapshot, other_view
        )
        assert result.diagnostic is not None
        self.assertEqual(result.diagnostic.code, PlacementFailureCode.STALE_REVISION)

    def test_a_side_change_is_refused_as_unsupported_rather_than_mirrored_wrongly(self) -> None:
        _, snapshot, view = _board(FIXTURES / "placement-legal.kicad_pcb")
        subject = sorted(view.footprints)[0]
        result = evaluate_placement(
            _intent(
                view,
                "placement-legal.kicad_pcb",
                proposals=[{"subject": subject, "side": "back"}],
            ),
            snapshot,
            view,
        )
        assert result.diagnostic is not None
        self.assertEqual(result.diagnostic.code, PlacementFailureCode.UNSUPPORTED_GEOMETRY)

    def test_a_nonorthogonal_source_pose_is_a_typed_refusal_after_an_orthogonal_proposal(
        self,
    ) -> None:
        """An orthogonal proposal must not mask a non-orthogonal pose it is measured against.

        Every pad offset and courtyard point is un-rotated out of the *saved* pose before being
        turned into the proposed one, so the stored angle has to be orthogonal too. Checking only
        the resulting orientation let an orthogonal proposal hide an oblique source, and the
        un-rotation escaped this boundary as a bare ``ValueError`` from ``rotate_offset``.
        """

        snapshot, view, subject = _skewed_saved_pose(FOOTPRINT_V02_BOARD)
        self.assertEqual(view.footprints[subject].orientation_udeg, 45_000_000)

        result = evaluate_placement(
            _intent(
                view,
                FOOTPRINT_V02_BOARD.name,
                proposals=[{"subject": subject, "orientation_udeg": 90_000_000}],
            ),
            snapshot,
            view,
        )

        self.assertEqual(result.status, "refused")
        self.assertIsNone(result.candidate)
        assert result.diagnostic is not None
        self.assertEqual(result.diagnostic.code, PlacementFailureCode.UNSUPPORTED_GEOMETRY)
        self.assertEqual(
            result.diagnostic.message, "a placement subject's saved pose is not orthogonal"
        )
        self.assertNotIn("45", result.diagnostic.message)

    def test_every_reference_to_a_nonorthogonal_saved_pose_is_refused_the_same_way(self) -> None:
        """Anchors and rule members reach the same footprint by a different path.

        ``_place`` resolves every footprint the view carries, not only proposal subjects, so one
        oblique saved pose has to be refused whether it is named as a subject, borrowed as an
        anchor, measured by a rule, or merely present. All four must land on the same code.
        """

        snapshot, view, subject = _skewed_saved_pose(FOOTPRINT_V02_BOARD)
        other = next(
            ref
            for ref in sorted(view.footprints)
            if ref != subject and not view.footprints[ref].locked
        )
        cases: dict[str, dict[str, Any]] = {
            "merely present": {},
            "translated subject": {"proposals": [{"subject": subject, "offset_x_nm": 1_000_000}]},
            "another proposal's anchor": {
                "proposals": [{"subject": other, "anchor": subject, "offset_x_nm": 1_000_000}]
            },
            "rule member": {
                "rules": [{"kind": "alignment", "axis": "x", "members": [subject, other]}]
            },
        }
        for reason, overrides in cases.items():
            with self.subTest(reason=reason):
                result = evaluate_placement(
                    _intent(view, FOOTPRINT_V02_BOARD.name, **overrides), snapshot, view
                )
                self.assertIsNone(result.candidate)
                assert result.diagnostic is not None
                self.assertEqual(result.diagnostic.code, PlacementFailureCode.UNSUPPORTED_GEOMETRY)


class RuleEvaluationTests(unittest.TestCase):
    def _view(self) -> tuple[Any, Any, list[str]]:
        _, snapshot, view = _board(FIXTURES / "placement-legal.kicad_pcb")
        return snapshot, view, sorted(view.footprints)

    def test_an_exactly_satisfied_rule_says_exactly(self) -> None:
        snapshot, view, refs = self._view()
        result = evaluate_placement(
            _intent(
                view,
                "placement-legal.kicad_pcb",
                rules=[{"kind": "alignment", "axis": "y", "members": refs}],
            ),
            snapshot,
            view,
        )
        assert result.candidate is not None
        outcome = result.candidate.evidence.rule_results[0]
        self.assertEqual(outcome.status, "satisfied_exactly")
        self.assertEqual(outcome.residual_nm, 0)

    def test_tolerance_is_never_applied_unless_it_was_asked_for(self) -> None:
        """A missing tolerance means exact, so a one-nanometre residual is a violation."""

        snapshot, view, refs = self._view()
        strict = evaluate_placement(
            _intent(
                view,
                "placement-legal.kicad_pcb",
                rules=[
                    {
                        "kind": "proximity",
                        "subject": refs[0],
                        "target": refs[1],
                        "max_distance_nm": 0,
                    }
                ],
            ),
            snapshot,
            view,
        )
        assert strict.candidate is not None
        strict_result = strict.candidate.evidence.rule_results[0]
        self.assertEqual(strict_result.status, "violated")
        self.assertGreater(strict_result.residual_nm, 0)

        lenient = evaluate_placement(
            _intent(
                view,
                "placement-legal.kicad_pcb",
                rules=[
                    {
                        "kind": "proximity",
                        "subject": refs[0],
                        "target": refs[1],
                        "max_distance_nm": 0,
                        "tolerance_nm": strict_result.residual_nm,
                    }
                ],
            ),
            snapshot,
            view,
        )
        assert lenient.candidate is not None
        self.assertEqual(
            lenient.candidate.evidence.rule_results[0].status, "satisfied_within_tolerance"
        )

    def test_a_violated_rule_does_not_by_itself_make_a_placement_illegal(self) -> None:
        """Rules express preference; legality is separate and is what refuses a candidate."""

        snapshot, view, refs = self._view()
        result = evaluate_placement(
            _intent(
                view,
                "placement-legal.kicad_pcb",
                rules=[
                    {
                        "kind": "proximity",
                        "subject": refs[0],
                        "target": refs[1],
                        "max_distance_nm": 0,
                    }
                ],
            ),
            snapshot,
            view,
        )
        self.assertEqual(result.status, "previewed")
        assert result.candidate is not None
        self.assertEqual(result.candidate.evidence.rule_results[0].status, "violated")
        self.assertTrue(result.candidate.evidence.legality.legal)

    def test_an_edge_rule_measures_from_the_board_outline(self) -> None:
        """The legal fixture's west pad spans 9..11mm on a board starting at x = 0."""

        snapshot, view, refs = self._view()
        exact = evaluate_placement(
            _intent(
                view,
                "placement-legal.kicad_pcb",
                rules=[
                    {"kind": "edge", "subject": refs[0], "edge": "west", "offset_nm": 9_000_000}
                ],
            ),
            snapshot,
            view,
        )
        assert exact.candidate is not None
        self.assertEqual(exact.candidate.evidence.rule_results[0].status, "satisfied_exactly")

        wrong = evaluate_placement(
            _intent(
                view,
                "placement-legal.kicad_pcb",
                rules=[
                    {"kind": "edge", "subject": refs[0], "edge": "west", "offset_nm": 1_000_000}
                ],
            ),
            snapshot,
            view,
        )
        assert wrong.candidate is not None
        outcome = wrong.candidate.evidence.rule_results[0]
        self.assertEqual(outcome.status, "violated")
        self.assertEqual(outcome.residual_nm, 8_000_000)

    def test_a_symmetry_rule_measures_the_mirror_exactly(self) -> None:
        """The two pads sit at x = 10mm and x = 30mm, so they mirror about x = 20mm."""

        snapshot, view, refs = self._view()
        # Anchor the mirror on a footprint placed midway by proposal, so "about" is real
        # geometry rather than a number the rule carries.
        result = evaluate_placement(
            _intent(
                view,
                "placement-legal.kicad_pcb",
                rules=[
                    {
                        "kind": "symmetry",
                        "axis": "x",
                        "about": refs[0],
                        "pairs": [[refs[0], refs[1]]],
                    }
                ],
            ),
            snapshot,
            view,
        )
        assert result.candidate is not None
        outcome = result.candidate.evidence.rule_results[0]
        # Mirroring the pair about the *first* footprint is off by the full span, and the
        # residual states that in nanometres rather than as a bare failure.
        self.assertEqual(outcome.status, "violated")
        self.assertEqual(outcome.residual_nm, 20_000_000)

    def test_a_region_rule_uses_the_named_boundary(self) -> None:
        _, snapshot, view = _board(FIXTURES / "placement-keepout.kicad_pcb")
        refs = sorted(view.footprints)
        keepout_id = snapshot.content.keepouts[0].id
        result = evaluate_placement(
            _intent(
                view,
                "placement-keepout.kicad_pcb",
                rules=[
                    {
                        "kind": "region",
                        "subject": refs[0],
                        "mode": "keep_out",
                        "boundary_ref": keepout_id,
                    },
                    {
                        "kind": "region",
                        "subject": refs[1],
                        "mode": "keep_out",
                        "boundary_ref": keepout_id,
                    },
                ],
            ),
            snapshot,
            view,
        )
        assert result.diagnostic is not None
        # The board is illegal, but the rule results still travel with the refusal.
        outcomes = {item.rule_index: item.status for item in result.diagnostic.rule_results}
        self.assertEqual(outcomes[0], "satisfied_exactly", "the west footprint is clear")
        self.assertEqual(outcomes[1], "violated", "the east footprint sits in the keepout")

    def test_region_keep_in_uses_the_pad_core(self) -> None:
        """The core is inside this diamond while the circular pad's box corners are outside."""

        source = (
            (FIXTURES / "placement-circle-keepout-bracket.kicad_pcb")
            .read_bytes()
            .replace(b"(at 25.6 10.6 0)", b"(at 29 14 0)")
        )
        converted = parse_kicad_bytes(source, _profile(), ParseLimits())
        assert converted.snapshot is not None
        snapshot = converted.snapshot
        view = build_placement_view(source, snapshot)
        pad_ref = next(iter(view.owner_by_pad))
        boundary_ref = snapshot.content.keepouts[0].id

        result = evaluate_placement(
            _intent(
                view,
                "directional-region-rule.kicad_pcb",
                rules=[
                    {
                        "kind": "region",
                        "subject": pad_ref,
                        "mode": "keep_in",
                        "boundary_ref": boundary_ref,
                    },
                ],
            ),
            snapshot,
            view,
        )

        assert result.diagnostic is not None
        assert result.diagnostic.legality is not None
        self.assertEqual(
            [item.status for item in result.diagnostic.rule_results],
            ["satisfied_exactly"],
        )
        self.assertEqual(result.diagnostic.legality.keepout_respect, "violated")

    def test_region_keep_out_uses_the_pad_bounds(self) -> None:
        """The box touches this diamond while the circular core remains outside it."""

        source = (FIXTURES / "placement-circle-keepout-bracket.kicad_pcb").read_bytes()
        converted = parse_kicad_bytes(source, _profile(), ParseLimits())
        assert converted.snapshot is not None
        snapshot = converted.snapshot
        view = build_placement_view(source, snapshot)
        pad_ref = next(iter(view.owner_by_pad))
        boundary_ref = snapshot.content.keepouts[0].id

        result = evaluate_placement(
            _intent(
                view,
                "directional-region-keep-out-rule.kicad_pcb",
                rules=[
                    {
                        "kind": "region",
                        "subject": pad_ref,
                        "mode": "keep_out",
                        "boundary_ref": boundary_ref,
                    }
                ],
            ),
            snapshot,
            view,
        )

        assert result.candidate is not None
        self.assertEqual(
            [item.status for item in result.candidate.evidence.rule_results],
            ["violated"],
        )
        self.assertEqual(result.candidate.evidence.legality.keepout_respect, "inconclusive")

    def test_alignment_position_does_not_come_from_an_asymmetric_pad_envelope(self) -> None:
        """Custom-pad preparation: position is the pad centre, never the envelope centre."""

        _, snapshot, view = _board(FIXTURES / "placement-legal.kicad_pcb")
        footprint_ref = sorted(view.footprints)[0]
        footprint = view.footprints[footprint_ref]
        pad = next(item for item in snapshot.content.pads if item.id in footprint.pad_ids)
        pad_centre = PointNM(11_000_000, 13_000_000)
        placed_pad = _PlacedPad(
            pad=pad,
            centre=pad_centre,
            rotation_udeg=0,
            bounds=(20_000_000, 30_000_000, 26_000_000, 38_000_000),
            core=(10_000_000, 12_000_000, 12_000_000, 14_000_000),
        )
        origin = PointNM(7_000_000, 9_000_000)
        placed = _PlacedFootprint(
            ref_id=footprint_ref,
            origin=origin,
            orientation_udeg=0,
            side="front",
            moved=False,
            pads=(placed_pad,),
            hull=placed_pad.bounds,
            courtyards=(),
            courtyard_circles=(),
        )
        placed_by_ref = {footprint_ref: placed}

        self.assertEqual(_resolve_centre(footprint_ref, placed_by_ref, view), origin)
        self.assertEqual(_resolve_centre(pad.id, placed_by_ref, view), pad_centre)

    def test_a_region_rule_naming_a_boundary_that_does_not_exist_is_unresolved(self) -> None:
        snapshot, view, refs = self._view()
        result = evaluate_placement(
            _intent(
                view,
                "placement-legal.kicad_pcb",
                rules=[
                    {
                        "kind": "region",
                        "subject": refs[0],
                        "mode": "keep_in",
                        "boundary_ref": "keepout:kicad:nope",
                    }
                ],
            ),
            snapshot,
            view,
        )
        assert result.diagnostic is not None
        self.assertEqual(result.diagnostic.code, PlacementFailureCode.UNRESOLVED_REF)

    def test_an_orientation_rule_reads_the_proposed_pose_not_the_stored_one(self) -> None:
        snapshot, view, refs = self._view()
        result = evaluate_placement(
            _intent(
                view,
                "placement-legal.kicad_pcb",
                proposals=[{"subject": refs[0], "orientation_udeg": 90_000_000}],
                rules=[{"kind": "orientation", "subject": refs[0], "allowed": [90_000_000]}],
            ),
            snapshot,
            view,
        )
        assert result.candidate is not None
        self.assertEqual(result.candidate.evidence.rule_results[0].status, "satisfied_exactly")
        placement = next(item for item in result.candidate.placements if item.ref_id == refs[0])
        self.assertEqual(placement.orientation_udeg, 90_000_000)
        self.assertTrue(placement.moved)


class CandidateTests(unittest.TestCase):
    def test_a_candidate_id_is_the_digest_of_its_own_content(self) -> None:
        result = _evaluate(FIXTURES / "placement-legal.kicad_pcb")
        assert result.candidate is not None
        self.assertTrue(verify_placement_id(result.candidate))
        self.assertEqual(result.candidate.ordering_policy, ORDERING_POLICY)
        self.assertEqual(result.candidate.placement_version, PLACEMENT_VERSION)

    def test_a_candidate_binds_to_both_digests(self) -> None:
        _, snapshot, view = _board(FIXTURES / "placement-legal.kicad_pcb")
        result = evaluate_placement(_intent(view, "placement-legal.kicad_pcb"), snapshot, view)
        assert result.candidate is not None
        self.assertEqual(result.candidate.base_revision, snapshot.snapshot_digest)
        self.assertEqual(result.candidate.view_revision, view.board_revision)

    def test_tampering_with_a_candidate_breaks_its_identity(self) -> None:
        from dataclasses import replace

        result = _evaluate(FIXTURES / "placement-legal.kicad_pcb")
        assert result.candidate is not None
        moved = replace(
            result.candidate,
            placements=tuple(
                replace(item, origin_x_nm=item.origin_x_nm + 1_000)
                for item in result.candidate.placements
            ),
        )
        with self.assertRaises(PlacementError):
            verify_placement_id(moved)

    def test_evaluation_is_deterministic(self) -> None:
        first = _evaluate(FIXTURES / "placement-legal.kicad_pcb")
        second = _evaluate(FIXTURES / "placement-legal.kicad_pcb")
        assert first.candidate is not None and second.candidate is not None
        self.assertEqual(first.candidate.candidate_id, second.candidate.candidate_id)
        self.assertEqual(
            canonical_candidate_bytes(first.candidate),
            canonical_candidate_bytes(second.candidate),
        )

    def test_placements_are_recorded_in_reference_order(self) -> None:
        result = _evaluate(FIXTURES / "placement-legal.kicad_pcb")
        assert result.candidate is not None
        refs = [item.ref_id for item in result.candidate.placements]
        self.assertEqual(refs, sorted(refs))


class SnapTests(unittest.TestCase):
    def test_snapping_is_deterministic_on_both_sides_of_zero(self) -> None:
        for value, grid, expected in (
            (0, 1_000, 0),
            (499, 1_000, 0),
            (500, 1_000, 1_000),
            (-499, 1_000, 0),
            (-500, 1_000, 0),
            (-501, 1_000, -1_000),
            (1_500, 1_000, 2_000),
        ):
            with self.subTest(value=value):
                self.assertEqual(snap(value, grid), expected)

    def test_a_snapped_origin_lands_on_the_grid(self) -> None:
        _, snapshot, view = _board(FIXTURES / "placement-legal.kicad_pcb")
        subject = sorted(view.footprints)[0]
        result = evaluate_placement(
            _intent(
                view,
                "placement-legal.kicad_pcb",
                placement_grid_nm=250_000,
                proposals=[{"subject": subject, "offset_x_nm": 1_234_567}],
            ),
            snapshot,
            view,
        )
        assert result.candidate is not None
        placement = next(item for item in result.candidate.placements if item.ref_id == subject)
        self.assertEqual(placement.origin_x_nm % 250_000, 0)
        self.assertEqual(placement.origin_y_nm % 250_000, 0)


class MetamorphicTests(unittest.TestCase):
    """Every verdict and residual must travel with the board under a rigid motion."""

    def _translated(self, delta: int) -> bytes:
        source = (FIXTURES / "placement-legal.kicad_pcb").read_text(encoding="utf-8")
        millimetres = delta // 1_000_000
        for original, moved in (
            ("(at 10 15 0)", f"(at {10 + millimetres} 15 0)"),
            ("(at 30 15 0)", f"(at {30 + millimetres} 15 0)"),
            ("(end 40 30)", f"(end {40 + millimetres} 30)"),
        ):
            source = source.replace(original, moved)
        return source.encode("utf-8")

    def test_translating_a_board_preserves_every_verdict_and_residual(self) -> None:
        base = _evaluate(FIXTURES / "placement-legal.kicad_pcb")
        assert base.candidate is not None

        moved_source = self._translated(5_000_000)
        result = parse_kicad_bytes(moved_source, _profile(), ParseLimits())
        assert result.snapshot is not None
        moved_view = build_placement_view(moved_source, result.snapshot)
        refs = sorted(moved_view.footprints)
        rules = [{"kind": "alignment", "axis": "y", "members": refs}]

        base_with_rules = _evaluate(FIXTURES / "placement-legal.kicad_pcb", rules=rules)
        moved = evaluate_placement(
            _intent(moved_view, "placement-legal.kicad_pcb", rules=rules),
            result.snapshot,
            moved_view,
        )
        assert base_with_rules.candidate is not None and moved.candidate is not None

        self.assertEqual(base_with_rules.status, moved.status)
        self.assertEqual(
            base_with_rules.candidate.evidence.legality.to_dict(),
            moved.candidate.evidence.legality.to_dict(),
        )
        self.assertEqual(
            [item.to_dict() for item in base_with_rules.candidate.evidence.rule_results],
            [item.to_dict() for item in moved.candidate.evidence.rule_results],
        )
        # References are invariant; only the coordinates moved.
        self.assertEqual(
            [item.ref_id for item in base_with_rules.candidate.placements],
            [item.ref_id for item in moved.candidate.placements],
        )
        for before, after in zip(
            base_with_rules.candidate.placements, moved.candidate.placements, strict=True
        ):
            self.assertEqual(after.origin_x_nm - before.origin_x_nm, 5_000_000)
            self.assertEqual(after.origin_y_nm, before.origin_y_nm)

    def test_the_relation_would_notice_a_verdict_that_did_not_travel(self) -> None:
        """Guard the guard: moving the board far enough must change the verdict."""

        moved_source = self._translated(500_000_000)
        # The outline moved with the board, so containment still holds; shrink it instead.
        shrunk = moved_source.replace(b"(end 540 30)", b"(end 40 30)")
        shrunk_result = parse_kicad_bytes(shrunk, _profile(), ParseLimits())
        assert shrunk_result.snapshot is not None
        shrunk_view = build_placement_view(shrunk, shrunk_result.snapshot)
        outcome = evaluate_placement(
            _intent(shrunk_view, "placement-legal.kicad_pcb"),
            shrunk_result.snapshot,
            shrunk_view,
        )
        assert outcome.candidate is not None
        self.assertEqual(outcome.status, "previewed")
        self.assertEqual(outcome.candidate.evidence.legality.outline_containment, "inconclusive")


class RotatedNonSquareTests(unittest.TestCase):
    """The fixture that exposed the pad-rotation defect, now used for placement."""

    def test_a_board_of_rotated_footprints_is_placed_and_proven_legal(self) -> None:
        result = _evaluate(ROTATION_BOARD)
        self.assertEqual(result.status, "previewed")
        assert result.candidate is not None
        self.assertEqual(result.candidate.evidence.legality.outline_containment, "proven_inside")

    def test_turning_a_footprint_moves_its_pads_without_changing_their_identity(self) -> None:
        _, snapshot, view = _board(ROTATION_BOARD)
        subject = sorted(view.footprints)[0]
        before = view.footprints[subject]
        result = evaluate_placement(
            _intent(
                view,
                ROTATION_BOARD.name,
                proposals=[{"subject": subject, "orientation_udeg": 90_000_000}],
            ),
            snapshot,
            view,
        )
        assert result.candidate is not None
        placement = next(item for item in result.candidate.placements if item.ref_id == subject)
        self.assertEqual(placement.orientation_udeg, 90_000_000)
        self.assertNotEqual(placement.orientation_udeg, before.orientation_udeg)
        # The footprint still owns exactly the pads it owned before.
        self.assertEqual(view.footprints[subject].pad_ids, before.pad_ids)


@pytest.mark.skipif(not REAL_KICAD_CLI.is_file(), reason="KiCad CLI is not installed")
class KiCadOracleTests(unittest.TestCase):
    """Cross-check the deterministic verdict against the authoritative tool.

    The binding direction is one-way and deliberately so: anything this legalizer calls
    ``violated`` must also be a DRC error, because a violation is claimed as a proof. An
    ``inconclusive`` may map either way - that is what inconclusive means - and a
    ``proven_clear`` is only a claim about pad overlap, not about every rule KiCad checks.
    """

    def _drc_violation_types(self, board: Path) -> dict[str, int]:
        import json
        import tempfile
        from collections import Counter

        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            shutil.copy2(board, work / board.name)
            report = work / "drc.json"
            completed = subprocess.run(  # noqa: S603
                [
                    str(REAL_KICAD_CLI),
                    "pcb",
                    "drc",
                    "--format",
                    "json",
                    "--units",
                    "mm",
                    "--severity-all",
                    "-o",
                    str(report),
                    str(work / board.name),
                ],
                capture_output=True,
                check=False,
                timeout=180,
            )
            self.assertIn(completed.returncode, (0, 5), completed.stderr)
            payload = json.loads(report.read_text(encoding="utf-8"))
        return dict(
            Counter(
                str(violation.get("type"))
                for violation in payload.get("violations", [])
                if violation.get("severity") == "error"
            )
        )

    def _drc_errors(self, board: Path) -> int:
        return sum(self._drc_violation_types(board).values())

    def test_every_proven_violation_is_also_a_kicad_error(self) -> None:
        """All three checks, each against the authoritative tool.

        Measured on these fixtures, KiCad names a different violation type for each -
        ``shorting_items`` for the overlap, ``items_not_allowed`` for the keepout, and
        ``copper_edge_clearance`` for the board edge - which is independent evidence that the
        three checks here are genuinely separate and not one flag wearing three names.
        """

        expected = {
            "placement-overlap.kicad_pcb": "pad_overlap",
            "placement-keepout.kicad_pcb": "keepout_respect",
            "placement-outside-outline.kicad_pcb": "outline_containment",
        }
        for name, field_name in expected.items():
            with self.subTest(fixture=name):
                board = FIXTURES / name
                result = _evaluate(board)
                assert result.diagnostic is not None
                assert result.diagnostic.legality is not None
                self.assertEqual(getattr(result.diagnostic.legality, field_name), "violated")
                self.assertGreater(
                    self._drc_errors(board),
                    0,
                    "a placement proven illegal must not be a board KiCad calls clean",
                )

    def test_a_board_this_legalizer_previews_is_not_contradicted_by_kicad(self) -> None:
        board = FIXTURES / "placement-legal.kicad_pcb"
        result = _evaluate(board)
        self.assertEqual(result.status, "previewed")
        self.assertEqual(self._drc_errors(board), 0)

    def test_direction_brackets_do_not_publish_kicad_violations_that_are_absent(self) -> None:
        cases = {
            "placement-circle-outline-bracket.kicad_pcb": "copper_edge_clearance",
            "placement-circle-keepout-bracket.kicad_pcb": "items_not_allowed",
        }
        for name, violation_type in cases.items():
            with self.subTest(fixture=name):
                board = FIXTURES / name
                result = _evaluate(board)
                assert result.candidate is not None
                self.assertIn(
                    "inconclusive",
                    result.candidate.evidence.legality.to_dict().values(),
                )
                self.assertEqual(self._drc_violation_types(board).get(violation_type, 0), 0)


@pytest.mark.skipif(not REAL_KICAD_CLI.is_file(), reason="KiCad CLI is not installed")
class KiCadCourtyardOracleTests(unittest.TestCase):
    """Corroborate the courtyard model against real ``kicad-cli`` 10.0.5 (ADR-0075).

    These read KiCad's own ``courtyards_overlap`` verdict rather than an error count, because the
    claim under test is specifically about the courtyard provider and not about DRC in general.
    """

    def _courtyard_violations(self, board: Path) -> int:
        import json
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            shutil.copy2(board, work / board.name)
            report = work / "drc.json"
            completed = subprocess.run(  # noqa: S603 - fixed local argv, trusted discovered CLI
                [
                    str(REAL_KICAD_CLI),
                    "pcb",
                    "drc",
                    "--format",
                    "json",
                    "--units",
                    "mm",
                    "--severity-all",
                    "-o",
                    str(report),
                    str(work / board.name),
                ],
                capture_output=True,
                check=False,
                timeout=180,
            )
            self.assertIn(completed.returncode, (0, 5), completed.stderr)
            payload = json.loads(report.read_text(encoding="utf-8"))
        return sum(
            1
            for violation in payload.get("violations", [])
            if violation.get("type") == "courtyards_overlap"
        )

    def test_kicad_reports_no_overlap_for_a_part_inside_a_donut_courtyard(self) -> None:
        """Issue #74: the hole is a hole. Treating the rings as solids contradicts the tool."""

        self.assertEqual(self._courtyard_violations(DONUT_COURTYARD_BOARD), 0)
        _, snapshot, view = _board(DONUT_COURTYARD_BOARD)
        result = evaluate_placement(_intent(view, DONUT_COURTYARD_BOARD.name), snapshot, view)
        assert result.candidate is not None
        self.assertEqual(result.candidate.evidence.legality.courtyard_overlap, "proven_clear")

    def test_kicad_agrees_at_and_below_the_cached_courtyard_inset_threshold(self) -> None:
        """Issue #72: 9,999 nm of penetration is clear to KiCad and 10,000 nm is a collision.

        The deterministic model must never contradict that: ``violated`` only where KiCad reports
        the overlap, and never ``proven_clear`` where real geometry overlaps.
        """

        below = ROOT / "tests" / "fixtures" / "board-ir-v0.2" / "courtyard-inset-below.kicad_pcb"
        at_threshold = (
            ROOT / "tests" / "fixtures" / "board-ir-v0.2" / "courtyard-inset-at.kicad_pcb"
        )

        self.assertEqual(self._courtyard_violations(below), 0)
        self.assertEqual(self._courtyard_violations(at_threshold), 1)

        _, snapshot, view = _board(below)
        previewed = evaluate_placement(_intent(view, below.name), snapshot, view)
        assert previewed.candidate is not None
        self.assertEqual(previewed.candidate.evidence.legality.courtyard_overlap, "inconclusive")

        _, snapshot, view = _board(at_threshold)
        refused = evaluate_placement(_intent(view, at_threshold.name), snapshot, view)
        assert refused.diagnostic is not None
        assert refused.diagnostic.legality is not None
        self.assertEqual(refused.diagnostic.legality.courtyard_overlap, "violated")

    def test_every_courtyard_violation_is_also_a_kicad_courtyard_violation(self) -> None:
        """The one-way binding: a proof of collision must be one the authoritative tool shares."""

        source = FRONT_BACK_FOOTPRINT_V02_BOARD.read_bytes()
        source = source.replace(b'(layer "B.Cu")', b'(layer "F.Cu")', 1)
        source = source.replace(b'(layer "B.CrtYd")', b'(layer "F.CrtYd")', 1)
        source = source.replace(b"(at 60 20 0)", b"(at 20 20 0)", 1)
        parsed = parse_kicad_bytes(source, _profile(), ParseLimits())
        assert parsed.snapshot is not None
        view = build_placement_view(source, parsed.snapshot)
        result = evaluate_placement(_intent(view, "same-side.kicad_pcb"), parsed.snapshot, view)
        assert result.diagnostic is not None
        assert result.diagnostic.legality is not None
        self.assertEqual(result.diagnostic.legality.courtyard_overlap, "violated")

        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            board = Path(directory) / "same-side.kicad_pcb"
            board.write_bytes(source)
            self.assertGreater(self._courtyard_violations(board), 0)


class CopperToneScaleTests(unittest.TestCase):
    def setUp(self) -> None:
        if not COPPERTONE.exists():
            self.skipTest("CopperTone board is not present")

    def test_the_real_board_places_within_the_default_budget(self) -> None:
        result = _evaluate(COPPERTONE)
        self.assertEqual(result.status, "previewed")
        assert result.candidate is not None
        self.assertEqual(len(result.candidate.placements), 26)
        self.assertLess(result.candidate.evidence.checks_used, 100_000)

    def test_the_inconclusive_rate_stays_negligible(self) -> None:
        """Pins the measurement that decided exact pad geometry is not needed in v0.1.

        Bounding boxes settle 1,359 of 1,360 different-net pad pairs on the real board. If this
        ever climbs, the three-valued verdict stops being informative and exact shapes become
        worth their complexity.
        """

        _, snapshot, _ = _board(COPPERTONE)
        pads = list(snapshot.content.pads)
        pairs = inconclusive = 0
        for index, first in enumerate(pads):
            for second in pads[index + 1 :]:
                if not set(first.layer_ids) & set(second.layer_ids):
                    continue
                if first.net_id is not None and first.net_id == second.net_id:
                    continue
                pairs += 1
                if not rects_overlap(pad_bounds(first), pad_bounds(second)):
                    continue
                left, right = pad_core(first), pad_core(second)
                if left is None or right is None or not rects_overlap(left, right):
                    inconclusive += 1
        self.assertEqual(pairs, 1_360)
        self.assertEqual(inconclusive, 1)
        self.assertLess(inconclusive / pairs, 0.01)

    def test_every_pad_on_the_real_board_has_a_modelled_core(self) -> None:
        """Without a core a pad can never be proven to collide with anything."""

        _, snapshot, _ = _board(COPPERTONE)
        missing = [pad.id for pad in snapshot.content.pads if pad_core(pad) is None]
        self.assertEqual(missing, [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
