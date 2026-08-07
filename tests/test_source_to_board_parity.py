"""Tests for authoritative source-to-board connectivity parity.

Two kinds of test live here, following the split ADR-0071's suite established. The report-parsing
and binding tests are hermetic: they feed the parser exact bytes and assert the typed refusal, so
every rejection path runs without a KiCad install. The real-CLI tests skip when KiCad is absent and
are the only place a claim about what KiCad *actually says* is allowed to come from.

The control that matters most is :meth:`RealKiCadParityTest.test_net_mismatch_is_detected`. Without
it the suite would pass just as happily against an oracle that never ran, because an empty
``schematic_parity`` array is what both a clean board and a dead check produce.
"""

from __future__ import annotations

import json
import shutil
import unittest
from copy import deepcopy
from pathlib import Path
from typing import Any

from copper_mcp.adapters.kicad_schematic import render_kicad_schematic
from copper_mcp.circuit_ir import decode_snapshot_json
from copper_mcp.config import Settings
from copper_mcp.kicad_cli import (
    KICAD_DRC_SCHEMA,
    PARITY_BOARD_SNAPSHOT_NAME,
    PARITY_CONNECTIVITY_TYPES,
    PARITY_PROJECTION_TYPES,
    KiCadCliError,
    SourceToBoardParityEvidence,
    _parse_parity_report,
    run_source_to_board_parity,
)
from copper_mcp.mcp_contracts import SourceToBoardParityToolResponse
from copper_mcp.security import WorkspaceViolationError
from copper_mcp.source_to_board_parity_service import (
    SourceToBoardParityResult,
    verify_source_to_board_parity_from_content,
    verify_source_to_board_parity_from_snapshot_json,
)

ROOT = Path(__file__).resolve().parents[1]
INTENT = ROOT / "benchmarks" / "audio" / "fixtures" / "rc-low-pass-intent-v1.json"
BOARDS = ROOT / "tests" / "fixtures" / "source-to-board-parity"
REAL_KICAD_CLI = Path("/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli")

INTENT_DIGEST = "sha256:" + "1" * 64
SCHEMATIC_DIGEST = "sha256:" + "2" * 64
PROJECTION_DIGEST = "sha256:" + "3" * 64
BOARD_REVISION = "sha256:" + "4" * 64


def finding(finding_type: str, *, severity: str = "warning") -> dict[str, Any]:
    return {
        "type": finding_type,
        "description": "finding",
        "severity": severity,
        "items": [],
    }


def parity_report(
    *,
    schema: str = KICAD_DRC_SCHEMA,
    source: str = PARITY_BOARD_SNAPSHOT_NAME,
    schematic_parity: list[dict[str, Any]] | None = None,
    included_severities: list[str] | None = None,
    coordinate_units: str = "mm",
    kicad_version: str = "10.0.5",
    date: str = "2026-08-06T20:34:38",
) -> dict[str, Any]:
    """Build the report shape KiCad 10.0.5 actually emits for a two-component projection."""

    if schematic_parity is None:
        # The default is the measured shape for a board that matches: every component accounted
        # for by a footprint_symbol_mismatch, and no connectivity finding.
        schematic_parity = [
            finding("footprint_symbol_mismatch"),
            finding("footprint_symbol_mismatch"),
            finding("footprint_symbol_field_mismatch"),
            finding("footprint_symbol_field_mismatch"),
        ]
    return {
        "$schema": schema,
        "coordinate_units": coordinate_units,
        "date": date,
        "ignored_checks": [{"key": "missing_courtyard", "description": "no courtyard"}],
        "included_severities": (
            ["error", "warning", "exclusion"]
            if included_severities is None
            else included_severities
        ),
        "kicad_version": kicad_version,
        "schematic_parity": schematic_parity,
        "source": source,
        "unconnected_items": [],
        "violations": [],
    }


def parse(report: dict[str, Any], *, return_code: int = 0, component_count: int = 2) -> Any:
    return _parse_parity_report(
        json.dumps(report).encode("utf-8"),
        return_code=return_code,
        component_count=component_count,
        intent_digest=INTENT_DIGEST,
        schematic_digest=SCHEMATIC_DIGEST,
        parity_schematic_digest=PROJECTION_DIGEST,
        board_revision=BOARD_REVISION,
    )


