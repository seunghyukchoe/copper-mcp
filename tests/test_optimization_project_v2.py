"""Captured project workflow controls with explicitly synthetic native observations."""

import asyncio
import hashlib

import pytest
from test_optimization_coordinator import run
from test_optimization_coordinator import synthetic_authority as synthetic_authority
from test_optimization_inputs import launch as launch
from test_optimization_mcp import app as app
from test_optimization_mcp import completed_search, modern_wire_call
from test_project_erc import build_project

from copper_mcp.config import Settings
from copper_mcp.engineering.erc_profile import OUTSIDE_CONNECTIVITY_SCOPE
from copper_mcp.engineering.project_board_parity import (
    ProjectBoardParityReport,
    ProjectParitySample,
)
from copper_mcp.engineering.project_erc import (
    ProjectConnectivityErcReport,
    ProjectErcError,
    ProjectErcSample,
)
from copper_mcp.optimization import project_evidence
from copper_mcp.optimization.contracts import OptimizationError
from copper_mcp.optimization.inputs import prepare_optimization

DIGEST = "sha256:" + "a" * 64


@pytest.fixture
def project_launch(launch, tmp_path):
    capture, libraries, context = build_project(tmp_path)
    library = libraries[0]
    (tmp_path / "library.kicad_sym").write_bytes(library.content)
    declaration = {
        "root_path": capture.root_path,
        "files": [{"path": row.path, "digest": row.digest} for row in capture._files],
        "libraries": [
            {"name": library.name, "path": "library.kicad_sym", "digest": library.digest}
        ],
    }
    return {**launch, "schema_version": "optimization/v2", "project": declaration}, capture, context


def _observe(monkeypatch, *, erc_status="pass", parity_status="pass", capture_fault=False):
    calls = []

    def erc(capture, libraries, settings, *, deadline):
        calls.append(("erc", capture.digest, deadline))
        if erc_status == "unavailable":
            raise ProjectErcError("owned unavailable profile")
        sample = ProjectErcSample(
            DIGEST,
            int(erc_status == "fail"),
            0,
            0,
            tuple(sorted(OUTSIDE_CONNECTIVITY_SCOPE)),
            1,
            (),
        )
        return ProjectConnectivityErcReport(
            "sha256:" + "b" * 64 if capture_fault else capture.digest,
            DIGEST,
            DIGEST,
            DIGEST,
            DIGEST,
            DIGEST,
            DIGEST,
            1,
            "10.0.5",
            (sample, sample),
            (),
            0,
        )

    def parity(capture, libraries, source, expected, settings, *, deadline):
        assert expected == "sha256:" + hashlib.sha256(source).hexdigest()
        calls.append(("parity", capture.digest, deadline))
        sample = ProjectParitySample(
            DIGEST, (("test_mismatch", 1),) if parity_status == "fail" else (), 0, 0
        )
        return ProjectBoardParityReport(
            capture.digest,
            DIGEST,
            DIGEST,
            expected,
            "10.0.5",
            DIGEST,
            DIGEST,
            DIGEST,
            DIGEST,
            (sample, sample),
        )

    monkeypatch.setattr(project_evidence, "run_project_erc", erc)
    monkeypatch.setattr(project_evidence, "run_project_board_parity", parity)
    return calls


def test_project_required_domains_are_derived_and_evidence_is_separate(
    project_launch, tmp_path, synthetic_authority, monkeypatch
):
    launch, capture, context = project_launch
    launch["required_domains"] = []
    prepared = prepare_optimization(launch, Settings(workspace=tmp_path))
    assert prepared.request.required_domains == ("DRC", "ERC", "DFM")
    calls = _observe(monkeypatch)
    record, retained, package = run(launch, tmp_path)
    assert record.status == "awaiting_approval", record.failure_code
    evidence = package.judge.project_evidence
    assert evidence.capture_digest == capture.digest
    assert evidence.candidate_board_revision == package.binding.candidate_board_revision
    assert evidence.erc_status == evidence.parity_status == "pass"
    assert package.judge.project_required and retained
    assert [item[0] for item in calls] == ["erc", "parity"]
    assert calls[0][2] == calls[1][2]
    assert all((tmp_path / path).read_bytes() == data for path, data in context.items())


