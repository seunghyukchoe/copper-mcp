"""Circuit Scene IR v0.1: region scoping, ceilings, reference durability, and quarantine.

The quarantine test is the load-bearing one. It does not check that the hostile strings are
labelled correctly in the place we chose to put them — it greps the entire serialized response
for a marker that appears in every author-controlled slot of the fixture, and requires that the
marker occur nowhere outside ``annotations``. A future field that innocently interpolates a
footprint reference into a structural value fails that test without anyone remembering to
update it.
"""

from __future__ import annotations

import json
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Any

from copper_mcp.circuit_scene import (
    SCENE_VERSION,
    CircuitSceneError,
    SceneAnnotation,
    observe_board_scene,
    parse_circuit_scene_request,
)
from copper_mcp.config import Settings
from copper_mcp.mcp_contracts import CircuitSceneToolResponse

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "circuit-scene-v0.1"
COPPERTONE = ROOT / "hardware" / "coppertone-buffer"

CONSTRAINTS = {
    "clearance_nm": 200_000,
    "track_width_nm": 250_000,
    "via_diameter_nm": 600_000,
    "via_drill_nm": 300_000,
}
WHOLE_BOARD = {
    "min_x_nm": -1_000_000_000,
    "min_y_nm": -1_000_000_000,
    "max_x_nm": 1_000_000_000,
    "max_y_nm": 1_000_000_000,
}

#: Every author-controlled string in scene-hostile-text.kicad_pcb contains this marker.
CANARY = "CANARY"


def _settings(workspace: Path, **overrides: Any) -> Settings:
    return replace(Settings(workspace=workspace.resolve()), **overrides)


def _request(board: str, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "board": board,
        "constraints": dict(CONSTRAINTS),
        "region": dict(WHOLE_BOARD),
    }
    payload.update(overrides)
    return payload


def _observe(board: str, workspace: Path = FIXTURES, **overrides: Any) -> dict[str, Any]:
    scene = observe_board_scene(_request(board, **overrides), _settings(workspace))
    document = scene.to_dict()
    # Every scene this suite produces must satisfy the advertised machine contract, so a
    # widened response cannot pass by only being checked field by field.
    CircuitSceneToolResponse.model_validate(document)
    return document


def _refs(document: dict[str, Any], partition: str, kind: str) -> set[str]:
    return {item["ref_id"] for item in document[partition][kind]}


class RequestBoundaryTests(unittest.TestCase):
    def test_region_must_be_exactly_one_of_the_two_documented_forms(self) -> None:
        for region, reason in (
            ({}, "neither form"),
            ({"min_x_nm": 0, "min_y_nm": 0}, "partial box"),
            ({"around_ref_id": "pad:kicad:x"}, "reference without radius"),
            ({**WHOLE_BOARD, "around_ref_id": "pad:kicad:x"}, "both forms"),
            ({**WHOLE_BOARD, "radius_nm": 10}, "box with radius"),
            ({"around_ref_id": "pad:kicad:x", "radius_nm": 0}, "non-positive radius"),
            (
                {"min_x_nm": 10, "min_y_nm": 0, "max_x_nm": 0, "max_y_nm": 10},
                "reversed bounds",
            ),
        ):
            with self.subTest(reason=reason):
                with self.assertRaises(CircuitSceneError):
                    parse_circuit_scene_request(_request("b.kicad_pcb", region=region))

    def test_unknown_and_duplicate_fields_are_refused(self) -> None:
        with self.assertRaises(CircuitSceneError):
            parse_circuit_scene_request(_request("b.kicad_pcb", surprise=1))
        with self.assertRaises(CircuitSceneError):
            parse_circuit_scene_request(_request("b.kicad_pcb", layers=["F.Cu", "F.Cu"]))

    def test_rejection_messages_do_not_echo_the_rejected_value(self) -> None:
        secret = "F.Cu-PRIVATE-PROJECT-NAME"
        with self.assertRaises(CircuitSceneError) as caught:
            parse_circuit_scene_request(_request("b.kicad_pcb", layers=[secret]))
        self.assertNotIn(secret, str(caught.exception))