class ParityProjectionRenderTest(unittest.TestCase):
    """The two derivatives must differ in exactly one way, and only one of them is delivered."""

    def setUp(self) -> None:
        self.snapshot = decode_snapshot_json(INTENT.read_bytes())

    def test_delivered_schematic_is_board_excluded(self) -> None:
        artifact = render_kicad_schematic(self.snapshot)
        self.assertIn(b"(on_board no)", artifact.content)
        self.assertNotIn(b"(on_board yes)", artifact.content)

    def test_projection_is_board_eligible(self) -> None:
        projection = render_kicad_schematic(self.snapshot, board_eligible=True)
        self.assertIn(b"(on_board yes)", projection.content)
        self.assertNotIn(b"(on_board no)", projection.content)

    def test_projection_differs_only_in_board_eligibility(self) -> None:
        delivered = render_kicad_schematic(self.snapshot).content.decode("utf-8")
        projection = render_kicad_schematic(self.snapshot, board_eligible=True).content.decode(
            "utf-8"
        )
        self.assertEqual(
            delivered.replace("(on_board no)", "(on_board yes)"),
            projection,
            "the projection must be the delivered schematic's board-eligibility flip and nothing "
            "else; any other divergence would make the parity verdict describe different bytes",
        )

    def test_projection_preserves_every_count(self) -> None:
        delivered = render_kicad_schematic(self.snapshot)
        projection = render_kicad_schematic(self.snapshot, board_eligible=True)
        self.assertEqual(delivered.intent_digest, projection.intent_digest)
        self.assertEqual(delivered.component_count, projection.component_count)
        self.assertEqual(delivered.net_count, projection.net_count)
        self.assertEqual(delivered.port_count, projection.port_count)
        self.assertNotEqual(delivered.artifact_digest, projection.artifact_digest)

    def test_rendering_is_deterministic(self) -> None:
        self.assertEqual(
            render_kicad_schematic(self.snapshot, board_eligible=True),
            render_kicad_schematic(self.snapshot, board_eligible=True),
        )

    def test_board_eligibility_must_be_a_bool(self) -> None:
        for value in (1, "yes", None):
            with self.assertRaises(ValueError):
                render_kicad_schematic(self.snapshot, board_eligible=value)  # type: ignore[arg-type]


class ParityLivenessTest(unittest.TestCase):
    """ADR-0084's invariant: the report must prove the check it reports on actually ran."""

    def test_matching_board_passes(self) -> None:
        evidence = parse(parity_report())
        self.assertTrue(evidence.passed)
        self.assertEqual(evidence.oracle_live, "passed")
        self.assertEqual(evidence.connectivity_finding_count, 0)
        self.assertEqual(evidence.projection_finding_count, 4)

    def test_empty_parity_array_is_refused_not_passed(self) -> None:
        """The central hazard: an unfetched netlist looks exactly like a clean board."""

        with self.assertRaises(KiCadCliError) as caught:
            parse(parity_report(schematic_parity=[]))
        self.assertIn("did not run", str(caught.exception))

    def test_under_accounted_components_are_refused(self) -> None:
        with self.assertRaises(KiCadCliError):
            parse(
                parity_report(
                    schematic_parity=[finding("footprint_symbol_mismatch")],
                ),
                component_count=2,
            )

    def test_over_accounted_components_are_refused(self) -> None:
        with self.assertRaises(KiCadCliError):
            parse(parity_report(), component_count=1)

    def test_missing_footprint_also_accounts_for_a_component(self) -> None:
        """A component absent from the board is accounted for, and is a connectivity failure."""

        evidence = parse(
            parity_report(
                schematic_parity=[
                    finding("missing_footprint"),
                    finding("footprint_symbol_mismatch"),
                ]
            )
        )
        self.assertFalse(evidence.passed)
        self.assertEqual(evidence.oracle_live, "passed")
        self.assertEqual(evidence.connectivity_finding_count, 1)

    def test_connectivity_findings_fail_the_verdict(self) -> None:
        for finding_type in sorted(PARITY_CONNECTIVITY_TYPES - {"missing_footprint"}):
            with self.subTest(finding_type=finding_type):
                evidence = parse(
                    parity_report(
                        schematic_parity=[
                            finding("footprint_symbol_mismatch"),
                            finding("footprint_symbol_mismatch"),
                            finding(finding_type),
                        ]
                    )
                )
                self.assertFalse(evidence.passed)
                self.assertEqual(evidence.parity_type_counts[finding_type], 1)

    def test_projection_findings_never_fail_the_verdict(self) -> None:
        for finding_type in sorted(PARITY_PROJECTION_TYPES - {"footprint_symbol_mismatch"}):
            with self.subTest(finding_type=finding_type):
                evidence = parse(
                    parity_report(
                        schematic_parity=[
                            finding("footprint_symbol_mismatch"),
                            finding("footprint_symbol_mismatch"),
                            finding(finding_type),
                        ]
                    )
                )
                self.assertTrue(evidence.passed)
                self.assertEqual(evidence.projection_finding_count, 3)


