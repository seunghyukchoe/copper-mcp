from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import replace
from typing import cast

import pytest

from copper_mcp.engineering._spice_terminal_binding import (
    SpiceTerminalBindingError,
    join_terminal_bindings,
    parse_terminal_bindings,
)
from copper_mcp.engineering.bom_reconciliation import BomItemAssociation, BomReconciliationReport
from copper_mcp.engineering.capture import (
    ElectricalArtifactCapture,
    PublicElectricalCaptureProjection,
    _CapturedArtifact,
)
from copper_mcp.engineering.inputs import ElectricalInputs, parse_electrical_inputs
from copper_mcp.engineering.native_pin_net_map import NativePinNet, NativePinNetMap, SourcePinAlias
from copper_mcp.engineering.project_pin_net_map import ProjectPinNetMap


def _sha(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _declaration(library: bytes, *, extra_nonspice: bool = False) -> ElectricalInputs:
    models: list[dict[str, str]] = [
        {
            "model_id": "resistor-model",
            "bom_item_id": "resistors",
            "artifact_id": "models-main",
            "model_kind": "spice",
            "model_digest": _sha(b"legacy opaque model identity"),
        }
    ]
    if extra_nonspice:
        models.append(
            {
                "model_id": "thermal-model",
                "bom_item_id": "resistors",
                "artifact_id": "models-main",
                "model_kind": "thermal",
                "model_digest": _sha(b"opaque thermal identity"),
            }
        )
    return parse_electrical_inputs(
        json.dumps(
            {
                "schema_version": "electrical-inputs/v1",
                "board_revision": _sha(b"board"),
                "snapshot_digest": _sha(b"snapshot"),
                "project_context_digest": _sha(b"project"),
                "profile_id": "analog-audio-v1",
                "source_artifacts": [
                    {"artifact_id": "bom-main", "role": "bom", "artifact_digest": _sha(b"bom")},
                    {
                        "artifact_id": "models-main",
                        "role": "model-library",
                        "artifact_digest": _sha(library),
                    },
                ],
                "bom_bindings": [
                    {"item_id": "resistors", "artifact_id": "bom-main", "quantity": 1}
                ],
                "model_bindings": models,
            },
            separators=(",", ":"),
        ).encode()
    )


def _capture(declaration: ElectricalInputs, library: bytes) -> ElectricalArtifactCapture:
    artifacts = (
        _CapturedArtifact("bom-main", "bom", _sha(b"bom"), b"bom"),
        _CapturedArtifact("models-main", "model-library", _sha(library), library),
    )
    digest = _sha(b"captured")
    return ElectricalArtifactCapture(
        artifacts,
        declaration.digest,
        digest,
        PublicElectricalCaptureProjection(
            declaration_digest=declaration.digest,
            capture_digest=digest,
            artifact_count=2,
            total_bytes=len(b"bom") + len(library),
        ),
    )


def _bom(
    declaration: ElectricalInputs, capture: ElectricalArtifactCapture
) -> BomReconciliationReport:
    return BomReconciliationReport(
        _sha(b"project-capture"),
        _sha(b"inventory"),
        declaration.digest,
        capture.digest,
        _sha(b"binding"),
        "pass",
        (("duplicate_component", 0),),
        (),
        (
            BomItemAssociation(
                "resistors",
                "bom-main",
                ("R1",),
                tuple(model.model_id for model in declaration.model_bindings),
            ),
        ),
        1,
        1,
        1,
        0,
    )


def _pin_map(
    project_capture_digest: str, *, pins: tuple[NativePinNet, ...] | None = None
) -> ProjectPinNetMap:
    nodes = pins or (
        NativePinNet("R1", "1", "Device:R", "1", "SIG", "Default", None, "passive", False, ()),
        NativePinNet("R1", "2", "Device:R", "2", "GND", "Default", None, "passive", False, ()),
    )
    mapping = NativePinNetMap(_sha(b"census"), _sha(b"inventory"), "10.0.5", nodes, 0)
    return ProjectPinNetMap(
        project_capture_digest,
        _sha(b"execution"),
        _sha(b"syntax"),
        _sha(b"command"),
        _sha(b"executable"),
        _sha(b"authentication"),
        mapping,
    )


def _bindings(
    declaration: ElectricalInputs, library: bytes, *, port: str = "A", kind: str = "port"
) -> bytes:
    source = b".model DMOD D(IS=1e-12)\n"
    binding: dict[str, str] = {"kind": kind}
    if kind == "port":
        binding["port"] = port
    return json.dumps(
        {
            "schema_version": "project-spice-terminal-bindings/v1",
            "declaration_digest": declaration.digest,
            "project_capture_digest": _sha(b"project-capture"),
            "models": [
                {
                    "model_id": "resistor-model",
                    "definition_name": "DMOD",
                    "definition_digest": _sha(source),
                    "references": [
                        {
                            "reference": "R1",
                            "pins": [
                                {"pin_number": "1", "binding": binding},
                                {"pin_number": "2", "binding": {"kind": "port", "port": "K"}},
                            ],
                        }
                    ],
                }
            ],
        },
        separators=(",", ":"),
    ).encode()


def _json(payload: bytes) -> dict[str, object]:
    return cast(dict[str, object], json.loads(payload))


def _payload(document: dict[str, object]) -> bytes:
    return json.dumps(document, separators=(",", ":")).encode()


def _assert_fixed(error: SpiceTerminalBindingError) -> None:
    assert error.__cause__ is None and error.__context__ is None


def test_complete_join_preserves_native_pin_and_redacts_private_values() -> None:
    library = b".model DMOD D(IS=1e-12)\n"
    declaration = _declaration(library)
    capture = _capture(declaration, library)
    document = parse_terminal_bindings(
        _bindings(declaration, library), deadline=time.monotonic() + 10
    )

    result = join_terminal_bindings(
        document,
        declaration,
        capture,
        _bom(declaration, capture),
        _pin_map(_sha(b"project-capture")),
        deadline=time.monotonic() + 10,
    )

    assert len(result) == 1
    assert result[0].model_id == "resistor-model"
    assert result[0].artifact_id == "models-main"
    assert result[0].definition.source == library
    assert result[0].pins[0].node.reference == "R1"
    assert result[0].pins[0].port == "A"
    assert "R1" not in repr(result[0])


def test_accepts_opaque_model_digest_and_extra_nonspice_model() -> None:
    library = b".model DMOD D(IS=1e-12)\n"
    declaration = _declaration(library, extra_nonspice=True)
    capture = _capture(declaration, library)
    document = parse_terminal_bindings(
        _bindings(declaration, library), deadline=time.monotonic() + 10
    )
    result = join_terminal_bindings(
        document,
        declaration,
        capture,
        _bom(declaration, capture),
        _pin_map(_sha(b"project-capture")),
        deadline=time.monotonic() + 10,
    )
    assert result[0].model_id == "resistor-model"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload.replace(b'"port":"A"', b'"port":"a"'),
        lambda payload: payload.replace(b'"kind":"port","port":"A"', b'"kind":"nc"'),
    ],
)
def test_rejects_nonexact_port_digest_and_connected_nc(mutate: Callable[[bytes], bytes]) -> None:
    library = b".model DMOD D(IS=1e-12)\n"
    declaration = _declaration(library)
    capture = _capture(declaration, library)
    document = parse_terminal_bindings(
        mutate(_bindings(declaration, library)), deadline=time.monotonic() + 10
    )
    with pytest.raises(SpiceTerminalBindingError):
        join_terminal_bindings(
            document,
            declaration,
            capture,
            _bom(declaration, capture),
            _pin_map(_sha(b"project-capture")),
            deadline=time.monotonic() + 10,
        )


