"""The comparability literal, and the prohibition that makes it load-bearing.

`B-107` found that two runs of the same runner, at the same commit, over byte-identical boards,
disagreed on nine boards' DRC counts. ADR-0109's answer is a one-value literal plus a prohibition
on citing anything but `repeated_agreement` in a differential.

The tests are organised by what could make the literal decorative:

* it could be earned by observations that did not agree, so every count field has to be compared
  (`test_..._agreement_...`, `test_..._every_count_field_...`);
* the comparison could quietly exclude the field that moved, which is why `INCOMPARABLE_KEYS` is
  pinned against the count tables rather than merely reviewed;
* an aggregate could launder a disagreement through an addition (`weakest`);
* the prohibition could be advisory, so `drc_differential` is tested by handing it each
  inadmissible literal and reading the refusal.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from copper_mcp.benchmarks.drc_comparability import (
    COMPARABILITY_LITERALS,
    COUNT_KEYS,
    INCOMPARABLE_KEYS,
    LITERAL_KEY,
    REPETITIONS_KEY,
    SECTION_KEYS,
    DrcComparabilityError,
    admissible_in_differential,
    comparability_of,
    drc_differential,
    drc_sections,
    qualified,
    require_qualified,
    weakest,
)

ROOT = Path(__file__).resolve().parents[1]


def _observation(**changes: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "elapsed_ms": 1964.7,
        "outcome": "reported",
        "kicad_version": "10.0.5",
        "passed": False,
        "clean": False,
        "error_count": 936,
        "warning_count": 111,
        "unconnected_count": 56,
        "violation_type_counts": {"clearance": 503, "hole_clearance": 201},
    }
    base.update(changes)
    return base


# ---------------------------------------------------------------------------
# The closed set
# ---------------------------------------------------------------------------


def test_the_literal_set_is_exactly_the_three_names_adr_0109_writes() -> None:
    assert set(COMPARABILITY_LITERALS) == {
        "single_invocation",
        "repeated_agreement",
        "repeated_disagreement",
    }


def test_the_literals_are_ordered_weakest_first_so_weakest_can_read_them() -> None:
    assert COMPARABILITY_LITERALS.index("repeated_disagreement") == 0
    assert COMPARABILITY_LITERALS.index("repeated_agreement") == len(COMPARABILITY_LITERALS) - 1


# ---------------------------------------------------------------------------
# Earning the literal
# ---------------------------------------------------------------------------


def test_one_observation_earns_single_invocation() -> None:
    assert comparability_of([_observation()]) == "single_invocation"


def test_no_observation_cannot_be_qualified_at_all() -> None:
    with pytest.raises(DrcComparabilityError, match="at least one observation"):
        comparability_of([])


def test_two_agreeing_observations_earn_repeated_agreement() -> None:
    assert comparability_of([_observation(), _observation()]) == "repeated_agreement"


def test_wall_clock_alone_does_not_break_agreement() -> None:
    """`elapsed_ms` is never equal across two runs and is not a property of the board."""

    observations = [_observation(elapsed_ms=1964.7), _observation(elapsed_ms=2011.2)]

    assert comparability_of(observations) == "repeated_agreement"


@pytest.mark.parametrize(
    ("field", "moved"),
    [
        ("error_count", 941),
        ("warning_count", 112),
        ("unconnected_count", 57),
        ("passed", True),
        ("clean", True),
        ("violation_type_counts", {"clearance": 503, "hole_clearance": 202}),
        # The type that appeared in one B-107 run and not the other.
        ("violation_type_counts", {"clearance": 503, "tracks_crossing": 4}),
    ],
)
def test_every_count_field_that_moves_costs_the_agreement(field: str, moved: Any) -> None:
    """B-107's actual movements, each one enough on its own to withdraw the claim."""

    observations = [_observation(), _observation(**{field: moved})]

    assert comparability_of(observations) == "repeated_disagreement"


def test_no_count_may_be_excluded_from_the_comparison() -> None:
    """Excluding a count would let a section claim agreement while its number moved."""

    assert INCOMPARABLE_KEYS.isdisjoint(COUNT_KEYS)
    assert INCOMPARABLE_KEYS == {"elapsed_ms", LITERAL_KEY, REPETITIONS_KEY}


def test_qualified_records_the_literal_and_how_many_invocations_earned_it() -> None:
    section = qualified(_observation(), [_observation(), _observation()])

    assert section[LITERAL_KEY] == "repeated_agreement"
    assert section[REPETITIONS_KEY] == 2
    assert section["error_count"] == 936


# ---------------------------------------------------------------------------
# Aggregates
# ---------------------------------------------------------------------------


def test_an_aggregate_takes_the_weakest_of_its_inputs() -> None:
    assert weakest(["repeated_agreement", "repeated_agreement"]) == "repeated_agreement"
    assert weakest(["repeated_agreement", "single_invocation"]) == "single_invocation"
    assert (
        weakest(["repeated_agreement", "single_invocation", "repeated_disagreement"])
        == "repeated_disagreement"
    )