class ParityReportRefusalTest(unittest.TestCase):
    """Every accepted report shape is reviewed; everything else is a typed refusal."""

    def test_non_utf8_json_is_refused(self) -> None:
        with self.assertRaises(KiCadCliError):
            _parse_parity_report(
                b"\xff\xfe not json",
                return_code=0,
                component_count=2,
                intent_digest=INTENT_DIGEST,
                schematic_digest=SCHEMATIC_DIGEST,
                parity_schematic_digest=PROJECTION_DIGEST,
                board_revision=BOARD_REVISION,
            )

    def test_unsupported_schema_is_refused(self) -> None:
        with self.assertRaises(KiCadCliError):
            parse(parity_report(schema="https://schemas.kicad.org/erc.v1.json"))

    def test_foreign_source_is_refused(self) -> None:
        with self.assertRaises(KiCadCliError):
            parse(parity_report(source="someone-elses-board.kicad_pcb"))

    def test_non_millimetre_units_are_refused(self) -> None:
        with self.assertRaises(KiCadCliError):
            parse(parity_report(coordinate_units="in"))

    def test_malformed_date_is_refused(self) -> None:
        with self.assertRaises(KiCadCliError):
            parse(parity_report(date="not-a-date"))

    def test_malformed_version_is_refused(self) -> None:
        with self.assertRaises(KiCadCliError):
            parse(parity_report(kicad_version="ten"))

    def test_narrowed_severities_are_refused(self) -> None:
        """Parity findings are warning-severity, so a narrowed set empties the array silently."""

        with self.assertRaises(KiCadCliError):
            parse(parity_report(included_severities=["error"]))

    def test_wrong_severities_of_the_right_length_are_refused(self) -> None:
        """A same-length but wrong severity set must not slip past on arity alone."""

        for severities in (
            ["error", "error", "error"],
            ["error", "warning", "warning"],
            ["error", "warning", "info"],
        ):
            with self.subTest(severities=severities):
                with self.assertRaises(KiCadCliError):
                    parse(parity_report(included_severities=severities))

    def test_duplicated_severities_are_refused(self) -> None:
        """KiCad emits each severity once; a padded list is not the shape we reviewed."""

        with self.assertRaises(KiCadCliError):
            parse(parity_report(included_severities=["error", "warning", "exclusion", "exclusion"]))

    def test_non_zero_exit_code_is_refused(self) -> None:
        with self.assertRaises(KiCadCliError):
            parse(parity_report(), return_code=5)

    def test_unreviewed_finding_type_is_refused(self) -> None:
        with self.assertRaises(KiCadCliError):
            parse(
                parity_report(
                    schematic_parity=[
                        finding("footprint_symbol_mismatch"),
                        finding("footprint_symbol_mismatch"),
                        finding("some_future_parity_check"),
                    ]
                )
            )

    def test_excluded_finding_is_refused(self) -> None:
        report = parity_report()
        report["schematic_parity"][0]["excluded"] = True
        with self.assertRaises(KiCadCliError):
            parse(report)

    def test_unsupported_severity_is_refused(self) -> None:
        with self.assertRaises(KiCadCliError):
            parse(
                parity_report(
                    schematic_parity=[
                        finding("footprint_symbol_mismatch", severity="catastrophe"),
                        finding("footprint_symbol_mismatch"),
                    ]
                )
            )

    def test_malformed_finding_fields_are_refused(self) -> None:
        for mutation in ("type", "description", "severity", "items"):
            with self.subTest(field=mutation):
                report = parity_report()
                report["schematic_parity"][0][mutation] = None
                with self.assertRaises(KiCadCliError):
                    parse(report)

    def test_missing_collections_are_refused(self) -> None:
        for collection in ("violations", "unconnected_items", "schematic_parity"):
            with self.subTest(collection=collection):
                report = parity_report()
                report[collection] = "not a list"
                with self.assertRaises(KiCadCliError):
                    parse(report)


