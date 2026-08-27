"""Bounded, read-only Board IR inspection for one board inside the workspace.

Hosts need to know whether a board is representable by the supported Board IR
subset before choosing a net and attempting a route preview. This service answers
that question and nothing else: it reports structural counts, digests, and copper
layer identities, never coordinates, net names, pad identities, or source bytes.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from copper_mcp.adapters import KiCadConstraintProfile, parse_kicad_bytes
from copper_mcp.board_ir import BoardIRSnapshot, NetClass
from copper_mcp.board_ir.diagnostics import ConversionResult
from copper_mcp.config import Settings
from copper_mcp.models import SCHEMA_VERSION
from copper_mcp.parse_budgets import parse_limits_for
from copper_mcp.request_boundary import (
    CONSTRAINT_FIELDS,
    MAX_JSON_SAFE_INTEGER,
    RequestError,
    board_path,
    integer,
    known_fields,
    mapping,
    net_class_constraints,
    required_fields,
)
from copper_mcp.security import read_workspace_file

_REQUIRED_FIELDS = ("board", "constraints")
# Every measured field on `ConversionResult`, published as one map rather than separate fields.
#
# Five of the six are the *disclosure* a documented risk depends on: `R-134` (groups), `R-139`
# (root board properties), `R-141` (edge-connector pads), `R-144` (pad fabrication properties),
# and `R-158` (thermal-bridge angles) each record that conversion accepts a construct, loses its
# token, and counts what it lost -- and each names the count as how a caller finds out. Until 0.9.0
# the earlier counts reached no MCP client at all, so those mitigations were partial and the
# direction of error was under-disclosure.
#
# This is a hand-maintained list on purpose, and the maintenance is the point. Building the map by
# reflecting over `ConversionResult` would make another counter appear here silently, which is
# exactly how the set once grew without anybody noticing; instead a new counter has to be added to
# this tuple, and `test_board_ir_service` reflects over the dataclass and fails until it
# is. One line, in a diff a reviewer sees.
#
# `max_roundrect_rounding_nm` is a magnitude in nanometres rather than a count, and it is in the
# map anyway: the audit specified it alongside the counts, the key carries its own unit, and a
# second map for one field would recreate the separate-field shape this exists to avoid.
_MEASURED_COUNT_FIELDS = (
    "max_roundrect_rounding_nm",
    "unmodelled_group_count",
    "edge_connector_pad_count",
    "unmodelled_board_property_count",
    "unmodelled_pad_property_count",
    "unmodelled_thermal_bridge_angle_pad_count",
    "unmodelled_setup_field_count",
    "unmodelled_stackup_layer_count",
    "unmodelled_footprint_field_count",
)
_OBJECT_COLLECTIONS = (
    "outline",
    "copper_layers",
    "nets",
    "footprints",
    "pads",
    "vias",
    "segments",
    "arcs",
    "zones",
    "keepouts",
)


class BoardIrError(RequestError):
    """Raised when a Board IR inspection request is malformed or cannot be honoured."""


@dataclass(frozen=True, slots=True)
class BoardIrRequest:
    """One validated request to convert and describe a single board."""

    board: str
    constraints: NetClass

    def __post_init__(self) -> None:
        if not isinstance(self.constraints, NetClass):
            raise BoardIrError("constraints must be a typed net class")
        board_path(self.board)

    def profile(self) -> KiCadConstraintProfile:
        """Build the typed constraint profile applied to the converted board."""

        return KiCadConstraintProfile(
            net_classes=(self.constraints,),
            default_net_class_id=self.constraints.id,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "board": self.board,
            "constraints": {field: getattr(self.constraints, field) for field in CONSTRAINT_FIELDS},
        }


def parse_board_ir_request(payload: Any) -> BoardIrRequest:
    """Validate one untrusted Board IR request without echoing unvalidated input."""

    try:
        fields = mapping("request", payload)
        known_fields("request", fields, frozenset(_REQUIRED_FIELDS))
        required_fields("request", fields, _REQUIRED_FIELDS)
        return BoardIrRequest(
            board=board_path(fields["board"]),
            constraints=net_class_constraints(fields["constraints"]),
        )
    except BoardIrError:
        raise
    except RequestError as error:
        raise BoardIrError(str(error)) from error


@dataclass(frozen=True, slots=True)
class BoardIrSummary:
    """Immutable structural description of one converted board, without content disclosure."""

    board_path: str
    board_revision: str
    supported: bool
    request: BoardIrRequest
    snapshot_digest: str | None = None
    constraint_digest: str | None = None
    ir_schema: str | None = None
    ir_schema_version: str | None = None
    distance_unit: str | None = None
    angle_unit: str | None = None
    copper_layer_ids: tuple[str, ...] = ()
    object_counts: Mapping[str, int] = field(default_factory=dict)
    conversion_diagnostic_counts: Mapping[str, int] = field(default_factory=dict)
    # What the conversion accepted and did not model, as one map keyed by the measured field it
    # comes from. Additive and optional: a client that does not read it is unaffected, and the
    # accepted set of this unversioned summary contract only widens. See `_MEASURED_COUNT_FIELDS`.
    unmodelled_counts: Mapping[str, int] = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.request, BoardIrRequest):
            raise BoardIrError("Board IR request is malformed")
        if self.schema_version != SCHEMA_VERSION:
            raise BoardIrError("Board IR summary schema version is unsupported")
        if not isinstance(self.supported, bool):
            raise BoardIrError("supported must be a boolean")
        for name in ("object_counts", "conversion_diagnostic_counts", "unmodelled_counts"):
            counts = getattr(self, name)
            if not isinstance(counts, Mapping):
                raise BoardIrError(f"{name} must be a mapping")
            frozen = {
                str(key): integer(f"{name}[{key}]", value, minimum=0, maximum=MAX_JSON_SAFE_INTEGER)
                for key, value in counts.items()
            }
            object.__setattr__(self, name, MappingProxyType(dict(sorted(frozen.items()))))
        if not isinstance(self.copper_layer_ids, tuple) or not all(
            isinstance(layer_id, str) for layer_id in self.copper_layer_ids
        ):
            raise BoardIrError("copper layer identities must be an immutable tuple of strings")

        described = (
            self.snapshot_digest,
            self.constraint_digest,
            self.ir_schema,
            self.ir_schema_version,
            self.distance_unit,
            self.angle_unit,
        )
        if self.supported:
            if any(value is None for value in described):
                raise BoardIrError("a supported board must describe its Board IR snapshot")
            if self.conversion_diagnostic_counts:
                raise BoardIrError("a supported board must not report conversion diagnostics")
            if not self.copper_layer_ids or not self.object_counts:
                raise BoardIrError("a supported board must report layers and object counts")
            # An empty map and an absent one read the same to a client, so a supported board
            # reporting nothing here would be indistinguishable from the pre-0.9.0 surface that
            # disclosed nothing. Every measured field is present, zeros included.
            if set(self.unmodelled_counts) != set(_MEASURED_COUNT_FIELDS):
                raise BoardIrError("a supported board must report every measured conversion count")
        else:
            if any(value is not None for value in described):
                raise BoardIrError("an unsupported board cannot describe a Board IR snapshot")
            if not self.conversion_diagnostic_counts:
                raise BoardIrError("an unsupported board must report conversion diagnostics")
            if self.copper_layer_ids or self.object_counts:
                raise BoardIrError("an unsupported board cannot report converted structure")
            # A refused conversion measured nothing: `ConversionResult` already refuses to carry a
            # non-zero count without a snapshot, so reporting zeros here would publish a
            # measurement that was never taken.
            if self.unmodelled_counts:
                raise BoardIrError("an unsupported board cannot report conversion counts")

    def to_dict(self) -> dict[str, Any]:
        """Return a detached plain dictionary; mutating it cannot alter this summary."""

        return {
            "schema_version": self.schema_version,
            "board_path": self.board_path,
            "board_revision": self.board_revision,
            "supported": self.supported,
            "request": self.request.to_dict(),
            "snapshot_digest": self.snapshot_digest,
            "constraint_digest": self.constraint_digest,
            "ir_schema": self.ir_schema,
            "ir_schema_version": self.ir_schema_version,
            "distance_unit": self.distance_unit,
            "angle_unit": self.angle_unit,
            "copper_layer_ids": list(self.copper_layer_ids),
            "object_counts": dict(self.object_counts),
            "conversion_diagnostic_counts": dict(self.conversion_diagnostic_counts),
            "unmodelled_counts": dict(self.unmodelled_counts),
        }


def _unmodelled_counts(conversion: ConversionResult) -> dict[str, int]:
    """Read every measured conversion count named by `_MEASURED_COUNT_FIELDS`."""

    return {name: int(getattr(conversion, name)) for name in _MEASURED_COUNT_FIELDS}


def _object_counts(snapshot: BoardIRSnapshot) -> dict[str, int]:
    content = snapshot.content
    counts = {name: len(getattr(content, name)) for name in _OBJECT_COLLECTIONS}
    counts["net_classes"] = len(content.constraints.net_classes)
    counts["net_class_assignments"] = len(content.constraints.assignments)
    counts["differential_pair_rules"] = len(content.constraints.differential_pairs)
    counts["length_rules"] = len(content.constraints.length_rules)
    return counts


def summarize_board_ir(payload: Any, settings: Settings) -> BoardIrSummary:
    """Describe one workspace board's Board IR structure without disclosing its content."""

    if not isinstance(settings, Settings):
        raise BoardIrError("Board IR settings are malformed")
    request = parse_board_ir_request(payload)

    board = read_workspace_file(
        settings.workspace,
        request.board,
        allowed_suffixes={".kicad_pcb"},
        max_bytes=settings.max_board_bytes,
    )
    relative_path = board.path.relative_to(settings.workspace.resolve(strict=True)).as_posix()
    source = board.content
    board_revision = f"sha256:{hashlib.sha256(source).hexdigest()}"

    limits = parse_limits_for(settings)
    conversion = parse_kicad_bytes(source, request.profile(), limits)
    if conversion.snapshot is None or conversion.diagnostics:
        return BoardIrSummary(
            board_path=relative_path,
            board_revision=board_revision,
            supported=False,
            request=request,
            conversion_diagnostic_counts=Counter(
                diagnostic.code for diagnostic in conversion.diagnostics
            ),
        )

    snapshot = conversion.snapshot
    if snapshot.content.source.revision != board_revision:
        raise BoardIrError("converted board revision is inconsistent with its source bytes")
    return BoardIrSummary(
        board_path=relative_path,
        board_revision=board_revision,
        supported=True,
        request=request,
        snapshot_digest=snapshot.snapshot_digest,
        constraint_digest=snapshot.content.constraint_digest,
        ir_schema=snapshot.schema,
        ir_schema_version=snapshot.schema_version,
        distance_unit=snapshot.content.units.distance,
        angle_unit=snapshot.content.units.angle,
        copper_layer_ids=tuple(sorted(layer.id for layer in snapshot.content.copper_layers)),
        object_counts=_object_counts(snapshot),
        unmodelled_counts=_unmodelled_counts(conversion),
    )
