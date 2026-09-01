"""The schema accepted-set drift gate, in both directions and on real history.

`scripts/check_schema_sets.py` exists because four published schemas changed
which documents they accept without their declared version moving, across three
releases, and nothing noticed. Two of the four are **narrowings** -- a required
key added -- which is the direction issue #172 does not discuss and the more
severe of the two: a document that validated yesterday fails today.

So the tests here are organised by what could make the gate useless:

* it could be blind to one direction (`test_..._narrowing`, `test_..._widening`,
  and the two that replay the real historical instances with their exemption
  removed);
* it could stop looking at the working tree, which is the half that catches the
  next break rather than recording the last four;
* its exemption list could become a suppression mechanism, so an entry matching
  nothing has to fail the run.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location(
    "check_schema_sets", ROOT / "scripts" / "check_schema_sets.py"
)
assert _SPEC is not None and _SPEC.loader is not None
check_schema_sets = importlib.util.module_from_spec(_SPEC)
sys.modules["check_schema_sets"] = check_schema_sets
_SPEC.loader.exec_module(check_schema_sets)


def _closed_object(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": required,
        "properties": properties,
    }


# ---------------------------------------------------------------------------
# The gate on this tree
# ---------------------------------------------------------------------------


def test_the_gate_is_green_on_this_tree_with_exactly_four_recorded_exemptions() -> None:
    """Green, and green for a stated reason.

    Four historical breaks, four exemptions, and no fifth. A count is asserted
    because an exemption list that grows quietly is the failure mode the whole
    mechanism exists to prevent.
    """

    assert len(check_schema_sets.EXEMPT_DRIFT) == 4
    assert check_schema_sets.main() == 0


def test_a_release_tag_this_checker_has_never_heard_of_fails_the_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The tag list is explicit, so it has to be unable to go stale quietly.

    The working-tree half compares against the newest listed tag that exists.
    If a release is tagged and not listed, that anchor silently stops being newest.
    """

    repository_tags = check_schema_sets._repository_release_tags()
    monkeypatch.setattr(
        check_schema_sets,
        "_repository_release_tags",
        lambda: {*repository_tags, "v0.13.0"},
    )

    with pytest.raises(SystemExit) as caught:
        check_schema_sets.main()

    assert "RELEASE_TAGS omits v0.13.0" in str(caught.value)


def test_the_listed_tags_are_the_repository_tags_plus_at_most_the_current_pending_tag() -> None:
    repository_tags = check_schema_sets._repository_release_tags()
    assert repository_tags <= set(check_schema_sets.RELEASE_TAGS)
    assert set(check_schema_sets.RELEASE_TAGS) - repository_tags in (
        set(),
        {check_schema_sets._current_project_tag()},
    )


def test_only_the_final_current_version_tag_may_be_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository_tags = set(check_schema_sets.RELEASE_TAGS) - {"v0.11.0", "v0.12.0"}
    monkeypatch.setattr(check_schema_sets, "_repository_release_tags", lambda: repository_tags)

    with pytest.raises(SystemExit) as caught:
        check_schema_sets.main()

    assert "listed historical release tag(s) are missing: v0.11.0, v0.12.0" in str(caught.value)


def test_pending_final_tag_must_match_the_project_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository_tags = set(check_schema_sets.RELEASE_TAGS) - {"v0.12.0"}
    monkeypatch.setattr(check_schema_sets, "_repository_release_tags", lambda: repository_tags)
    monkeypatch.setattr(check_schema_sets, "_current_project_tag", lambda: "v0.13.0")

    with pytest.raises(SystemExit) as caught:
        check_schema_sets.main()

    assert "pending final tag v0.12.0 does not match project version tag v0.13.0" in str(
        caught.value
    )


def test_every_exemption_names_the_record_that_carries_its_break() -> None:
    for key, reason in check_schema_sets.EXEMPT_DRIFT.items():
        assert "ADR-0105" in reason, key
        assert "D-197" in reason, key
        assert "narrowing" in reason or "widening" in reason, key


def test_the_four_exemptions_span_three_releases_and_two_directions() -> None:
    """The shape of the finding, pinned so a later reader cannot lose it."""

    tags = {tag for _, _, tag in check_schema_sets.EXEMPT_DRIFT}
    reasons = " ".join(check_schema_sets.EXEMPT_DRIFT.values())

    assert tags == {"v0.3.0", "v0.7.0", "v0.8.0"}
    assert "narrowing" in reasons
    assert "widening" in reasons


# ---------------------------------------------------------------------------
# The two historical instances, replayed with their exemption removed
# ---------------------------------------------------------------------------


