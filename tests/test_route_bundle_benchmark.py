from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import copper_mcp
from copper_mcp.config import Settings
from copper_mcp.kicad_cli import KiCadCliError
from scripts import benchmark_route_bundle as benchmark


def test_committed_route_bundle_artifact_binds_its_inputs_and_clean_kicad_evidence() -> None:
    report = json.loads(benchmark.OUTPUT.read_text(encoding="utf-8"))
    without_run_id = dict(report)
    run_id = without_run_id.pop("run_id")

    assert (
        run_id
        == "sha256:"
        + hashlib.sha256(
            json.dumps(
                without_run_id, sort_keys=True, separators=(",", ":"), allow_nan=False
            ).encode()
        ).hexdigest()
    )
    # CopperMCP writes ``(generator_version "<package version>")`` into every board it renders,
    # so ``authoritative_kicad_drc.base_revision`` below is only reproducible by the exact
    # version that recorded it.  This artifact is therefore regenerated in the release commit
    # that bumps the version, and both assertions must keep agreeing: the literal names the
    # release the measurement belongs to, and the equality proves the shipping source can still
    # reproduce its own committed evidence.
    assert report["copper_mcp_version"] == "0.7.0"
    assert report["copper_mcp_version"] == copper_mcp.__version__
    assert report["fixture"] == benchmark.FIXTURE.relative_to(benchmark.ROOT).as_posix()
    assert report["fixture_sha256"] == hashlib.sha256(benchmark.FIXTURE.read_bytes()).hexdigest()
    assert report["script"] == benchmark.SCRIPT.as_posix()
    assert (
        report["script_sha256"]
        == hashlib.sha256((benchmark.ROOT / benchmark.SCRIPT).read_bytes()).hexdigest()
    )
    assert report["license"] == "Apache-2.0"
    assert report["fixture_origin"] == "CopperMCP-original committed public fixture"
    assert report["source_unchanged"] is True
    assert report["candidate_applied"] is False
    assert report["baseline"]["same_base_independent_overflow_units"] == 1
    assert report["bundle"]["candidate_count"] == 2
    assert report["bundle"]["core_replays"] == 1
    assert report["bundle"]["physical_pair_checks"] == 3
    assert report["bundle"]["overflow_units"] == 0
    assert report["metric"] == {"overflow_reduction_units": 1, "improved": True}
    authority = report["authoritative_kicad_drc"]
    assert authority["status"] == "completed"
    assert authority["execution"] == "copper_mcp.kicad_cli.run_board_drc"
    source = benchmark.FIXTURE.read_bytes()
    conversion = benchmark.parse_kicad_bytes(source, benchmark._profile())
    assert conversion.snapshot is not None
    with tempfile.TemporaryDirectory(prefix="copper-mcp-route-bundle-test-") as temporary:
        workspace = Path(temporary)
        board = workspace / benchmark.FIXTURE.name
        board.write_bytes(source)
        result = benchmark.preview_route_bundle(
            benchmark._payload(board.name, source, conversion.snapshot.snapshot_digest),
            Settings(workspace=workspace),
        )
        assert result.plan is not None
        rendered = benchmark.render_kicad_route_bundle_board(
            source, conversion.snapshot, result.plan, benchmark._profile()
        )
    assert authority["base_revision"] == f"sha256:{hashlib.sha256(rendered).hexdigest()}"
    assert authority["drc_context_revision"].startswith("sha256:")
    assert authority["exit_code"] == 0
    assert authority["error_count"] == 0
    assert authority["unconnected_count"] == 0
    assert authority["passed"] is True
    assert authority["kicad_version"] == "10.0.5"
    assert authority["executable_sha256"].startswith("sha256:")


def test_benchmark_core_replay_stays_useful_when_kicad_evidence_is_unavailable() -> None:
    unavailable = {"status": "unavailable", "reason": "bounded KiCad DRC evidence is unavailable"}
    with patch.object(benchmark, "_drc", return_value=unavailable):
        report = benchmark.build_report()

    assert report["authoritative_kicad_drc"] == unavailable
    assert report["bundle"]["candidate_count"] == 2
    assert report["metric"] == {"overflow_reduction_units": 1, "improved": True}


def test_missing_kicad_fails_closed_without_synthesizing_drc_evidence(tmp_path: Path) -> None:
    board = tmp_path / "derivative.kicad_pcb"
    board.write_bytes(b"placeholder")
    with (
        patch.object(benchmark, "discover_kicad_cli", side_effect=KiCadCliError("missing")),
        patch.object(benchmark, "run_board_drc", side_effect=AssertionError("must not run")),
    ):
        result = benchmark._drc(board)

    assert result == {
        "status": "unavailable",
        "reason": "bounded KiCad DRC evidence is unavailable",
    }
