"""Tests for authoritative KiCad schematic ERC and round-trip verification.

Two kinds of test live here. The report-parsing and binding tests are hermetic: they feed the
parser exact bytes and assert the typed refusal, so every rejection path is exercised without a
KiCad install. The real-CLI tests skip when KiCad is absent, and are the only place a claim about
what KiCad *actually says* is allowed to come from.
"""

from __future__ import annotations

import json
import unittest
from copy import deepcopy
from pathlib import Path
from typing import Any
from unittest.mock import patch

from copper_mcp.adapters.kicad_schematic import render_kicad_schematic
from copper_mcp.adapters.kicad_schematic_parity import KiCadSchematicParityEvidence
from copper_mcp.circuit_ir import decode_snapshot_json
from copper_mcp.config import Settings
from copper_mcp.kicad_cli import (
    KICAD_ERC_SCHEMA,
    SCHEMATIC_SNAPSHOT_NAME,
    KiCadCliError,
    _parse_erc_report,
    export_circuit_schematic_netlist,
    run_circuit_schematic_erc,
)
from copper_mcp.mcp_contracts import CircuitSchematicErcToolResponse
from copper_mcp.models import ErcSummary
from copper_mcp.schematic_erc_service import (
    CircuitSchematicErcResult,
    verify_schematic_erc_from_content,
    verify_schematic_erc_from_snapshot_json,
)

ROOT = Path(__file__).resolve().parents[1]
INTENT = ROOT / "benchmarks" / "audio" / "fixtures" / "rc-low-pass-intent-v1.json"
REAL_KICAD_CLI = Path("/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli")

INTENT_DIGEST = "sha256:" + "1" * 64
SCHEMATIC_DIGEST = "sha256:" + "2" * 64


def violation(
    violation_type: str,
    severity: str,
    *,
    description: str = "finding",
    excluded: bool | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "type": violation_type,
        "description": description,
        "severity": severity,
        "items": [],
    }
    if excluded is not None:
        payload["excluded"] = excluded
    return payload


def erc_report(
    *,
    schema: str = KICAD_ERC_SCHEMA,
    source: str = SCHEMATIC_SNAPSHOT_NAME,
    sheets: list[dict[str, Any]] | None = None,
    ignored_checks: list[dict[str, Any]] | None = None,
    included_severities: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "$schema": schema,
        "source": source,
        "date": "2026-08-06T06:48:29",
        "coordinate_units": "mm",
        "kicad_version": "10.0.5",
        "included_severities": (
            ["error", "warning", "exclusion"]
            if included_severities is None
            else included_severities
        ),
        "ignored_checks": [] if ignored_checks is None else ignored_checks,
        "sheets": (
            [{"path": "/", "uuid_path": "/abcd", "violations": []}] if sheets is None else sheets
        ),
    }


def parse(
    report: dict[str, Any],
    *,
    return_code: int = 0,
    source: str = SCHEMATIC_SNAPSHOT_NAME,
) -> ErcSummary:
    return _parse_erc_report(
        json.dumps(report).encode("utf-8"),
        return_code=return_code,
        intent_digest=INTENT_DIGEST,
        schematic_digest=SCHEMATIC_DIGEST,
        expected_source=source,
    )