def test_join_rejects_wrong_definition_digest() -> None:
    library = b".model DMOD D(IS=1e-12)\n"
    declaration = _declaration(library)
    capture = _capture(declaration, library)
    payload = _bindings(declaration, library).replace(
        _sha(library).encode(), _sha(b"other").encode()
    )
    document = parse_terminal_bindings(payload, deadline=time.monotonic() + 10)
    with pytest.raises(SpiceTerminalBindingError):
        join_terminal_bindings(
            document,
            declaration,
            capture,
            _bom(declaration, capture),
            _pin_map(_sha(b"project-capture")),
            deadline=time.monotonic() + 10,
        )


def test_join_rejects_incomplete_bom_scope_and_duplicate_native_pin() -> None:
    library = b".model DMOD D(IS=1e-12)\n"
    declaration = _declaration(library)
    capture = _capture(declaration, library)
    document = parse_terminal_bindings(
        _bindings(declaration, library), deadline=time.monotonic() + 10
    )
    incomplete = _bom(declaration, capture)
    object.__setattr__(incomplete, "associations", ())
    with pytest.raises(SpiceTerminalBindingError):
        join_terminal_bindings(
            document,
            declaration,
            capture,
            incomplete,
            _pin_map(_sha(b"project-capture")),
            deadline=time.monotonic() + 10,
        )


