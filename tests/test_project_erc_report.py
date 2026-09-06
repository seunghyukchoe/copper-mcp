"""Hermetic tests for identity-neutral KiCad project ERC report observations."""

from __future__ import annotations

import json
import unittest
from typing import Any

from copper_mcp.kicad_cli import (
    KICAD_ERC_SCHEMA,
    KiCadCliError,
    _parse_erc_observation,
    _parse_erc_report,
)

_ROOT_UUID = "/123e4567-e89b-12d3-a456-426614174000"
_CHILD_UUID = "/123e4567-e89b-12d3-a456-426614174001"
_EXTRA_UUID = "/123e4567-e89b-12d3-a456-426614174002"
_INTENT_DIGEST = "sha256:" + "1" * 64
_SCHEMATIC_DIGEST = "sha256:" + "2" * 64


def _violation(
    violation_type: str = "pin_not_connected", *, description: str = "finding"
) -> dict[str, Any]:
    return {
        "type": violation_type,
        "description": description,
        "severity": "warning",
        "items": [],
    }


def _report(*, sheets: list[dict[str, Any]], date: str = "2026-08-06T06:48:29") -> dict[str, Any]:
    return {
        "$schema": KICAD_ERC_SCHEMA,
        "source": "project.kicad_sch",
        "date": date,
        "coordinate_units": "mm",
        "kicad_version": "10.0.5",
        "included_severities": ["error", "warning", "exclusion"],
        "ignored_checks": [],
        "sheets": sheets,
    }


def _observe(
    report: dict[str, Any], *, expected: frozenset[str] | None = None, return_code: int = 0
) -> Any:
    return _parse_erc_observation(
        json.dumps(report).encode("utf-8"),
        return_code=return_code,
        expected_source="project.kicad_sch",
        expected_uuid_paths=expected,
    )