class ErcReportParsingTests(unittest.TestCase):
    """Only the reviewed KiCad ERC report shape may become evidence."""

    def test_clean_report_becomes_a_passing_and_clean_summary(self) -> None:
        summary = parse(erc_report())

        self.assertTrue(summary.passed)
        self.assertTrue(summary.clean)
        self.assertEqual(summary.error_count, 0)
        self.assertEqual(summary.sheet_count, 1)
        self.assertEqual(summary.erc_schema, KICAD_ERC_SCHEMA)
        self.assertEqual(summary.intent_digest, INTENT_DIGEST)
        self.assertEqual(summary.schematic_digest, SCHEMATIC_DIGEST)

    def test_warning_only_report_passes_the_hard_gate_but_is_never_clean(self) -> None:
        """The whole point of the passed/clean split: warnings must not read as clean."""

        summary = parse(
            erc_report(
                sheets=[
                    {
                        "path": "/",
                        "uuid_path": "/abcd",
                        "violations": [violation("isolated_pin_label", "warning")],
                    }
                ]
            ),
            return_code=5,
        )

        self.assertTrue(summary.passed)
        self.assertFalse(summary.clean)
        self.assertEqual(summary.warning_count, 1)
        self.assertEqual(dict(summary.violation_type_counts), {"isolated_pin_label": 1})

    def test_an_error_severity_violation_fails_the_hard_gate(self) -> None:
        summary = parse(
            erc_report(
                sheets=[
                    {
                        "path": "/",
                        "uuid_path": "/abcd",
                        "violations": [violation("pin_not_connected", "error")],
                    }
                ]
            ),
            return_code=5,
        )

        self.assertFalse(summary.passed)
        self.assertFalse(summary.clean)
        self.assertEqual(summary.error_count, 1)

    def test_ignored_checks_alone_prevent_a_clean_claim(self) -> None:
        summary = parse(
            erc_report(ignored_checks=[{"key": "four_way_junction", "description": "ignored"}])
        )

        self.assertTrue(summary.passed)
        self.assertFalse(summary.clean)
        self.assertEqual(summary.ignored_check_count, 1)

    def test_excluded_violations_count_as_exclusions_not_severities(self) -> None:
        summary = parse(
            erc_report(
                sheets=[
                    {
                        "path": "/",
                        "uuid_path": "/abcd",
                        "violations": [violation("pin_not_driven", "error", excluded=True)],
                    }
                ]
            ),
            return_code=5,
        )

        self.assertEqual(summary.error_count, 0)
        self.assertEqual(summary.exclusion_count, 1)
        self.assertTrue(summary.passed)
        self.assertFalse(summary.clean)

    def test_violations_are_summed_across_every_sheet(self) -> None:
        summary = parse(
            erc_report(
                sheets=[
                    {
                        "path": "/",
                        "uuid_path": "/a",
                        "violations": [violation("pin_not_driven", "error")],
                    },
                    {
                        "path": "/sub/",
                        "uuid_path": "/b",
                        "violations": [violation("pin_not_driven", "warning")],
                    },
                ]
            ),
            return_code=5,
        )

        self.assertEqual(summary.sheet_count, 2)
        self.assertEqual(summary.error_count, 1)
        self.assertEqual(summary.warning_count, 1)
        self.assertEqual(dict(summary.violation_type_counts), {"pin_not_driven": 2})

    def test_report_is_refused_when_the_exit_code_contradicts_the_findings(self) -> None:
        """A report and an exit code that disagree cannot both be true; refuse rather than pick."""

        with self.assertRaises(KiCadCliError) as clean_report_nonzero_exit:
            parse(erc_report(), return_code=5)
        self.assertIn("exit code", str(clean_report_nonzero_exit.exception))

        with self.assertRaises(KiCadCliError):
            parse(
                erc_report(
                    sheets=[
                        {
                            "path": "/",
                            "uuid_path": "/abcd",
                            "violations": [violation("pin_not_connected", "error")],
                        }
                    ]
                ),
                return_code=0,
            )

    def test_unsupported_schema_fails_closed(self) -> None:
        with self.assertRaises(KiCadCliError) as error:
            parse(erc_report(schema="https://schemas.kicad.org/erc.v2.json"))
        self.assertIn("schema is unsupported", str(error.exception))

    def test_a_drc_report_is_not_accepted_as_erc_evidence(self) -> None:
        with self.assertRaises(KiCadCliError):
            parse(erc_report(schema="https://schemas.kicad.org/drc.v1.json"))

    def test_report_for_a_different_source_is_refused(self) -> None:
        with self.assertRaises(KiCadCliError) as error:
            parse(erc_report(source="somebody-elses.kicad_sch"))
        self.assertIn("source does not match", str(error.exception))

    def test_missing_requested_severities_are_refused(self) -> None:
        with self.assertRaises(KiCadCliError) as error:
            parse(erc_report(included_severities=["error"]))
        self.assertIn("did not include all requested severities", str(error.exception))

    def test_malformed_documents_are_refused_without_echoing_them(self) -> None:
        cases: list[tuple[str, bytes]] = [
            ("not json", b"{"),
            ("not utf-8", b'{"$schema": "\xff"}'),
            ("not an object", b"[]"),
        ]
        for name, payload in cases:
            with self.subTest(name=name), self.assertRaises(KiCadCliError) as error:
                _parse_erc_report(
                    payload,
                    return_code=0,
                    intent_digest=INTENT_DIGEST,
                    schematic_digest=SCHEMATIC_DIGEST,
                    expected_source=SCHEMATIC_SNAPSHOT_NAME,
                )
            self.assertIn("KiCad ERC report", str(error.exception))

    def test_structurally_invalid_reports_are_refused(self) -> None:
        cases: dict[str, dict[str, Any]] = {
            "no sheets": erc_report(sheets=[]),
            "sheets not a list": {**erc_report(), "sheets": {}},
            "sheet not an object": {**erc_report(), "sheets": ["/"]},
            "duplicate sheet": {
                **erc_report(),
                "sheets": [
                    {"path": "/", "uuid_path": "/a", "violations": []},
                    {"path": "/", "uuid_path": "/b", "violations": []},
                ],
            },
            "missing uuid path": {
                **erc_report(),
                "sheets": [{"path": "/", "violations": []}],
            },
            "violations not a list": {
                **erc_report(),
                "sheets": [{"path": "/", "uuid_path": "/a", "violations": {}}],
            },
            "bad units": {**erc_report(), "coordinate_units": "mils"},
            "bad date": {**erc_report(), "date": "yesterday"},
            "bad version": {**erc_report(), "kicad_version": "not-a-version"},
            "malformed ignored check": {**erc_report(), "ignored_checks": [{"key": 1}]},
        }
        for name, report in cases.items():
            with self.subTest(name=name), self.assertRaises(KiCadCliError):
                parse(report)

    def test_invalid_violations_are_refused(self) -> None:
        cases: dict[str, dict[str, Any]] = {
            "unknown severity": violation("pin_not_connected", "catastrophe"),
            "missing type": {"description": "x", "severity": "error", "items": []},
            "non-string type": {
                "type": 7,
                "description": "x",
                "severity": "error",
                "items": [],
            },
            "items not a list": {
                "type": "pin_not_connected",
                "description": "x",
                "severity": "error",
                "items": "nope",
            },
            "non-boolean exclusion": {
                "type": "pin_not_connected",
                "description": "x",
                "severity": "error",
                "items": [],
                "excluded": "yes",
            },
        }
        for name, bad_violation in cases.items():
            report = erc_report(
                sheets=[{"path": "/", "uuid_path": "/a", "violations": [bad_violation]}]
            )
            with self.subTest(name=name), self.assertRaises(KiCadCliError):
                parse(report, return_code=5)

    def test_duplicate_json_keys_are_refused(self) -> None:
        payload = (
            b'{"$schema": "' + KICAD_ERC_SCHEMA.encode() + b'", "$schema": "other", '
            b'"source": "circuit.kicad_sch"}'
        )
        with self.assertRaises(KiCadCliError):
            _parse_erc_report(
                payload,
                return_code=0,
                intent_digest=INTENT_DIGEST,
                schematic_digest=SCHEMATIC_DIGEST,
                expected_source=SCHEMATIC_SNAPSHOT_NAME,
            )