@pytest.mark.parametrize(
    "erc_status,parity_status", [("fail", "pass"), ("pass", "fail"), ("unavailable", "pass")]
)
def test_project_failure_or_unavailable_cannot_be_reviewed(
    project_launch, tmp_path, synthetic_authority, monkeypatch, erc_status, parity_status
):
    launch, _, _ = project_launch
    _observe(monkeypatch, erc_status=erc_status, parity_status=parity_status)
    record, retained, package = run(launch, tmp_path)
    assert record.status == "failed"
    assert record.failure_code in {"required_domain_inconclusive", "judge_failed"}
    assert not retained and package is None
    assert record.comparison.outcomes[0].metrics is None


@pytest.mark.parametrize("field", ["files", "libraries"])
def test_project_capture_drift_refuses_before_native(project_launch, tmp_path, monkeypatch, field):
    launch, _, _ = project_launch
    path = launch["project"][field][0]["path"]
    (tmp_path / path).write_bytes((tmp_path / path).read_bytes() + b"\n")
    monkeypatch.setattr(
        project_evidence,
        "run_project_erc",
        lambda *args, **kwargs: pytest.fail("stale capture reached native"),
    )
    with pytest.raises(OptimizationError):
        prepare_optimization(launch, Settings(workspace=tmp_path))


def test_project_caller_cannot_supply_observations_or_executable(project_launch, tmp_path):
    launch, _, _ = project_launch
    for extra in (
        {"erc_report": {}},
        {"kicad_cli": "/caller/cli"},
        {"project": {"report_digest": DIGEST}},
    ):
        with pytest.raises(OptimizationError):
            prepare_optimization({**launch, **extra}, Settings(workspace=tmp_path))


def test_project_mixed_capture_report_is_refused(
    project_launch, tmp_path, synthetic_authority, monkeypatch
):
    launch, _, _ = project_launch
    _observe(monkeypatch, capture_fault=True)
    record, retained, package = run(launch, tmp_path)
    assert record.status != "awaiting_approval" and not retained and package is None


def test_project_metadata_exports_through_current_mcp_protocol(
    app, project_launch, synthetic_authority, monkeypatch
):
    launch, capture, _ = project_launch
    _observe(monkeypatch)
    status = completed_search(app, launch)
    record = status["record"]
    assert record["status"] == "awaiting_approval"
    result = asyncio.run(
        modern_wire_call(
            app[0],
            "export_optimization_package",
            {
                "job_id": record["job_id"],
                "expected_record_revision": record["revision"],
                "expected_package_digest": record["package_digest"],
            },
        )
    )
    package = result.structured_content["package"]
    assert package["judge"]["project_required"]
    assert package["judge"]["project_evidence"]["capture_digest"] == capture.digest
    assert (
        package["judge"]["project_evidence"]["candidate_board_revision"]
        == package["binding"]["candidate_board_revision"]
    )
    assert result.structured_content["geometry_disclosure"] == "not_disclosed"


@pytest.mark.parametrize("which", ["files", "libraries"])
def test_project_changed_after_delivery_refuses_host_approval(
    app, project_launch, tmp_path, synthetic_authority, monkeypatch, which
):
    launch, _, _ = project_launch
    _observe(monkeypatch)
    record = completed_search(app, launch)["record"]
    assert record["status"] == "awaiting_approval"
    service, owner = app[1].service()
    artifact = tmp_path / launch["project"][which][0]["path"]
    artifact.write_bytes(artifact.read_bytes() + b"\n")
    monkeypatch.setattr(
        service.authority,
        "issue_from_human_channel",
        lambda *args, **kwargs: pytest.fail("stale project reached consent issuance"),
    )
    with pytest.raises((OptimizationError, ValueError)):
        service.approve_from_host(
            record["job_id"],
            owner,
            record["revision"],
            record["package_digest"],
            record["judge_digest"],
        )
    assert service.repository.get(record["job_id"], owner).status == "awaiting_approval"
