"""Coordinator-owned repeated KiCad evidence for a complete private composition."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path

from copper_mcp import kicad_cli
from copper_mcp.config import Settings
from copper_mcp.models import DrcSummary
from copper_mcp.optimization.contracts import digest_document
from copper_mcp.optimization.erc import judge_electrical_intent
from copper_mcp.optimization.inputs import PreparedOptimization
from copper_mcp.optimization.judge import (
    Authority,
    DomainResult,
    EvidenceBinding,
    EvidenceSample,
    JudgeReport,
    unavailable_report,
)
from copper_mcp.optimization.package import CandidateBinding
from copper_mcp.optimization.worker import OptimizationExecutionProbe
from copper_mcp.security import read_workspace_file


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as source:
        while chunk := source.read(1_048_576):
            size += len(chunk)
            if size > 512 * 1_048_576:
                raise kicad_cli.KiCadCliError("authority executable exceeds its fingerprint budget")
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def verify_original_context(prepared: PreparedOptimization, settings: Settings) -> None:
    current = kicad_cli._drc_context(settings.workspace / prepared.board_path, settings)
    if kicad_cli._context_revision(current) != prepared.original_context_digest:
        raise kicad_cli.KiCadCliError("optimization source or rules changed during execution")
    for path, expected in prepared.input_artifact_bindings:
        artifact = read_workspace_file(
            settings.workspace, path, allowed_suffixes={".json"}, max_bytes=96_000
        )
        if "sha256:" + hashlib.sha256(artifact.content).hexdigest() != expected:
            raise kicad_cli.KiCadCliError("optimization intent changed during execution")


def composition_context(
    prepared: PreparedOptimization, source: bytes, settings: Settings
) -> dict[str, bytes]:
    return kicad_cli._candidate_drc_context(
        dict(prepared.context),
        board_relative=prepared.board_path,
        patched_board=source,
        settings=settings,
    )


def _domain(
    domain: str,
    authority: Authority,
    observations: tuple[DrcSummary, ...],
    prepared: PreparedOptimization,
    binding: CandidateBinding,
    executable_digest: str,
) -> DomainResult:
    for summary in observations:
        if (
            summary.base_revision != binding.candidate_board_revision
            or summary.drc_context_revision != binding.rule_context_digest
        ):
            raise kicad_cli.KiCadCliError("optimization evidence describes another composition")
    evidence = EvidenceBinding(
        candidate_id=binding.digest,
        board_revision=binding.board_revision,
        input_digest=binding.candidate_board_revision,
        settings_digest=prepared.request.judge_profile_digest,
        backend=authority,
        backend_version=observations[0].kicad_version,
        executable_digest=executable_digest,
        command_digest=digest_document(
            "optimization-kicad-command/v1",
            [
                "pcb",
                "drc",
                "--format",
                "json",
                "--units",
                "mm",
                "--severity-all",
                "--exit-code-violations",
            ],
        ),
        rule_context_digest=binding.rule_context_digest,
        samples=tuple(
            EvidenceSample(
                verdict="pass" if summary.passed else "fail",
                normalized_result_digest=digest_document(
                    "optimization-drc-observation/v1", summary.to_dict()
                ),
            )
            for summary in observations
        ),
        suppressed_check_count=max(
            summary.ignored_check_count + summary.exclusion_count for summary in observations
        ),
    )
    fields = {"domain": domain, "evidence": evidence}
    if not evidence.repeated_agreement:
        return DomainResult.model_validate(
            {**fields, "status": "inconclusive", "reason": "evidence_disagreement"}
        )
    if evidence.suppressed_check_count:
        return DomainResult.model_validate(
            {**fields, "status": "inconclusive", "reason": "suppressed_checks"}
        )
    verdict = evidence.samples[0].verdict
    return DomainResult.model_validate(
        {**fields, "status": verdict, "reason": "verified" if verdict == "pass" else "check_failed"}
    )


def judge_composition(
    prepared: PreparedOptimization,
    binding: CandidateBinding,
    source: bytes,
    settings: Settings,
    probe: OptimizationExecutionProbe,
) -> JudgeReport:
    """Invoke the fixed KiCad adapter twice; absent authority never manufactures a verdict."""

    report = unavailable_report(
        prepared.request,
        candidate_id=binding.digest,
        candidate_input_digest=binding.candidate_board_revision,
        rule_context_digest=binding.rule_context_digest,
    )
    try:
        verify_original_context(prepared, settings)
        executable = kicad_cli.discover_kicad_cli(settings)
        executable_digest = file_digest(executable)
        # All invocations use the exact same discovered executable, not a fresh PATH resolution.
        fixed_settings = replace(settings, kicad_cli=executable)
        context = composition_context(prepared, source, fixed_settings)
        if (
            kicad_cli._revision(source) != binding.candidate_board_revision
            or kicad_cli._context_revision(context) != binding.rule_context_digest
        ):
            raise kicad_cli.KiCadCliError("optimization composition context is inconsistent")
        observations: list[DrcSummary] = []
        for _ in range(2):
            probe.checkpoint()
            # The KiCad adapter consumes its private mapping; retain the immutable input capture.
            observations.append(
                kicad_cli._run_captured_drc(
                    dict(context),
                    board_relative=prepared.board_path,
                    settings=fixed_settings,
                    deadline=prepared.started_at + prepared.request.limits.max_runtime_ms / 1000,
                )
            )
            probe.checkpoint()
        verify_original_context(prepared, fixed_settings)
        if file_digest(executable) != executable_digest:
            raise kicad_cli.KiCadCliError("optimization authority changed during execution")
        drc = _domain(
            "DRC", "kicad-drc-v1", tuple(observations), prepared, binding, executable_digest
        )
        dfm = _domain(
            "DFM", "kicad-drc-dfm-v1", tuple(observations), prepared, binding, executable_digest
        )
        erc = judge_electrical_intent(prepared, binding, fixed_settings, probe, executable_digest)
        verify_original_context(prepared, fixed_settings)
        if file_digest(executable) != executable_digest:
            raise kicad_cli.KiCadCliError("optimization authority changed during ERC")
        return JudgeReport.model_validate(
            {
                **report.model_dump(),
                "domains": tuple(
                    drc
                    if row.domain == "DRC"
                    else dfm
                    if row.domain == "DFM"
                    else erc
                    if row.domain == "ERC"
                    else row
                    for row in report.domains
                ),
            }
        )
    except (kicad_cli.KiCadCliError, OSError):
        return JudgeReport.model_validate(
            {
                **report.model_dump(),
                "domains": tuple(
                    DomainResult(
                        domain=row.domain,
                        status="inconclusive",
                        reason="backend_failure",
                        evidence=None,
                    )
                    if row.domain in ("DRC", "DFM")
                    else row
                    for row in report.domains
                ),
            }
        )
