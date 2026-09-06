"""Identity-neutral parity observations do not change the legacy intent contract."""

import copy
import json

import pytest
from test_source_to_board_parity import finding, parity_report, parse

from copper_mcp import kicad_cli


def observe(document, **kwargs):
    return kicad_cli._parse_parity_observation(
        json.dumps(document).encode(),
        return_code=0,
        expected_source=document["source"],
        required_enabled_checks=kicad_cli._PARITY_TYPES,
        **kwargs,
    )


def test_project_observation_accepts_an_empty_parity_array_without_invented_intent():
    document = parity_report(source="ordinary.kicad_pcb", schematic_parity=[])
    observation = observe(document)
    assert dict(observation.parity_type_counts) == {}
    assert observation.normalized_report_digest.startswith("sha256:")
    assert not hasattr(observation, "intent_digest")
    assert not hasattr(observation, "passed")  # Liveness and full-project verdict belong upstream.


def test_required_checks_cannot_be_ignored_and_legacy_interpretation_is_unchanged():
    document = parity_report()
    document["ignored_checks"] = [{"key": "net_conflict", "description": "disabled"}]
    with pytest.raises(kicad_cli.KiCadCliError, match="required check was ignored"):
        observe(document)
    assert parse(document).passed


def test_project_companion_collections_must_be_well_formed_without_narrowing_legacy():
    document = parity_report()
    document["violations"] = ["invalid"]
    with pytest.raises(kicad_cli.KiCadCliError, match="companion finding"):
        observe(document)
    assert parse(document).passed


def test_full_finding_digest_is_order_date_invariant_but_detail_sensitive():
    document = parity_report(schematic_parity=[finding("net_conflict"), finding("extra_footprint")])
    first = observe(document)
    reordered = copy.deepcopy(document)
    reordered["date"] = "2026-09-07T01:00:00"
    reordered["schematic_parity"].reverse()
    assert observe(reordered).normalized_report_digest == first.normalized_report_digest
    reordered["schematic_parity"][0]["description"] = "a different finding"
    assert observe(reordered).normalized_report_digest != first.normalized_report_digest
    with pytest.raises(TypeError):
        first.parity_type_counts["net_conflict"] = 99


@pytest.mark.parametrize("value", (True, "bad", float("inf"), float("nan")))
def test_bad_deadlines_refuse(value):
    with pytest.raises(kicad_cli.KiCadCliError, match="deadline is malformed"):
        observe(parity_report(), deadline=value)


def test_expiry_during_canonicalization_cannot_return_an_observation(monkeypatch):
    clock = [0.0]
    dumps = kicad_cli.json.dumps
    payload = json.dumps(parity_report()).encode()

    def expiring(value, *args, **kwargs):
        result = dumps(value, *args, **kwargs)
        if isinstance(value, dict) and "schematic_parity" in value:
            clock[0] = 2.0
        return result

    monkeypatch.setattr(kicad_cli.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(kicad_cli.json, "dumps", expiring)
    with pytest.raises(kicad_cli.KiCadCliError, match="deadline expired"):
        kicad_cli._parse_parity_observation(
            payload,
            return_code=0,
            expected_source="parity.kicad_pcb",
            required_enabled_checks=kicad_cli._PARITY_TYPES,
            deadline=1.0,
        )