class ErcSummaryInvariantTests(unittest.TestCase):
    """The summary refuses to represent a verdict it did not receive."""

    def _summary(self, **overrides: Any) -> ErcSummary:
        fields: dict[str, Any] = {
            "intent_digest": INTENT_DIGEST,
            "schematic_digest": SCHEMATIC_DIGEST,
            "kicad_version": "10.0.5",
            "erc_schema": KICAD_ERC_SCHEMA,
            "coordinate_units": "mm",
            "error_count": 0,
            "warning_count": 0,
            "exclusion_count": 0,
            "ignored_check_count": 0,
            "sheet_count": 1,
            "violation_type_counts": {},
            "passed": True,
        }
        fields.update(overrides)
        return ErcSummary(**fields)

    def test_passed_must_reflect_the_absence_of_errors(self) -> None:
        with self.assertRaises(ValueError):
            self._summary(error_count=1, passed=True, violation_type_counts={"x": 1})
        with self.assertRaises(ValueError):
            self._summary(error_count=0, passed=False)

    def test_violation_type_counts_must_equal_the_aggregate_findings(self) -> None:
        with self.assertRaises(ValueError):
            self._summary(warning_count=1, violation_type_counts={})
        with self.assertRaises(ValueError):
            self._summary(warning_count=0, violation_type_counts={"x": 1})

    def test_digests_must_be_content_addressed(self) -> None:
        for field in ("intent_digest", "schematic_digest"):
            with self.subTest(field=field), self.assertRaises(ValueError):
                self._summary(**{field: "not-a-digest"})

    def test_unsupported_schema_and_units_are_refused(self) -> None:
        with self.assertRaises(ValueError):
            self._summary(erc_schema="https://schemas.kicad.org/drc.v1.json")
        with self.assertRaises(ValueError):
            self._summary(coordinate_units="mils")

    def test_a_report_must_cover_at_least_one_sheet(self) -> None:
        with self.assertRaises(ValueError):
            self._summary(sheet_count=0)

    def test_summary_is_immutable_and_serializes_both_signals(self) -> None:
        summary = self._summary(warning_count=1, violation_type_counts={"x": 1})
        payload = summary.to_dict()

        self.assertTrue(payload["passed"])
        self.assertFalse(payload["clean"])
        with self.assertRaises(AttributeError):
            summary.passed = False  # type: ignore[misc]