class RegionScopingTests(unittest.TestCase):
    """scene-region.kicad_pcb puts WEST copper near x=10mm and EAST copper near x=90mm."""

    def test_a_bounding_box_returns_only_objects_that_overlap_it(self) -> None:
        west = _observe(
            "scene-region.kicad_pcb",
            region={"min_x_nm": 0, "min_y_nm": 0, "max_x_nm": 30_000_000, "max_y_nm": 30_000_000},
        )
        self.assertEqual(len(west["static"]["pads"]), 1)
        self.assertEqual(len(west["mutable"]["segments"]), 1)
        self.assertEqual(west["mutable"]["vias"], [])
        self.assertEqual(west["mutable"]["zones"], [])
        self.assertEqual(west["region"]["source"], "explicit")

        east = _observe(
            "scene-region.kicad_pcb",
            region={
                "min_x_nm": 60_000_000,
                "min_y_nm": 0,
                "max_x_nm": 100_000_000,
                "max_y_nm": 30_000_000,
            },
        )
        self.assertEqual(len(east["static"]["pads"]), 1)
        self.assertEqual(len(east["mutable"]["vias"]), 1)
        self.assertEqual(len(east["mutable"]["zones"]), 1)
        self.assertEqual(
            _refs(west, "static", "pads") & _refs(east, "static", "pads"),
            set(),
            "the two windows must not share an object",
        )

    def test_the_whole_board_is_the_union_of_disjoint_windows(self) -> None:
        whole = _observe("scene-region.kicad_pcb")
        self.assertEqual(len(whole["static"]["pads"]), 2)
        self.assertEqual(len(whole["mutable"]["segments"]), 2)
        self.assertEqual(len(whole["mutable"]["vias"]), 1)

    def test_around_ref_expands_the_referenced_object_by_the_radius(self) -> None:
        whole = _observe("scene-region.kicad_pcb")
        west_pad = next(
            item
            for item in whole["static"]["pads"]
            if item["geometry"]["center_nm"][0] < 50_000_000
        )
        near = _observe(
            "scene-region.kicad_pcb",
            region={"around_ref_id": west_pad["ref_id"], "radius_nm": 9_000_000},
        )
        self.assertEqual(near["region"]["source"], "around_ref")
        # The pad spans 9..11mm, so a 9mm radius reaches 0..20mm and cannot touch x=80mm.
        self.assertEqual(near["region"]["min_x_nm"], 0)
        self.assertEqual(near["region"]["max_x_nm"], 20_000_000)
        self.assertEqual(_refs(near, "static", "pads"), {west_pad["ref_id"]})
        self.assertEqual(near["mutable"]["vias"], [])

    def test_an_unknown_reference_is_refused_without_quoting_it(self) -> None:
        with self.assertRaises(CircuitSceneError) as caught:
            observe_board_scene(
                _request(
                    "scene-region.kicad_pcb",
                    region={"around_ref_id": "pad:kicad:no-such-object", "radius_nm": 1_000},
                ),
                _settings(FIXTURES),
            )
        self.assertNotIn("no-such-object", str(caught.exception))

    def test_a_layer_filter_drops_copper_on_other_layers(self) -> None:
        back = _observe("scene-region.kicad_pcb", layers=["B.Cu"])
        self.assertEqual(len(back["mutable"]["segments"]), 1)
        self.assertEqual(back["mutable"]["segments"][0]["layer_ids"], ["layer:B.Cu"])
        self.assertEqual(back["mutable"]["zones"], [], "the only zone is on F.Cu")
        # A through via and a through-hole pad live on both layers and survive either filter.
        self.assertEqual(len(back["mutable"]["vias"]), 1)


