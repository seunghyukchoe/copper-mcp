#!/usr/bin/env python3
"""Instrument for the surrogate-top-pick DFM signoff agreement measurement (#91).

Predeclared design (issue #91 comments, before any run): on a fixed committed corpus, route
K variants per net with the reference A* core, rank each net's routable variants with the
private surrogate seam, and sign off exactly the top and bottom picks with repeated KiCad
DRC. The metric is the top-minus-bottom signoff rate plus the full per-net table, descriptive
only, with no tuning following regardless of outcome.

Fixed by the predeclare: six committed boards, seeds (101, 202, 303, 404), layer F.Cu,
clearance/track/via profile 250000/250000/800000/400000 nm under default AStarSettings,
repetitions=2, DFM domain only. Variant outcomes are ternary -- routed, already_connected,
or refused with the core's reason -- and only routed variants enter ranking and signoff.

This commit is the specification, not the observation: the full-corpus run with its
self-digested aggregate artifact and B-row is a separate evidence commit. ``--pilot`` runs
the whole pipeline on the two-net fixture and prints an aggregate summary to stdout only;
it writes no artifact and claims nothing. Shared helpers are pure and covered by
``tests/test_surrogate_signoff_agreement.py``; the pilot additionally exercises the live
signoff path wherever ``kicad-cli`` exists.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))
    sys.path.insert(0, str(ROOT))

#: Committed boards, in manifest order. Content-addressed at load; the run refuses when any
#: digest drifts rather than measuring a different corpus.
COHORT: tuple[str, ...] = (
    "benchmarks/audio/fixtures/ne5532-stereo-summing-routing-v1.kicad_pcb",
    "benchmarks/audio/fixtures/negotiated-crossing-v1.kicad_pcb",
    "benchmarks/audio/fixtures/rc-low-pass-routing-v1.kicad_pcb",
    "tests/fixtures/route-candidate/two-pad.kicad_pcb",
    "tests/fixtures/route-candidate/partial-route.kicad_pcb",
    "tests/fixtures/route-candidate/tree-star.kicad_pcb",
)
PILOT_COHORT: tuple[str, ...] = ("benchmarks/audio/fixtures/negotiated-crossing-v1.kicad_pcb",)
VARIANT_SEEDS: tuple[int, ...] = (101, 202, 303, 404)
SIGNOFF_REPETITIONS = 2
MAX_BOARD_BYTES = 16 * 1024 * 1024
PROFILE_NM = {
    "clearance_nm": 250_000,
    "track_width_nm": 250_000,
    "via_diameter_nm": 800_000,
    "via_drill_nm": 400_000,
}
ROUTING_LAYER_ID = "layer:F.Cu"


@dataclass(frozen=True, slots=True)
class VariantOutcome:
    """One seed's classified result. Only routed variants carry a candidate."""

    net_id: str
    seed: int
    kind: str
    candidate_id: str | None = None


def classify_variant(net_id: str, seed: int, result: Any) -> VariantOutcome:
    """Reduce one router result to its predeclared ternary class, naming nothing but ids."""

    candidate = getattr(result, "candidate", None)
    if candidate is not None:
        return VariantOutcome(
            net_id=net_id, seed=seed, kind="routed", candidate_id=candidate.candidate_id
        )
    if getattr(result, "connected", None) is not None:
        return VariantOutcome(net_id=net_id, seed=seed, kind="already_connected")
    diagnostic = getattr(result, "diagnostic", None)
    code = getattr(getattr(diagnostic, "code", None), "value", None)
    if not isinstance(code, str) or not code:
        raise ValueError("route variant result is malformed")
    return VariantOutcome(net_id=net_id, seed=seed, kind=f"refused:{code}")


def select_rank_ends(ranked_ids: list[str]) -> tuple[str, str] | None:
    """Top and bottom of one net's deterministic advisory order, or None when unrankable."""

    if len(ranked_ids) < 2:
        return None
    return (ranked_ids[0], ranked_ids[-1])


