"""Frozen requirements and a preliminary evidence score, never release/apply authority.

This evaluator checks submitted receipt bindings, not the authenticity of an execution or a
human review. A score is explicitly preliminary until the referenced artifacts are independently
audited. Missing receipts are unassessed, not claims that the product has no implementation.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, model_validator

from copper_mcp.optimization.contracts import ClosedModel, Digest, OptimizationError, Verdict

Area = Literal[
    "core_mcp_safety", "routing", "placement", "engineering_judgement", "ai_live_autonomy"
]
Pillar = Literal["capability", "real_validation", "integration_recovery", "release_evidence"]
Origin = Literal[
    "integration",
    "real_engine",
    "physical_validation",
    "independent_review",
    "hosted",
    "unit",
    "test_double",
]
AREAS: tuple[Area, ...] = (
    "core_mcp_safety",
    "routing",
    "placement",
    "engineering_judgement",
    "ai_live_autonomy",
)
PILLAR_POINTS: tuple[tuple[Pillar, int], ...] = (
    ("capability", 40),
    ("real_validation", 30),
    ("integration_recovery", 20),
    ("release_evidence", 10),
)


class Requirement(ClosedModel):
    requirement_id: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_.]{1,95}$")]
    area: Area
    pillar: Pillar
    points: Literal[10] = 10
    critical: bool
    origin: Origin
    acceptance: Annotated[str, Field(min_length=1, max_length=512)]


class ReadinessCatalog(ClosedModel):
    identity_namespace = "copper-mcp/readiness-catalog/v1"
    schema_version: Literal["readiness-catalog/v1"] = "readiness-catalog/v1"
    profiles: tuple[Literal["analog-audio-v1", "mcu-sensor-v1", "low-power-supply-v1"], ...]
    copper_layers: tuple[Literal[2, 4, 6, 8], ...]
    exclusions: tuple[str, ...]
    requirements: Annotated[tuple[Requirement, ...], Field(min_length=50, max_length=50)]

    @model_validator(mode="after")
    def frozen_denominators(self) -> ReadinessCatalog:
        if len({item.requirement_id for item in self.requirements}) != 50:
            raise ValueError("readiness requirements must be unique")
        for area in AREAS:
            for pillar, points in PILLAR_POINTS:
                if (
                    sum(
                        item.points
                        for item in self.requirements
                        if item.area == area and item.pillar == pillar
                    )
                    != points
                ):
                    raise ValueError("readiness area denominator changed")
        return self


def _requirements(
    area: Area, rows: tuple[tuple[str, bool, Origin, str], ...]
) -> tuple[Requirement, ...]:
    pillars: tuple[Pillar, ...] = (
        ("capability",) * 4
        + ("real_validation",) * 3
        + ("integration_recovery",) * 2
        + ("release_evidence",)
    )
    return tuple(
        Requirement(
            requirement_id=f"{area}.{name}",
            area=area,
            pillar=pillar,
            critical=critical,
            origin=origin,
            acceptance=acceptance,
        )
        for pillar, (name, critical, origin, acceptance) in zip(pillars, rows, strict=True)
    )


FROZEN_CATALOG = ReadinessCatalog(
    profiles=("analog-audio-v1", "mcu-sensor-v1", "low-power-supply-v1"),
    copper_layers=(2, 4, 6, 8),
    exclusions=("rf", "ddr_pcie", "mains", "safety_critical_signoff"),
    requirements=(
        *_requirements(
            "core_mcp_safety",
            (
                (
                    "ownership",
                    True,
                    "integration",
                    "All operations enforce server-owned identity, scoped inputs and revision "
                    "checks.",
                ),
                (
                    "immutable_candidates",
                    True,
                    "integration",
                    "Candidate and composition identity are rederived from immutable captured "
                    "inputs.",
                ),
                (
                    "authority_separation",
                    True,
                    "integration",
                    "AI cannot mint evidence, disclosure, human approval or apply authority.",
                ),
                (
                    "bounded_execution",
                    True,
                    "integration",
                    "Inputs, workers, subprocesses, iterations and output have enforced "
                    "cumulative bounds.",
                ),
                (
                    "adversarial_review",
                    True,
                    "independent_review",
                    "Independent hostile-input review confirms no unauthorized write or "
                    "disclosure.",
                ),
                (
                    "real_host_consent",
                    True,
                    "real_engine",
                    "The real configured host handles accept, decline, stale and unavailable "
                    "consent correctly.",
                ),
                (
                    "python_matrix",
                    True,
                    "hosted",
                    "Product and contract checks pass on Python 3.11, 3.12 and 3.13.",
                ),
                (
                    "recovery",
                    True,
                    "integration",
                    "Crash, lease, cancellation, expiry and duplicate-request tests preserve "
                    "fencing and privacy.",
                ),
                (
                    "feedback_speed",
                    False,
                    "hosted",
                    "Comparable observations show a PR path below 20 minutes and local fast "
                    "feedback at least 3x serial.",
                ),
                (
                    "release_gate",
                    True,
                    "independent_review",
                    "Exact-source make check, artifacts, hosted calibration, documentation and "
                    "ledgers are audited.",
                ),
            ),
        ),
        *_requirements(
            "routing",
            (
                (
                    "source_fidelity",
                    True,
                    "integration",
                    "Production conversion preserves or explicitly refuses every declared KiCad "
                    "construct.",
                ),
                (
                    "layered_multipin",
                    True,
                    "real_engine",
                    "Native routes form complete multi-pin trees on declared 2/4/6/8-layer cases.",
                ),
                (
                    "fresh_fill",
                    True,
                    "real_engine",
                    "Composed candidate zones use fresh, candidate-bound authoritative fill.",
                ),
                (
                    "hybrid_repair",
                    True,
                    "real_engine",
                    "Both external routes normalize through the disposer and bounded repair "
                    "uses one global budget.",
                ),
                (
                    "held_out_completion",
                    True,
                    "real_engine",
                    "At least 90 percent of frozen eligible held-out target nets route; runtime "
                    "failures remain in the denominator.",
                ),
                (
                    "composed_drc",
                    True,
                    "real_engine",
                    "Accepted complete compositions have zero hard KiCad DRC errors and no "
                    "post-apply regression.",
                ),
                (
                    "deterministic_replay",
                    True,
                    "independent_review",
                    "Pinned sources, profiles and recorded policy decisions reproduce candidate "
                    "and judge identities.",
                ),
                (
                    "portfolio_accounting",
                    True,
                    "integration",
                    "Backend selection, failed attempts and repairs cannot reset budgets or "
                    "hide an unavailable required engine.",
                ),
                (
                    "cancellation",
                    True,
                    "integration",
                    "Cancelled or stale work cannot publish any partial successful route package.",
                ),
                (
                    "coverage_disclosure",
                    True,
                    "independent_review",
                    "Held-out licenses, all four layer counts, families, exclusions and source "
                    "hashes are audited.",
                ),
            ),
        ),
        *_requirements(
            "placement",
            (
                (
                    "scope_legality",
                    True,
                    "integration",
                    "Only explicitly movable footprints move; locks, side, cardinal rotation, "
                    "grid and legalizer rules hold.",
                ),
                (
                    "staged_search",
                    False,
                    "integration",
                    "Heuristic screening precedes bounded real-routing evaluation and full "
                    "candidate validation.",
                ),
                (
                    "measured_ranking",
                    True,
                    "real_engine",
                    "Final ranking uses measured routing evidence and never substitutes "
                    "Manhattan distance or constant headroom.",
                ),
                (
                    "electrical_constraints",
                    False,
                    "real_engine",
                    "Declared, validated electrical constraints influence placement without "
                    "inventing missing input data.",
                ),
                (
                    "no_hard_regression",
                    True,
                    "real_engine",
                    "Held-out placement causes no hard legality or DRC regression and preserves "
                    "completion.",
                ),
                (
                    "objective_improvement",
                    True,
                    "real_engine",
                    "At least three held-out boards strictly improve the frozen routing "
                    "objective against identity placement at equal budgets.",
                ),
                (
                    "held_out_profiles",
                    True,
                    "independent_review",
                    "Placement evidence covers the declared families/layers without tuning on "
                    "held-out cases.",
                ),
                (
                    "composition_binding",
                    True,
                    "integration",
                    "Routing and judge evidence bind to the resulting placement, not the "
                    "original scene.",
                ),
                (
                    "recovery",
                    True,
                    "integration",
                    "Placement cancellation and repair preserve source immutability and "
                    "cumulative budgets.",
                ),
                (
                    "claim_review",
                    True,
                    "independent_review",
                    "Published metrics, unavailable measurements and implementation limits "
                    "agree with reviewed artifacts.",
                ),
            ),
        ),
        *_requirements(
            "engineering_judgement",
            (
                (
                    "electrical_inputs",
                    True,
                    "integration",
                    "Complete project capture binds hierarchy, PCB parity, BOM/models, stackup, "
                    "loads and operating conditions.",
                ),
                (
                    "drc_erc_dfm",
                    True,
                    "real_engine",
                    "Real project DRC/ERC and versioned fabrication checks run; ordinary DRC is "
                    "not full DFM.",
                ),
                (
                    "si_pi",
                    True,
                    "real_engine",
                    "Bounded SI and PI executors run reviewed models with explicit validity "
                    "ranges and convergence checks.",
                ),
                (
                    "thermal_emc",
                    True,
                    "real_engine",
                    "Bounded thermal and EMC pre-compliance executors run actual models, not "
                    "placeholder passes.",
                ),
                (
                    "reference_cases",
                    True,
                    "independent_review",
                    "Known-good, known-bad, incomplete and out-of-profile cases have zero "
                    "safety-critical false passes.",
                ),
                (
                    "physical_calibration",
                    True,
                    "physical_validation",
                    "Profile-specific numerical tolerances are calibrated against independent "
                    "measurements and reviewed models.",
                ),
                (
                    "domain_coverage",
                    True,
                    "independent_review",
                    "Every declared domain meets its frozen capability requirements; unknown "
                    "domains cannot be offset by DRC coverage.",
                ),
                (
                    "evidence_binding",
                    True,
                    "integration",
                    "Every result binds source, model, context, backend and settings; "
                    "disagreement is never an arbitrary winner.",
                ),
                (
                    "input_failure",
                    True,
                    "integration",
                    "Missing, stale, malicious, divergent or out-of-range electrical inputs "
                    "produce fixed non-success outcomes.",
                ),
                (
                    "claim_review",
                    True,
                    "independent_review",
                    "Results disclose profile limits, unresolved domains and separate "
                    "circuit-function findings without general certification.",
                ),
            ),
        ),
        *_requirements(
            "ai_live_autonomy",
            (
                (
                    "bounded_orchestration",
                    True,
                    "integration",
                    "AI drives bounded optimization through validated proposals, not geometry "
                    "or authority instructions.",
                ),
                (
                    "advisory_policy",
                    False,
                    "real_engine",
                    "Optional Orca ordering/ranking records unavailability and deterministic "
                    "fallback; replay pins decisions.",
                ),
                (
                    "native_transaction",
                    True,
                    "real_engine",
                    "A KiCad-side guard owns the document, atomically checks revisions and "
                    "excludes concurrent edits for the batch.",
                ),
                (
                    "exact_batch_consent",
                    True,
                    "real_engine",
                    "One explicit human confirmation authorizes only the exact package and "
                    "session with separate review/mutation capabilities.",
                ),
                (
                    "hostile_requests",
                    True,
                    "independent_review",
                    "Models and hostile clients cannot bypass disclosure, validation, approval, "
                    "transaction protection or apply.",
                ),
                (
                    "live_faults",
                    True,
                    "real_engine",
                    "Real fault tests cover undo, concurrency, partial staging, crashes, lost "
                    "acknowledgements, duplicates and rollback.",
                ),
                (
                    "live_postcheck",
                    True,
                    "real_engine",
                    "Applied state matches the approved composition, passes post-checks and "
                    "creates one undo step without saving.",
                ),
                (
                    "outcome_reconciliation",
                    True,
                    "integration",
                    "Lost acknowledgements query the original operation; uncertain outcomes "
                    "stop writes and never trigger blind retries.",
                ),
                (
                    "operator_control",
                    True,
                    "real_engine",
                    "Real operator stop/decline/stale/restart paths preserve clear, accurate "
                    "mutation outcomes.",
                ),
                (
                    "claim_review",
                    True,
                    "independent_review",
                    "Stock IPC refuses strict apply without the native guard; no "
                    "observation-only transaction is advertised as atomic.",
                ),
            ),
        ),
    ),
)


class RequirementReceipt(ClosedModel):
    requirement_id: str
    catalog_digest: Digest
    evaluated_source_digest: Digest
    status: Verdict
    origin: Origin
    artifact_digest: Digest | None

    @model_validator(mode="after")
    def evidence_for_claim(self) -> RequirementReceipt:
        if self.status in ("pass", "fail") and self.artifact_digest is None:
            raise ValueError("a readiness claim requires an artifact")
        return self


class ReadinessSubmission(ClosedModel):
    schema_version: Literal["readiness-submission/v1"] = "readiness-submission/v1"
    catalog_digest: Digest
    evaluated_source_digest: Digest
    receipts: Annotated[tuple[RequirementReceipt, ...], Field(max_length=50)]

    @model_validator(mode="after")
    def bound_to_frozen_scope(self) -> ReadinessSubmission:
        if self.catalog_digest != FROZEN_CATALOG.digest:
            raise ValueError("readiness scope is not the frozen catalog")
        requirements = {item.requirement_id: item for item in FROZEN_CATALOG.requirements}
        seen: set[str] = set()
        for receipt in self.receipts:
            if receipt.requirement_id not in requirements or receipt.requirement_id in seen:
                raise ValueError("readiness requirement is unknown or repeated")
            seen.add(receipt.requirement_id)
            if (
                receipt.catalog_digest != self.catalog_digest
                or receipt.evaluated_source_digest != self.evaluated_source_digest
            ):
                raise ValueError("readiness receipt describes different source or scope")
            if receipt.origin != requirements[receipt.requirement_id].origin:
                raise ValueError("readiness receipt has the wrong evidence origin")
        return self


def assess_readiness(submission: ReadinessSubmission) -> dict[str, object]:
    """Grade submitted evidence without claiming its referenced artifacts were authenticated."""
    submission = ReadinessSubmission.model_validate(submission)
    supplied = {item.requirement_id: item for item in submission.receipts}
    areas = []
    eligible = True
    for area in AREAS:
        requirements = [item for item in FROZEN_CATALOG.requirements if item.area == area]
        passed = {
            item.requirement_id
            for item in requirements
            if item.requirement_id in supplied and supplied[item.requirement_id].status == "pass"
        }
        blockers = [
            item.requirement_id
            for item in requirements
            if item.critical and item.requirement_id not in passed
        ]
        points = sum(item.points for item in requirements if item.requirement_id in passed)
        eligible = eligible and points >= 90 and not blockers
        areas.append(
            {
                "area": area,
                "submitted_points": points,
                "unassessed_requirements": [
                    item.requirement_id
                    for item in requirements
                    if item.requirement_id not in supplied
                ],
                "critical_blockers": blockers,
            }
        )
    return {
        "catalog_digest": FROZEN_CATALOG.digest,
        "evaluated_source_digest": submission.evaluated_source_digest,
        "areas": areas,
        "status": "eligible_for_independent_artifact_review" if eligible else "blocked",
        "audited_readiness_score": None,
        "artifact_authenticity_verified": False,
        "release_authorized": False,
        "apply_authority": "none",
    }


def decode_submission(payload: bytes) -> ReadinessSubmission:
    from copper_mcp.optimization.contracts import bounded_json

    try:
        bounded_json(payload)
        return ReadinessSubmission.model_validate_json(payload)
    except ValueError:
        raise OptimizationError("readiness submission is invalid") from None
