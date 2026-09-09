"""Coordinator-owned repeated KiCad evidence for a complete private composition."""

from __future__ import annotations

import hashlib
import time
from dataclasses import replace
from pathlib import Path

from copper_mcp import kicad_cli
from copper_mcp.config import Settings
from copper_mcp.models import DrcSummary
from copper_mcp.optimization.contracts import digest_document
from copper_mcp.optimization.drc_profile import prepare_drc_profile
from copper_mcp.optimization.erc import judge_electrical_intent
from copper_mcp.optimization.inputs import PreparedOptimization
from copper_mcp.optimization.judge import (
    AnyJudgeReport,
    Authority,
    DomainResult,
    EvidenceBinding,
    EvidenceSample,
    JudgeReportV2,
    unavailable_report,
)
from copper_mcp.optimization.package import CandidateBinding, CandidateBindingV2
from copper_mcp.optimization.worker import OptimizationExecutionError, OptimizationExecutionProbe
from copper_mcp.security import read_workspace_file


def _check_deadline(deadline: float | None) -> None:
    if deadline is not None and time.monotonic() >= deadline:
        raise OptimizationExecutionError("budget_exhausted")


def file_digest(path: Path, *, deadline: float | None = None) -> str:
    _check_deadline(deadline)
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as source:
        while chunk := source.read(1_048_576):
            _check_deadline(deadline)
            size += len(chunk)
            if size > 512 * 1_048_576:
                raise kicad_cli.KiCadCliError("authority executable exceeds its fingerprint budget")
            digest.update(chunk)
    _check_deadline(deadline)
    return "sha256:" + digest.hexdigest()


def verify_original_context(
    prepared: PreparedOptimization, settings: Settings, *, deadline: float | None = None
) -> None:
    if deadline is None and prepared.request.schema_version == "optimization/v2":
        deadline = prepared.started_at + prepared.request.limits.max_runtime_ms / 1000
    if deadline is not None:
        deadline = min(
            deadline, prepared.started_at + prepared.request.limits.max_runtime_ms / 1000
        )
        _check_deadline(deadline)
        settings = kicad_cli._candidate_drc_deadline_settings(settings, deadline)
    unavailable = False
    try:
        current = kicad_cli._drc_context(settings.workspace / prepared.board_path, settings)
    except (kicad_cli.KiCadCliError, OSError):
        if prepared.request.schema_version == "optimization/v1":
            raise
        unavailable = True
    if unavailable:
        _check_deadline(deadline)
        raise OptimizationExecutionError("stale_revision")
    _check_deadline(deadline)
    if kicad_cli._context_revision(current) != prepared.original_context_digest:
        if prepared.request.schema_version == "optimization/v2":
            raise OptimizationExecutionError("stale_revision")
        raise kicad_cli.KiCadCliError("optimization source or rules changed during execution")
    for path, expected in prepared.input_artifact_bindings:
        _check_deadline(deadline)
        artifact = read_workspace_file(
            settings.workspace, path, allowed_suffixes={".json"}, max_bytes=96_000
        )
        if "sha256:" + hashlib.sha256(artifact.content).hexdigest() != expected:
            if prepared.request.schema_version == "optimization/v2":
                raise OptimizationExecutionError("stale_revision")
            raise kicad_cli.KiCadCliError("optimization intent changed during execution")
    if prepared.project is not None and prepared.request.schema_version == "optimization/v2":
        from copper_mcp.optimization.project_evidence import capture_project_inputs

        capture_failed = False
        try:
            capture, _libraries, libraries_digest = capture_project_inputs(
                prepared.project,
                settings,
                deadline
                if deadline is not None
                else prepared.started_at + prepared.request.limits.max_runtime_ms / 1000,
            )
        except (ValueError, OSError):
            capture_failed = True
        _check_deadline(deadline)
        if capture_failed:
            raise OptimizationExecutionError("stale_revision")
        if (
            capture.digest != prepared.request.project_capture_digest
            or libraries_digest != prepared.request.project_libraries_digest
        ):
            raise OptimizationExecutionError("stale_revision")
    _check_deadline(deadline)