class UntrustedTextQuarantineTests(unittest.TestCase):
    def test_board_text_is_absent_unless_it_is_explicitly_requested(self) -> None:
        document = _observe("scene-hostile-text.kicad_pcb")
        self.assertTrue(document["supported"])
        self.assertEqual(document["annotations"], [])
        self.assertNotIn(CANARY, json.dumps(document))

    def test_requested_board_text_appears_only_inside_annotations(self) -> None:
        document = _observe("scene-hostile-text.kicad_pcb", include_annotations=True)
        self.assertTrue(document["annotations"])

        # The whole-response grep. Removing the quarantined collection must remove every
        # trace of the marker from the response, whatever shape the rest of it grows into.
        elsewhere = {key: value for key, value in document.items() if key != "annotations"}
        self.assertNotIn(CANARY, json.dumps(elsewhere))

        # Within an annotation the marker may only ever be the payload, never a label.
        for annotation in document["annotations"]:
            structural = {
                key: value for key, value in annotation.items() if key not in {"text", "ref_id"}
            }
            self.assertNotIn(CANARY, json.dumps(structural))
            self.assertEqual(annotation["trust"], "untrusted_board_author")

    def test_hostile_net_names_never_reach_the_response(self) -> None:
        document = _observe("scene-hostile-text.kicad_pcb", include_annotations=True)
        for pad in document["static"]["pads"]:
            net_id = pad["geometry"]["net_id"]
            self.assertIsNotNone(net_id)
            assert isinstance(net_id, str)
            self.assertTrue(net_id.startswith("net:"))
            self.assertNotIn("CANARY_NET_NAME", net_id)

    def test_every_hostile_slot_in_the_fixture_is_actually_reported(self) -> None:
        """Guard the guard: a fixture whose text silently stopped being read proves nothing."""

        document = _observe("scene-hostile-text.kicad_pcb", include_annotations=True)
        texts = {annotation["text"] for annotation in document["annotations"]}
        for marker in (
            "CANARY_SILK_GR",
            "CANARY_FAB_GR",
            "CANARY_FP_TEXT",
            "CANARY_REFERENCE_U1",
            "CANARY_PROPERTY_NAME",
            "CANARY_PROPERTY_VALUE",
        ):
            with self.subTest(marker=marker):
                self.assertTrue(
                    any(marker in text for text in texts),
                    f"{marker} is in the fixture but was never quarantined",
                )

    def test_there_is_no_vocabulary_for_a_trusted_annotation(self) -> None:
        with self.assertRaises(CircuitSceneError):
            SceneAnnotation(
                ref_id="annotation:x",
                layer_id=None,
                origin="board_text",
                text="hi",
                trust="trusted",
            )


class ReferenceStabilityTests(unittest.TestCase):
    def test_board_objects_report_their_native_kicad_identity(self) -> None:
        document = _observe("scene-region.kicad_pcb")
        for partition in ("static", "mutable"):
            for kind, items in document[partition].items():
                for item in items:
                    if kind == "rules":
                        continue
                    with self.subTest(kind=kind, ref=item["ref_id"]):
                        self.assertEqual(item["ref_stability"], "native")
                        self.assertIn(":kicad:", item["ref_id"])

    def test_request_scoped_rules_do_not_pollute_the_board_durability_signal(self) -> None:
        document = _observe("scene-region.kicad_pcb")
        rules = document["static"]["rules"]
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0]["ref_stability"], "request_scoped")
        summary = document["ref_stability"]
        self.assertTrue(summary["all_board_refs_native"])
        self.assertEqual(summary["content_derived_count"], 0)
        self.assertEqual(summary["request_scoped_count"], 1)

    def test_references_are_stable_across_repeated_observation(self) -> None:
        first = _observe("scene-region.kicad_pcb")
        second = _observe("scene-region.kicad_pcb")
        self.assertEqual(first, second)


class BudgetTests(unittest.TestCase):
    def test_an_object_ceiling_truncates_and_says_so(self) -> None:
        scene = observe_board_scene(
            _request("scene-region.kicad_pcb"), _settings(FIXTURES, max_scene_objects=3)
        )
        document = scene.to_dict()
        CircuitSceneToolResponse.model_validate(document)
        truncation = document["truncation"]
        self.assertEqual(truncation["objects_returned"], 3)
        self.assertGreater(truncation["objects_omitted"], 0)
        self.assertEqual(truncation["ceiling_hit"], "max_scene_objects")

    def test_a_vertex_ceiling_truncates_and_says_which_ceiling(self) -> None:
        scene = observe_board_scene(
            _request("scene-region.kicad_pcb"), _settings(FIXTURES, max_scene_vertices=5)
        )
        document = scene.to_dict()
        CircuitSceneToolResponse.model_validate(document)
        self.assertEqual(document["truncation"]["ceiling_hit"], "max_scene_vertices")
        self.assertGreater(document["truncation"]["objects_omitted"], 0)

    def test_an_untruncated_scene_states_completeness_rather_than_implying_it(self) -> None:
        document = _observe("scene-region.kicad_pcb")
        self.assertIsNone(document["truncation"]["ceiling_hit"])
        self.assertEqual(document["truncation"]["objects_omitted"], 0)
        total = sum(len(items) for items in document["static"].values()) + sum(
            len(items) for items in document["mutable"].values()
        )
        self.assertEqual(document["truncation"]["objects_returned"], total)

    def test_truncation_is_deterministic(self) -> None:
        settings = _settings(FIXTURES, max_scene_objects=3)
        first = observe_board_scene(_request("scene-region.kicad_pcb"), settings).to_dict()
        second = observe_board_scene(_request("scene-region.kicad_pcb"), settings).to_dict()
        self.assertEqual(first, second)