def test_it_reports_adr_0097s_historical_widening_when_that_exemption_is_removed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The instance issue #172 was filed about, detected rather than assumed.

    ADR-0097 added `far_side_courtyards` and `far_side_courtyard_circles` to
    `$defs/footprint` in `board-ir/0.2.0.schema.json` at `v0.8.0`, with the
    version unmoved and `additionalProperties: false` in force. Dropping the
    exemption must make the gate say exactly that.
    """

    key = ("schemas/board-ir/0.2.0.schema.json", "0.2.0", "v0.8.0")
    remaining = {k: v for k, v in check_schema_sets.EXEMPT_DRIFT.items() if k != key}
    monkeypatch.setattr(check_schema_sets, "EXEMPT_DRIFT", remaining)

    with pytest.raises(SystemExit) as caught:
        check_schema_sets.main()

    message = str(caught.value)
    assert "schemas/board-ir/0.2.0.schema.json" in message
    assert "v0.8.0" in message
    assert "far_side_courtyards" in message
    assert "far_side_courtyard_circles" in message
    assert "[widening]" in message


def test_it_reports_the_drc_summary_required_key_narrowing_when_that_exemption_is_removed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The direction #172 does not discuss, on a real published instance.

    `clean` became a required key of `drc-summary.schema.json` at `v0.7.0`. A
    payload a `v0.6.0` consumer emitted stops validating -- the stronger break,
    and the reason this gate is not a widening detector.
    """

    key = ("schemas/drc-summary.schema.json", "1.0", "v0.7.0")
    remaining = {k: v for k, v in check_schema_sets.EXEMPT_DRIFT.items() if k != key}
    monkeypatch.setattr(check_schema_sets, "EXEMPT_DRIFT", remaining)

    with pytest.raises(SystemExit) as caught:
        check_schema_sets.main()

    message = str(caught.value)
    assert "schemas/drc-summary.schema.json" in message
    assert "v0.7.0" in message
    assert "|required" in message
    assert "[narrowing]" in message


# ---------------------------------------------------------------------------
# The working-tree half
# ---------------------------------------------------------------------------


def test_the_working_tree_is_compared_against_the_newest_release_tag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without this half the gate only records the past.

    A schema present at `v0.8.0` is widened in the working tree at an unmoved
    version -- exactly ADR-0097's edit, re-applied where it would be caught.
    """

    real = check_schema_sets._working_tree_schemas

    def widened() -> dict[str, Any]:
        documents = real()
        footprint = documents["schemas/board-ir/0.2.0.schema.json"]["$defs"]["footprint"]
        footprint["properties"]["near_side_courtyards"] = {"type": "array"}
        return documents

    monkeypatch.setattr(check_schema_sets, "_working_tree_schemas", widened)

    with pytest.raises(SystemExit) as caught:
        check_schema_sets.main()

    message = str(caught.value)
    assert "the working tree" in message
    assert "near_side_courtyards" in message
    assert "[widening]" in message


def test_removing_a_published_schema_fails_the_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deleting the frozen file is the one way to defeat the freeze silently.

    It is not an accepted-set *change*, so the comparison never sees it. If this
    did not fail, ADR-0105's freeze would be enforced by nothing at all.
    """

    real = check_schema_sets._working_tree_schemas

    def deleted() -> dict[str, Any]:
        documents = real()
        del documents["schemas/board-ir/0.2.0.schema.json"]
        return documents

    monkeypatch.setattr(check_schema_sets, "_working_tree_schemas", deleted)

    with pytest.raises(SystemExit) as caught:
        check_schema_sets.main()

    message = str(caught.value)
    assert "published schema removed" in message
    assert "schemas/board-ir/0.2.0.schema.json" in message


def test_a_working_tree_required_key_addition_fails_as_a_narrowing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real = check_schema_sets._working_tree_schemas

    def narrowed() -> dict[str, Any]:
        documents = real()
        footprint = documents["schemas/board-ir/0.2.0.schema.json"]["$defs"]["footprint"]
        footprint["required"] = sorted([*footprint["required"], "courtyard_circles"])
        return documents

    monkeypatch.setattr(check_schema_sets, "_working_tree_schemas", narrowed)

    with pytest.raises(SystemExit) as caught:
        check_schema_sets.main()

    message = str(caught.value)
    assert "the working tree" in message
    assert "|required" in message
    assert "[narrowing]" in message


def test_the_new_0_4_0_schema_is_not_reported_as_drift_against_0_3_0() -> None:
    """A first publication breaks no promise, so the bump itself must be silent.

    This is the control for the gate's own change: ADR-0105 added a file rather
    than editing one, and a gate that fired on that would be unusable. The
    0.3.0 schema remains frozen; 0.4.0 is the active publication.
    """

    documents = check_schema_sets._working_tree_schemas()
    active = documents["schemas/board-ir/0.4.0.schema.json"]
    frozen = documents["schemas/board-ir/0.3.0.schema.json"]

    assert check_schema_sets.declared_version("schemas/board-ir/0.4.0.schema.json", active) == (
        "0.4.0"
    )
    assert check_schema_sets.declared_version("schemas/board-ir/0.3.0.schema.json", frozen) == (
        "0.3.0"
    )
    assert check_schema_sets.main() == 0


