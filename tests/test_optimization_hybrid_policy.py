"""Hybrid attempt evidence is complete without changing old v2 document identities."""

import json
from dataclasses import replace

import pytest
from pydantic import TypeAdapter, ValidationError
from test_optimization_search_allocation import FIXTURE, _prepared, _slot

from copper_mcp.optimization.contracts import digest_document, routing_backend_order
from copper_mcp.optimization.evaluation_v2 import _route_with_backends
from copper_mcp.optimization.lifecycle import ResourceUsage
from copper_mcp.optimization.package import ComparisonOutcome, RoutingAttemptV2, SlotWork
from copper_mcp.optimization.placement import PrivatePlacement
from copper_mcp.optimization.worker import OptimizationExecutionError

DIGEST = "sha256:" + "a" * 64


@pytest.mark.parametrize("late_stage", ["checkpoint", "freshness"])
def test_deadline_between_attempts_keeps_the_real_failure_without_inventing_a_backend(
    tmp_path, monkeypatch, late_stage
):
    prepared, settings = _prepared(tmp_path, FIXTURE.read_bytes())
    prepared = replace(
        prepared,
        request=prepared.request.model_copy(
            update={
                "allowed_backends": ("internal-layered-v1", "simpleroutejson-v1"),
            }
        ),
    )
    slot = _slot(prepared)
    checkpoint = slot.checkpoint

    def after_attempt():
        if slot.routing_attempts and late_stage == "checkpoint":
            raise OptimizationExecutionError("budget_exhausted")
        return checkpoint()

    def freshness(*_args, **_kwargs):
        if late_stage == "freshness":
            raise OptimizationExecutionError("budget_exhausted")

    def refused(*_args, **_kwargs):
        slot.reserve(ResourceUsage(route_attempts=1))
        raise OptimizationExecutionError("invalid_candidate")

    monkeypatch.setattr(slot, "checkpoint", after_attempt)
    monkeypatch.setattr("copper_mcp.optimization.external_routing._runtime", lambda *_args: None)
    monkeypatch.setattr("copper_mcp.optimization.external_routing.route_external_targets", refused)
    monkeypatch.setattr("copper_mcp.optimization.evaluation.verify_original_context", freshness)
    monkeypatch.setattr(
        "copper_mcp.optimization.routing.route_targets",
        lambda *_args: pytest.fail("routing continued after exhaustion"),
    )
    placed = PrivatePlacement(prepared.source, prepared.snapshot, DIGEST, 0, 0)
    with pytest.raises(OptimizationExecutionError) as failed:
        _route_with_backends(prepared, placed, settings, slot)
    assert failed.value.code == "budget_exhausted"
    outcome = ComparisonOutcome(
        placement_candidate_id=DIGEST,
        role="identity",
        status=failed.value.code,
        candidate_id=None,
        metrics=None,
        route_probes=None,
        charged=slot.charged_work(),
        routing_attempts=slot.routing_attempts,
    )
    assert len(outcome.routing_attempts) == 1
    assert outcome.routing_attempts[0].outcome == "invalid_candidate"


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


def test_budget_exhaustion_cannot_be_followed_by_another_backend():
    row = failed_attempt()
    document = row.model_dump(mode="json")
    terminal = RoutingAttemptV2(
        backend="simpleroutejson-v1",
        outcome="budget_exhausted",
        composition_digest=None,
        external_run_digest=None,
        charged=SlotWork(),
    )
    document["routing_attempts"].insert(0, terminal.model_dump(mode="json"))
    with pytest.raises(ValidationError, match="routing continued"):
        ComparisonOutcome.model_validate_json(json.dumps(document))


def test_routing_policy_never_adds_an_unrequested_backend():
    assert routing_backend_order(("simpleroutejson-v1",)) == ("simpleroutejson-v1",)
    assert routing_backend_order(("internal-layered-v1", "simpleroutejson-v1")) == (
        "simpleroutejson-v1",
        "internal-layered-v1",
    )
    assert routing_backend_order(
        ("freerouting-dsn-ses-v1", "internal-layered-v1", "simpleroutejson-v1")
    ) == ("freerouting-dsn-ses-v1", "simpleroutejson-v1", "internal-layered-v1")
