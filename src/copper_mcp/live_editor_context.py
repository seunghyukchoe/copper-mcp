"""Revision-bound, redaction-safe context from the active KiCad PCB editor.

The service is intentionally narrower than a scene: it reports only the active layer and
native references in the current GUI selection.  It does not expose board text, protobuf
messages, coordinates, net names, selection strings, or mutation capabilities.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

from copper_mcp.config import Settings
from copper_mcp.kicad_ipc import (
    ACCEPTED_API_COMPATIBILITY,
    DOCUMENT_BINDING_IN_MEMORY,
    LiveEditorContextSnapshot,
    capture_live_editor_context,
)
from copper_mcp.request_boundary import (
    MAX_JSON_SAFE_INTEGER,
    RequestError,
    integer,
    known_fields,
    mapping,
    required_fields,
    text,
)

LIVE_EDITOR_CONTEXT_VERSION = "0.2.0"
_SHA256_DIGEST = re.compile(r"^sha256:[a-f0-9]{64}$")
_MAX_SELECTION = 256
_REQUIRED_FIELDS = ("board", "expect_board_revision")
_OPTIONAL_FIELDS = ("expect_context_digest", "max_selection")


class LiveEditorContextError(RequestError):
    """Raised when the live editor context cannot be safely observed or is stale."""


def _digest(name: str, value: Any) -> str:
    result = text(name, value, maximum=71)
    if _SHA256_DIGEST.fullmatch(result) is None:
        raise LiveEditorContextError(f"{name} must be content-addressed with sha256")
    return result


@dataclass(frozen=True, slots=True)
class LiveEditorContextRequest:
    """Validated compare-and-swap preconditions for one editor-context read."""

    board: str
    expect_board_revision: str
    expect_context_digest: str | None = None
    max_selection: int = _MAX_SELECTION

    def __post_init__(self) -> None:
        if self.board != "live":
            raise LiveEditorContextError("live editor context requests must set board to 'live'")
        _digest("expect_board_revision", self.expect_board_revision)
        if self.expect_context_digest is not None:
            _digest("expect_context_digest", self.expect_context_digest)
        if not 1 <= self.max_selection <= _MAX_SELECTION:
            raise LiveEditorContextError("max_selection is outside the bounded range")


def parse_live_editor_context_request(payload: Any) -> LiveEditorContextRequest:
    """Validate untrusted MCP input without echoing malformed private values."""

    fields = mapping("request", payload)
    required_fields("request", fields, _REQUIRED_FIELDS)
    known_fields("request", fields, frozenset((*_REQUIRED_FIELDS, *_OPTIONAL_FIELDS)))
    board = text("request.board", fields["board"], maximum=8)
    expected_board = _digest("request.expect_board_revision", fields["expect_board_revision"])
    expected_context = (
        _digest("request.expect_context_digest", fields["expect_context_digest"])
        if "expect_context_digest" in fields
        else None
    )
    max_selection = (
        integer(
            "request.max_selection",
            fields["max_selection"],
            minimum=1,
            maximum=min(MAX_JSON_SAFE_INTEGER, _MAX_SELECTION),
        )
        if "max_selection" in fields
        else _MAX_SELECTION
    )
    return LiveEditorContextRequest(
        board=board,
        expect_board_revision=expected_board,
        expect_context_digest=expected_context,
        max_selection=max_selection,
    )


def _context_digest(snapshot: LiveEditorContextSnapshot) -> str:
    payload = {
        "active_layer": {
            "index": snapshot.active_layer_index,
            "name": snapshot.active_layer_name,
        },
        "board_revision": snapshot.board_digest,
        "selection": [{"kind": item.kind, "ref_id": item.ref_id} for item in snapshot.selection],
        # This currently aliases board_revision because the context adapter has no separate
        # Board IR profile. Keeping the field explicit preserves the scene CAS contract and
        # leaves room for a future semantic snapshot digest without changing MCP shape.
        "snapshot_digest": snapshot.board_digest,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


@dataclass(frozen=True, slots=True)
class LiveEditorContext:
    """Structured live editor context safe to return over MCP."""

    board_revision: str
    snapshot_digest: str
    context_digest: str
    active_layer_index: int
    active_layer_name: str
    selection: tuple[dict[str, str], ...]
    #: Carried from the capture so this surface answers the same compatibility question
    #: ``inspect_live_board`` answers. B-138 measured this path refusing a real editor the board
    #: surface could observe, and even on its internal accept path it published no verdict at
    #: all -- so a caller had no way to learn that a context had been read across a drifted API.
    kicad_version: str = ""
    api_version: str = ""
    compatibility: str = "compatible"
    document_binding: str = "in_memory_unsaved_state_unobservable"
    read_only: bool = True
    schema: str = "copper.live-editor-context"
    schema_version: str = LIVE_EDITOR_CONTEXT_VERSION
    source: str = "kicad-ipc-live"

    def __post_init__(self) -> None:
        if self.schema != "copper.live-editor-context" or self.source != "kicad-ipc-live":
            raise LiveEditorContextError("live editor context provenance is invalid")
        if not self.read_only:
            raise LiveEditorContextError("live editor context is read-only")
        if self.compatibility not in ACCEPTED_API_COMPATIBILITY:
            raise LiveEditorContextError("live editor context compatibility is invalid")
        if self.document_binding != DOCUMENT_BINDING_IN_MEMORY:
            raise LiveEditorContextError("live editor context document binding is invalid")
        for name, value in (
            ("board_revision", self.board_revision),
            ("snapshot_digest", self.snapshot_digest),
            ("context_digest", self.context_digest),
        ):
            _digest(name, value)
        if len(self.selection) > _MAX_SELECTION:
            raise LiveEditorContextError("live editor selection exceeds the observation budget")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "schema_version": self.schema_version,
            "source": self.source,
            "board_revision": self.board_revision,
            "snapshot_digest": self.snapshot_digest,
            "context_digest": self.context_digest,
            "active_layer": {
                "index": self.active_layer_index,
                "name": self.active_layer_name,
                "id": f"layer:{self.active_layer_name}",
            },
            "selection": [dict(item) for item in self.selection],
            "selection_count": len(self.selection),
            "kicad_version": self.kicad_version,
            "api_version": self.api_version,
            "compatibility": self.compatibility,
            "document_binding": self.document_binding,
            "read_only": self.read_only,
        }


def inspect_live_editor_context_raw(
    payload: Any,
    settings: Settings,
    *,
    client_factory: Any = None,
) -> LiveEditorContext:
    """Capture and return editor context only when all caller revisions still match."""

    if not isinstance(settings, Settings):
        raise LiveEditorContextError("live editor settings are malformed")
    request = parse_live_editor_context_request(payload)
    snapshot = capture_live_editor_context(
        settings,
        client_factory=client_factory,
        max_selection=request.max_selection,
    )
    if request.expect_board_revision != snapshot.board_digest:
        raise LiveEditorContextError("live board revision is stale")
    context_digest = _context_digest(snapshot)
    if (
        request.expect_context_digest is not None
        and request.expect_context_digest != context_digest
    ):
        raise LiveEditorContextError("live editor context is stale")
    selection = tuple(
        {"ref_id": item.ref_id, "kind": item.kind, "ref_stability": "native"}
        for item in snapshot.selection
    )
    return LiveEditorContext(
        board_revision=snapshot.board_digest,
        snapshot_digest=snapshot.board_digest,
        context_digest=context_digest,
        active_layer_index=snapshot.active_layer_index,
        active_layer_name=snapshot.active_layer_name,
        selection=selection,
        kicad_version=snapshot.kicad_version,
        api_version=snapshot.api_version,
        compatibility=snapshot.compatibility,
    )


def inspect_live_editor_context(payload: Any, settings: Settings) -> dict[str, Any]:
    """Return a detached read-only editor context dictionary."""

    return inspect_live_editor_context_raw(payload, settings).to_dict()


__all__ = [
    "LIVE_EDITOR_CONTEXT_VERSION",
    "LiveEditorContext",
    "LiveEditorContextError",
    "LiveEditorContextRequest",
    "inspect_live_editor_context",
    "inspect_live_editor_context_raw",
    "parse_live_editor_context_request",
]