# ---------------------------------------------------------------------------
# The exemption discipline
# ---------------------------------------------------------------------------


def test_every_exemption_is_keyed_to_a_release_tag() -> None:
    for path, version, tag in check_schema_sets.EXEMPT_DRIFT:
        assert tag in check_schema_sets.RELEASE_TAGS, (path, version, tag)


def test_an_exemption_keyed_to_the_working_tree_cannot_wave_through_live_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The exact attack adversarial review used to defeat the first draft.

    Real drift in the working tree, plus one line here keyed to the working tree
    and citing a row that does not exist, and the run went green. Only a
    *published* break is unrepairable, so only a published break is exemptible;
    anything in the working tree can simply be fixed. Rejecting the key makes the
    smuggled entry inexpressible rather than something a reviewer must catch.
    """

    real = check_schema_sets._working_tree_schemas

    def widened() -> dict[str, Any]:
        documents = real()
        footprint = documents["schemas/board-ir/0.2.0.schema.json"]["$defs"]["footprint"]
        footprint["properties"]["smuggled_courtyards"] = {"type": "array"}
        return documents

    smuggled = ("schemas/board-ir/0.2.0.schema.json", "0.2.0", check_schema_sets.WORKING_TREE)
    monkeypatch.setattr(check_schema_sets, "_working_tree_schemas", widened)
    monkeypatch.setattr(
        check_schema_sets,
        "EXEMPT_DRIFT",
        {**check_schema_sets.EXEMPT_DRIFT, smuggled: "D-999: widening"},
    )

    with pytest.raises(SystemExit) as caught:
        check_schema_sets.main()

    message = str(caught.value)
    assert "is not a release tag" in message
    # And the drift itself is still reported, rather than the run stopping at the key check.
    assert "smuggled_courtyards" in message


def test_an_exemption_recording_a_direction_the_change_does_not_have_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`narrowing` and `widening` inside a reason are checked, not prose.

    Adversarial review flipped this exact word on this exact entry and nothing
    noticed. ADR-0097's edit is a widening; recording it as a narrowing must now
    fail against the gate's own re-derivation.
    """

    key = ("schemas/board-ir/0.2.0.schema.json", "0.2.0", "v0.8.0")
    flipped = dict(check_schema_sets.EXEMPT_DRIFT)
    flipped[key] = flipped[key].replace("(widening)", "(narrowing)")
    monkeypatch.setattr(check_schema_sets, "EXEMPT_DRIFT", flipped)

    with pytest.raises(SystemExit) as caught:
        check_schema_sets.main()

    message = str(caught.value)
    assert "records narrowing" in message
    assert "it shows widening" in message


def test_an_exemption_recording_no_direction_at_all_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    key = ("schemas/drc-summary.schema.json", "1.0", "v0.7.0")
    silent = dict(check_schema_sets.EXEMPT_DRIFT)
    silent[key] = "ADR-0105 / D-197: something changed"
    monkeypatch.setattr(check_schema_sets, "EXEMPT_DRIFT", silent)

    with pytest.raises(SystemExit) as caught:
        check_schema_sets.main()

    assert "records no direction" in str(caught.value)


def test_a_recorded_direction_need_not_be_exhaustive_and_drc_summary_is_why() -> None:
    """The check is containment, and this is the case that forces it to be.

    `clean` became a required key at `v0.7.0`. That is simultaneously a widening
    of the object's property set and a narrowing of its `required` list. The
    entry records the net effect on a consumer -- the narrowing -- and an
    equality check would reject that true record.
    """

    key = ("schemas/drc-summary.schema.json", "1.0", "v0.7.0")
    reason = check_schema_sets.EXEMPT_DRIFT[key]

    assert "narrowing" in reason
    assert "widening" not in reason

    before = check_schema_sets.accepted_set(
        check_schema_sets._document_at("v0.6.0", "schemas/drc-summary.schema.json")
    )
    after = check_schema_sets.accepted_set(
        check_schema_sets._document_at("v0.7.0", "schemas/drc-summary.schema.json")
    )
    observed = check_schema_sets._observed_directions(check_schema_sets.drift(before, after))

    assert observed == {"narrowing", "widening"}
    assert check_schema_sets.main() == 0


