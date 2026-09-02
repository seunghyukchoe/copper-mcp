"""The census's negative is only as wide as the surfaces it sweeps.

`B-144` closes `X2` on the claim that the IPC protocol carries no document
revision, no dirty flag and no conditional-write precondition. A sweep of field
*names* cannot support that sentence: a message called ``ConditionalUpdate``
whose fields are an ordinary ``header`` and ``items`` is a conditional write
that shows no field-name hit at all. These tests hold the widened sweep to the
project's own rule -- *an absence is evidence only if the observation was
capable of reporting a presence* -- by planting primitives the narrow sweep
would have missed and requiring each to be found.
"""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from pathlib import Path
from typing import Any

_SCRIPT = Path(__file__).parent.parent / "scripts" / "census_ipc_apply_primitives.py"


# `kicad-python` — and with it protobuf — is an optional extra (`[kicad]`), and CI installs only
# `[dev,security]`. The census reads protobuf lazily inside one function for that reason, and the
# suite's house rule is the same: stand in for the shape rather than importing the package, the
# way `tests/test_kicad_ipc.py` mirrors `kipy`'s classes. These mirror exactly the attributes
# `_named_surface_rows` reads, so the sweep is tested against the descriptor *contract* rather
# than against protobuf's implementation of it -- and the test runs everywhere the suite does.
@dataclass
class _Field:
    name: str
    type_name: str = ""


@dataclass
class _EnumValue:
    name: str


@dataclass
class _Enum:
    name: str
    value: list[_EnumValue] = dataclass_field(default_factory=list)


@dataclass
class _Method:
    name: str


@dataclass
class _Service:
    name: str
    method: list[_Method] = dataclass_field(default_factory=list)


@dataclass
class _Message:
    name: str
    field: list[_Field] = dataclass_field(default_factory=list)
    enum_type: list[_Enum] = dataclass_field(default_factory=list)
    nested_type: list[_Message] = dataclass_field(default_factory=list)


@dataclass
class _File:
    name: str = "synthetic/planted.proto"
    package: str = "planted"
    message_type: list[_Message] = dataclass_field(default_factory=list)
    enum_type: list[_Enum] = dataclass_field(default_factory=list)
    service: list[_Service] = dataclass_field(default_factory=list)


def _census() -> Any:
    spec = importlib.util.spec_from_file_location("census_ipc_apply_primitives", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _file_with(
    *,
    messages: list[_Message] | None = None,
    enums: list[_Enum] | None = None,
    services: list[_Service] | None = None,
) -> _File:
    return _File(message_type=messages or [], enum_type=enums or [], service=services or [])


def _message(name: str, fields: list[tuple[str, str]]) -> _Message:
    return _Message(name=name, field=[_Field(name=n, type_name=t) for n, t in fields])


def _hits(descriptor: Any) -> list[dict[str, str]]:
    module = _census()
    rows = module._named_surface_rows([("synthetic/planted.proto", descriptor)])
    return [
        row for row in rows if any(token in row["name"].lower() for token in module._STATE_TOKENS)
    ]


def test_a_conditional_write_named_only_by_its_message_is_detected() -> None:
    """The exact case the field-name sweep could not see."""

    planted = _file_with(messages=[_message("ConditionalUpdate", [("header", ""), ("items", "")])])

    found = _hits(planted)

    assert [row["name"] for row in found] == ["planted.ConditionalUpdate"]
    assert found[0]["surface"] == "message"


def test_a_precondition_carried_only_in_a_field_type_is_detected() -> None:
    """A field called `header` whose *type* is the precondition."""

    planted = _file_with(
        messages=[_message("UpdateItems", [("header", ".planted.RevisionPrecondition")])]
    )

    found = _hits(planted)

    assert {row["surface"] for row in found} == {"field_type"}
    assert found[0]["name"] == ".planted.RevisionPrecondition"
    assert found[0]["owner"] == "planted.UpdateItems.header"


def test_a_document_state_rpc_is_detected_by_its_method_name() -> None:
    """Services and their methods are swept; the vendored descriptors happen to declare none."""

    service = _Service(name="BoardService", method=[_Method(name="GetDocumentRevision")])

    found = _hits(_file_with(services=[service]))

    assert [(row["surface"], row["name"]) for row in found] == [("method", "GetDocumentRevision")]


def test_a_dirty_bit_spelled_as_an_enum_value_is_detected() -> None:
    enum = _Enum(
        name="DocumentState", value=[_EnumValue(name="DS_CLEAN"), _EnumValue(name="DS_MODIFIED")]
    )

    found = _hits(_file_with(enums=[enum]))

    assert [(row["surface"], row["name"]) for row in found] == [("enum_value", "DS_MODIFIED")]


def test_the_sweep_is_silent_on_a_protocol_that_carries_no_document_state() -> None:
    """The control: without it, the positives above could be a predicate matching anything."""

    planted = _file_with(messages=[_message("CreateItems", [("header", ""), ("items", "")])])

    assert _hits(planted) == []


def test_every_named_surface_the_census_declares_is_actually_collected() -> None:
    """A surface named in the artifact but never walked would be a silent gap in the claim."""

    module = _census()
    service = _Service(name="S", method=[_Method(name="M")])
    enum = _Enum(name="E", value=[_EnumValue(name="V")])
    planted = _file_with(
        messages=[_message("M1", [("f", ".planted.T")])], enums=[enum], services=[service]
    )

    rows = module._named_surface_rows([("synthetic/planted.proto", planted)])

    assert {row["surface"] for row in rows} == set(module._NAMED_SURFACES)