class ProjectErcReportTests(unittest.TestCase):
    def test_severity_map_is_validated_even_without_findings(self):
        report = _report(sheets=[{"path": "/", "uuid_path": _ROOT_UUID, "violations": []}])
        cases = ({}, [], {"rule": False}, {"rule": "off"}, {"": "error"}, {"r" * 129: "error"})
        for constraints in cases:
            with self.subTest(constraints=constraints), self.assertRaises(KiCadCliError):
                _parse_erc_observation(
                    json.dumps(report).encode(),
                    return_code=0,
                    expected_source="project.kicad_sch",
                    minimum_severities=constraints,
                )

    def test_explicit_disabled_category_is_allowed_but_cannot_emit_findings(self):
        report = _report(sheets=[{"path": "/", "uuid_path": _ROOT_UUID, "violations": []}])
        report["ignored_checks"] = [{"key": "outside_profile", "description": "not checked"}]
        observation = _parse_erc_observation(
            json.dumps(report).encode(),
            return_code=0,
            expected_source="project.kicad_sch",
            minimum_severities={"outside_profile": "ignore"},
        )
        self.assertEqual(observation.ignored_check_keys, ("outside_profile",))
        report["sheets"][0]["violations"] = [_violation("outside_profile")]
        with self.assertRaisesRegex(KiCadCliError, "severity floor"):
            _parse_erc_observation(
                json.dumps(report).encode(),
                return_code=5,
                expected_source="project.kicad_sch",
                minimum_severities={"outside_profile": "ignore"},
            )

    def test_active_severity_floor_cannot_be_ignored_or_excluded(self):
        for mode in ("ignored", "excluded"):
            with self.subTest(mode=mode):
                finding = {**_violation(), "severity": "error", "excluded": True}
                report = _report(
                    sheets=[
                        {
                            "path": "/",
                            "uuid_path": _ROOT_UUID,
                            "violations": [finding] if mode == "excluded" else [],
                        }
                    ]
                )
                if mode == "ignored":
                    report["ignored_checks"] = [
                        {"key": "pin_not_connected", "description": "ignored"}
                    ]
                with self.assertRaisesRegex(KiCadCliError, "severity floor"):
                    _parse_erc_observation(
                        json.dumps(report).encode(),
                        expected_source="project.kicad_sch",
                        return_code=5 if mode == "excluded" else 0,
                        minimum_severities={"pin_not_connected": "error"},
                    )

    def test_project_severity_floor_refuses_downgrade_without_changing_legacy_interpretation(self):
        report = _report(
            sheets=[{"path": "/", "uuid_path": _ROOT_UUID, "violations": [_violation()]}]
        )
        payload = json.dumps(report).encode()
        self.assertEqual(_observe(report, return_code=5).warning_count, 1)
        with self.assertRaisesRegex(KiCadCliError, "severity floor"):
            _parse_erc_observation(
                payload,
                return_code=5,
                expected_source="project.kicad_sch",
                expected_uuid_paths=frozenset({_ROOT_UUID}),
                minimum_severities={"pin_not_connected": "error"},
            )
        accepted = _parse_erc_observation(
            payload,
            return_code=5,
            expected_source="project.kicad_sch",
            minimum_severities={"pin_not_connected": "warning"},
        )
        self.assertEqual(accepted.warning_count, 1)

    def test_native_optional_trailing_slash_cannot_duplicate_a_sheet_identity(self) -> None:
        one = _report(sheets=[{"path": "/", "uuid_path": _ROOT_UUID + "/", "violations": []}])
        self.assertEqual(_observe(one, expected=frozenset({_ROOT_UUID})).sheet_count, 1)
        duplicate = _report(
            sheets=[
                {"path": "/a/", "uuid_path": _ROOT_UUID, "violations": []},
                {"path": "/b/", "uuid_path": _ROOT_UUID + "/", "violations": []},
            ]
        )
        with self.assertRaisesRegex(KiCadCliError, "duplicate sheet UUID"):
            _observe(duplicate, expected=frozenset({_ROOT_UUID}))
        with self.assertRaisesRegex(KiCadCliError, "ambiguous"):
            _observe(one, expected=frozenset({_ROOT_UUID, _ROOT_UUID + "/"}))

    def test_legacy_mode_preserves_duplicate_display_path_refusal_and_permissive_uuid_paths(
        self,
    ) -> None:
        report = _report(
            sheets=[
                {"path": "/", "uuid_path": "/not-a-uuid", "violations": []},
                {"path": "/", "uuid_path": "/also-not-a-uuid", "violations": []},
            ]
        )
        with self.assertRaisesRegex(KiCadCliError, "duplicate sheet"):
            _parse_erc_report(
                json.dumps(report).encode("utf-8"),
                return_code=0,
                intent_digest=_INTENT_DIGEST,
                schematic_digest=_SCHEMATIC_DIGEST,
                expected_source="project.kicad_sch",
            )

    def test_project_mode_accepts_duplicate_display_paths_with_exact_uuid_coverage(self) -> None:
        observation = _observe(
            _report(
                sheets=[
                    {"path": "/duplicate/", "uuid_path": _ROOT_UUID, "violations": []},
                    {"path": "/duplicate/", "uuid_path": _CHILD_UUID, "violations": []},
                ]
            ),
            expected=frozenset({_ROOT_UUID, _CHILD_UUID}),
        )
        self.assertEqual(observation.sheet_count, 2)

    def test_project_mode_refuses_missing_extra_duplicate_and_stale_uuid_paths(self) -> None:
        cases = {
            "missing": (
                [{"path": "/", "uuid_path": _ROOT_UUID, "violations": []}],
                frozenset({_ROOT_UUID, _CHILD_UUID}),
            ),
            "extra": (
                [
                    {"path": "/", "uuid_path": _ROOT_UUID, "violations": []},
                    {"path": "/child/", "uuid_path": _CHILD_UUID, "violations": []},
                    {"path": "/extra/", "uuid_path": _EXTRA_UUID, "violations": []},
                ],
                frozenset({_ROOT_UUID, _CHILD_UUID}),
            ),
            "duplicate": (
                [
                    {"path": "/", "uuid_path": _ROOT_UUID, "violations": []},
                    {"path": "/duplicate/", "uuid_path": _ROOT_UUID, "violations": []},
                ],
                frozenset({_ROOT_UUID, _CHILD_UUID}),
            ),
            "stale": (
                [{"path": "/", "uuid_path": _EXTRA_UUID, "violations": []}],
                frozenset({_ROOT_UUID}),
            ),
        }
        for name, (sheets, expected) in cases.items():
            with self.subTest(name=name), self.assertRaises(KiCadCliError):
                _observe(_report(sheets=sheets), expected=expected)

    def test_project_mode_requires_canonical_uuid_paths_and_preserves_native_trailing_slash(
        self,
    ) -> None:
        trailing = _ROOT_UUID + "/"
        _observe(
            _report(sheets=[{"path": "/", "uuid_path": trailing, "violations": []}]),
            expected=frozenset({trailing}),
        )
        with self.assertRaisesRegex(KiCadCliError, "UUID path is malformed"):
            _observe(
                _report(sheets=[{"path": "/", "uuid_path": _ROOT_UUID.upper(), "violations": []}]),
                expected=frozenset({_ROOT_UUID}),
            )

    def test_normalized_report_digest_ignores_date_and_sheet_violation_order_but_captures_findings(
        self,
    ) -> None:
        first = _report(
            sheets=[
                {
                    "path": "/",
                    "uuid_path": _ROOT_UUID,
                    "violations": [_violation("a"), _violation("b")],
                },
                {"path": "/child/", "uuid_path": _CHILD_UUID, "violations": []},
            ]
        )
        reordered = _report(
            date="2027-01-01T00:00:00Z",
            sheets=[
                {"path": "/child/", "uuid_path": _CHILD_UUID, "violations": []},
                {
                    "path": "/",
                    "uuid_path": _ROOT_UUID,
                    "violations": [_violation("b"), _violation("a")],
                },
            ],
        )
        changed = _report(
            sheets=[
                {
                    "path": "/",
                    "uuid_path": _ROOT_UUID,
                    "violations": [
                        _violation("a", description="changed"),
                        _violation("b"),
                    ],
                },
                {"path": "/child/", "uuid_path": _CHILD_UUID, "violations": []},
            ]
        )
        expected = frozenset({_ROOT_UUID, _CHILD_UUID})
        first_digest = _observe(first, expected=expected, return_code=5).normalized_report_digest
        reordered_digest = _observe(
            reordered, expected=expected, return_code=5
        ).normalized_report_digest
        changed_digest = _observe(
            changed, expected=expected, return_code=5
        ).normalized_report_digest
        self.assertEqual(first_digest, reordered_digest)
        self.assertNotEqual(first_digest, changed_digest)
