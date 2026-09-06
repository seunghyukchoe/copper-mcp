"""Real KiCad ERC on a captured bounded Circuit Intent, separately bound from PCB DRC."""

from __future__ import annotations

from copper_mcp import kicad_cli
from copper_mcp.adapters.kicad_schematic import render_kicad_schematic
from copper_mcp.circuit_ir import decode_snapshot_json
from copper_mcp.config import Settings
from copper_mcp.optimization.contracts import digest_document
from copper_mcp.optimization.inputs import PreparedOptimization
from copper_mcp.optimization.judge import DomainResult, EvidenceBinding, EvidenceSample
from copper_mcp.optimization.package import CandidateBinding
from copper_mcp.optimization.worker import OptimizationExecutionProbe


def judge_electrical_intent(
    prepared: PreparedOptimization,
    binding: CandidateBinding,
    settings: Settings,
    probe: OptimizationExecutionProbe,
    executable_digest: str,
) -> DomainResult:
    if prepared.electrical_source is None:
        return DomainResult(
            domain="ERC", status="inconclusive", reason="insufficient_inputs", evidence=None
        )
    snapshot = decode_snapshot_json(prepared.electrical_source)
    if snapshot.snapshot_digest != prepared.request.electrical_inputs_digest:
        raise kicad_cli.KiCadCliError("optimization electrical input binding is inconsistent")
    schematic = render_kicad_schematic(snapshot)
    if schematic != render_kicad_schematic(snapshot):
        raise kicad_cli.KiCadCliError("optimization schematic replay is inconsistent")
    summaries = []
    for _ in range(2):
        probe.checkpoint()
        clipped = kicad_cli._candidate_drc_deadline_settings(
            settings, prepared.started_at + prepared.request.limits.max_runtime_ms / 1000
        )
        summary = kicad_cli.run_circuit_schematic_erc(
            schematic.content,
            intent_digest=snapshot.snapshot_digest,
            schematic_digest=schematic.artifact_digest,
            settings=clipped,
        )
        if (
            summary.intent_digest != snapshot.snapshot_digest
            or summary.schematic_digest != schematic.artifact_digest
        ):
            raise kicad_cli.KiCadCliError("optimization ERC describes different electrical input")
        summaries.append(summary)
        probe.checkpoint()
    evidence = EvidenceBinding(
        candidate_id=binding.digest,
        board_revision=binding.board_revision,
        input_digest=snapshot.snapshot_digest,
        settings_digest=prepared.request.judge_profile_digest,
        backend="kicad-erc-v1",
        backend_version=summaries[0].kicad_version,
        executable_digest=executable_digest,
        command_digest=digest_document(
            "optimization-erc-command/v1",
            [
                "sch",
                "erc",
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
                    "optimization-erc-observation/v1", summary.to_dict()
                ),
            )
            for summary in summaries
        ),
        suppressed_check_count=max(
            summary.ignored_check_count + summary.exclusion_count for summary in summaries
        ),
    )
    if not evidence.repeated_agreement:
        return DomainResult(
            domain="ERC", status="inconclusive", reason="evidence_disagreement", evidence=evidence
        )
    if evidence.suppressed_check_count:
        return DomainResult(
            domain="ERC", status="inconclusive", reason="suppressed_checks", evidence=evidence
        )
    status = evidence.samples[0].verdict
    return DomainResult(
        domain="ERC",
        status=status,
        reason="verified" if status == "pass" else "check_failed",
        evidence=evidence,
    )