class ParityEvidenceBindingTest(unittest.TestCase):
    """Evidence that is not bound to what it describes is evidence about nothing."""

    def evidence(self, **overrides: Any) -> SourceToBoardParityEvidence:
        fields: dict[str, Any] = {
            "intent_digest": INTENT_DIGEST,
            "schematic_digest": SCHEMATIC_DIGEST,
            "parity_schematic_digest": PROJECTION_DIGEST,
            "board_revision": BOARD_REVISION,
            "kicad_version": "10.0.5",
            "drc_schema": KICAD_DRC_SCHEMA,
            "coordinate_units": "mm",
            "component_count": 2,
            "connectivity_finding_count": 0,
            "projection_finding_count": 4,
            "parity_type_counts": {"footprint_symbol_mismatch": 2},
            "oracle_live": "passed",
            "passed": True,
        }
        fields.update(overrides)
        return SourceToBoardParityEvidence(**fields)

    def test_well_formed_evidence_is_accepted(self) -> None:
        self.assertTrue(self.evidence().passed)

    def test_malformed_digests_are_refused(self) -> None:
        for field in (
            "intent_digest",
            "schematic_digest",
            "parity_schematic_digest",
            "board_revision",
        ):
            with self.subTest(field=field):
                with self.assertRaises(ValueError):
                    self.evidence(**{field: "not-a-digest"})

    def test_projection_equal_to_schematic_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            self.evidence(parity_schematic_digest=SCHEMATIC_DIGEST)

    def test_verdict_must_match_its_findings(self) -> None:
        """The guard that makes `passed` derived rather than asserted."""

        with self.assertRaises(ValueError):
            self.evidence(passed=True, connectivity_finding_count=1)
        with self.assertRaises(ValueError):
            self.evidence(passed=False, connectivity_finding_count=0)

    def test_dead_oracle_cannot_be_represented(self) -> None:
        with self.assertRaises(ValueError):
            self.evidence(oracle_live="not_run")

    def test_zero_components_are_refused(self) -> None:
        with self.assertRaises(ValueError):
            self.evidence(component_count=0)


class ParityInputRefusalTest(unittest.TestCase):
    """The subprocess entry point refuses malformed bindings before running anything."""

    def setUp(self) -> None:
        self.settings = Settings(workspace=ROOT, kicad_cli=REAL_KICAD_CLI)
        self.projection = render_kicad_schematic(
            decode_snapshot_json(INTENT.read_bytes()), board_eligible=True
        )

    def call(self, **overrides: Any) -> None:
        fields: dict[str, Any] = {
            "requested_path": "tests/fixtures/source-to-board-parity/matching.kicad_pcb",
            "projection": self.projection.content,
            "component_count": 2,
            "intent_digest": INTENT_DIGEST,
            "schematic_digest": SCHEMATIC_DIGEST,
            "parity_schematic_digest": self.projection.artifact_digest,
            "settings": self.settings,
        }
        fields.update(overrides)
        path = fields.pop("requested_path")
        projection = fields.pop("projection")
        run_source_to_board_parity(path, projection, **fields)

    def test_empty_projection_is_refused(self) -> None:
        with self.assertRaises(KiCadCliError):
            self.call(projection=b"")

    def test_projection_digest_must_match_its_bytes(self) -> None:
        with self.assertRaises(KiCadCliError):
            self.call(parity_schematic_digest=PROJECTION_DIGEST)

    def test_projection_equal_to_delivered_digest_is_refused(self) -> None:
        with self.assertRaises(KiCadCliError):
            self.call(schematic_digest=self.projection.artifact_digest)

    def test_malformed_component_count_is_refused(self) -> None:
        for value in (0, -1, True, "two"):
            with self.subTest(value=value):
                with self.assertRaises(KiCadCliError):
                    self.call(component_count=value)

    def test_malformed_digest_is_refused(self) -> None:
        with self.assertRaises(KiCadCliError):
            self.call(intent_digest="nope")

    def test_non_board_suffix_is_refused(self) -> None:
        # The bounded workspace reader owns this refusal, so the type asserted here is its own.
        # A blind `Exception` would also pass on an import error or a typo in this very test.
        with self.assertRaises(WorkspaceViolationError):
            self.call(requested_path="README.md")

    def test_escaping_path_is_refused(self) -> None:
        with self.assertRaises(WorkspaceViolationError):
            self.call(requested_path="../../etc/passwd.kicad_pcb")


