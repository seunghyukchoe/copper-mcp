"""Internally captured ordinary-project inputs and candidate-bound native observations."""

from __future__ import annotations

import hashlib
import time
from typing import TYPE_CHECKING

from copper_mcp.config import Settings
from copper_mcp.engineering.capture import CaptureLimits
from copper_mcp.engineering.project_board_parity import (
    ProjectBoardParityError,
    run_project_board_parity,
)
from copper_mcp.engineering.project_erc import ProjectErcError, run_project_erc
from copper_mcp.engineering.project_erc_inputs import SymbolLibraryInput
from copper_mcp.engineering.schematic_project_capture import (
    ProjectFileBinding,
    SchematicProjectCapture,
    capture_schematic_project,
)
from copper_mcp.optimization.contracts import OptimizationError, digest_document
from copper_mcp.optimization.judge import (
    DomainResult,
    EvidenceBinding,
    EvidenceSample,
    ProjectEvidence,
)
from copper_mcp.security import read_workspace_file

if TYPE_CHECKING:
    from copper_mcp.optimization.inputs import PreparedOptimization, ProjectDeclaration
    from copper_mcp.optimization.package import CandidateBindingV2


def capture_project_inputs(
    declaration: ProjectDeclaration, settings: Settings, deadline: float
) -> tuple[SchematicProjectCapture, tuple[SymbolLibraryInput, ...], str]:
    limits = CaptureLimits()
    capture = capture_schematic_project(
        settings.workspace,
        declaration.root_path,
        tuple(ProjectFileBinding(row.path, row.digest) for row in declaration.files),
        limits=limits,
        deadline=deadline,
    )
    libraries = []
    total = sum(len(row.content) for row in capture._files)
    for row in declaration.libraries:
        if time.monotonic() >= deadline:
            raise OptimizationError("optimization project capture deadline expired")
        payload = read_workspace_file(
            settings.workspace,
            row.path,
            allowed_suffixes={".kicad_sym"},
            max_bytes=min(limits.max_file_bytes, max(0, limits.max_total_bytes - total)),
        ).content
        total += len(payload)
        actual = hashlib.sha256()
        for offset in range(0, len(payload), 65536):
            if time.monotonic() >= deadline:
                raise OptimizationError("optimization project capture deadline expired")
            actual.update(payload[offset : offset + 65536])
        if "sha256:" + actual.hexdigest() != row.digest or total > limits.max_total_bytes:
            raise OptimizationError("optimization project library binding changed")
        libraries.append(SymbolLibraryInput(row.name, payload, row.digest))
    if len({row.name for row in libraries}) != len(libraries):
        raise OptimizationError("optimization project libraries are ambiguous")
    identity = digest_document(
        "optimization-project-libraries/v2",
        tuple(sorted((row.name, row.digest) for row in libraries)),
    )
    return capture, tuple(libraries), identity


def judge_project(
    prepared: PreparedOptimization,
    binding: CandidateBindingV2,
    source: bytes,
    settings: Settings,
    deadline: float,
) -> tuple[DomainResult, ProjectEvidence | None]:
    declaration = prepared.project
    request = prepared.request
    if declaration is None or request.schema_version != "optimization/v2":
        raise OptimizationError("optimization project is unavailable")
    capture, libraries, library_digest = capture_project_inputs(declaration, settings, deadline)
    if (
        capture.digest != request.project_capture_digest
        or library_digest != request.project_libraries_digest
    ):
        raise OptimizationError("optimization project changed during execution")
    native_failed = False
    try:
        erc = run_project_erc(capture, libraries, settings, deadline=deadline)
        parity = run_project_board_parity(
            capture,
            libraries,
            source,
            binding.candidate_board_revision,
            settings,
            deadline=deadline,
        )
    except (ProjectErcError, ProjectBoardParityError, OSError):
        native_failed = True
    if native_failed:
        return DomainResult(
            domain="ERC", status="inconclusive", reason="backend_failure", evidence=None
        ), None
    if (
        erc.capture_digest != capture.digest
        or parity.capture_digest != capture.digest
        or parity.board_revision != binding.candidate_board_revision
        or erc.profile_id != "kicad-project-connectivity/v1"
        or parity.profile_id != "kicad-project-board-parity/v1"
    ):
        raise OptimizationError("optimization project observation binding is invalid")
    evidence = ProjectEvidence(
        capture_digest=capture.digest,
        libraries_digest=library_digest,
        candidate_board_revision=binding.candidate_board_revision,
        erc_status=erc.status,
        parity_status=parity.status,
        erc_report_digest=erc.digest,
        parity_report_digest=parity.digest,
        erc_profile_id="kicad-project-connectivity/v1",
        parity_profile_id="kicad-project-board-parity/v1",
        erc_authentication_digest=erc.backend_authentication_digest,
        parity_authentication_digest=parity.backend_authentication_digest,
    )
    if erc.status == "inconclusive":
        return DomainResult(
            domain="ERC", status="inconclusive", reason="backend_failure", evidence=None
        ), evidence
    binding_record = EvidenceBinding(
        candidate_id=binding.digest,
        board_revision=binding.board_revision,
        input_digest=capture.digest,
        settings_digest=request.judge_profile_digest,
        backend="kicad-erc-v1",
        backend_version=erc.backend_version,
        executable_digest=erc.executable_digest,
        command_digest=erc.command_digest,
        rule_context_digest=binding.rule_context_digest,
        samples=tuple(
            EvidenceSample(
                verdict="fail" if sample.error_count else "pass",
                normalized_result_digest=sample.normalized_report_digest,
            )
            for sample in erc.samples
        ),
        suppressed_check_count=max(sample.exclusion_count for sample in erc.samples),
    )
    if not binding_record.repeated_agreement or binding_record.suppressed_check_count:
        return DomainResult(
            domain="ERC", status="inconclusive", reason="backend_failure", evidence=None
        ), evidence
    return DomainResult(
        domain="ERC",
        status=erc.status,
        reason="verified" if erc.status == "pass" else "check_failed",
        evidence=binding_record,
    ), evidence