def composition_context(
    prepared: PreparedOptimization,
    source: bytes,
    settings: Settings,
    *,
    deadline: float | None = None,
) -> dict[str, bytes]:
    context = dict(prepared.context)
    if prepared.request.schema_version == "optimization/v2":
        deadline = min(
            prepared.started_at + prepared.request.limits.max_runtime_ms / 1000,
            deadline if deadline is not None else float("inf"),
        )
        _check_deadline(deadline)
        profile_failed = False
        try:
            context, profile = prepare_drc_profile(context, prepared.board_path, settings, deadline)
        except (ValueError, OSError):
            profile_failed = True
        _check_deadline(deadline)
        if profile_failed:
            raise OptimizationExecutionError("invalid_candidate")
        if profile != prepared.drc_profile or profile.digest != prepared.request.drc_profile_digest:
            raise OptimizationExecutionError("invalid_candidate")
    result = kicad_cli._candidate_drc_context(
        context,
        board_relative=prepared.board_path,
        patched_board=source,
        settings=settings,
    )
    _check_deadline(deadline)
    return result


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
) -> AnyJudgeReport:
    """Invoke the fixed KiCad adapter twice; absent authority never manufactures a verdict."""

    report = unavailable_report(
        prepared.request,
        candidate_id=binding.digest,
        candidate_input_digest=binding.candidate_board_revision,
        rule_context_digest=binding.rule_context_digest,
    )
    global_deadline = prepared.started_at + prepared.request.limits.max_runtime_ms / 1000
    local_deadline = min(global_deadline, getattr(probe, "deadline", global_deadline))
    # V1 retains its original source-check and helper-call behavior.
    check_deadline = (
        local_deadline if prepared.request.schema_version == "optimization/v2" else None
    )
    if isinstance(report, JudgeReportV2):
        report = JudgeReportV2.model_validate(
            {**report.model_dump(), "drc_profile": prepared.drc_profile}
        )
    try:
        verify_original_context(prepared, settings, deadline=check_deadline)
        executable = kicad_cli.discover_kicad_cli(settings)
        executable_digest = file_digest(executable, deadline=check_deadline)
        # All invocations use the exact same discovered executable, not a fresh PATH resolution.
        fixed_settings = replace(settings, kicad_cli=executable)
        context = composition_context(prepared, source, fixed_settings, deadline=check_deadline)
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
                    deadline=local_deadline,
                )
            )
            probe.checkpoint()
        verify_original_context(prepared, fixed_settings, deadline=check_deadline)
        if file_digest(executable, deadline=check_deadline) != executable_digest:
            raise kicad_cli.KiCadCliError("optimization authority changed during execution")
        drc = _domain(
            "DRC", "kicad-drc-v1", tuple(observations), prepared, binding, executable_digest
        )
        dfm = _domain(
            "DFM", "kicad-drc-dfm-v1", tuple(observations), prepared, binding, executable_digest
        )
        project_evidence = None
        if prepared.project is not None and isinstance(binding, CandidateBindingV2):
            from copper_mcp.optimization.project_evidence import judge_project

            erc, project_evidence = judge_project(
                prepared,
                binding,
                source,
                fixed_settings,
                local_deadline,
            )
        else:
            erc = judge_electrical_intent(
                prepared, binding, fixed_settings, probe, executable_digest, deadline=check_deadline
            )
        verify_original_context(prepared, fixed_settings, deadline=check_deadline)
        if file_digest(executable, deadline=check_deadline) != executable_digest:
            raise kicad_cli.KiCadCliError("optimization authority changed during ERC")
        return type(report).model_validate(
            {
                **report.model_dump(),
                **(
                    {"project_evidence": project_evidence}
                    if isinstance(report, JudgeReportV2)
                    else {}
                ),
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
        return type(report).model_validate(
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
