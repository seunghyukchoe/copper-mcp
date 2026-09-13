"""Hybrid attempt evidence is complete without changing old v2 document identities."""

import json

import pytest
from pydantic import TypeAdapter, ValidationError

from copper_mcp.optimization.contracts import digest_document, routing_backend_order
from copper_mcp.optimization.package import ComparisonOutcome, RoutingAttemptV2, SlotWork

DIGEST = "sha256:" + "a" * 64


def failed_attempt():
    work = SlotWork(
        expansions=10,
        route_attempts=2,
        repair_rounds=1,
        routing_checks=12,
        external_output_bytes=20,
    )
    return ComparisonOutcome(
        placement_candidate_id=DIGEST,
        role="identity",
        status="backend_failure",
        candidate_id=None,
        metrics=None,
        route_probes=None,
        charged=work,
        routing_attempts=(
            RoutingAttemptV2(
                backend="internal-layered-v1",
                outcome="backend_failure",
                composition_digest=None,
                external_run_digest=None,
                charged=work,
            ),
        ),
    )


def test_legacy_outcome_remains_identical_inside_nested_documents():
    old = failed_attempt().model_copy(update={"routing_attempts": ()})
    document = old.model_dump(mode="json")
    assert "routing_attempts" not in document
    assert old.digest == digest_document(old.identity_namespace, document)
    nested = TypeAdapter(list[ComparisonOutcome])
    decoded = nested.validate_json(json.dumps([document]))
    assert nested.dump_python(decoded, mode="json") == [document]
    assert decoded[0].digest == old.digest


@pytest.mark.parametrize(
    "field",
    ["expansions", "route_attempts", "repair_rounds", "routing_checks", "external_output_bytes"],
)
def test_failed_routing_cost_cannot_disappear_into_unattributed_slot_work(field):
    document = failed_attempt().model_dump(mode="json")
    document["routing_attempts"][0]["charged"][field] = 0
    with pytest.raises(ValidationError, match="work was not charged"):
        ComparisonOutcome.model_validate_json(json.dumps(document))


def test_later_native_validation_work_remains_distinct_from_routing_work():
    document = failed_attempt().model_dump(mode="json")
    document["charged"]["validation_checks"] = 50
    assert (
        ComparisonOutcome.model_validate_json(json.dumps(document)).charged.validation_checks == 50
    )


def test_routing_policy_never_adds_an_unrequested_backend():
    assert routing_backend_order(("simpleroutejson-v1",)) == ("simpleroutejson-v1",)
    assert routing_backend_order(("internal-layered-v1", "simpleroutejson-v1")) == (
        "simpleroutejson-v1",
        "internal-layered-v1",
    )
    assert routing_backend_order(
        ("freerouting-dsn-ses-v1", "internal-layered-v1", "simpleroutejson-v1")
    ) == ("freerouting-dsn-ses-v1", "simpleroutejson-v1", "internal-layered-v1")