class SchematicErcDigestBindingTests(unittest.TestCase):
    """Evidence that is not bound to the exact bytes checked is not evidence."""

    def test_run_refuses_a_digest_that_does_not_match_the_bytes(self) -> None:
        with self.assertRaises(KiCadCliError) as error:
            run_circuit_schematic_erc(
                b"(kicad_sch)",
                intent_digest=INTENT_DIGEST,
                schematic_digest=SCHEMATIC_DIGEST,
                settings=Settings(workspace=ROOT),
            )
        self.assertIn("digest does not match", str(error.exception))

    def test_run_refuses_malformed_digests_and_bytes(self) -> None:
        settings = Settings(workspace=ROOT)
        with self.assertRaises(KiCadCliError):
            run_circuit_schematic_erc(
                b"(kicad_sch)",
                intent_digest="nope",
                schematic_digest=SCHEMATIC_DIGEST,
                settings=settings,
            )
        with self.assertRaises(KiCadCliError):
            run_circuit_schematic_erc(
                b"",
                intent_digest=INTENT_DIGEST,
                schematic_digest=SCHEMATIC_DIGEST,
                settings=settings,
            )

    def test_result_refuses_evidence_bound_to_a_different_artifact(self) -> None:
        """This is the guard that makes the whole response meaningful; mutating it must fail."""

        snapshot = decode_snapshot_json(INTENT.read_bytes())
        artifact = render_kicad_schematic(snapshot)
        erc = ErcSummary(
            intent_digest=artifact.intent_digest,
            schematic_digest=artifact.artifact_digest,
            kicad_version="10.0.5",
            erc_schema=KICAD_ERC_SCHEMA,
            coordinate_units="mm",
            error_count=0,
            warning_count=0,
            exclusion_count=0,
            ignored_check_count=0,
            sheet_count=1,
            violation_type_counts={},
            passed=True,
        )
        parity = KiCadSchematicParityEvidence(
            intent_digest=artifact.intent_digest,
            schematic_digest=artifact.artifact_digest,
            netlist_digest=SCHEMATIC_DIGEST,
            netlist_format_version="E",
            component_count=2,
            net_count=3,
            connection_count=4,
            source_replay="passed",
            component_parity="passed",
            connectivity_parity="passed",
        )

        # The honest combination is accepted.
        CircuitSchematicErcResult(artifact=artifact, erc=erc, parity=parity)

        foreign = "sha256:" + "9" * 64

        with self.assertRaises(ValueError):
            CircuitSchematicErcResult(
                artifact=artifact,
                erc=ErcSummary(
                    intent_digest=foreign,
                    schematic_digest=artifact.artifact_digest,
                    kicad_version="10.0.5",
                    erc_schema=KICAD_ERC_SCHEMA,
                    coordinate_units="mm",
                    error_count=0,
                    warning_count=0,
                    exclusion_count=0,
                    ignored_check_count=0,
                    sheet_count=1,
                    violation_type_counts={},
                    passed=True,
                ),
                parity=parity,
            )

        with self.assertRaises(ValueError):
            CircuitSchematicErcResult(
                artifact=artifact,
                erc=erc,
                parity=KiCadSchematicParityEvidence(
                    intent_digest=artifact.intent_digest,
                    schematic_digest=foreign,
                    netlist_digest=SCHEMATIC_DIGEST,
                    netlist_format_version="E",
                    component_count=2,
                    net_count=3,
                    connection_count=4,
                    source_replay="passed",
                    component_parity="passed",
                    connectivity_parity="passed",
                ),
            )

    def test_result_refuses_malformed_members(self) -> None:
        with self.assertRaises(ValueError):
            CircuitSchematicErcResult(artifact=object(), erc=object(), parity=object())  # type: ignore[arg-type]