class ParityServiceBindingTest(unittest.TestCase):
    """The service refuses a result whose parts describe different things."""

    def setUp(self) -> None:
        self.snapshot = decode_snapshot_json(INTENT.read_bytes())
        self.artifact = render_kicad_schematic(self.snapshot)
        self.projection = render_kicad_schematic(self.snapshot, board_eligible=True)
        self.evidence = SourceToBoardParityEvidence(
            intent_digest=self.artifact.intent_digest,
            schematic_digest=self.artifact.artifact_digest,
            parity_schematic_digest=self.projection.artifact_digest,
            board_revision=BOARD_REVISION,
            kicad_version="10.0.5",
            drc_schema=KICAD_DRC_SCHEMA,
            coordinate_units="mm",
            component_count=self.artifact.component_count,
            connectivity_finding_count=0,
            projection_finding_count=4,
            parity_type_counts={"footprint_symbol_mismatch": 2},
            oracle_live="passed",
            passed=True,
        )

    def result(self) -> SourceToBoardParityResult:
        return SourceToBoardParityResult(
            artifact=self.artifact,
            projection=self.projection,
            parity=self.evidence,
        )

    def test_bound_result_is_accepted(self) -> None:
        self.assertTrue(self.result().parity.passed)

    def test_unbound_schematic_digest_is_refused(self) -> None:
        import dataclasses

        stray = dataclasses.replace(self.evidence, schematic_digest=SCHEMATIC_DIGEST)
        with self.assertRaises(ValueError):
            SourceToBoardParityResult(
                artifact=self.artifact, projection=self.projection, parity=stray
            )

    def test_unbound_projection_digest_is_refused(self) -> None:
        import dataclasses

        stray = dataclasses.replace(self.evidence, parity_schematic_digest=PROJECTION_DIGEST)
        with self.assertRaises(ValueError):
            SourceToBoardParityResult(
                artifact=self.artifact, projection=self.projection, parity=stray
            )

    def test_projection_used_as_the_delivered_artifact_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            SourceToBoardParityResult(
                artifact=self.projection, projection=self.projection, parity=self.evidence
            )

    def test_serialized_result_matches_the_mcp_contract(self) -> None:
        payload = self.result().to_dict()
        validated = SourceToBoardParityToolResponse.model_validate(payload)
        self.assertEqual(validated.verification.schematic_board_parity, "passed")
        self.assertEqual(validated.parity_projection.differs_from_schematic_by, "board_eligibility")
        self.assertEqual(validated.board.board_revision, BOARD_REVISION)

    def test_failed_verdict_serializes_as_failed(self) -> None:
        import dataclasses

        failed = dataclasses.replace(self.evidence, connectivity_finding_count=1, passed=False)
        payload = SourceToBoardParityResult(
            artifact=self.artifact, projection=self.projection, parity=failed
        ).to_dict()
        validated = SourceToBoardParityToolResponse.model_validate(payload)
        self.assertEqual(validated.verification.schematic_board_parity, "failed")

    def test_serialized_result_leaks_no_design_text(self) -> None:
        """Parity descriptions embed net names verbatim; none of it may cross the boundary."""

        payload = json.dumps(self.result().to_dict())
        for secret in ("AUDIO_IN", "AUDIO_OUT", "GND", "R1", "C1", "1k", "100n", "kicad_sch\n"):
            self.assertNotIn(secret, payload)

    def test_non_claims_are_explicit(self) -> None:
        verification = self.result().to_dict()["verification"]
        self.assertEqual(verification["erc"], "not_run")
        self.assertEqual(verification["footprint_correctness"], "not_run")
        self.assertEqual(verification["electrical_validation"], "not_run")
        self.assertIs(verification["board_ready"], False)


