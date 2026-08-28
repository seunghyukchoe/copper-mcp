"""The committed B-134 census and B-135 differential must be internally checkable.

The measuring script is not committed — by the B-114/B-118/B-119 precedent it duplicates the
adapter's conversion call and needs boards that are not in the tree, so a committed copy could not
be replayed from a clean checkout. What makes the result auditable instead is the artifact itself:
it carries its own digest, the cohort fingerprint it was taken against, the hash of a prediction
written before the code, and per-board outcomes that must add up to the aggregate the ledger row
quotes. This test checks all four, so a hand-edited number fails here rather than in review.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "benchmarks" / "results" / "board-ir"
CENSUS = RESULTS / "2026-08-28-public-edge-cuts-curve-census-v1.json"
DIFFERENTIAL = RESULTS / "2026-08-28-outline-arc-acceptance-differential-v1.json"
COHORT_FINGERPRINT = "sha256:bfec8210d6d4eb746ffdbfb3b70309ce"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _self_digest(document: dict) -> str:
    body = {key: value for key, value in document.items() if key != "run_id"}
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()


def test_each_artifact_authenticates_its_own_canonical_bytes() -> None:
    """A self-digest is the only thing standing between a recorded number and an edited one."""

    for path in (CENSUS, DIFFERENTIAL):
        document = _load(path)
        assert document["run_id"] == _self_digest(document), path.name


def test_both_artifacts_were_taken_on_the_same_clean_commit_and_cohort() -> None:
    """Two measurements only compare if they measured the same boards from the same tree.

    ``dirty`` is checked because a run over a modified worktree measures something no commit
    contains, and the whole point of recording the commit is that someone else can return to it.
    """

    census, differential = _load(CENSUS), _load(DIFFERENTIAL)

    assert census["dirty"] is False
    assert differential["dirty"] is False
    assert census["commit"] == differential["commit"]
    for document in (census, differential):
        assert document["cohort"]["cohort_fingerprint"] == COHORT_FINGERPRINT
        assert (
            document["cohort"]["cohort_fingerprint"]
            == (document["cohort"]["predeclared_cohort_fingerprint"])
        )
        assert document["cohort"]["digests_reverified_before_use"] is True
        assert document["cohort"]["entries"] == 13
        assert document["cohort"]["public_entries"] == 10
        assert document["cohort"]["private_entries"] == 3


def test_the_census_records_that_every_curve_primitive_is_an_arc() -> None:
    """B-134's headline, pinned: the design rests on there being no circle and no Bezier.

    ADR-0124 refuses `gr_circle`, `gr_curve`, `gr_bezier` and `gr_poly` partly *because* no
    measured board writes one. If that ever stops being true the decision needs revisiting, and
    this is where it shows.
    """

    aggregates = _load(CENSUS)["aggregates"]
    heads = aggregates["root_edge_cuts_head_occurrences"]

    assert heads["gr_arc"] == 97
    assert heads["gr_line"] == 123
    for absent in ("gr_bezier", "gr_circle", "gr_curve", "gr_poly"):
        assert heads.get(absent, 0) == 0
    # The closed payload grammar the adapter's allowlist is built from.
    assert aggregates["arc_positional_atom_occurrences"] == 0
    assert set(aggregates["arc_child_head_occurrences"]) == {
        "end",
        "layer",
        "mid",
        "start",
        "stroke",
        "uuid",
    }
    assert all(count == 97 for count in aggregates["arc_child_head_occurrences"].values())


def test_the_census_records_the_topology_and_direction_split_the_decision_rests_on() -> None:
    """Seven boards close, three do not, and 13 of 51 arcs cut into the board.

    The concave count is the one that justifies refusing that case by name rather than treating it
    as hypothetical, and the sub-micron gap bucket is what makes the zero chaining epsilon a
    measured position rather than a stated one.
    """

    aggregates = _load(CENSUS)["aggregates"]

    assert aggregates["board_topology"]["one_closed_loop"] == 7
    assert aggregates["board_topology"]["unpaired_endpoints"] == 3
    assert aggregates["unpaired_endpoint_separation_buckets"]["under_1000nm"] == 2
    assert aggregates["arc_direction_on_closing_boards"] == {"concave": 13, "convex": 38}
    assert aggregates["arc_span_on_closing_boards"] == {"minor": 51}
    assert aggregates["arc_circumcentre_on_closing_boards"] == {"rational": 51}


def test_the_differential_says_no_board_converts_and_the_gate_is_gone() -> None:
    """The claim the ledger row and the pull request both make, checked against the artifact.

    Two numbers together, because either alone is misleading: the gate count going to zero without
    the conversion count staying at zero would be a first conversion, and the conversion count
    alone would not show that the slice did anything.
    """

    metrics = _load(DIFFERENTIAL)["metrics"]

    assert metrics["public_converted"] == 0
    assert metrics["private_converted"] == 0
    assert metrics["boards_still_refusing_at_the_edge_cuts_curve_gate"] == 0
    assert metrics["committed_board_bytes"] == 0
    assert (
        metrics["first_refusal_histogram"][
            "copper text has no envelope derivable from the board and is unsupported"
        ]
        == 6
    )


def test_the_differential_per_board_rows_reconcile_with_its_own_aggregates() -> None:
    """A total that is not recomputed from its parts is a number nobody checked.

    B-132's review round added exactly this guard one slice ago, for the same reason: an aggregate
    and a breakdown that are written independently can disagree, and the disagreement is invisible.
    """

    metrics = _load(DIFFERENTIAL)["metrics"]
    boards = metrics["boards"]

    assert len(boards) == 13
    assert (
        sum(row["converted"] for row in boards if row["visibility"] == "public")
        == (metrics["public_converted"])
    )
    assert (
        sum(row["converted"] for row in boards if row["visibility"] == "private")
        == (metrics["private_converted"])
    )
    histogram = Counter(
        row["first_refusal"]["message"] for row in boards if row["first_refusal"] is not None
    )
    assert dict(histogram) == metrics["first_refusal_histogram"]
    # Every board is accounted for by exactly one outcome: a refusal or a conversion, never both
    # and never neither.
    assert all((row["first_refusal"] is None) is row["converted"] for row in boards)
    assert not any(
        "Edge.Cuts outline" in row["first_refusal"]["message"]
        for row in boards
        if row["first_refusal"] is not None
    )


def test_the_prediction_was_recorded_before_the_code_and_is_named_by_its_hash() -> None:
    """A prediction that cannot be shown to predate the result is not a prediction.

    The hash is of a file written and digested against a clean worktree at the base commit; the
    base commit is recorded beside it so the claim is checkable rather than asserted.
    """

    predeclaration = _load(DIFFERENTIAL)["predeclaration"]

    assert predeclaration["recorded_before_the_adapter_was_touched"] is True
    assert predeclaration["base_commit"] == "bdd6589c85ebf9bc80d7bfcdebbb3e624eda1770"
    assert predeclaration["sha256"] == (
        "29c3336173a47e5b0674151487c88de48188540ed0fb1bcb2bc6913aec6ff2f8"
    )
    assert predeclaration["predicted_public_converted"] == 0
    assert predeclaration["predicted_first_conversion"] is False
    assert predeclaration["predicted_curve_gate_cleared_boards"] == 8


def test_neither_artifact_carries_a_board_identity_that_could_be_resold() -> None:
    """Aggregate-only is a privacy claim, so it is checked rather than stated in prose."""

    for path in (CENSUS, DIFFERENTIAL):
        document = _load(path)
        privacy = document.get("privacy")
        if privacy is not None:
            assert privacy["aggregate_only"] is True
            assert privacy["board_bytes_committed"] == 0
            assert privacy["board_paths_committed"] == 0
            assert privacy["board_digests_committed"] == 0
        text = path.read_text(encoding="utf-8")
        assert ".kicad_pcb" not in text
        assert "/Users/" not in text
