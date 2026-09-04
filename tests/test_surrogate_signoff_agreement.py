"""The surrogate-signoff agreement instrument proves its own contract synthetically.

No test here runs a router, a ranking, or KiCad: the pure helpers (variant classification,
rank-end selection, signoff classification, tallying, cohort fingerprinting, bounded reads)
are exercised against synthetic inputs, and the refusal and redaction properties they must
have in the real run are pinned here. The live pipeline itself is validated by ``--pilot``,
which publishes nothing.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.benchmark_surrogate_signoff_agreement import (  # noqa: E402
    COHORT,
    PILOT_COHORT,
    SIGNOFF_REPETITIONS,
    VARIANT_SEEDS,
    board_eligible_for_signoff,
    classify_signoff,
    classify_variant,
    cohort_fingerprint,
    read_board_bytes,
    select_rank_ends,
    tally_nets,
)


def _result(*, candidate=None, connected=None, diagnostic=None):
    return SimpleNamespace(candidate=candidate, connected=connected, diagnostic=diagnostic)


def _candidate(identifier: str):
    return SimpleNamespace(candidate_id=identifier)


def test_variant_outcomes_are_ternary_and_name_no_geometry() -> None:
    routed = classify_variant("net:x", 101, _result(candidate=_candidate("sha256:" + "a" * 64)))
    assert (routed.kind, routed.seed) == ("routed", 101)
    assert routed.candidate_id == "sha256:" + "a" * 64
    assert classify_variant("net:x", 101, _result(connected=object())).kind == ("already_connected")
    code = SimpleNamespace(value="no_path")
    refused = classify_variant("net:x", 101, _result(diagnostic=SimpleNamespace(code=code)))
    assert refused.kind == "refused:no_path"
    assert refused.candidate_id is None
    with pytest.raises(ValueError, match="malformed"):
        classify_variant("net:x", 101, _result())
    with pytest.raises(ValueError, match="malformed"):
        classify_variant("net:x", 101, _result(diagnostic=SimpleNamespace(code=SimpleNamespace())))


def test_rank_ends_need_two_entries_and_keep_advisory_order() -> None:
    assert select_rank_ends([]) is None
    assert select_rank_ends(["only"]) is None
    assert select_rank_ends(["top", "mid", "bottom"]) == ("top", "bottom")


def test_signoff_outcomes_are_claimed_or_coded() -> None:
    assert classify_signoff(SimpleNamespace(claimed=lambda: True)) == "signed_off"
    code = SimpleNamespace(value="backend_unavailable")
    assert classify_signoff(SimpleNamespace(claimed=lambda: False, code=code)) == (
        "not_signed_off:backend_unavailable"
    )
    with pytest.raises(ValueError, match="malformed"):
        classify_signoff(SimpleNamespace(claimed=lambda: None))
    with pytest.raises(ValueError, match="no code"):
        classify_signoff(SimpleNamespace(claimed=lambda: False, code=None))


def test_tally_reports_rates_and_never_a_verdict() -> None:
    per_net = {
        "a": {
            "variants": ["routed", "routed", "refused:no_path", "already_connected"],
            "rank_ends": ["t", "b"],
            "top_signoff": "signed_off",
            "bottom_signoff": "not_signed_off:backend_unavailable",
        },
        "b": {
            "variants": ["routed", "refused:off_grid", "refused:off_grid", "refused:off_grid"],
            "rank_ends": None,
        },
        "c": {
            "variants": ["routed", "routed", "routed", "routed"],
            "rank_ends": ["t", "b"],
            "ranking_refused": True,
        },
        "d": {
            "variants": ["routed", "routed", "routed", "routed"],
            "rank_ends": ["t", "b"],
            "signoff_skipped": "board_context_ineligible",
        },
    }
    totals = tally_nets(per_net)
    assert totals["nets_attempted"] == 4
    assert totals["nets_qualified"] == 1
    assert totals["nets_excluded_fewer_than_two_routed"] == 1
    assert totals["nets_excluded_ranking_refused"] == 1
    assert totals["nets_excluded_board_context_ineligible"] == 1
    assert totals["variant_outcomes"]["routed"] == 11
    assert totals["variant_outcomes"]["already_connected"] == 1
    assert totals["top_signoff_rate"] == 1.0
    assert totals["bottom_signoff_rate"] == 0.0
    assert totals["rate_differential"] == 1.0
    assert tally_nets({})["rate_differential"] is None


def test_tally_output_carries_no_board_content() -> None:
    totals = tally_nets(
        {
            "board::net:secret": {
                "variants": ["routed", "routed"],
                "rank_ends": ["t", "b"],
                "top_signoff": "signed_off",
                "bottom_signoff": "signed_off",
            }
        }
    )
    rendered = json.dumps(totals, sort_keys=True)
    assert "secret" not in rendered
    assert set(json.loads(rendered)["signoff_top"]) == {"signed_off"}


def test_cohort_fingerprint_is_deterministic_and_sensitive(tmp_path: Path) -> None:
    first = tmp_path / "first.kicad_pcb"
    first.write_bytes(b"(kicad_pcb (version 20260206))\n")
    content, digest = read_board_bytes(first)
    assert content.startswith(b"(kicad_pcb")
    entries = [("first.kicad_pcb", digest)]
    assert cohort_fingerprint(entries) == cohort_fingerprint(list(entries))
    first.write_bytes(b"(kicad_pcb (version 20260206))\n ")
    _, drifted = read_board_bytes(first)
    assert cohort_fingerprint([("first.kicad_pcb", drifted)]) != cohort_fingerprint(entries)
    empty = tmp_path / "empty.kicad_pcb"
    empty.write_bytes(b"")
    with pytest.raises(ValueError, match="outside the supported range"):
        read_board_bytes(empty)


def test_board_eligibility_is_plain_drc_with_nothing_skipped() -> None:
    assert board_eligible_for_signoff(0, 0) is True
    assert board_eligible_for_signoff(5, 0) is False
    assert board_eligible_for_signoff(0, 1) is False


def test_predeclared_constants_match_the_issue() -> None:
    assert VARIANT_SEEDS == (101, 202, 303, 404)
    assert SIGNOFF_REPETITIONS == 2
    assert len(COHORT) == 6
    assert PILOT_COHORT == ("benchmarks/audio/fixtures/negotiated-crossing-v1.kicad_pcb",)
    for relative in COHORT:
        assert (ROOT / relative).is_file(), relative