class UnsupportedBoardTests(unittest.TestCase):
    def test_a_board_outside_the_workspace_is_refused(self) -> None:
        """The region is a view onto one board, never a way to reach a second one."""

        for path in ("../board-ir-v0.1/subset.kicad_pcb", "/etc/hosts", "subset.kicad_pcb"):
            with self.subTest(path=path), self.assertRaises(Exception) as caught:
                observe_board_scene(_request(path), _settings(FIXTURES))
            self.assertNotIsInstance(caught.exception, AssertionError)

    def test_an_unsupported_board_never_returns_annotations(self) -> None:
        board = ROOT / "tests" / "fixtures" / "board-ir-v0.1" / "malformed-unbalanced.kicad_pcb"
        document = observe_board_scene(
            _request(board.name, include_annotations=True),
            _settings(board.parent),
        ).to_dict()
        CircuitSceneToolResponse.model_validate(document)
        self.assertFalse(document["supported"])
        self.assertIsNone(document["region"])
        self.assertEqual(document["annotations"], [])
        self.assertTrue(document["conversion_diagnostic_counts"])
        # Diagnostics are counted by code, never echoed with their message or content.
        for code, count in document["conversion_diagnostic_counts"].items():
            self.assertRegex(code, r"^[a-z_]+\.[a-z_]+$")
            self.assertGreaterEqual(count, 1)


class ProvenanceTests(unittest.TestCase):
    def test_a_scene_carries_the_versions_and_digests_needed_to_re_derive_it(self) -> None:
        document = _observe("scene-region.kicad_pcb")
        self.assertEqual(document["scene_version"], SCENE_VERSION)
        self.assertEqual(document["board_path"], "scene-region.kicad_pcb")
        self.assertRegex(document["board_revision"], r"^sha256:[0-9a-f]{64}$")
        self.assertRegex(document["snapshot_digest"], r"^sha256:[0-9a-f]{64}$")
        self.assertEqual(document["request"]["constraints"], CONSTRAINTS)


class CopperToneScaleTests(unittest.TestCase):
    """The real board, not a synthetic one, is what the ceilings have to be right for."""

    def setUp(self) -> None:
        if not (COPPERTONE / "coppertone-buffer.kicad_pcb").exists():
            self.skipTest("CopperTone board is not present")

    def test_the_whole_real_board_fits_inside_the_default_ceilings(self) -> None:
        document = _observe("coppertone-buffer.kicad_pcb", workspace=COPPERTONE)
        self.assertTrue(document["supported"])
        self.assertIsNone(
            document["truncation"]["ceiling_hit"],
            "a real two-layer board must not need truncation at the default ceilings",
        )
        total = document["truncation"]["objects_returned"]
        self.assertGreater(total, 100, "the board should be substantial enough to be a real test")
        self.assertLess(total, Settings(workspace=COPPERTONE).max_scene_objects)

    def test_a_whole_real_board_scene_stays_within_a_usable_response_size(self) -> None:
        document = _observe("coppertone-buffer.kicad_pcb", workspace=COPPERTONE)
        size = len(json.dumps(document))
        self.assertLess(size, 250_000, "an unscoped real board must still be a usable response")

    def test_region_scoping_is_what_makes_a_dense_board_affordable(self) -> None:
        whole = _observe("coppertone-buffer.kicad_pcb", workspace=COPPERTONE)
        window = _observe(
            "coppertone-buffer.kicad_pcb",
            workspace=COPPERTONE,
            region={
                "min_x_nm": 0,
                "min_y_nm": 0,
                "max_x_nm": 10_000_000,
                "max_y_nm": 10_000_000,
            },
        )
        self.assertLess(
            window["truncation"]["objects_returned"],
            whole["truncation"]["objects_returned"],
        )
        self.assertLess(len(json.dumps(window)), len(json.dumps(whole)) // 2)

    def test_every_reference_on_the_real_board_is_natively_stable(self) -> None:
        document = _observe("coppertone-buffer.kicad_pcb", workspace=COPPERTONE)
        self.assertTrue(document["ref_stability"]["all_board_refs_native"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