class SchematicErcRefusalTests(unittest.TestCase):
    """Missing or unusable KiCad must produce a typed refusal, never a fabricated verdict."""

    def test_missing_kicad_cli_is_a_typed_refusal(self) -> None:
        snapshot = decode_snapshot_json(INTENT.read_bytes())
        artifact = render_kicad_schematic(snapshot)
        with patch(
            "copper_mcp.kicad_cli.discover_kicad_cli",
            side_effect=KiCadCliError("KiCad CLI is not available"),
        ):
            with self.assertRaises(KiCadCliError):
                run_circuit_schematic_erc(
                    artifact.content,
                    intent_digest=artifact.intent_digest,
                    schematic_digest=artifact.artifact_digest,
                    settings=Settings(workspace=ROOT),
                )

    def test_unsupported_platform_is_a_typed_refusal(self) -> None:
        snapshot = decode_snapshot_json(INTENT.read_bytes())
        artifact = render_kicad_schematic(snapshot)
        with (
            patch(
                "copper_mcp.kicad_cli.discover_kicad_cli",
                return_value=Path("/trusted/kicad-cli"),
            ),
            patch("copper_mcp.kicad_cli.os.name", "nt"),
        ):
            with self.assertRaises(KiCadCliError) as error:
                run_circuit_schematic_erc(
                    artifact.content,
                    intent_digest=artifact.intent_digest,
                    schematic_digest=artifact.artifact_digest,
                    settings=Settings(workspace=ROOT),
                )
            self.assertIn("unsupported on this platform", str(error.exception))

    def test_oversized_schematic_is_refused_before_any_subprocess(self) -> None:
        with patch("copper_mcp.kicad_cli.discover_kicad_cli") as discovery:
            with self.assertRaises(KiCadCliError) as error:
                export_circuit_schematic_netlist(
                    b"x" * 2_000_000,
                    settings=Settings(workspace=ROOT),
                )
            self.assertIn("byte ceiling", str(error.exception))
        discovery.assert_not_called()


