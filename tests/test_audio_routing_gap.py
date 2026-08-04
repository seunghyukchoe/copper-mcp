from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

import pytest

from scripts.benchmark_audio_routing_gap import (
    FIXTURE,
    build_report,
    run_benchmark,
)


def test_audio_microcase_serializes_a_route_candidate_as_new_copper() -> None:
    metrics = run_benchmark(3)

    assert metrics["fixture_origin"] == "coppermcp-original"
    assert metrics["fixture_license_spdx"] == "Apache-2.0"
    assert metrics["fixture_source_sha256"] == hashlib.sha256(FIXTURE.read_bytes()).hexdigest()
    assert metrics["deterministic_candidate_id"] is True
    assert metrics["deterministic_derivative_bytes"] is True
    assert metrics["original_segment_count"] == 0
    assert metrics["rendered_segment_count"] == 1
    assert metrics["new_copper_segment_count"] == 1
    assert metrics["candidate_edge_count"] == 1
    assert metrics["new_copper_length_nm"] == 8_000_000
    assert metrics["candidate_wire_length_nm"] == metrics["new_copper_length_nm"]
    assert metrics["source_unchanged"] is True
    assert metrics["candidate_applied"] is False
    assert metrics["authoritative_drc_performed"] is False


def test_audio_routing_gap_report_is_content_addressed_for_a_fixed_timestamp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "scripts.benchmark_audio_routing_gap._git_commit",
        lambda: "test-commit",
    )
    timestamp = datetime(2026, 8, 5, tzinfo=UTC)

    first = build_report(2, timestamp=timestamp)
    second = build_report(2, timestamp=timestamp)

    assert first == second
    canonical = dict(first)
    run_id = canonical.pop("run_id")
    expected = hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()
    assert run_id == f"sha256:{expected}"