@pytest.mark.parametrize(
    "payload",
    (
        b"{}",
        b'{"schema_version":"project-spice-terminal-bindings/v1","schema_version":"x"}',
        b'{"schema_version":"project-spice-terminal-bindings/v1","models":[]}',
    ),
)
def test_parse_refuses_malformed_closed_json_without_context(payload: bytes) -> None:
    with pytest.raises(SpiceTerminalBindingError) as error:
        parse_terminal_bindings(payload, deadline=time.monotonic() + 10)
    _assert_fixed(error.value)


@pytest.mark.parametrize("deadline", (None, True, float("inf"), -1.0))
def test_invalid_or_expired_deadlines_are_fixed(deadline: object) -> None:
    with pytest.raises(SpiceTerminalBindingError) as error:
        parse_terminal_bindings(b"{}", deadline=deadline)  # type: ignore[arg-type]
    _assert_fixed(error.value)


@pytest.mark.parametrize("field", ("models", "references", "pins"))
def test_parse_refuses_empty_complete_sets(field: str) -> None:
    library = b".model DMOD D(IS=1e-12)\n"
    declaration = _declaration(library)
    document = _json(_bindings(declaration, library))
    if field == "models":
        document["models"] = []
    elif field == "references":
        document["models"][0]["references"] = []  # type: ignore[index]
    else:
        document["models"][0]["references"][0]["pins"] = []  # type: ignore[index]
    with pytest.raises(SpiceTerminalBindingError):
        parse_terminal_bindings(_payload(document), deadline=time.monotonic() + 10)


@pytest.mark.parametrize("kind", ("extra", "duplicate", "missing"))
def test_join_refuses_complete_set_mismatches(kind: str) -> None:
    library = b".model DMOD D(IS=1e-12)\n"
    declaration = _declaration(library)
    capture = _capture(declaration, library)
    document = _json(_bindings(declaration, library))
    pins = document["models"][0]["references"][0]["pins"]  # type: ignore[index]
    if kind == "extra":
        pins.append({"pin_number": "3", "binding": {"kind": "nc"}})
    elif kind == "duplicate":
        pins.append(pins[0])
    else:
        pins.pop()
    if kind == "duplicate":
        with pytest.raises(SpiceTerminalBindingError):
            parse_terminal_bindings(_payload(document), deadline=time.monotonic() + 10)
        return
    parsed = parse_terminal_bindings(_payload(document), deadline=time.monotonic() + 10)
    with pytest.raises(SpiceTerminalBindingError):
        join_terminal_bindings(
            parsed,
            declaration,
            capture,
            _bom(declaration, capture),
            _pin_map(_sha(b"project-capture")),
            deadline=time.monotonic() + 10,
        )


def test_join_refuses_identity_artifact_missing_and_duplicate_native_nodes() -> None:
    library = b".model DMOD D(IS=1e-12)\n"
    declaration = _declaration(library)
    capture = _capture(declaration, library)
    document = parse_terminal_bindings(
        _bindings(declaration, library), deadline=time.monotonic() + 10
    )
    base = _pin_map(_sha(b"project-capture"))
    tampered_artifacts = (
        capture._artifacts[0],
        replace(capture._artifacts[1], content=b".model DMOD D(IS=2e-12)\n"),
    )
    cases = (
        (replace(capture, declaration_digest=_sha(b"wrong")), _bom(declaration, capture), base),
        (replace(capture, _artifacts=tampered_artifacts), _bom(declaration, capture), base),
        (capture, replace(_bom(declaration, capture), metadata_agreement="fail"), base),
        (capture, _bom(declaration, capture), replace(base, capture_digest=_sha(b"wrong"))),
        (
            capture,
            _bom(declaration, capture),
            replace(base, mapping=replace(base.mapping, nodes=base.mapping.nodes[:1])),
        ),
        (
            capture,
            _bom(declaration, capture),
            replace(base, mapping=replace(base.mapping, nodes=(base.mapping.nodes[0],) * 2)),
        ),
    )
    for bad_capture, bad_bom, bad_map in cases:
        with pytest.raises(SpiceTerminalBindingError) as error:
            join_terminal_bindings(
                document, declaration, bad_capture, bad_bom, bad_map, deadline=time.monotonic() + 10
            )
        _assert_fixed(error.value)