def test_an_aggregate_cannot_be_built_from_a_literal_that_is_not_one() -> None:
    with pytest.raises(DrcComparabilityError, match="is not a comparability literal"):
        weakest(["repeated_agreement", "probably_fine"])


# ---------------------------------------------------------------------------
# Finding the sections
# ---------------------------------------------------------------------------


def test_a_section_is_found_wherever_it_sits_including_inside_a_list() -> None:
    document = {
        "boards": [{"drc": _observation()}, {"drc": {"outcome": "refused"}}],
        "totals": {"drc_reported": 1, "drc_passed": 0},
    }

    assert {path for path, _ in drc_sections(document)} == {"/boards[0]/drc", "/totals"}


def test_a_derived_flag_alone_does_not_make_a_section() -> None:
    """`passed` and `clean` mean too many things elsewhere to identify a DRC record by."""

    assert list(drc_sections({"suite": {"passed": True, "clean": True}})) == []
    assert "passed" in COUNT_KEYS
    assert "passed" not in SECTION_KEYS


# ---------------------------------------------------------------------------
# The emission gate
# ---------------------------------------------------------------------------


def test_emitting_an_unqualified_count_is_refused() -> None:
    with pytest.raises(DrcComparabilityError, match="without a 'drc_comparability' literal"):
        require_qualified({"boards": [{"drc": _observation()}]}, where="probe")


def test_a_literal_outside_the_closed_set_is_refused() -> None:
    section = {**_observation(), LITERAL_KEY: "probably_fine"}

    with pytest.raises(DrcComparabilityError, match="probably_fine"):
        require_qualified({"drc": section}, where="probe")


def test_a_qualified_document_emits() -> None:
    report = {
        "boards": [{"drc": qualified(_observation(), [_observation()])}],
        "totals": {"drc_reported": 1, LITERAL_KEY: "single_invocation"},
    }

    require_qualified(report, where="probe")


# ---------------------------------------------------------------------------
# The prohibition
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("literal", ["single_invocation", "repeated_disagreement", None])
def test_a_differential_may_not_cite_an_incomparable_count(literal: str | None) -> None:
    section = {**_observation(), LITERAL_KEY: literal} if literal else dict(_observation())

    with pytest.raises(DrcComparabilityError, match="a differential may not cite"):
        admissible_in_differential(section, where="probe")


def test_a_differential_refuses_when_either_side_is_incomparable() -> None:
    agreed = {**_observation(), LITERAL_KEY: "repeated_agreement"}
    single = {**_observation(error_count=941), LITERAL_KEY: "single_invocation"}

    with pytest.raises(DrcComparabilityError, match=r"\(before\)"):
        drc_differential(single, agreed, "error_count", where="probe")
    with pytest.raises(DrcComparabilityError, match=r"\(after\)"):
        drc_differential(agreed, single, "error_count", where="probe")


def test_a_differential_over_two_agreed_counts_is_the_difference() -> None:
    before = {**_observation(error_count=936), LITERAL_KEY: "repeated_agreement"}
    after = {**_observation(error_count=941), LITERAL_KEY: "repeated_agreement"}

    assert drc_differential(before, after, "error_count", where="probe") == 5


def test_a_differential_refuses_a_field_that_is_not_a_count() -> None:
    before = {**_observation(), LITERAL_KEY: "repeated_agreement"}
    after = {**_observation(), LITERAL_KEY: "repeated_agreement"}

    with pytest.raises(DrcComparabilityError, match="is not a DRC count"):
        drc_differential(before, after, "elapsed_ms", where="probe")


def test_a_differential_refuses_a_boolean_dressed_as_a_count() -> None:
    """`passed` is a count-derived flag; subtracting two of them is not a measurement."""

    before = {**_observation(), LITERAL_KEY: "repeated_agreement"}
    after = {**_observation(passed=True), LITERAL_KEY: "repeated_agreement"}

    with pytest.raises(DrcComparabilityError, match="is not an integer count"):
        drc_differential(before, after, "passed", where="probe")


# ---------------------------------------------------------------------------
# The boundary this policy deliberately does not cross
# ---------------------------------------------------------------------------


def test_the_live_drc_summary_schema_is_untouched_by_this_policy() -> None:
    """`drc-summary` is the payload a caller receives, not the benchmark projection.

    A live response is one invocation by construction, so a literal in it would be a constant
    field, and the audit's decision is explicit that the schema is not the policy's subject.
    Pinning it here is what stops a later slice widening the schema on this policy's authority --
    which is exactly the accepted-set drift ADR-0105 exists to prevent.
    """

    schemas = sorted((ROOT / "schemas").rglob("drc-summary*.json"))

    assert schemas, "the drc-summary schema should exist"
    for schema in schemas:
        assert LITERAL_KEY not in json.dumps(json.loads(schema.read_text(encoding="utf-8")))
