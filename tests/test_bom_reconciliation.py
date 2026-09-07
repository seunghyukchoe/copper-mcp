"""Read-only BOM reconciliation over captured artifacts and fresh native inventory."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import time
import traceback
from pathlib import Path

import pytest
from test_project_erc import build_project

from copper_mcp.config import Settings
from copper_mcp.engineering.bom_reconciliation import (
    BomReconciliationError,
    BomReconciliationReport,
    run_bom_reconciliation,
)
from copper_mcp.engineering.component_netlist import NativeComponent
from copper_mcp.engineering.inputs import parse_electrical_inputs
from copper_mcp.engineering.project_components import ProjectComponentInventory
from copper_mcp.engineering.schematic_project_capture import (
    ProjectFileBinding,
    capture_schematic_project,
)
from copper_mcp.optimization.contracts import digest_document


def _sha(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _csv(*rows: tuple[str, str, str, int, bool], extra: bool = False) -> bytes:
    header = '"Refs","Value","Footprint","Qty","DNP"'
    if extra:
        header += ',"Supplier"'
    lines = [header]
    for refs, value, footprint, quantity, dnp in rows:
        line = f'"{refs}","{value}","{footprint}","{quantity}","{"DNP" if dnp else ""}"'
        if extra:
            line += ',"private supplier"'
        lines.append(line)
    return ("\n".join(lines) + "\n").encode()


def _component(
    reference: str,
    value: str,
    footprint: str = "",
    *,
    dnp: bool = False,
    excluded_from_bom: bool = False,
) -> NativeComponent:
    return NativeComponent(
        reference,
        value,
        footprint,
        "",
        "Device:Part",
        "/",
        ("00000000-0000-4000-8000-000000000001",),
        excluded_from_bom,
        False,
        dnp,
    )


def _inventory(
    capture_digest: str, components: tuple[NativeComponent, ...]
) -> ProjectComponentInventory:
    return ProjectComponentInventory(
        capture_digest,
        "sha256:" + "1" * 64,
        "sha256:" + "2" * 64,
        "sha256:" + "3" * 64,
        "sha256:" + "4" * 64,
        "sha256:" + "5" * 64,
        "10.0.5",
        components,
        ("/",),
    )


def _write_artifacts(tmp_path: Path, artifacts: dict[str, tuple[str, bytes]]) -> bytes:
    paths = []
    for artifact_id, (path, payload) in artifacts.items():
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
        paths.append({"artifact_id": artifact_id, "path": path})
    return json.dumps(
        {
            "schema_version": "electrical-artifact-paths/v1",
            "artifacts": sorted(paths, key=lambda item: item["artifact_id"]),
        },
        separators=(",", ":"),
    ).encode()


def _declaration(
    artifacts: dict[str, tuple[str, bytes]],
    items: tuple[tuple[str, str, int], ...],
    *,
    model_item: str | None = None,
) -> bytes:
    roles = {
        artifact_id: (
            "bom"
            if path.endswith(".csv")
            else "schematic"
            if path.endswith(".kicad_sch")
            else "model-library"
        )
        for artifact_id, (path, _payload) in artifacts.items()
    }
    document: dict[str, object] = {
        "schema_version": "electrical-inputs/v1",
        "board_revision": "sha256:" + "a" * 64,
        "snapshot_digest": "sha256:" + "b" * 64,
        "project_context_digest": "sha256:" + "c" * 64,
        "profile_id": "mcu-sensor-v1",
        "source_artifacts": [
            {
                "artifact_id": artifact_id,
                "role": roles[artifact_id],
                "artifact_digest": _sha(payload),
            }
            for artifact_id, (_path, payload) in sorted(artifacts.items())
        ],
        "bom_bindings": [
            {"item_id": item_id, "artifact_id": artifact_id, "quantity": quantity}
            for item_id, artifact_id, quantity in items
        ],
    }
    if model_item is not None:
        document["model_bindings"] = [
            {
                "model_id": "checked-model",
                "bom_item_id": model_item,
                "artifact_id": "models-main",
                "model_kind": "spice",
                "model_digest": _sha(artifacts["models-main"][1]),
            }
        ]
    return json.dumps(document, separators=(",", ":")).encode()


def _bindings(declaration: bytes, capture_digest: str, items: dict[str, tuple[str, ...]]) -> bytes:
    return json.dumps(
        {
            "schema_version": "bom-component-bindings/v1",
            "declaration_digest": parse_electrical_inputs(declaration).digest,
            "project_capture_digest": capture_digest,
            "items": [
                {"item_id": item_id, "references": list(references)}
                for item_id, references in sorted(items.items())
            ],
        },
        separators=(",", ":"),
    ).encode()


def _case(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    bom: bytes | None = None,
    native: tuple[NativeComponent, ...] | None = None,
    item_quantities: tuple[tuple[str, str, int], ...] = (
        ("capacitors", "bom-main", 1),
        ("resistors", "bom-main", 1),
    ),
    item_references: dict[str, tuple[str, ...]] | None = None,
    extra_artifacts: dict[str, tuple[str, bytes]] | None = None,
    model_item: str | None = None,
):
    capture, libraries, _context = build_project(tmp_path)
    bom = bom or _csv(("C1", "100n", "", 1, False), ("R1", "1k", "", 1, False))
    artifacts = {"bom-main": ("inputs/bom.csv", bom), **(extra_artifacts or {})}
    declaration = _declaration(artifacts, item_quantities, model_item=model_item)
    paths = _write_artifacts(tmp_path, artifacts)
    if item_references is None:
        item_references = {"capacitors": ("C1",), "resistors": ("R1",)}
    bindings = _bindings(declaration, capture.digest, item_references)
    if native is None:
        native = (_component("C1", "100n"), _component("R1", "1k"))
    monkeypatch.setattr(
        "copper_mcp.engineering.bom_reconciliation.run_project_component_inventory",
        lambda *args, **kwargs: _inventory(capture.digest, native),
    )
    report = run_bom_reconciliation(
        capture,
        libraries,
        declaration,
        paths,
        bindings,
        Settings(workspace=tmp_path),
    )
    return report, capture, libraries, declaration, paths, bindings


def _counts(report) -> dict[str, int]:
    return dict(report.mismatch_counts)


def test_internal_pair_retains_exact_inventory_and_legacy_report_digest(tmp_path, monkeypatch):
    from copper_mcp.engineering import bom_reconciliation as reconciliation

    legacy, capture, libraries, declaration, paths, bindings = _case(tmp_path, monkeypatch)
    assert (
        legacy.digest == "sha256:2b3a1d68c105695f6cbb3274779dcf1a47db0a8e5caa7f078b3cb9ff9353cf7b"
    )
    inventory = _inventory(capture.digest, (_component("C1", "100n"), _component("R1", "1k")))
    calls = []

    def native(*args, **kwargs):
        calls.append(True)
        return inventory

    monkeypatch.setattr(reconciliation, "_native_inventory", native)
    report, retained = reconciliation._run_bom_reconciliation(
        capture, libraries, declaration, paths, bindings, Settings(workspace=tmp_path)
    )
    assert calls == [True]
    assert retained is inventory
    assert report == legacy and report.digest == legacy.digest
    assert report.native_inventory_digest == inventory.digest
    assert report.native_inventory_digest != inventory.inventory_digest


def test_complete_match_binds_models_and_redacts_private_metadata(tmp_path, monkeypatch):
    report, capture, _libraries, declaration, _paths, _bindings_json = _case(
        tmp_path,
        monkeypatch,
        bom=_csv(("C1", "100n", "", 1, False), ("R1", "1k", "", 1, False), extra=True),
        extra_artifacts={"models-main": ("inputs/models.lib", b"private model bytes")},
        model_item="resistors",
    )

    assert report.metadata_agreement == "pass"
    assert not any(_counts(report).values())
    assert report.project_capture_digest == capture.digest
    assert report.declaration_digest == parse_electrical_inputs(declaration).digest
    assert report.binding_document_digest
    assert report.unvalidated_extra_field_count == 2
    associations = {item.item_id: item for item in report.associations}
    assert associations["resistors"].declared_model_ids == ("checked-model",)
    assert associations["capacitors"].declared_model_ids == ()
    assert repr(report) == "<BomReconciliationReport redacted>"
    assert repr(associations["resistors"]) == "<BomItemAssociation redacted>"

    document = report.document()
    assert document["metadata_agreement"] == "pass"
    assert document["coverage_fields"] == ["reference", "value", "footprint", "quantity", "dnp"]
    assert document["model_definition_validation"] == "not_run"
    assert document["ratings_validation"] == "not_run"
    assert document["engineering_verdict"] == "not_run"
    assert document["apply_authority"] == "none"
    rendered = json.dumps(document, sort_keys=True)
    for private in ("R1", "100n", "private", "Supplier", "checked-model", "resistors"):
        assert private not in rendered
    assert report.digest
    assert report.binding_document_digest != report.declaration_digest


def test_full_native_execution_identity_changes_report_identity(tmp_path, monkeypatch):
    first, capture, libraries, declaration, paths, bindings = _case(tmp_path, monkeypatch)
    components = (_component("C1", "100n"), _component("R1", "1k"))
    baseline = _inventory(capture.digest, components)
    changed_backend = dataclasses.replace(
        baseline, backend_authentication_digest="sha256:" + "9" * 64
    )
    assert baseline.inventory_digest == changed_backend.inventory_digest
    assert baseline.digest != changed_backend.digest
    monkeypatch.setattr(
        "copper_mcp.engineering.bom_reconciliation.run_project_component_inventory",
        lambda *args, **kwargs: changed_backend,
    )

    second = run_bom_reconciliation(
        capture,
        libraries,
        declaration,
        paths,
        bindings,
        Settings(workspace=tmp_path),
    )

    assert first.native_inventory_digest != second.native_inventory_digest
    assert first.digest != second.digest


def test_streamed_receipt_hashes_preserve_canonical_identities(tmp_path, monkeypatch):
    report, capture, *_ = _case(tmp_path, monkeypatch)
    document = dataclasses.asdict(report)
    document["mismatch_counts"] = dict(report.mismatch_counts)
    document.update(
        coverage_fields=("reference", "value", "footprint", "quantity", "dnp"),
        model_definition_validation="not_run",
        ratings_validation="not_run",
        engineering_verdict="not_run",
        apply_authority="none",
    )
    assert (
        report.digest
        == report._digest(time.monotonic() + 60)
        == digest_document("copper-mcp/bom-reconciliation/v1", document)
    )
    inventory = _inventory(capture.digest, (_component("R1", "한글 Ω"),))
    native_document = dataclasses.asdict(inventory)
    del native_document["components"], native_document["sheet_paths"]
    native_document["native_inventory_digest"] = inventory.inventory_digest
    assert (
        inventory.digest
        == inventory._digest(time.monotonic() + 60)
        == digest_document("copper-mcp/project-component-inventory/v1", native_document)
    )


@pytest.mark.parametrize("stage", ("native", "report"))
def test_hashing_stops_at_the_shared_deadline(tmp_path, monkeypatch, stage):
    original_clock = time.monotonic
    original_encode = json.JSONEncoder.iterencode
    expired = False

    def expire_after_token(encoder, document, *args, **kwargs):
        nonlocal expired
        selected = isinstance(document, dict) and (
            (stage == "native" and "components" in document and "backend_version" in document)
            or (stage == "report" and "associations" in document)
        )
        for token in original_encode(encoder, document, *args, **kwargs):
            if selected:
                expired = True
            yield token
            if selected:
                pytest.fail("hashing continued beyond the shared BOM deadline")

    monkeypatch.setattr(json.JSONEncoder, "iterencode", expire_after_token)
    monkeypatch.setattr(time, "monotonic", lambda: original_clock() + (3601 if expired else 0))
    with pytest.raises(BomReconciliationError, match="deadline expired") as caught:
        _case(tmp_path, monkeypatch)
    assert caught.value.__cause__ is None and caught.value.__context__ is None


@pytest.mark.parametrize(
    ("bom", "native", "quantities", "references", "expected"),
    (
        (
            _csv(("R1", "1k", "", 1, False)),
            (_component("C1", "100n"), _component("R1", "1k")),
            (("resistors", "bom-main", 1),),
            {"resistors": ("R1",)},
            "missing_component",
        ),
        (
            _csv(("C1", "100n", "", 1, False), ("R1", "1k", "", 1, False)),
            (_component("R1", "1k"),),
            (("capacitors", "bom-main", 1), ("resistors", "bom-main", 1)),
            {"capacitors": ("C1",), "resistors": ("R1",)},
            "unexpected_component",
        ),
        (
            _csv(("C1", "wrong", "", 1, False), ("R1", "1k", "", 1, False)),
            None,
            None,
            None,
            "value_mismatch",
        ),
        (
            _csv(("C1", "100n", "0805", 1, False), ("R1", "1k", "", 1, False)),
            None,
            None,
            None,
            "footprint_mismatch",
        ),
        (
            _csv(("C1", "100n", "", 1, True), ("R1", "1k", "", 1, False)),
            None,
            None,
            None,
            "dnp_mismatch",
        ),
        (
            _csv(("C1", "100n", "", 1, False), ("R1", "1k", "", 1, False)),
            None,
            (("capacitors", "bom-main", 2), ("resistors", "bom-main", 1)),
            None,
            "quantity_mismatch",
        ),
        (
            _csv(("C1", "100n", "", 1, False), ("R1", "1k", "", 1, False)),
            None,
            None,
            {"capacitors": ("C1", "R1"), "resistors": ("R1",)},
            "item_row_mismatch",
        ),
    ),
)
def test_well_formed_disagreements_return_fail(
    tmp_path, monkeypatch, bom, native, quantities, references, expected
):
    kwargs = {"bom": bom}
    if native is not None:
        kwargs["native"] = native
    if quantities is not None:
        kwargs["item_quantities"] = quantities
    if references is not None:
        kwargs["item_references"] = references
    report, *_ = _case(tmp_path, monkeypatch, **kwargs)
    assert report.metadata_agreement == "fail"
    assert _counts(report)[expected] > 0


def test_duplicate_component_and_row_selection_across_artifacts_fail(tmp_path, monkeypatch):
    second = _csv(("R1", "1k", "", 1, False))
    report, *_ = _case(
        tmp_path,
        monkeypatch,
        bom=_csv(("C1", "100n", "", 1, False), ("R1", "1k", "", 1, False)),
        extra_artifacts={"bom-secondary": ("inputs/secondary.csv", second)},
        item_quantities=(
            ("capacitors", "bom-main", 1),
            ("resistors", "bom-main", 1),
            ("second-resistor", "bom-secondary", 1),
        ),
        item_references={
            "capacitors": ("C1",),
            "resistors": ("R1",),
            "second-resistor": ("R1",),
        },
    )
    assert report.metadata_agreement == "fail"
    assert _counts(report)["duplicate_component"] == 1


def test_declared_schematic_digest_must_belong_to_project_capture(tmp_path, monkeypatch):
    report, *_ = _case(
        tmp_path,
        monkeypatch,
        extra_artifacts={
            "schematic-declaration": ("inputs/declared.kicad_sch", b"private other schematic")
        },
    )
    assert report.metadata_agreement == "fail"
    assert _counts(report)["declared_schematic_mismatch"] == 1


def test_unselected_and_multiply_selected_rows_are_counted(tmp_path, monkeypatch):
    report, *_ = _case(
        tmp_path,
        monkeypatch,
        item_references={"capacitors": ("R1",), "resistors": ("R1",)},
    )
    counts = _counts(report)
    assert counts["missing_row_binding"] == 1
    assert counts["duplicate_row_binding"] == 1


def test_empty_native_scope_with_contradictory_bom_fails_before_inconclusive(tmp_path, monkeypatch):
    empty, *_ = _case(tmp_path, monkeypatch, native=())
    assert empty.metadata_agreement == "fail"
    assert empty.reasons == ("no_component_scope",)
    assert _counts(empty)["unexpected_component"] == 2


@pytest.mark.parametrize("excluded", (False, True))
def test_matching_empty_bindings_and_eligible_scope_are_inconclusive(
    tmp_path, monkeypatch, excluded
):
    native = (_component("R1", "1k", excluded_from_bom=True),) if excluded else ()
    report, *_ = _case(
        tmp_path,
        monkeypatch,
        bom=_csv(),
        native=native,
        item_quantities=(),
        item_references={},
    )
    assert report.metadata_agreement == "inconclusive"
    assert report.reasons == ("no_component_scope",)
    assert not any(_counts(report).values())
    assert report.bom_row_count == report.bom_reference_count == 0
    assert report.associations == ()
    assert report.document()["model_definition_validation"] == "not_run"
    assert report.document()["apply_authority"] == "none"


def test_empty_bindings_do_not_hide_nonempty_native_scope(tmp_path, monkeypatch):
    report, *_ = _case(
        tmp_path,
        monkeypatch,
        bom=_csv(),
        item_quantities=(),
        item_references={},
    )
    assert report.metadata_agreement == "fail"
    assert _counts(report)["missing_component"] == 2
    assert report.reasons == ()


def test_empty_bindings_do_not_hide_contradictory_bom_rows(tmp_path, monkeypatch):
    report, *_ = _case(
        tmp_path,
        monkeypatch,
        native=(),
        item_quantities=(),
        item_references={},
    )
    assert report.metadata_agreement == "fail"
    assert _counts(report)["unexpected_component"] == 2
    assert _counts(report)["missing_row_binding"] == 2
    assert report.reasons == ("no_component_scope",)


@pytest.mark.parametrize("empty_declaration", (False, True))
def test_one_sided_empty_binding_sets_still_refuse(tmp_path, monkeypatch, empty_declaration):
    with pytest.raises(BomReconciliationError, match="bindings are malformed"):
        _case(
            tmp_path,
            monkeypatch,
            bom=_csv(),
            native=(),
            item_quantities=() if empty_declaration else (("resistors", "bom-main", 1),),
            item_references={"resistors": ("R1",)} if empty_declaration else {},
        )


def test_empty_scope_still_requires_an_explicit_items_field(tmp_path, monkeypatch):
    report, capture, libraries, declaration, paths, bindings = _case(
        tmp_path,
        monkeypatch,
        bom=_csv(),
        native=(),
        item_quantities=(),
        item_references={},
    )
    assert report.metadata_agreement == "inconclusive"
    missing_items = json.loads(bindings)
    del missing_items["items"]
    with pytest.raises(BomReconciliationError, match="bindings are malformed"):
        run_bom_reconciliation(
            capture,
            libraries,
            declaration,
            paths,
            json.dumps(missing_items).encode(),
            Settings(workspace=tmp_path),
        )


def test_all_bom_excluded_components_have_no_eligible_scope(tmp_path, monkeypatch):
    report, *_ = _case(
        tmp_path,
        monkeypatch,
        native=(_component("R1", "1k", excluded_from_bom=True),),
    )
    assert report.metadata_agreement == "fail"
    assert report.reasons == ("no_component_scope",)


@pytest.mark.parametrize("bound", ("_MAX_BOM_ROWS", "_MAX_BOM_REFERENCES"))
def test_bom_limits_are_cumulative_across_artifacts(tmp_path, monkeypatch, bound):
    from copper_mcp.engineering import bom_reconciliation as reconciliation

    monkeypatch.setattr(reconciliation, bound, 2, raising=False)
    report, *_ = _case(tmp_path / "at-limit", monkeypatch)
    assert report.metadata_agreement == "pass"
    with pytest.raises(BomReconciliationError):
        _case(
            tmp_path / "over-limit",
            monkeypatch,
            extra_artifacts={
                "bom-secondary": ("inputs/secondary.csv", _csv(("R2", "1k", "", 1, False)))
            },
            item_quantities=(
                ("capacitors", "bom-main", 1),
                ("resistors", "bom-main", 1),
                ("second-resistor", "bom-secondary", 1),
            ),
            item_references={
                "capacitors": ("C1",),
                "resistors": ("R1",),
                "second-resistor": ("R2",),
            },
        )


def test_excluded_parts_are_not_expected(tmp_path, monkeypatch):
    excluded, *_ = _case(
        tmp_path,
        monkeypatch,
        native=(
            _component("C1", "100n"),
            _component("R1", "1k"),
            _component("X1", "ignored", excluded_from_bom=True),
        ),
    )
    assert excluded.metadata_agreement == "pass"


def test_dnp_native_component_remains_in_expected_scope(tmp_path, monkeypatch):
    report, *_ = _case(
        tmp_path,
        monkeypatch,
        bom=_csv(("C1", "100n", "", 1, True), ("R1", "1k", "", 1, False)),
        native=(_component("C1", "100n", dnp=True), _component("R1", "1k")),
    )
    assert report.metadata_agreement == "pass"


def test_malformed_binding_refuses_before_capture_or_native_execution(tmp_path, monkeypatch):
    capture, libraries, _ = build_project(tmp_path)
    declaration = _declaration(
        {"bom-main": ("inputs/bom.csv", _csv(("R1", "1k", "", 1, False)))},
        (("resistors", "bom-main", 1),),
    )
    monkeypatch.setattr(
        "copper_mcp.engineering.bom_reconciliation.capture_electrical_artifacts",
        lambda *args, **kwargs: pytest.fail("must refuse before capture"),
    )
    monkeypatch.setattr(
        "copper_mcp.engineering.bom_reconciliation.run_project_component_inventory",
        lambda *args, **kwargs: pytest.fail("must refuse before native execution"),
    )
    with pytest.raises(
        BomReconciliationError, match=r"^BOM reconciliation bindings are malformed$"
    ):
        run_bom_reconciliation(
            capture,
            libraries,
            declaration,
            b"{}",
            b'{"schema_version":"wrong"}',
            Settings(workspace=tmp_path),
        )


def test_recapture_tampering_and_project_source_drift_are_refused(tmp_path, monkeypatch):
    from copper_mcp.engineering import bom_reconciliation as reconciliation

    capture, libraries, context = build_project(tmp_path)
    bom = _csv(("C1", "100n", "", 1, False), ("R1", "1k", "", 1, False))
    artifacts = {"bom-main": ("inputs/bom.csv", bom)}
    declaration = _declaration(
        artifacts, (("capacitors", "bom-main", 1), ("resistors", "bom-main", 1))
    )
    paths = _write_artifacts(tmp_path, artifacts)
    bindings = _bindings(declaration, capture.digest, {"capacitors": ("C1",), "resistors": ("R1",)})
    monkeypatch.setattr(
        reconciliation,
        "run_project_component_inventory",
        lambda *args, **kwargs: _inventory(
            capture.digest, (_component("C1", "100n"), _component("R1", "1k"))
        ),
    )
    original_capture = reconciliation.capture_electrical_artifacts
    capture_calls = 0

    def tampering_capture(*args, **kwargs):
        nonlocal capture_calls
        result = original_capture(*args, **kwargs)
        capture_calls += 1
        if capture_calls == 1:
            (tmp_path / "inputs/bom.csv").write_bytes(bom + b"\n")
        return result

    monkeypatch.setattr(reconciliation, "capture_electrical_artifacts", tampering_capture)
    with pytest.raises(BomReconciliationError, match="freshness") as caught:
        run_bom_reconciliation(
            capture, libraries, declaration, paths, bindings, Settings(workspace=tmp_path)
        )
    assert caught.value.__cause__ is None and caught.value.__context__ is None

    (tmp_path / "inputs/bom.csv").write_bytes(bom)
    monkeypatch.setattr(reconciliation, "capture_electrical_artifacts", original_capture)

    def drifting_inventory(*args, **kwargs):
        (tmp_path / capture.root_path).write_bytes(context[capture.root_path] + b"\n")
        return _inventory(capture.digest, (_component("C1", "100n"), _component("R1", "1k")))

    monkeypatch.setattr(reconciliation, "run_project_component_inventory", drifting_inventory)
    with pytest.raises(BomReconciliationError, match="freshness") as caught:
        run_bom_reconciliation(
            capture, libraries, declaration, paths, bindings, Settings(workspace=tmp_path)
        )
    assert caught.value.__cause__ is None and caught.value.__context__ is None


def test_expired_deadline_refuses_without_execution_or_private_trace(tmp_path, monkeypatch):
    capture, libraries, _ = build_project(tmp_path)
    bom = _csv(("R1", "private-value", "", 1, False))
    artifacts = {"bom-main": ("inputs/bom.csv", bom)}
    declaration = _declaration(artifacts, (("resistors", "bom-main", 1),))
    paths = _write_artifacts(tmp_path, artifacts)
    bindings = _bindings(declaration, capture.digest, {"resistors": ("R1",)})
    monkeypatch.setattr(
        "copper_mcp.engineering.bom_reconciliation.run_project_component_inventory",
        lambda *args, **kwargs: pytest.fail("must not execute"),
    )
    with pytest.raises(BomReconciliationError, match="deadline expired") as caught:
        run_bom_reconciliation(
            capture,
            libraries,
            declaration,
            paths,
            bindings,
            Settings(workspace=tmp_path),
            deadline=time.monotonic() - 1,
        )
    rendered = "".join(traceback.format_exception(caught.value))
    assert "private-value" not in rendered
    assert caught.value.__cause__ is None and caught.value.__context__ is None


def test_controls_and_deadline_are_checked_before_json_parsing(tmp_path, monkeypatch):
    capture, libraries, _ = build_project(tmp_path)
    monkeypatch.setattr(
        "copper_mcp.engineering.bom_reconciliation._parse_declaration",
        lambda *_args: pytest.fail("JSON parsing must not start"),
    )
    bad_settings = dataclasses.replace(Settings(workspace=tmp_path), workspace="private")
    with pytest.raises(BomReconciliationError, match="settings are malformed"):
        run_bom_reconciliation(
            capture,
            libraries,
            b"private malformed declaration",
            b"private malformed paths",
            b"private malformed bindings",
            bad_settings,  # type: ignore[arg-type]
        )
    with pytest.raises(BomReconciliationError, match="deadline expired"):
        run_bom_reconciliation(
            capture,
            libraries,
            b"private malformed declaration",
            b"private malformed paths",
            b"private malformed bindings",
            Settings(workspace=tmp_path),
            deadline=time.monotonic() - 1,
        )


def test_deadline_is_rechecked_after_declaration_parsing(tmp_path, monkeypatch):
    from copper_mcp.engineering import bom_reconciliation as reconciliation

    capture, libraries, _ = build_project(tmp_path)
    bom = _csv(("R1", "1k", "", 1, False))
    declaration = _declaration(
        {"bom-main": ("inputs/bom.csv", bom)}, (("resistors", "bom-main", 1),)
    )
    expired = False
    original_parse = reconciliation._parse_declaration

    def expiring_parse(payload: bytes):
        nonlocal expired
        result = original_parse(payload)
        expired = True
        return result

    monkeypatch.setattr(reconciliation, "_parse_declaration", expiring_parse)
    monkeypatch.setattr(
        reconciliation,
        "_parse_bindings",
        lambda *_args: pytest.fail("binding parse must not start after deadline"),
    )
    monkeypatch.setattr(reconciliation.time, "monotonic", lambda: 2.0 if expired else 0.0)
    with pytest.raises(BomReconciliationError, match="deadline expired"):
        run_bom_reconciliation(
            capture,
            libraries,
            declaration,
            b"{}",
            b"{}",
            Settings(workspace=tmp_path, kicad_timeout_seconds=1),
            deadline=1.0,
        )


def test_source_change_while_hashing_report_is_caught(tmp_path, monkeypatch):
    from copper_mcp.engineering import bom_reconciliation as reconciliation

    capture, libraries, context = build_project(tmp_path)
    bom = _csv(("C1", "100n", "", 1, False), ("R1", "1k", "", 1, False))
    artifacts = {"bom-main": ("inputs/bom.csv", bom)}
    declaration = _declaration(
        artifacts, (("capacitors", "bom-main", 1), ("resistors", "bom-main", 1))
    )
    paths = _write_artifacts(tmp_path, artifacts)
    bindings = _bindings(declaration, capture.digest, {"capacitors": ("C1",), "resistors": ("R1",)})
    monkeypatch.setattr(
        reconciliation,
        "run_project_component_inventory",
        lambda *args, **kwargs: _inventory(
            capture.digest, (_component("C1", "100n"), _component("R1", "1k"))
        ),
    )
    original_digest = BomReconciliationReport._digest

    def drifting_digest(report: BomReconciliationReport, deadline: float) -> str:
        (tmp_path / capture.root_path).write_bytes(context[capture.root_path] + b"\n")
        return original_digest(report, deadline)

    monkeypatch.setattr(BomReconciliationReport, "_digest", drifting_digest)
    with pytest.raises(BomReconciliationError, match="freshness"):
        run_bom_reconciliation(
            capture, libraries, declaration, paths, bindings, Settings(workspace=tmp_path)
        )


_CONFIGURED_CLI = os.environ.get("COPPER_MCP_TEST_PROJECT_ERC_CLI")


@pytest.mark.real_kicad
@pytest.mark.parametrize("scenario", ("match", "missing-component", "wrong-value", "wrong-dnp"))
@pytest.mark.skipif(
    not _CONFIGURED_CLI, reason="requires explicit vendor-sealed KiCad 10.0.5 test backend"
)
def test_real_native_reconciliation_checks_complete_known_fixture(tmp_path, scenario):
    capture, libraries, context = build_project(tmp_path)
    rows = [("C1", "100n", "", 1, scenario == "wrong-dnp")]
    if scenario == "missing-component":
        rows.clear()
    rows.append(("R1", "2k" if scenario == "wrong-value" else "1k", "", 1, False))
    bom = _csv(*rows)
    artifacts = {"bom-main": ("inputs/bom.csv", bom)}
    item_refs = {"resistors": ("R1",)}
    if scenario != "missing-component":
        item_refs["capacitors"] = ("C1",)
    declaration = _declaration(
        artifacts, tuple((item, "bom-main", 1) for item in sorted(item_refs))
    )
    paths = _write_artifacts(tmp_path, artifacts)
    bindings = _bindings(declaration, capture.digest, item_refs)
    report = run_bom_reconciliation(
        capture,
        libraries,
        declaration,
        paths,
        bindings,
        Settings(
            workspace=tmp_path,
            kicad_cli=Path(_CONFIGURED_CLI),
            kicad_timeout_seconds=30,
        ),
    )
    assert report.metadata_agreement == ("pass" if scenario == "match" else "fail")
    if scenario != "match":
        expected = {
            "missing-component": "missing_component",
            "wrong-value": "value_mismatch",
            "wrong-dnp": "dnp_mismatch",
        }[scenario]
        assert dict(report.mismatch_counts)[expected] == 1
    assert all((tmp_path / name).read_bytes() == payload for name, payload in context.items())


@pytest.mark.real_kicad
@pytest.mark.parametrize("exclude_all", (False, True))
@pytest.mark.skipif(
    not _CONFIGURED_CLI, reason="requires explicit vendor-sealed KiCad 10.0.5 test backend"
)
def test_real_empty_bindings_distinguish_missing_and_ineligible_components(tmp_path, exclude_all):
    capture, libraries, context = build_project(tmp_path)
    if exclude_all:
        assert b"(in_bom yes)" in context[capture.root_path]
        context = {
            name: payload.replace(b"(in_bom yes)", b"(in_bom no)")
            if name.endswith(".kicad_sch")
            else payload
            for name, payload in context.items()
        }
        libraries = tuple(
            dataclasses.replace(
                library,
                content=(content := library.content.replace(b"(in_bom yes)", b"(in_bom no)")),
                digest=_sha(content),
            )
            for library in libraries
        )
        for name, payload in context.items():
            (tmp_path / name).write_bytes(payload)
        capture = capture_schematic_project(
            tmp_path,
            capture.root_path,
            tuple(ProjectFileBinding(name, _sha(payload)) for name, payload in context.items()),
        )
    artifacts = {"bom-main": ("inputs/bom.csv", _csv())}
    declaration = _declaration(artifacts, ())
    paths = _write_artifacts(tmp_path, artifacts)
    bindings = _bindings(declaration, capture.digest, {})
    report = run_bom_reconciliation(
        capture,
        libraries,
        declaration,
        paths,
        bindings,
        Settings(workspace=tmp_path, kicad_cli=Path(_CONFIGURED_CLI), kicad_timeout_seconds=30),
    )
    assert report.metadata_agreement == ("inconclusive" if exclude_all else "fail")
    assert report.native_component_count == 2
    assert report.reasons == (("no_component_scope",) if exclude_all else ())
    assert dict(report.mismatch_counts)["missing_component"] == (0 if exclude_all else 2)
    if exclude_all:
        assert not any(dict(report.mismatch_counts).values())
    assert report.document()["apply_authority"] == "none"
    assert all((tmp_path / name).read_bytes() == payload for name, payload in context.items())
