"""Redacted unsigned in-toto Statement payloads for candidate DRC evidence.

The deterministic routing core remains the authority for candidate identity and
the KiCad adapter remains the authority for DRC results.  This module only
projects those already-validated values into the standard in-toto Statement
shape.  It deliberately does not sign, persist, or verify a Statement.
"""

from __future__ import annotations

import json
import re
from typing import Any

from copper_mcp.models import DrcSummary

INTOTO_STATEMENT_TYPE = "https://in-toto.io/Statement/v1"
INTOTO_LINK_PREDICATE_TYPE = "https://in-toto.io/attestation/link/v0.3"
LINK_STEP_NAME = "kicad-candidate-drc"
EVIDENCE_SCOPE = "disposable-candidate"

_SHA256_ID = re.compile(r"^sha256:[a-f0-9]{64}$")


class AttestationError(ValueError):
    """Raised when a candidate DRC Statement cannot be bound safely."""


def _resource_descriptor(name: str, revision: str) -> dict[str, Any]:
    if not name or not _SHA256_ID.fullmatch(revision):
        raise AttestationError("attestation resource must have a sha256 digest")
    return {"name": name, "digest": {"sha256": revision.removeprefix("sha256:")}}


def build_candidate_drc_statement(
    *,
    candidate_id: str,
    candidate_base_revision: str,
    source_revision: str,
    patched_board_revision: str,
    patched_drc_context_revision: str,
    summary: DrcSummary,
) -> dict[str, Any]:
    """Build one redacted, unsigned in-toto Link Statement payload.

    The subject is the candidate product.  Board/source revisions are Link
    materials, while DRC counts are opaque byproducts.  Names are fixed to
    avoid leaking paths, net names, UUIDs, prompts, or raw KiCad findings.
    """

    revisions = {
        "candidate_id": candidate_id,
        "candidate_base_revision": candidate_base_revision,
        "source_revision": source_revision,
        "patched_board_revision": patched_board_revision,
        "patched_drc_context_revision": patched_drc_context_revision,
    }
    for name, revision in revisions.items():
        if not isinstance(revision, str) or not _SHA256_ID.fullmatch(revision):
            raise AttestationError(f"{name} must be content-addressed with sha256")
    if not isinstance(summary, DrcSummary):
        raise AttestationError("summary must be strict KiCad DRC evidence")
    if summary.base_revision != patched_board_revision:
        raise AttestationError("DRC summary is not bound to the patched board revision")
    if summary.drc_context_revision != patched_drc_context_revision:
        raise AttestationError("DRC summary is not bound to the patched context revision")

    materials = [
        _resource_descriptor("board-source", source_revision),
        _resource_descriptor("board-ir-base", candidate_base_revision),
        _resource_descriptor("patched-board", patched_board_revision),
        _resource_descriptor("patched-drc-context", patched_drc_context_revision),
    ]
    materials.sort(key=lambda descriptor: descriptor["name"])
    return {
        "_type": INTOTO_STATEMENT_TYPE,
        "subject": [_resource_descriptor("route-candidate", candidate_id)],
        "predicateType": INTOTO_LINK_PREDICATE_TYPE,
        "predicate": {
            "name": LINK_STEP_NAME,
            "command": [],
            "materials": materials,
            "byproducts": {
                "drc_summary": summary.to_dict(),
                "evidence_scope": EVIDENCE_SCOPE,
            },
            "environment": {
                "tool": "kicad-cli",
                "kicad_version": summary.kicad_version,
                "drc_schema": summary.drc_schema,
                "coordinate_units": summary.coordinate_units,
            },
        },
    }


def build_bundle_drc_statement(
    *,
    bundle_id: str,
    bundle_base_revision: str,
    candidate_ids: tuple[str, ...],
    source_revision: str,
    patched_board_revision: str,
    patched_drc_context_revision: str,
    summary: DrcSummary,
) -> dict[str, Any]:
    """Build one redacted, unsigned in-toto Link Statement payload for a composed bundle.

    One DRC run covers the whole composition, so there is one statement and its subject is
    the bundle, not any single candidate: per-candidate statements sharing one run would
    invite cherry-picked differentials. The composed candidate set rides as a sorted
    byproduct digest list, bound the same way every other revision is.
    """

    revisions = {
        "bundle_id": bundle_id,
        "bundle_base_revision": bundle_base_revision,
        "source_revision": source_revision,
        "patched_board_revision": patched_board_revision,
        "patched_drc_context_revision": patched_drc_context_revision,
    }
    for name, revision in revisions.items():
        if not isinstance(revision, str) or not _SHA256_ID.fullmatch(revision):
            raise AttestationError(f"{name} must be content-addressed with sha256")
    if (
        not isinstance(candidate_ids, tuple)
        or not 2 <= len(candidate_ids) <= 8
        or any(
            not isinstance(item, str) or not _SHA256_ID.fullmatch(item) for item in candidate_ids
        )
        or len(set(candidate_ids)) != len(candidate_ids)
    ):
        raise AttestationError("bundle candidate ids must be two to eight distinct digests")
    if not isinstance(summary, DrcSummary):
        raise AttestationError("summary must be strict KiCad DRC evidence")
    if summary.base_revision != patched_board_revision:
        raise AttestationError("DRC summary is not bound to the patched board revision")
    if summary.drc_context_revision != patched_drc_context_revision:
        raise AttestationError("DRC summary is not bound to the patched context revision")

    materials = [
        _resource_descriptor("board-source", source_revision),
        _resource_descriptor("board-ir-base", bundle_base_revision),
        _resource_descriptor("patched-board", patched_board_revision),
        _resource_descriptor("patched-drc-context", patched_drc_context_revision),
    ]
    materials.sort(key=lambda descriptor: descriptor["name"])
    return {
        "_type": INTOTO_STATEMENT_TYPE,
        "subject": [_resource_descriptor("route-bundle", bundle_id)],
        "predicateType": INTOTO_LINK_PREDICATE_TYPE,
        "predicate": {
            "name": LINK_STEP_NAME,
            "command": [],
            "materials": materials,
            "byproducts": {
                "drc_summary": summary.to_dict(),
                "evidence_scope": EVIDENCE_SCOPE,
                "candidate_ids": sorted(candidate_ids),
            },
            "environment": {
                "tool": "kicad-cli",
                "kicad_version": summary.kicad_version,
                "drc_schema": summary.drc_schema,
                "coordinate_units": summary.coordinate_units,
            },
        },
    }


def canonical_statement_bytes(statement: dict[str, Any]) -> bytes:
    """Serialize a Statement deterministically without signing or hashing it."""

    if not isinstance(statement, dict):
        raise AttestationError("statement must be an object")
    try:
        return json.dumps(
            statement,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise AttestationError("statement contains unsupported JSON values") from exc
