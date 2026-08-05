from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "benchmarks" / "audio" / "catalog.json"
CHECKER = ROOT / "scripts" / "check_audio_benchmarks.py"
RUNNER = ROOT / "scripts" / "run_audio_benchmarks.py"
SCHEMA = ROOT / "schemas" / "audio-benchmark-catalog" / "0.1.0.schema.json"
RC_FIXTURE = ROOT / "benchmarks" / "audio" / "fixtures" / "rc-low-pass-routing-v1.kicad_pcb"
NE5532_FIXTURE = (
    ROOT / "benchmarks" / "audio" / "fixtures" / "ne5532-stereo-summing-routing-v1.kicad_pcb"
)
COPPERTONE = ROOT / "hardware" / "coppertone-buffer" / "coppertone-buffer.kicad_pcb"
REAL_KICAD_CLI = Path("/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli")


def _run(
    script: Path,
    *arguments: str,
    pythonpath_prefix: Path | None = None,
    extra_environment: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    python_paths = [str(ROOT / "src")]
    if pythonpath_prefix is not None:
        python_paths.insert(0, str(pythonpath_prefix))
    environment["PYTHONPATH"] = os.pathsep.join(python_paths)
    if extra_environment is not None:
        environment.update(extra_environment)
    return subprocess.run(  # noqa: S603 - fixed interpreter and repository script paths
        [sys.executable, str(script), *arguments],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )


def _catalog() -> dict[str, Any]:
    document = json.loads(CATALOG.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return document


def _fixture(document: dict[str, Any], identifier: str) -> dict[str, Any]:
    fixture = next(item for item in document["fixtures"] if item["id"] == identifier)
    assert isinstance(fixture, dict)
    return fixture


def _write_catalog(tmp_path: Path, document: dict[str, Any]) -> Path:
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def _copy_catalog_root(tmp_path: Path, document: dict[str, Any]) -> tuple[Path, Path]:
    root = tmp_path / "repository"
    schema = root / SCHEMA.relative_to(ROOT)
    schema.parent.mkdir(parents=True)
    schema.write_bytes(SCHEMA.read_bytes())
    for fixture in document["fixtures"]:
        for key in ("artifact_path", "license_path"):
            source = ROOT / fixture[key]
            destination = root / fixture[key]
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(source.read_bytes())
    catalog = root / "benchmarks" / "audio" / "catalog.json"
    catalog.parent.mkdir(parents=True, exist_ok=True)
    catalog.write_text(json.dumps(document), encoding="utf-8")
    return root, catalog


def _write_sitecustomize(tmp_path: Path, source: str) -> Path:
    directory = tmp_path / "python-hook"
    directory.mkdir()
    (directory / "sitecustomize.py").write_text(source, encoding="utf-8")
    return directory


def test_catalog_and_capability_run_are_valid_and_deterministic() -> None:
    first = _run(CHECKER)
    second = _run(RUNNER)
    third = _run(RUNNER)

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert third.returncode == 0, third.stderr
    assert second.stdout == third.stdout

    report = json.loads(second.stdout)
    assert report["network_access"] is False
    assert report["source_mutation"] is False
    assert all(reference["executed"] is False for reference in report["external_references"])
    fixtures = {fixture["fixture_id"]: fixture for fixture in report["fixtures"]}
    assert fixtures["rc-low-pass-routing-v1"]["inspection"]["supported"] is True
    # All three nets now route: the two multi-pin ones as trees rather than as refusals.
    assert [route["status"] for route in fixtures["rc-low-pass-routing-v1"]["routes"]] == [
        "routed",
        "routed",
        "routed",
    ]
    assert [route["pad_count"] for route in fixtures["rc-low-pass-routing-v1"]["routes"]] == [
        2,
        3,
        3,
    ]
    assert fixtures["coppertone-buffer-preview-v1"]["inspection"]["supported"] is True


def test_catalog_rejects_a_tampered_artifact_digest(tmp_path: Path) -> None:
    document = _catalog()
    document["fixtures"][0]["artifact_sha256"] = "0" * 64
    catalog = _write_catalog(tmp_path, document)

    result = _run(CHECKER, "--catalog", str(catalog), "--root", str(ROOT))

    assert result.returncode != 0
    assert "artifact_sha256 does not match" in result.stderr


def test_catalog_rejects_a_tampered_license_digest(tmp_path: Path) -> None:
    document = _catalog()
    document["fixtures"][0]["license_sha256"] = "0" * 64
    catalog = _write_catalog(tmp_path, document)

    result = _run(CHECKER, "--catalog", str(catalog), "--root", str(ROOT))

    assert result.returncode != 0
    assert "license_sha256" in result.stderr


def test_catalog_binds_the_declared_license_to_exact_bytes(tmp_path: Path) -> None:
    document = _catalog()
    root, catalog = _copy_catalog_root(tmp_path, document)
    license_path = root / document["fixtures"][0]["license_path"]
    license_path.write_bytes(license_path.read_bytes() + b"\nmodified after review\n")

    result = _run(CHECKER, "--catalog", str(catalog), "--root", str(root))

    assert result.returncode != 0
    assert "license_sha256" in result.stderr


def test_catalog_rejects_license_evidence_for_a_different_spdx_identity(
    tmp_path: Path,
) -> None:
    document = _catalog()
    fixture = document["fixtures"][0]
    cern_license = ROOT / _fixture(document, "coppertone-buffer-preview-v1")["license_path"]
    fixture["license_path"] = str(cern_license.relative_to(ROOT))
    fixture["license_sha256"] = hashlib.sha256(cern_license.read_bytes()).hexdigest()
    catalog = _write_catalog(tmp_path, document)

    result = _run(CHECKER, "--catalog", str(catalog), "--root", str(ROOT))

    assert result.returncode != 0
    assert "license_spdx does not match licence evidence" in result.stderr


def test_catalog_and_artifact_reads_are_bounded(tmp_path: Path) -> None:
    oversized_catalog = tmp_path / "oversized-catalog.json"
    oversized_catalog.write_bytes(b" " * 256_001)
    catalog_result = _run(
        CHECKER,
        "--catalog",
        str(oversized_catalog),
        "--root",
        str(ROOT),
    )

    document = _catalog()
    root, catalog = _copy_catalog_root(tmp_path / "artifact-case", document)
    artifact = root / document["fixtures"][0]["artifact_path"]
    artifact.write_bytes(b"x" * 2_000_001)
    artifact_result = _run(CHECKER, "--catalog", str(catalog), "--root", str(root))

    assert catalog_result.returncode != 0
    assert "audio benchmark catalog exceeds 256000 bytes" in catalog_result.stderr
    assert artifact_result.returncode != 0
    assert "fixture artifact_path exceeds 2000000 bytes" in artifact_result.stderr


def test_catalog_rejects_redistribution_claims_for_reference_only_sources(
    tmp_path: Path,
) -> None:
    document = _catalog()
    document["external_references"][0]["redistribution_allowed"] = True
    catalog = _write_catalog(tmp_path, document)

    result = _run(CHECKER, "--catalog", str(catalog), "--root", str(ROOT))

    assert result.returncode != 0
    assert "schema violation" in result.stderr


def test_catalog_rejects_duplicate_ids_and_path_escape(tmp_path: Path) -> None:
    duplicate = _catalog()
    duplicate["fixtures"][1]["id"] = duplicate["fixtures"][0]["id"]
    duplicate_result = _run(
        CHECKER,
        "--catalog",
        str(_write_catalog(tmp_path, duplicate)),
        "--root",
        str(ROOT),
    )

    escaped = _catalog()
    escaped["fixtures"][0]["artifact_path"] = "../private.kicad_pcb"
    escaped_result = _run(
        CHECKER,
        "--catalog",
        str(_write_catalog(tmp_path, escaped)),
        "--root",
        str(ROOT),
    )

    assert duplicate_result.returncode != 0
    assert "IDs must be unique" in duplicate_result.stderr
    assert escaped_result.returncode != 0
    assert "schema violation" in escaped_result.stderr


def test_catalog_rejects_url_credentials(tmp_path: Path) -> None:
    document = _catalog()
    document["external_references"][0]["index_url"] = "https://user:pass@example.com/catalog"
    catalog = _write_catalog(tmp_path, document)

    result = _run(CHECKER, "--catalog", str(catalog), "--root", str(ROOT))

    assert result.returncode != 0
    assert "without credentials" in result.stderr


def test_catalog_rejects_claims_that_are_also_disclaimed(tmp_path: Path) -> None:
    document = _catalog()
    claim = document["fixtures"][0]["claims"][0]
    document["fixtures"][0]["not_claimed"].append(claim)
    catalog = _write_catalog(tmp_path, document)

    result = _run(CHECKER, "--catalog", str(catalog), "--root", str(ROOT))

    assert result.returncode != 0
    assert "claims" in result.stderr
    assert "not_claimed" in result.stderr


@pytest.mark.parametrize("claim", ["two-pin-route-preview", "typed-route-refusal"])
def test_catalog_rejects_route_claims_without_declared_routes(
    tmp_path: Path,
    claim: str,
) -> None:
    document = _catalog()
    fixture = _fixture(document, "coppertone-buffer-preview-v1")
    assert fixture["routes"] == []
    fixture["claims"].append(claim)
    catalog = _write_catalog(tmp_path, document)

    result = _run(CHECKER, "--catalog", str(catalog), "--root", str(ROOT))

    assert result.returncode != 0
    assert "claims must exactly match declared observable evidence" in result.stderr


def test_catalog_rejects_a_symlink_escape(tmp_path: Path) -> None:
    fake_root = tmp_path / "repository"
    schema = fake_root / "schemas" / "audio-benchmark-catalog" / "0.1.0.schema.json"
    fixture_dir = fake_root / "fixtures"
    catalog_dir = fake_root / "benchmarks" / "audio"
    schema.parent.mkdir(parents=True)
    fixture_dir.mkdir(parents=True)
    catalog_dir.mkdir(parents=True)
    schema.write_bytes(SCHEMA.read_bytes())
    (fake_root / "LICENSE").write_text("test licence", encoding="utf-8")

    outside = tmp_path / "outside.kicad_pcb"
    outside.write_text("(kicad_pcb)", encoding="utf-8")
    (fixture_dir / "escaped.kicad_pcb").symlink_to(outside)

    document = _catalog()
    fixture = document["fixtures"][0]
    fixture["artifact_path"] = "fixtures/escaped.kicad_pcb"
    fixture["artifact_sha256"] = hashlib.sha256(outside.read_bytes()).hexdigest()
    fixture["license_path"] = "LICENSE"
    document["fixtures"] = [fixture]
    catalog = catalog_dir / "catalog.json"
    catalog.write_text(json.dumps(document), encoding="utf-8")

    result = _run(CHECKER, "--catalog", str(catalog), "--root", str(fake_root))

    assert result.returncode != 0
    assert "escapes the repository" in result.stderr


def test_capability_runner_never_changes_committed_board_sources() -> None:
    before = {
        path: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in (RC_FIXTURE, NE5532_FIXTURE, COPPERTONE)
    }

    result = _run(RUNNER)

    assert result.returncode == 0, result.stderr
    assert {
        path: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in (RC_FIXTURE, NE5532_FIXTURE, COPPERTONE)
    } == before


def test_capability_runner_rejects_a_routed_result_without_a_candidate_id(
    tmp_path: Path,
) -> None:
    hook = _write_sitecustomize(
        tmp_path,
        """from copper_mcp import tools

_original_preview_route = tools.preview_route

def _preview_without_candidate(payload, settings=None):
    document = _original_preview_route(payload, settings)
    if document.get("status") == "routed":
        document = dict(document)
        document.pop("candidate", None)
    return document

tools.preview_route = _preview_without_candidate
""",
    )

    result = _run(RUNNER, pythonpath_prefix=hook)

    assert result.returncode != 0
    assert "candidate" in result.stderr.lower()


def test_runner_binds_report_to_catalog_bytes_validated_before_replacement(
    tmp_path: Path,
) -> None:
    document = _catalog()
    root, catalog = _copy_catalog_root(tmp_path, document)
    expected_digest = hashlib.sha256(catalog.read_bytes()).hexdigest()
    hook = _write_sitecustomize(
        tmp_path,
        """import os
from pathlib import Path
import check_audio_benchmarks as checker

_original_load = checker.load_and_validate_catalog

def _load_then_replace(catalog_path, *, root=checker.ROOT):
    validated = _original_load(catalog_path, root=root)
    target = Path(os.environ["COPPER_TEST_CATALOG"])
    target.write_bytes(target.read_bytes() + b"\\n")
    return validated

checker.load_and_validate_catalog = _load_then_replace
""",
    )

    result = _run(
        RUNNER,
        "--catalog",
        str(catalog),
        "--root",
        str(root),
        pythonpath_prefix=hook,
        extra_environment={"COPPER_TEST_CATALOG": str(catalog)},
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["catalog_sha256"] == expected_digest


def test_artifact_replacement_after_validation_cannot_change_the_benchmark_input(
    tmp_path: Path,
) -> None:
    document = _catalog()
    root, catalog = _copy_catalog_root(tmp_path, document)
    fixture = document["fixtures"][0]
    expected_digest = fixture["artifact_sha256"]
    artifact = root / fixture["artifact_path"]
    hook = _write_sitecustomize(
        tmp_path,
        """import os
from pathlib import Path
import check_audio_benchmarks as checker

_original_load = checker.load_and_validate_catalog

def _load_then_replace(catalog_path, *, root=checker.ROOT):
    validated = _original_load(catalog_path, root=root)
    target = Path(os.environ["COPPER_TEST_ARTIFACT"])
    target.write_bytes(target.read_bytes() + b"\\n")
    return validated

checker.load_and_validate_catalog = _load_then_replace
""",
    )

    result = _run(
        RUNNER,
        "--catalog",
        str(catalog),
        "--root",
        str(root),
        pythonpath_prefix=hook,
        extra_environment={"COPPER_TEST_ARTIFACT": str(artifact)},
    )

    if result.returncode == 0:
        fixtures = {item["fixture_id"]: item for item in json.loads(result.stdout)["fixtures"]}
        assert fixtures[fixture["id"]]["artifact_sha256"] == expected_digest
    else:
        assert "changed" in result.stderr.lower() or "replacement" in result.stderr.lower()


@pytest.mark.skipif(not REAL_KICAD_CLI.is_file(), reason="KiCad CLI is not installed")
def test_real_kicad_parses_and_plots_the_original_microcase(tmp_path: Path) -> None:
    source = RC_FIXTURE.read_bytes()
    source_mtime = RC_FIXTURE.stat().st_mtime_ns
    board = tmp_path / RC_FIXTURE.name
    output = tmp_path / "gerbers"
    board.write_bytes(source)
    output.mkdir()

    result = subprocess.run(  # noqa: S603 - fixed local KiCad CLI command
        [
            str(REAL_KICAD_CLI),
            "pcb",
            "export",
            "gerbers",
            "--layers",
            "F.Cu,Edge.Cuts",
            "--output",
            str(output),
            str(board),
        ],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert (output / "rc-low-pass-routing-v1-F_Cu.gtl").stat().st_size > 0
    assert (output / "rc-low-pass-routing-v1-Edge_Cuts.gm1").stat().st_size > 0
    assert (output / "rc-low-pass-routing-v1-job.gbrjob").stat().st_size > 0
    assert board.read_bytes() == source
    assert RC_FIXTURE.read_bytes() == source
    assert RC_FIXTURE.stat().st_mtime_ns == source_mtime