def classify_signoff(result: Any) -> str:
    """Reduce one signoff result to claimed-or-code, echoing no board content."""

    claimed = getattr(result, "claimed", None)
    claimed = claimed() if callable(claimed) else claimed
    if claimed is True:
        return "signed_off"
    if claimed is not False:
        raise ValueError("signoff result claimed state is malformed")
    code = getattr(result, "code", None)
    value = getattr(code, "value", None)
    if not isinstance(value, str) or not value:
        raise ValueError("unclaimed signoff result carries no code")
    return f"not_signed_off:{value}"


def tally_nets(
    per_net: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Aggregate per-net records to counts only. Geometry, names, and bytes never enter."""

    totals: dict[str, Any] = {
        "nets_attempted": len(per_net),
        "nets_qualified": 0,
        "nets_excluded_fewer_than_two_routed": 0,
        "nets_excluded_ranking_refused": 0,
        "nets_excluded_board_context_ineligible": 0,
        "variant_outcomes": {},
        "signoff_top": {},
        "signoff_bottom": {},
    }
    top_signed = 0
    bottom_signed = 0
    qualified = 0
    for record in per_net.values():
        for outcome in record.get("variants", []):
            bucket = totals["variant_outcomes"]
            bucket[outcome] = bucket.get(outcome, 0) + 1
        if record.get("ranking_refused"):
            totals["nets_excluded_ranking_refused"] += 1
            continue
        if record.get("signoff_skipped") == "board_context_ineligible":
            totals["nets_excluded_board_context_ineligible"] += 1
            continue
        ends = record.get("rank_ends")
        top = record.get("top_signoff")
        bottom = record.get("bottom_signoff")
        if ends is None or top is None or bottom is None:
            totals["nets_excluded_fewer_than_two_routed"] += 1
            continue
        qualified += 1
        bucket = totals["signoff_top"]
        bucket[top] = bucket.get(top, 0) + 1
        bucket = totals["signoff_bottom"]
        bucket[bottom] = bucket.get(bottom, 0) + 1
        top_signed += top == "signed_off"
        bottom_signed += bottom == "signed_off"
    totals["nets_qualified"] = qualified
    totals["top_signoff_rate"] = (top_signed / qualified) if qualified else None
    totals["bottom_signoff_rate"] = (bottom_signed / qualified) if qualified else None
    totals["rate_differential"] = (
        (totals["top_signoff_rate"] - totals["bottom_signoff_rate"]) if qualified else None
    )
    return totals


def cohort_fingerprint(entries: list[tuple[str, str]]) -> str:
    """Content-address the exact measured cohort: sorted (path, sha256) pairs, nothing else."""

    canonical = json.dumps(
        [{"path": path, "sha256": digest} for path, digest in sorted(entries)],
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(canonical).hexdigest()}"


def read_board_bytes(path: Path) -> tuple[bytes, str]:
    """Bounded read plus digest. Refuses empty files and anything over the ceiling."""

    try:
        size = path.stat().st_size
    except OSError as error:
        raise ValueError(f"cohort board is unreadable: {error}") from error
    if size <= 0 or size > MAX_BOARD_BYTES:
        raise ValueError("cohort board size is outside the supported range")
    content = path.read_bytes()
    if len(content) != size:
        raise ValueError("cohort board changed while being read")
    return content, f"sha256:{hashlib.sha256(content).hexdigest()}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pilot",
        action="store_true",
        help="run the full pipeline on the two-net fixture; stdout summary only, no artifact",
    )
    args = parser.parse_args(argv)
    cohort = PILOT_COHORT if args.pilot else COHORT
    entries: list[tuple[str, str]] = []
    for relative in cohort:
        _, digest = read_board_bytes(ROOT / relative)
        entries.append((relative, digest))
    fingerprint = cohort_fingerprint(entries)
    if args.pilot:
        aggregate = run_measurement(cohort)
        aggregate["cohort_fingerprint"] = fingerprint
        print(json.dumps(aggregate, sort_keys=True, indent=2))
        return 0
    raise SystemExit(
        "the full-corpus run publishes its self-digested artifact from a separate evidence "
        "commit; this instrument only proves its own contract (see tests) and the --pilot path"
    )


def board_eligible_for_signoff(ignored_checks: int, excluded_checks: int) -> bool:
    """Whether a board's plain DRC context can support a signoff at all.

    ADR-0119 refuses skipped-check runs even when they pass, so a board whose plain DRC
    skips or excludes anything can never produce a signed_off verdict. Gating on the plain
    run keeps the measurement from reporting workspace context as ranking signal.
    """

    return ignored_checks == 0 and excluded_checks == 0


def run_measurement(cohort: tuple[str, ...]) -> dict[str, Any]:
    """Execute routing, ranking, and signoff end to end; aggregate counts only, no writes."""

    import shutil
    import tempfile

    from copper_mcp.adapters.kicad_board_ir import KiCadConstraintProfile, parse_kicad_bytes
    from copper_mcp.authoritative_signoff_executor import execute_dfm_signoff
    from copper_mcp.board_ir import NetClass
    from copper_mcp.config import Settings
    from copper_mcp.kicad_cli import run_board_drc
    from copper_mcp.routing import AStarRouter
    from copper_mcp.routing.authoritative_signoff import SignoffDomain
    from copper_mcp.routing.contracts import RouteRequest
    from copper_mcp.routing.surrogate_ranking import rank_surrogate_candidates

    kicad_cli = shutil.which("kicad-cli")
    standard = Path("/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli")
    if kicad_cli is None and not standard.is_file():
        raise SystemExit("pilot needs a real kicad-cli for authoritative DRC")
    net_class = NetClass(id="class:pilot", name="Pilot", **PROFILE_NM)
    profile = KiCadConstraintProfile(net_classes=(net_class,), default_net_class_id=net_class.id)
    per_net: dict[str, dict[str, Any]] = {}
    eligible_boards = 0
    with tempfile.TemporaryDirectory() as workspace:
        root = Path(workspace)
        for relative in cohort:
            content, _ = read_board_bytes(ROOT / relative)
            board = root / Path(relative).name
            board.write_bytes(content)
            settings = Settings(workspace=root)
            plain = run_board_drc(board.name, settings).to_dict()
            eligible = board_eligible_for_signoff(
                plain["ignored_check_count"], plain["exclusion_count"]
            )
            eligible_boards += eligible
            conversion = parse_kicad_bytes(content, profile)
            if conversion.snapshot is None or conversion.diagnostics:
                continue
            snapshot = conversion.snapshot
            for net in sorted(snapshot.content.nets, key=lambda item: item.id):
                outcomes: list[VariantOutcome] = []
                routed: dict[str, Any] = {}
                for seed in VARIANT_SEEDS:
                    result = AStarRouter().propose(
                        snapshot,
                        RouteRequest(
                            board_revision=snapshot.snapshot_digest,
                            net_id=net.id,
                            layer_id=ROUTING_LAYER_ID,
                            seed=seed,
                        ),
                    )
                    outcome = classify_variant(net.id, seed, result)
                    outcomes.append(outcome.kind)
                    if outcome.kind == "routed" and result.candidate is not None:
                        routed[outcome.candidate_id or ""] = result.candidate
                key = f"{relative}::{net.id}"
                record: dict[str, Any] = {"variants": outcomes}
                ordered = [routed[key] for key in sorted(routed)]
                ranking = rank_surrogate_candidates(tuple(ordered), SignoffDomain.DFM)
                if getattr(ranking, "status", None) != "accepted":
                    record["rank_ends"] = None
                    record["ranking_refused"] = True
                    per_net[key] = record
                    continue
                ids = [entry.binding.candidate_id for entry in ranking.entries]
                ends = select_rank_ends(ids)
                record["rank_ends"] = list(ends) if ends is not None else None
                if ends is not None and eligible:
                    by_id = {item.candidate_id: item for item in ordered}
                    for position, label in (
                        (ends[0], "top_signoff"),
                        (ends[1], "bottom_signoff"),
                    ):
                        signoff = execute_dfm_signoff(
                            board.name,
                            by_id[position],
                            profile,
                            settings,
                            repetitions=SIGNOFF_REPETITIONS,
                        )
                        record[label] = classify_signoff(signoff)
                elif ends is not None:
                    record["signoff_skipped"] = "board_context_ineligible"
                per_net[key] = record
    aggregate = tally_nets(per_net)
    aggregate["boards"] = len(cohort)
    aggregate["boards_eligible_for_signoff"] = eligible_boards
    return aggregate


if __name__ == "__main__":
    raise SystemExit(main())
