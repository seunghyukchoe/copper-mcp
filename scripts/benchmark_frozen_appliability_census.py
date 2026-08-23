#!/usr/bin/env python3
"""Run the frozen, read-only appliability census.

This is deliberately smaller than the historical tier-2 runner: it measures only the two
source-preserving gates and emits aggregate evidence suitable for a public result.  The corpus is
operator-owned and is never copied into the report.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from copper_mcp.adapters.kicad_placement_patch import (
    KiCadPlacementPatchError,
    render_kicad_placement_candidate_board,
)
from copper_mcp.adapters.kicad_route_patch import (
    KiCadRoutePatchError,
    _require_native_geometry_identities,
)
from copper_mcp.config import Settings
from copper_mcp.parse_budgets import parse_limits_for
from copper_mcp.placement.contracts import parse_placement_intent
from copper_mcp.placement_preview import _preview_placement_source
from copper_mcp.security import read_workspace_file
from scripts.benchmark_real_board_capability import CONSTRAINTS, DERIVED_STEMS, _convert

EXPECTED_COHORT = 18
PREDECLARED_CORPUS_FINGERPRINT = "sha256:afe5d9d09b4aa89ffa8d5ae84df284ee"


@dataclass(frozen=True, slots=True)
class Snapshot:
    path: Path
    relative: str
    source: bytes
    digest: str


def select_frozen_corpus(corpus: Path, *, superseded_phono_v2: Iterable[str] = ()) -> list[Path]:
    """Select the B-112/B-117 cohort without accepting a moving denominator.

    The two superseded names are explicit input because they are corpus facts, not a filename
    convention.  They must both be under ``phono-v2/pcb``; three derived stems and history are
    excluded by the existing runner rules.
    """

    supplied = list(superseded_phono_v2)
    superseded = {Path(name).as_posix() for name in supplied}
    if len(supplied) != 2 or len(superseded) != 2:
        raise ValueError("exactly two superseded phono-v2 saves are required")
    prefix = "phono-v2/pcb/"
    for name in superseded:
        relative = Path(name)
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or not name.startswith(prefix)
            or relative.suffix != ".kicad_pcb"
        ):
            raise ValueError(
                "superseded saves must be relative .kicad_pcb files under phono-v2/pcb"
            )
    all_candidates = sorted(
        path
        for path in corpus.rglob("*.kicad_pcb")
        if ".history" not in path.parts and not any(stem in path.name for stem in DERIVED_STEMS)
    )
    available = {path.relative_to(corpus).as_posix() for path in all_candidates}
    if not superseded <= available:
        raise ValueError("superseded saves must exist in phono-v2/pcb")
    candidates = [
        path for path in all_candidates if path.relative_to(corpus).as_posix() not in superseded
    ]
    if len(candidates) != EXPECTED_COHORT:
        raise ValueError(f"frozen cohort must contain {EXPECTED_COHORT} saves")
    return candidates


def _snapshot(paths: Iterable[Path], corpus: Path, *, max_bytes: int) -> list[Snapshot]:
    return [
        Snapshot(
            path,
            path.relative_to(corpus).as_posix(),
            source,
            hashlib.sha256(source).hexdigest(),
        )
        for path in paths
        for source in (
            read_workspace_file(
                corpus,
                path.relative_to(corpus).as_posix(),
                allowed_suffixes={".kicad_pcb"},
                max_bytes=max_bytes,
            ).content,
        )
    ]


def _route_gate(conversion: Any) -> str:
    if conversion.snapshot is None:
        return "conversion_refused"
    try:
        _require_native_geometry_identities(conversion.snapshot)
    except KiCadRoutePatchError:
        return "route_identity_refused"
    return "appliable"


def _fingerprint(snapshots: Iterable[Snapshot]) -> str:
    aggregate = hashlib.sha256(
        "".join(f"{item.relative}:{item.digest}\n" for item in snapshots).encode()
    ).hexdigest()[:32]
    return f"sha256:{aggregate}"


def _placement_gate(snapshot: Snapshot, conversion: Any, settings: Settings) -> str:
    if conversion.snapshot is None:
        return "conversion_refused"
    content = conversion.snapshot.content
    subjects = sorted(item.id for item in content.footprints if getattr(item, "pad_ids", ()))[:1]
    if not subjects:
        return "placement_no_candidate"
    request = {"board": snapshot.relative, "constraints": dict(CONSTRAINTS), "subjects": subjects}
    try:
        intent = parse_placement_intent(
            request,
            max_subjects=settings.max_placement_subjects,
            max_rules=settings.max_placement_rules,
        )
        result = _preview_placement_source(
            intent,
            snapshot.source,
            snapshot.relative,
            f"sha256:{snapshot.digest}",
            settings,
            token_authority=None,
            mints_apply_tokens=False,
        )
    except Exception:
        return "measurement_error"
    if result.status != "previewed" or result.candidate is None:
        return "placement_no_candidate"
    try:
        render_kicad_placement_candidate_board(
            snapshot.source,
            conversion.snapshot,
            result.candidate,
            intent.profile(),
            limits=parse_limits_for(settings),
        )
    except KiCadPlacementPatchError:
        return "placement_source_preservation_refused"
    except Exception:
        return "measurement_error"
    return "appliable"


def measure_frozen_corpus(
    corpus: Path,
    settings: Settings,
    *,
    superseded: Iterable[str],
    expected_fingerprint: str = PREDECLARED_CORPUS_FINGERPRINT,
) -> dict[str, Any]:
    paths = select_frozen_corpus(corpus, superseded_phono_v2=superseded)
    snapshots = _snapshot(paths, corpus, max_bytes=settings.max_board_bytes)
    if settings.allow_apply or settings.allow_live_ipc or settings.allow_live_apply:
        raise RuntimeError("read-only census requires apply and live IPC flags to remain disabled")
    if (
        len(expected_fingerprint) != 39
        or not expected_fingerprint.startswith("sha256:")
        or any(character not in "0123456789abcdef" for character in expected_fingerprint[7:])
    ):
        raise ValueError("predeclared corpus fingerprint is malformed")
    actual_fingerprint = _fingerprint(snapshots)
    if actual_fingerprint != expected_fingerprint:
        raise RuntimeError("frozen corpus fingerprint does not match the predeclared cohort")
    route_counts: dict[str, int] = {}
    placement_counts: dict[str, int] = {}
    for item in snapshots:
        try:
            conversion = _convert(item.source, item.relative, settings)
        except Exception:
            route = placement = "measurement_error"
        else:
            try:
                route = _route_gate(conversion)
            except Exception:
                route = "measurement_error"
            try:
                placement = _placement_gate(item, conversion, settings)
            except Exception:
                placement = "measurement_error"
        route_counts[route] = route_counts.get(route, 0) + 1
        placement_counts[placement] = placement_counts.get(placement, 0) + 1
    unchanged = all(
        hashlib.sha256(
            read_workspace_file(
                corpus,
                item.relative,
                allowed_suffixes={".kicad_pcb"},
                max_bytes=settings.max_board_bytes,
            ).content
        ).hexdigest()
        == item.digest
        for item in snapshots
    )
    if not unchanged:
        raise RuntimeError("frozen corpus changed during measurement")
    return {
        "cohort_count": len(snapshots),
        "source_hashes_unchanged": unchanged,
        "corpus_fingerprint": actual_fingerprint,
        "route_gate": dict(sorted(route_counts.items())),
        "placement_gate": dict(sorted(placement_counts.items())),
    }


def _git_state(root: Path) -> tuple[str, bool]:
    commit = subprocess.run(  # noqa: S603
        ["git", "-C", str(root), "rev-parse", "HEAD"],  # noqa: S607
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    dirty = bool(
        subprocess.run(  # noqa: S603
            ["git", "-C", str(root), "status", "--porcelain"],  # noqa: S607
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    )
    return commit, dirty


def _runner_bytes(path: Path) -> bytes:
    return path.read_bytes()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--superseded", required=True, action="append", help="relative path; repeat exactly twice"
    )
    args = parser.parse_args()
    corpus = args.corpus.expanduser().resolve(strict=True)
    output = args.output.expanduser().resolve()
    if output == corpus or corpus in output.parents:
        raise SystemExit("output must be outside the corpus")
    settings = Settings(workspace=corpus)
    if settings.allow_apply or settings.allow_live_ipc or settings.allow_live_apply:
        raise SystemExit("read-only census requires apply and live IPC flags to remain disabled")
    root = Path(__file__).resolve().parents[1]
    runner_path = Path(__file__).resolve()
    commit, dirty = _git_state(root)
    if dirty:
        raise SystemExit("measurement worktree must start clean")
    runner_source = _runner_bytes(runner_path)
    result = measure_frozen_corpus(corpus, settings, superseded=args.superseded)
    final_commit, final_dirty = _git_state(root)
    if final_commit != commit:
        raise SystemExit("measurement commit changed during run")
    if final_dirty:
        raise SystemExit("measurement worktree became dirty during run")
    if _runner_bytes(runner_path) != runner_source:
        raise SystemExit("runner source changed during run")
    result.update({"benchmark": "frozen-appliability-census-v1", "commit": commit, "dirty": dirty})
    result["runner_digest"] = "sha256:" + hashlib.sha256(runner_source).hexdigest()
    payload = json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False)
    result["run_id"] = "sha256:" + hashlib.sha256(payload.encode()).hexdigest()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