@unittest.skipUnless(REAL_KICAD_CLI.is_file(), "KiCad CLI is not installed")
class RealKiCadSchematicErcTests(unittest.TestCase):
    """The only tests permitted to claim what KiCad actually reports."""

    def setUp(self) -> None:
        self.settings = Settings(workspace=ROOT, kicad_cli=REAL_KICAD_CLI)

    def test_generated_fixture_has_no_erc_errors_but_is_not_clean(self) -> None:
        """The reviewed truth for this fixture: warnings exist and must stay visible."""

        result = verify_schematic_erc_from_snapshot_json(INTENT.read_bytes(), self.settings)

        self.assertTrue(result.erc.passed)
        self.assertFalse(result.erc.clean)
        self.assertEqual(result.erc.error_count, 0)
        self.assertGreater(result.erc.warning_count, 0)
        self.assertEqual(result.erc.erc_schema, KICAD_ERC_SCHEMA)
        self.assertEqual(result.erc.sheet_count, 1)

    def test_evidence_is_bound_to_the_exact_rendered_schematic(self) -> None:
        snapshot = decode_snapshot_json(INTENT.read_bytes())
        artifact = render_kicad_schematic(snapshot)

        result = verify_schematic_erc_from_snapshot_json(INTENT.read_bytes(), self.settings)

        self.assertEqual(result.erc.schematic_digest, artifact.artifact_digest)
        self.assertEqual(result.erc.intent_digest, artifact.intent_digest)
        self.assertEqual(result.parity.schematic_digest, artifact.artifact_digest)

    def test_round_trip_recovers_the_source_topology_through_kicad(self) -> None:
        """KiCad re-reads what we wrote and reports the same components and nets."""

        snapshot = decode_snapshot_json(INTENT.read_bytes())
        result = verify_schematic_erc_from_snapshot_json(INTENT.read_bytes(), self.settings)

        self.assertEqual(result.parity.component_count, len(snapshot.content.components))
        self.assertEqual(result.parity.net_count, len(snapshot.content.nets))
        self.assertEqual(result.parity.source_replay, "passed")
        self.assertEqual(result.parity.component_parity, "passed")
        self.assertEqual(result.parity.connectivity_parity, "passed")

    def test_repeated_runs_produce_identical_public_evidence(self) -> None:
        first = verify_schematic_erc_from_snapshot_json(INTENT.read_bytes(), self.settings)
        second = verify_schematic_erc_from_snapshot_json(INTENT.read_bytes(), self.settings)

        self.assertEqual(first.to_dict(), second.to_dict())

    def test_response_declares_its_non_claims(self) -> None:
        result = verify_schematic_erc_from_snapshot_json(INTENT.read_bytes(), self.settings)
        verification = result.to_dict()["verification"]

        self.assertEqual(verification["erc"], "completed")
        self.assertEqual(verification["kicad_cli_parse"], "passed")
        self.assertEqual(verification["schematic_round_trip"], "passed")
        self.assertEqual(verification["schematic_board_parity"], "not_run")
        self.assertEqual(verification["electrical_validation"], "not_run")
        self.assertIs(verification["board_ready"], False)

    def test_response_discloses_no_design_text(self) -> None:
        """Component references, net names, values, and titles must not cross the boundary."""

        snapshot = decode_snapshot_json(INTENT.read_bytes())
        result = verify_schematic_erc_from_snapshot_json(INTENT.read_bytes(), self.settings)
        serialized = json.dumps(result.to_dict())

        markers = {snapshot.content.title, snapshot.content.circuit_id}
        markers.update(component.reference for component in snapshot.content.components)
        markers.update(component.value for component in snapshot.content.components)
        markers.update(net.id for net in snapshot.content.nets)
        for marker in markers:
            if not marker:
                continue
            with self.subTest(marker=marker):
                self.assertNotIn(marker, serialized)

    def test_structured_response_satisfies_the_advertised_mcp_contract(self) -> None:
        result = verify_schematic_erc_from_content(
            json.loads(INTENT.read_bytes())["content"], self.settings
        )

        response = CircuitSchematicErcToolResponse.model_validate(result.to_dict())

        self.assertEqual(response.status, "checked")
        self.assertEqual(response.erc.authority, "kicad-cli-sch-erc")
        self.assertEqual(response.round_trip.authority, "kicad-cli-sch-export-netlist")
        self.assertEqual(response.verification.schematic_board_parity, "not_run")

    def test_erc_does_not_mutate_the_schematic_it_checks(self) -> None:
        snapshot = decode_snapshot_json(INTENT.read_bytes())
        artifact = render_kicad_schematic(snapshot)
        original = deepcopy(artifact.content)

        run_circuit_schematic_erc(
            artifact.content,
            intent_digest=artifact.intent_digest,
            schematic_digest=artifact.artifact_digest,
            settings=self.settings,
        )

        self.assertEqual(artifact.content, original)

    def test_netlist_export_returns_a_format_e_document(self) -> None:
        snapshot = decode_snapshot_json(INTENT.read_bytes())
        artifact = render_kicad_schematic(snapshot)

        netlist = export_circuit_schematic_netlist(artifact.content, settings=self.settings)

        self.assertIn(b'<export version="E">', netlist)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