@unittest.skipUnless(REAL_KICAD_CLI.exists(), "real KiCad CLI is unavailable")
class RealKiCadParityTest(unittest.TestCase):
    """Executed against KiCad 10.0.5. The only place a claim about KiCad's verdict comes from."""

    def setUp(self) -> None:
        import tempfile

        self.snapshot = decode_snapshot_json(INTENT.read_bytes())
        self._workspace = tempfile.TemporaryDirectory()
        self.workspace = Path(self._workspace.name)
        self.addCleanup(self._workspace.cleanup)

    def verify(self, board: str) -> SourceToBoardParityResult:
        shutil.copy(BOARDS / board, self.workspace / "board.kicad_pcb")
        settings = Settings(workspace=self.workspace, kicad_cli=REAL_KICAD_CLI)
        return verify_source_to_board_parity_from_snapshot_json(
            INTENT.read_bytes(), "board.kicad_pcb", settings
        )

    def test_matching_board_passes(self) -> None:
        result = self.verify("matching.kicad_pcb")
        self.assertTrue(result.parity.passed)
        self.assertEqual(result.parity.oracle_live, "passed")
        self.assertEqual(result.parity.connectivity_finding_count, 0)
        self.assertEqual(result.parity.kicad_version, "10.0.5")
        # The projection artifacts are expected and are not a parity failure.
        self.assertEqual(result.parity.parity_type_counts["footprint_symbol_mismatch"], 2)

    def test_net_mismatch_is_detected(self) -> None:
        """The control. A board wired to the wrong net must not report as matching.

        Without this, every other test in this class would pass against an oracle that never
        ran: an empty parity array is what a clean board and a dead check both produce.
        """

        result = self.verify("net-mismatch.kicad_pcb")
        self.assertFalse(result.parity.passed)
        self.assertEqual(result.parity.oracle_live, "passed")
        self.assertEqual(result.parity.parity_type_counts["net_conflict"], 1)
        self.assertEqual(result.to_dict()["verification"]["schematic_board_parity"], "failed")

    def test_extra_footprint_is_detected(self) -> None:
        result = self.verify("extra-footprint.kicad_pcb")
        self.assertFalse(result.parity.passed)
        self.assertEqual(result.parity.parity_type_counts["extra_footprint"], 1)

    def test_missing_footprint_is_detected(self) -> None:
        result = self.verify("missing-footprint.kicad_pcb")
        self.assertFalse(result.parity.passed)
        self.assertEqual(result.parity.parity_type_counts["missing_footprint"], 1)

    def test_board_excluded_schematic_is_refused_not_passed(self) -> None:
        """Handing KiCad the *delivered* schematic must refuse, never report a clean pass.

        This is the failure ADR-0084 exists to prevent: an ``on_board no`` symbol never enters
        the board-side netlist, so a correct board and a wrong one both come back empty.
        """

        delivered = render_kicad_schematic(self.snapshot)
        projection = render_kicad_schematic(self.snapshot, board_eligible=True)
        shutil.copy(BOARDS / "matching.kicad_pcb", self.workspace / "board.kicad_pcb")
        settings = Settings(workspace=self.workspace, kicad_cli=REAL_KICAD_CLI)
        with self.assertRaises(KiCadCliError) as caught:
            run_source_to_board_parity(
                "board.kicad_pcb",
                delivered.content,
                component_count=delivered.component_count,
                intent_digest=delivered.intent_digest,
                # The delivered artifact stands in as the projection here, which is exactly the
                # mistake being guarded; the digests must still be distinct to reach the CLI.
                schematic_digest=projection.artifact_digest,
                parity_schematic_digest=delivered.artifact_digest,
                settings=settings,
            )
        self.assertIn("did not run", str(caught.exception))

    def test_board_revision_binds_the_verdict(self) -> None:
        import hashlib

        board_bytes = (BOARDS / "matching.kicad_pcb").read_bytes()
        result = self.verify("matching.kicad_pcb")
        self.assertEqual(
            result.parity.board_revision,
            f"sha256:{hashlib.sha256(board_bytes).hexdigest()}",
        )

    def test_verdict_binds_both_schematic_digests(self) -> None:
        result = self.verify("matching.kicad_pcb")
        delivered = render_kicad_schematic(self.snapshot)
        projection = render_kicad_schematic(self.snapshot, board_eligible=True)
        self.assertEqual(result.parity.schematic_digest, delivered.artifact_digest)
        self.assertEqual(result.parity.parity_schematic_digest, projection.artifact_digest)
        self.assertNotEqual(delivered.artifact_digest, projection.artifact_digest)

    def test_board_input_is_not_modified(self) -> None:
        original = (BOARDS / "matching.kicad_pcb").read_bytes()
        self.verify("matching.kicad_pcb")
        self.assertEqual((self.workspace / "board.kicad_pcb").read_bytes(), original)

    def test_structured_content_entry_point_agrees(self) -> None:
        shutil.copy(BOARDS / "matching.kicad_pcb", self.workspace / "board.kicad_pcb")
        settings = Settings(workspace=self.workspace, kicad_cli=REAL_KICAD_CLI)
        content = deepcopy(json.loads(INTENT.read_bytes())["content"])
        result = verify_source_to_board_parity_from_content(content, "board.kicad_pcb", settings)
        self.assertTrue(result.parity.passed)
        SourceToBoardParityToolResponse.model_validate(result.to_dict())


if __name__ == "__main__":
    unittest.main()
