"""Whole-board proposals retain baseline identities and disclose narrower disposal."""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest
from test_optimization_search_allocation import FIXTURE, _prepared, _slot

from copper_mcp.optimization.external_routing import (
    _extract_imported_copper,
    _render_disposed_copper,
    _validate_ses,
)
from copper_mcp.optimization.worker import OptimizationExecutionError

_BASE_VIA = b"""(via (at 20 17.5) (size 0.8) (drill 0.4) (layers "F.Cu" "B.Cu")
  (net "POWER") (uuid "82000000-0000-0000-0000-000000000001"))"""
_TREE = b"""(segment (start 10 15) (end 10 10) (width 0.25) (layer "F.Cu")
  (net "TREE") (uuid "82000000-0000-0000-0000-000000000002"))"""
_POWER = b"""(segment (start 20 17.5) (end 20 18) (width 0.25) (layer "F.Cu")
  (net "POWER") (uuid "82000000-0000-0000-0000-000000000003"))"""


def _append(source: bytes, *items: bytes) -> bytes:
    return source.rstrip()[:-1] + b"\n" + b"\n".join(items) + b"\n)\n"


def test_disposal_retains_original_copper_and_discards_known_out_of_scope_additions(tmp_path):
    source = _append(FIXTURE.read_bytes(), _BASE_VIA)
    prepared, settings = _prepared(tmp_path, source)
    tree = next(net.id for net in prepared.snapshot.content.nets if net.name == "TREE")
    probe = _slot(prepared)

    segments, vias, evidence = _extract_imported_copper(
        prepared, prepared.snapshot, _append(source, _TREE, _POWER), settings, (tree,), probe
    )
    output, snapshot, digest = _render_disposed_copper(
        prepared, source, prepared.snapshot, segments, vias, settings
    )

    assert evidence.proposal_scope == "whole-board"
    assert (evidence.original_target_count, evidence.routing_target_count) == (2, 1)
    assert evidence.retained_copper_count == evidence.accepted_copper_count == 1
    assert evidence.discarded_copper_count == 1
    assert evidence.accepted_copper_digest == digest
    assert evidence.original_target_scope_digest != evidence.routing_target_scope_digest
    assert snapshot.content.vias == prepared.snapshot.content.vias
    assert len(snapshot.content.segments) == 1 and snapshot.content.segments[0].net_id == tree
    assert _BASE_VIA in output and probe.routing_checks > 0


@pytest.mark.parametrize(
    "change",
    [
        "missing-original",
        "changed-original",
        "reidentified-original",
        "duplicate-geometry",
        "unsupported-discard",
    ],
)
def test_disposal_refuses_preservation_or_unclassified_geometry_failures(tmp_path, change):
    source = _append(FIXTURE.read_bytes(), _BASE_VIA)
    prepared, settings = _prepared(tmp_path, source)
    tree = next(net.id for net in prepared.snapshot.content.nets if net.name == "TREE")
    imported = _append(source, _TREE, _POWER)
    if change == "missing-original":
        imported = imported.replace(_BASE_VIA, b"")
    elif change == "changed-original":
        imported = imported.replace(_BASE_VIA, _BASE_VIA.replace(b"(at 20 17.5)", b"(at 21 17.5)"))
    elif change == "reidentified-original":
        imported = imported.replace(
            b"82000000-0000-0000-0000-000000000001", b"82000000-0000-0000-0000-000000000004"
        )
    elif change == "duplicate-geometry":
        imported = _append(imported, _POWER.replace(b"000000000003", b"000000000004"))
    else:
        imported = imported.replace(_POWER, _POWER.replace(b"(width 0.25)", b"(width 0.26)"))
    with pytest.raises(OptimizationExecutionError):
        _extract_imported_copper(
            prepared, prepared.snapshot, imported, settings, (tree,), _slot(prepared)
        )


@pytest.mark.parametrize(
    "case", ["identical-padstacks", "conflicting-padstacks", "pin-swap", "wrong-base"]
)
def test_native_session_metadata_is_admitted_only_without_semantic_ambiguity(tmp_path, case):
    prepared, settings = _prepared(tmp_path, FIXTURE.read_bytes())
    tree = next(net.id for net in prepared.snapshot.content.nets if net.name == "TREE")
    padstack = "(padstack VIA (shape (circle F.Cu 8000 0 0)) (attach off))"
    second = padstack.replace("8000", "9000") if case == "conflicting-padstacks" else padstack
    renaming = "(was_is (pins J1-1 J2-1))" if case == "pin-swap" else "(was_is)"
    base = "different" if case == "wrong-base" else "input"
    source = f"""(session input (base_design {base}) {renaming}
      (routes (resolution um 10) (parser)
        (library_out {padstack} {second})
        (network_out (net TREE (via VIA 100000 -166835)))))""".encode()
    if case == "identical-padstacks":
        _validate_ses(source, prepared.snapshot, settings, (tree,))
    else:
        with pytest.raises(OptimizationExecutionError):
            _validate_ses(source, prepared.snapshot, settings, (tree,))


@pytest.mark.parametrize("operation", ["export", "import"])
def test_native_worker_locks_only_private_copper_and_restores_original_lock_bits(
    monkeypatch, operation
):
    from copper_mcp.optimization import specctra_worker

    class Item:
        def __init__(self, identity, locked):
            self.m_Uuid = SimpleNamespace(AsString=lambda: identity)
            self.locked = locked

        def IsLocked(self):  # noqa: N802 - native pcbnew API
            return self.locked

        def SetLocked(self, locked):  # noqa: N802 - native pcbnew API
            self.locked = locked

    original = [Item("original-unlocked", False), Item("original-locked", True)]
    items = original.copy()
    board = SimpleNamespace(GetTracks=lambda: items)
    operations = []

    def native_stage(observed, _path):
        assert observed is board and all(item.locked for item in items)
        operations.append(operation)
        if operation == "import":
            items.append(Item("new", False))
        return True

    def save(_path, observed):
        assert observed is board and [item.locked for item in items] == [False, True, False]
        operations.append("save")
        return True

    monkeypatch.setitem(
        sys.modules,
        "pcbnew",
        SimpleNamespace(
            LoadBoard=lambda _path: board,
            ExportSpecctraDSN=native_stage,
            ImportSpecctraSES=native_stage,
            SaveBoard=save,
        ),
    )
    monkeypatch.setattr(sys, "argv", ["specctra_worker.py", operation])
    assert specctra_worker.main() == 0
    assert operations == (["import", "save"] if operation == "import" else ["export"])