def test_an_exemption_that_matches_no_drift_fails_the_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Not a suppression mechanism: a stale entry is itself a failure."""

    stale = ("schemas/board-ir/0.1.0.schema.json", "0.1.0", "v0.4.0")
    monkeypatch.setattr(
        check_schema_sets,
        "EXEMPT_DRIFT",
        {**check_schema_sets.EXEMPT_DRIFT, stale: "ADR-0105 / D-197: no such widening"},
    )

    with pytest.raises(SystemExit) as caught:
        check_schema_sets.main()

    message = str(caught.value)
    assert "matched no accepted-set change" in message
    assert "schemas/board-ir/0.1.0.schema.json" in message


# ---------------------------------------------------------------------------
# Accepted-set extraction and direction classification
# ---------------------------------------------------------------------------


def test_a_required_key_addition_is_a_narrowing() -> None:
    before = _closed_object({"a": {}, "b": {}}, ["a"])
    after = _closed_object({"a": {}, "b": {}}, ["a", "b"])

    reported = check_schema_sets.drift(
        check_schema_sets.accepted_set(before), check_schema_sets.accepted_set(after)
    )

    assert len(reported) == 1
    assert "|required" in reported[0]
    assert "[narrowing]" in reported[0]


def test_a_property_addition_to_a_closed_object_is_a_widening() -> None:
    before = _closed_object({"a": {}}, ["a"])
    after = _closed_object({"a": {}, "b": {}}, ["a"])

    reported = check_schema_sets.drift(
        check_schema_sets.accepted_set(before), check_schema_sets.accepted_set(after)
    )

    assert len(reported) == 1
    assert "|properties" in reported[0]
    assert "[widening]" in reported[0]


@pytest.mark.parametrize(
    ("before", "after", "direction"),
    [
        ({"enum": ["a"]}, {"enum": ["a", "b"]}, "[widening]"),
        ({"enum": ["a", "b"]}, {"enum": ["a"]}, "[narrowing]"),
        ({"type": ["string"]}, {"type": ["null", "string"]}, "[widening]"),
        ({"type": ["null", "string"]}, {"type": ["string"]}, "[narrowing]"),
        ({"const": "0.2.0"}, {"const": "0.3.0"}, "[narrowing/widening]"),
        (
            _closed_object({"a": {}}, []),
            {"type": "object", "required": [], "properties": {"a": {}}},
            "[widening]",
        ),
        (
            {"type": "object", "required": [], "properties": {"a": {}}},
            _closed_object({"a": {}}, []),
            "[narrowing]",
        ),
    ],
)
def test_each_keyword_is_classified_by_the_direction_it_breaks(
    before: dict[str, Any], after: dict[str, Any], direction: str
) -> None:
    reported = check_schema_sets.drift(
        check_schema_sets.accepted_set(before), check_schema_sets.accepted_set(after)
    )

    assert reported
    assert any(direction in line for line in reported)


def test_a_whole_constraint_site_appearing_is_labelled_shape_and_claims_no_direction() -> None:
    """Honest silence beats a confident wrong word in a failure message.

    A new closed `$def` with a `required` list narrows the documents it governs;
    the same `$def` reached only through a new *optional* property widens the
    schema. Nothing local distinguishes the two, so nothing local claims to.
    """

    before: dict[str, Any] = {"$defs": {}}
    after: dict[str, Any] = {"$defs": {"circle": _closed_object({"radius": {}}, ["radius"])}}

    reported = check_schema_sets.drift(
        check_schema_sets.accepted_set(before), check_schema_sets.accepted_set(after)
    )

    assert reported
    assert all("[shape]" in line for line in reported)
    assert not any("[narrowing]" in line or "[widening]" in line for line in reported)


def test_an_identical_set_written_in_a_different_order_is_not_drift() -> None:
    before = _closed_object({"a": {}, "b": {}}, ["b", "a"])
    after = _closed_object({"b": {}, "a": {}}, ["a", "b"])

    assert (
        check_schema_sets.drift(
            check_schema_sets.accepted_set(before), check_schema_sets.accepted_set(after)
        )
        == []
    )


def test_an_absent_additional_properties_is_read_as_open() -> None:
    """`additionalProperties` defaults to `true`, so its absence is a promise too."""

    extracted = check_schema_sets.accepted_set({"properties": {"a": {}}})

    assert extracted["$ |additionalProperties"] is True


def test_the_declared_version_comes_from_the_schema_when_it_pins_one() -> None:
    document = {"properties": {"schema_version": {"const": "9.9.9"}}}

    assert check_schema_sets.declared_version("schemas/anything.schema.json", document) == "9.9.9"


def test_the_declared_version_falls_back_to_the_filename() -> None:
    assert check_schema_sets.declared_version("schemas/board-ir/0.4.0.schema.json", {}) == "0.4.0"
    assert check_schema_sets.declared_version("schemas/kicad-pcm/pcm.v1.schema.json", {}) == (
        "pcm.v1"
    )