def test_nc_positive_retains_aliases_and_requires_full_definition_ports() -> None:
    library = b".model DMOD D(IS=1e-12)\n"
    declaration = _declaration(library)
    capture = _capture(declaration, library)
    document = _json(_bindings(declaration, library))
    document["models"][0]["references"][0]["pins"].append(  # type: ignore[index]
        {"pin_number": "3", "binding": {"kind": "nc"}}
    )
    parsed = parse_terminal_bindings(_payload(document), deadline=time.monotonic() + 10)
    alias = SourcePinAlias("/", "symbol-uuid", "pin-uuid")
    baseline = _pin_map(_sha(b"project-capture")).mapping.nodes
    aliased = replace(baseline[0], aliases=(alias,))
    extra = NativePinNet("R1", "3", "Device:R", "3", "", "Default", None, "passive", True, ())
    result = join_terminal_bindings(
        parsed,
        declaration,
        capture,
        _bom(declaration, capture),
        _pin_map(_sha(b"project-capture"), pins=(aliased, baseline[1], extra)),
        deadline=time.monotonic() + 10,
    )
    assert result[0].pins[0].node.aliases == (alias,)
    assert result[0].pins[2].port is None


@pytest.mark.parametrize("first_port", ("1", "01", "9" * 64))
def test_numeric_subcircuit_ports_keep_exact_source_spelling(first_port):
    library = f".subckt NUMERIC {first_port} 2\nR1 {first_port} 2 1k\n.ends NUMERIC\n".encode()
    declaration = _declaration(library)
    capture = _capture(declaration, library)
    payload = json.loads(_bindings(declaration, library))
    model = payload["models"][0]
    model.update(definition_name="NUMERIC", definition_digest=_sha(library))
    model["references"][0]["pins"][0]["binding"]["port"] = first_port
    model["references"][0]["pins"][1]["binding"]["port"] = "2"
    document = parse_terminal_bindings(json.dumps(payload).encode(), deadline=time.monotonic() + 30)
    result = join_terminal_bindings(
        document,
        declaration,
        capture,
        _bom(declaration, capture),
        _pin_map(_sha(b"project-capture")),
        deadline=time.monotonic() + 30,
    )
    assert result[0].definition.ports == (first_port, "2")
    assert tuple(pin.port for pin in result[0].pins) == (first_port, "2")


def test_selected_definition_hash_uses_finite_caller_deadline(monkeypatch):
    from copper_mcp.engineering import _spice_terminal_binding as terminal
    from copper_mcp.engineering import spice_model_library as models

    source = b".model DMOD D(IS=1e-12)\n"
    library = b"* owned prefix separates artifact and definition bytes\n" + source
    declaration = _declaration(library)
    capture = _capture(declaration, library)
    document = parse_terminal_bindings(
        _bindings(declaration, library), deadline=time.monotonic() + 30
    )
    original = models._digest
    deadline = time.monotonic() + 30
    selected_deadlines = []

    def checked_hash(content, active_deadline):
        if content == source:
            selected_deadlines.append(active_deadline)
            assert active_deadline == deadline, "selected definition hash lost the caller deadline"
        return original(content, active_deadline)

    monkeypatch.setattr(terminal, "_library_digest", checked_hash)
    monkeypatch.setattr(models, "_digest", checked_hash)
    result = join_terminal_bindings(
        document,
        declaration,
        capture,
        _bom(declaration, capture),
        _pin_map(_sha(b"project-capture")),
        deadline=deadline,
    )
    assert result[0].definition.source == source
    assert selected_deadlines == [deadline]
