"""A frozen denominator cannot turn missing physics or invented provenance into readiness."""

import json

import pytest

from copper_mcp.optimization.readiness import (
    AREAS,
    FROZEN_CATALOG,
    PILLAR_POINTS,
    ReadinessCatalog,
    ReadinessSubmission,
    RequirementReceipt,
    assess_readiness,
    decode_submission,
)

SOURCE = "sha256:" + "a" * 64
ARTIFACT = "sha256:" + "b" * 64


def submission(*, status="pass", omit=()):
    return ReadinessSubmission(
        catalog_digest=FROZEN_CATALOG.digest,
        evaluated_source_digest=SOURCE,
        receipts=tuple(
            RequirementReceipt(
                requirement_id=item.requirement_id,
                catalog_digest=FROZEN_CATALOG.digest,
                evaluated_source_digest=SOURCE,
                status=status,
                origin=item.origin,
                artifact_digest=ARTIFACT if status in ("pass", "fail") else None,
            )
            for item in FROZEN_CATALOG.requirements
            if item.requirement_id not in omit
        ),
    )


def test_all_five_denominators_have_exact_frozen_weights():
    assert len(FROZEN_CATALOG.requirements) == 50
    for area in AREAS:
        for pillar, points in PILLAR_POINTS:
            assert (
                sum(
                    row.points
                    for row in FROZEN_CATALOG.requirements
                    if row.area == area and row.pillar == pillar
                )
                == points
            )
    encoded = FROZEN_CATALOG.model_dump_json().encode()
    assert ReadinessCatalog.model_validate_json(encoded).digest == FROZEN_CATALOG.digest


def test_missing_evidence_is_unassessed_not_audited_zero_implementation():
    result = assess_readiness(
        submission(omit=tuple(row.requirement_id for row in FROZEN_CATALOG.requirements))
    )
    assert result["status"] == "blocked"
    assert result["audited_readiness_score"] is None
    assert all(len(row["unassessed_requirements"]) == 10 for row in result["areas"])


@pytest.mark.parametrize("status", ["inconclusive", "not_run", "fail"])
def test_nonpasses_earn_no_capability_or_other_points(status):
    result = assess_readiness(submission(status=status))
    assert all(row["submitted_points"] == 0 for row in result["areas"])
    assert result["status"] == "blocked"


def test_ninety_points_cannot_offset_a_missing_native_guard():
    missing = "ai_live_autonomy.native_transaction"
    result = assess_readiness(submission(omit=(missing,)))
    live = next(row for row in result["areas"] if row["area"] == "ai_live_autonomy")
    assert live["submitted_points"] == 90
    assert missing in live["critical_blockers"]
    assert result["status"] == "blocked"


def test_all_submitted_passes_still_cannot_authenticate_or_authorize_anything():
    result = assess_readiness(submission())
    assert result["status"] == "eligible_for_independent_artifact_review"
    assert result["audited_readiness_score"] is None
    assert result["artifact_authenticity_verified"] is False
    assert result["release_authorized"] is False
    assert result["apply_authority"] == "none"


@pytest.mark.parametrize(
    "mutation", ["scope", "source", "origin", "duplicate", "unknown", "artifact"]
)
def test_receipt_tampering_and_test_doubles_cannot_meet_a_real_evidence_requirement(mutation):
    raw = submission().document()
    receipt = raw["receipts"][0]
    if mutation == "scope":
        raw["catalog_digest"] = ARTIFACT
    elif mutation == "source":
        receipt["evaluated_source_digest"] = ARTIFACT
    elif mutation == "origin":
        receipt["origin"] = "test_double"
    elif mutation == "duplicate":
        raw["receipts"][1] = receipt
    elif mutation == "unknown":
        receipt["requirement_id"] = "PRIVATE-CANARY"
    else:
        receipt["artifact_digest"] = None
    with pytest.raises(ValueError, match="readiness submission is invalid") as error:
        decode_submission(json.dumps(raw).encode())
    assert "PRIVATE-CANARY" not in str(error.value)


def test_receipts_bind_the_unchanged_canonical_catalog():
    raw = FROZEN_CATALOG.document()
    raw["requirements"] = raw["requirements"][:-1]
    with pytest.raises(ValueError):
        ReadinessCatalog.model_validate_json(json.dumps(raw))


@pytest.mark.parametrize("payload", [b'{"x":1,"x":2}', b'{"x":NaN}', b"[]", b" " * 131073])
def test_closed_bounded_decoder(payload):
    with pytest.raises(ValueError, match="readiness submission is invalid"):
        decode_submission(payload)


def test_catalog_and_electrical_input_profile_ids_are_identical():
    from typing import get_args

    from copper_mcp.engineering.inputs import ProfileId

    assert set(get_args(ProfileId)) == set(FROZEN_CATALOG.profiles)


def test_cli_is_non_mutating_and_explicitly_not_authority(tmp_path, capsys):
    from scripts.check_readiness import main

    assert main([]) == 0
    assert json.loads(capsys.readouterr().out)["digest"] == FROZEN_CATALOG.digest
    path = tmp_path / "submission.json"
    original = submission().model_dump_json().encode()
    path.write_bytes(original)
    assert main([str(path)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["artifact_authenticity_verified"] is False
    assert result["audited_readiness_score"] is None
    assert result["release_authorized"] is False
    assert path.read_bytes() == original
    path.write_bytes(b'{"PRIVATE-CANARY":NaN}')
    assert main([str(path)]) == 2
    assert "PRIVATE-CANARY" not in capsys.readouterr().out
    link = tmp_path / "linked.json"
    link.symlink_to(path)
    assert main([str(link)]) == 2
