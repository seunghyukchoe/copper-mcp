#!/usr/bin/env python3
"""Enumerate, from source, every IPC primitive a one-undo-commit apply would rest on.

This is a **closed enumeration of an API surface**, not an observation of a session.  It opens no
socket, launches no editor, and reads no board.  Everything it records comes from two places that
are on this machine already: the installed ``kicad-python`` (``kipy``) package, and the Protobuf
descriptors compiled into that package from KiCad's own ``.proto`` files.

It exists because [ADR-0074](../docs/adr/0074-live-ipc-one-undo-commit-apply.md) and
[B-138](../docs/ledgers/benchmark-ledger.md) both assert *negatives* about this protocol -- no
revision, no dirty flag, no conditional write -- and a negative asserted from reading is weaker
than a negative asserted from an enumeration that could have found a counterexample and did not.
Every "absent" below is therefore reported with the size of the set that was searched, so a reader
can tell an exhaustive search from a failed recollection.

**Direction of error.** Every question is posed so that a bug in this instrument produces a
*false positive* -- a claim that something exists -- rather than a false negative.  The
revision/dirty/conditional-write sweep matches a deliberately over-broad substring list against
every field of every message; a miss would have to be a field whose name contains none of
``revision``, ``dirty``, ``modified``, ``version``, ``generation``, ``sequence``, ``serial``,
``etag``, ``timestamp``, ``stamp``, ``mtime``, ``saved``, ``expect``, ``if_match``, ``precondition``
or ``token``.  The hits it does return are then classified by hand in the ledger row, not here.

**What it deliberately does not do.** It makes no claim about KiCad's C++ side.  Undo granularity,
commit-orphan cleanup and mid-batch partial staging are properties of the *editor*, and the only
honest thing a client-side census can say about them is which of them the client can observe --
which is what the ``client_observability`` section records.  The C++ findings live in
[the IPC apply research note](../docs/research/ipc-apply-v1.md) and are cited, not re-derived.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import inspect
import json
import platform
import sys
import textwrap
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "0.1.0"

#: Deliberately over-broad.  A field that carries a document generation, a dirty bit, or a
#: conditional-write precondition would have to avoid every one of these substrings to escape.
_STATE_TOKENS = (
    "revision",
    "dirty",
    "modified",
    "version",
    "generation",
    "sequence",
    "serial",
    "etag",
    "timestamp",
    "stamp",
    "mtime",
    "saved",
    "expect",
    "if_match",
    "precondition",
    "token",
)

#: The primitives a one-undo-commit apply would rest on, as method names on ``kipy.board.Board``
#: (plus the two client-level calls).  Naming them here rather than pattern-matching is the point:
#: the census must report a *missing* primitive as missing, and a pattern match cannot.
_EXPECTED_BOARD_METHODS = (
    "begin_commit",
    "push_commit",
    "drop_commit",
    "create_items",
    "update_items",
    "remove_items",
    "remove_items_by_id",
    "get_as_string",
    "save",
    "save_as",
    "revert",
)

#: Primitives that would exist if the protocol offered them.  Every one of these is expected to be
#: absent; the census fails loudly (by recording ``present: true``) if one appears.
_ABSENT_BOARD_METHODS = (
    "get_revision",
    "get_generation",
    "is_dirty",
    "is_modified",
    "get_board_as_of",
    "update_items_if_unchanged",
    "undo",
    "redo",
    "rollback_commit",
    "get_undo_stack",
    "lock",
    "unlock",
)


def _sha256_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha256_bytes(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _provenance(kipy_root: Path) -> dict[str, Any]:
    """Bind this census to **content**, and record why it binds nothing else.

    A recorded commit SHA is a pointer into a mutable namespace: a branch commit does not survive
    a squash merge, and B-141's artifact is this repository's own worked example -- the number it
    recorded stopped resolving the moment its branch was squashed, and the artifact's validator
    correctly refused a measurement that had not changed at all.  So this artifact records the
    SHA-256 of the runner's own bytes and of every source file in the measured package, and
    records **no** commit identifier.  A digest that cannot be located in history is still a
    checkable fact about bytes; a garbage-collected SHA is nothing.
    """
    package_files = sorted(
        path for path in kipy_root.rglob("*") if path.is_file() and path.suffix in {".py", ".pyi"}
    )
    manifest = "\n".join(
        f"{path.relative_to(kipy_root).as_posix()} {hashlib.sha256(path.read_bytes()).hexdigest()}"
        for path in package_files
    )
    return {
        "binds": "content_digests_only",
        "commit_identifier": None,
        "why_no_commit_identifier": (
            "A branch commit does not survive a squash merge; B-141 recorded one and it stopped "
            "resolving. Content digests are checkable without the history that produced them."
        ),
        "runner_sha256": _sha256_bytes(Path(__file__).read_bytes()),
        "measured_package_file_count": len(package_files),
        "measured_package_manifest_sha256": _sha256_text(manifest),
    }


def _cite(obj: Any, root: Path) -> dict[str, Any]:
    """Return a ``file:line`` citation for a live Python object."""
    source_file = Path(inspect.getsourcefile(obj) or "<unknown>")
    _, first_line = inspect.getsourcelines(obj)
    try:
        relative = source_file.relative_to(root)
    except ValueError:
        relative = source_file
    return {"file": str(relative), "line": first_line}


def _serialized_file_descriptors(package_root: Path) -> list[tuple[str, Any]]:
    """Read every generated ``*_pb2.py`` file and recover the ``FileDescriptorProto`` inside it.

    Deliberately **not** by importing the modules.  Generated modules register themselves into
    Protobuf's process-global descriptor pool, which imposes an import order and -- measured on
    this very package -- fails outright for ``board/board_jobs.proto`` in ``kicad-python`` 0.7.1,
    whose serialized descriptor references ``.kiapi.common.types.Units`` without a dependency that
    supplies it.  An importing census would have to either skip that file or crash, and skipping
    is exactly the silent omission a closed enumeration may not have.

    Parsing the serialized bytes directly needs no cross-file resolution: message and field names
    are present in each file's own descriptor.  So every generated file is enumerated, including
    the one the runtime cannot load.
    """
    from google.protobuf import descriptor_pb2

    found: list[tuple[str, Any]] = []
    for path in sorted(package_root.rglob("*_pb2.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        payloads = [
            node.args[0].value
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "AddSerializedFile"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, bytes)
        ]
        if len(payloads) != 1:
            raise RuntimeError(f"expected exactly one serialized descriptor in {path}")
        descriptor = descriptor_pb2.FileDescriptorProto()
        descriptor.ParseFromString(payloads[0])
        found.append((descriptor.name, descriptor))
    return sorted(found)


def _message_descriptors(files: list[tuple[str, Any]]) -> list[dict[str, Any]]:
    """Flatten every message, including nested ones, to ``full_name`` plus its field names."""
    rows: list[dict[str, Any]] = []
    for _, file_descriptor in files:
        prefix = file_descriptor.package
        stack = [
            (message, f"{prefix}.{message.name}" if prefix else message.name)
            for message in file_descriptor.message_type
        ]
        while stack:
            message, full_name = stack.pop()
            rows.append(
                {
                    "full_name": full_name,
                    "file": file_descriptor.name,
                    "fields": [field.name for field in message.field],
                }
            )
            stack.extend((nested, f"{full_name}.{nested.name}") for nested in message.nested_type)
    return sorted(rows, key=lambda row: row["full_name"])


def _field_rows(messages: list[dict[str, Any]]) -> list[dict[str, str]]:
    return [
        {"message": message["full_name"], "field": field}
        for message in messages
        for field in message["fields"]
    ]


def _fields_of(messages: list[dict[str, Any]], full_name: str) -> list[str] | None:
    for message in messages:
        if message["full_name"] == full_name:
            return message["fields"]
    return None


def _method_census(board_module: Any, root: Path) -> dict[str, Any]:
    board = board_module.Board
    present: dict[str, Any] = {}
    for name in _EXPECTED_BOARD_METHODS:
        member = getattr(board, name, None)
        if member is None:
            present[name] = {"present": False}
            continue
        present[name] = {"present": True, **_cite(member, root)}
    absent: dict[str, Any] = {}
    for name in _ABSENT_BOARD_METHODS:
        member = getattr(board, name, None)
        absent[name] = (
            {"present": False} if member is None else {"present": True, **_cite(member, root)}
        )
    public = sorted(
        name
        for name in dir(board)
        if not name.startswith("_") and callable(getattr(board, name, None))
    )
    return {
        "expected": present,
        "expected_absent": absent,
        "public_method_count": len(public),
        "public_methods": public,
    }


def _mutating_surface(board_module: Any, root: Path) -> dict[str, Any]:
    """Which ``Board`` methods send a wire command that changes the document.

    Measured by reading each method's own source for the command class it constructs, so the list
    is derived from the binding rather than from this instrument's memory of it.
    """
    mutating_commands = {
        "CreateItems",
        "UpdateItems",
        "DeleteItems",
        "BeginCommit",
        "EndCommit",
        "SaveDocument",
        "SaveCopyOfDocument",
        "RevertDocument",
    }
    found: list[dict[str, Any]] = []
    for name in dir(board_module.Board):
        if name.startswith("_"):
            continue
        member = getattr(board_module.Board, name, None)
        if not callable(member) or not inspect.isfunction(member):
            continue
        try:
            source = inspect.getsource(member)
        except (OSError, TypeError):
            continue
        hits = sorted(command for command in mutating_commands if command + "(" in source)
        if hits:
            found.append({"method": name, "commands": hits, **_cite(member, root)})
    return {"count": len(found), "methods": found}


def _discarded_status(board_module: Any, root: Path) -> dict[str, Any]:
    """Does the binding read the per-item status the wire protocol returns?

    Read from source: the response messages carry ``status`` at the request level and a per-item
    ``status`` inside each result, and this records, per mutation method, which of those substrings
    appear anywhere in the method body.  Absence here is the load-bearing measurement.
    """
    rows: list[dict[str, Any]] = []
    for name in ("create_items", "update_items", "remove_items", "remove_items_by_id"):
        member = getattr(board_module.Board, name)
        tree = ast.parse(textwrap.dedent(inspect.getsource(member)))
        attributes = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
        discarded = any(
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Attribute)
            and node.value.func.attr == "send"
            for node in ast.walk(tree)
        )
        rows.append(
            {
                "method": name,
                # ``status`` names two distinct fields in every response message: one at the
                # request level and one inside each per-item result.  This asks whether the
                # binding reads *either* of them anywhere in the method.
                "reads_a_status_attribute": "status" in attributes,
                "reads_per_item_results": bool(
                    attributes & {"created_items", "updated_items", "deleted_items"}
                ),
                "discards_the_response_entirely": discarded,
                **_cite(member, root),
            }
        )
    return {"methods": rows}


def _client_observability(kipy_root: Path, root: Path) -> dict[str, Any]:
    from kipy import client as client_module
    from kipy import errors as errors_module
    from kipy import kicad as kicad_module

    error_classes = sorted(
        name
        for name, value in vars(errors_module).items()
        if isinstance(value, type) and issubclass(value, Exception) and not name.startswith("_")
    )
    send_source = inspect.getsource(client_module.KiCadClient.send)
    connect_source = inspect.getsource(client_module.KiCadClient._connect)
    default_token = inspect.getsource(kicad_module._default_kicad_token)
    return {
        "error_classes": error_classes,
        "error_class_count": len(error_classes),
        "kipy_root": str(kipy_root.relative_to(kipy_root.parent)),
        "send": {
            **_cite(client_module.KiCadClient.send, root),
            "adopts_server_token_when_client_token_empty": (
                'if self._kicad_token == "":' in send_source
            ),
            "clears_connected_flag_on_transport_error": "_connected = False" in send_source,
            "raises_single_connection_error_type": send_source.count("raise ConnectionError") >= 2,
        },
        "connect": {
            **_cite(client_module.KiCadClient._connect, root),
            "clears_connected_flag_on_dial_failure": "_connected = False" in connect_source,
        },
        "default_kicad_token": {
            **_cite(kicad_module._default_kicad_token, root),
            "returns_empty_string_when_env_absent": 'return ""' in default_token,
        },
        "default_client_name": {
            **_cite(kicad_module._random_client_name, root),
            "is_random_per_object": "random.choices"
            in inspect.getsource(kicad_module._random_client_name),
        },
        "check_version": {
            **_cite(kicad_module.KiCad.check_version, root),
            "raises_only_for_newer_editor": (
                "kicad_version > api_version" in inspect.getsource(kicad_module.KiCad.check_version)
            ),
        },
    }


def build_census() -> dict[str, Any]:
    import kipy
    from kipy import board as board_module
    from kipy.kicad_api_version import KICAD_API_VERSION

    kipy_root = Path(kipy.__file__).parent
    site_packages = kipy_root.parent

    files = _serialized_file_descriptors(kipy_root / "proto")
    descriptors = _message_descriptors(files)
    fields = _field_rows(descriptors)

    hits = [row for row in fields if any(token in row["field"].lower() for token in _STATE_TOKENS)]

    commit_messages = {
        name: _fields_of(descriptors, name)
        for name in (
            "kiapi.common.commands.BeginCommit",
            "kiapi.common.commands.BeginCommitResponse",
            "kiapi.common.commands.EndCommit",
            "kiapi.common.commands.EndCommitResponse",
        )
    }
    item_messages = {
        name: _fields_of(descriptors, name)
        for name in (
            "kiapi.common.commands.CreateItems",
            "kiapi.common.commands.CreateItemsResponse",
            "kiapi.common.commands.UpdateItems",
            "kiapi.common.commands.UpdateItemsResponse",
            "kiapi.common.commands.DeleteItems",
            "kiapi.common.commands.DeleteItemsResponse",
            "kiapi.common.commands.GetItems",
            "kiapi.common.commands.GetItemsResponse",
            "kiapi.common.types.ItemHeader",
            "kiapi.common.types.DocumentSpecifier",
            "kiapi.common.ApiRequestHeader",
            "kiapi.common.ApiResponseHeader",
        )
    }

    census: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "provenance": _provenance(kipy_root),
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "kicad_python_version": version("kicad-python"),
            "bundled_kicad_api_version": KICAD_API_VERSION,
            "site_packages": str(site_packages),
        },
        "proto_surface": {
            "file_count": len(files),
            "files": [name for name, _ in files],
            "message_count": len(descriptors),
            "field_count": len(fields),
            "state_token_substrings": list(_STATE_TOKENS),
            "state_token_hit_count": len(hits),
            "state_token_hits": hits,
        },
        "commit_messages": commit_messages,
        "item_messages": item_messages,
        "board_methods": _method_census(board_module, site_packages),
        "mutating_surface": _mutating_surface(board_module, site_packages),
        "per_item_status": _discarded_status(board_module, site_packages),
        "client_observability": _client_observability(kipy_root, site_packages),
    }
    # The canonical form is fixed by ``scripts/check_ledgers.py``: sorted keys, no whitespace,
    # no non-finite floats, and the ``run_id`` field excluded from its own digest.  Matching it
    # here is what lets the repository's own gate re-verify this artifact.
    census["run_id"] = _sha256_text(
        json.dumps(census, sort_keys=True, separators=(",", ":"), allow_nan=False)
    )
    return census


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    census = build_census()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(census, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {args.output}")
    print(
        f"messages={census['proto_surface']['message_count']} "
        f"fields={census['proto_surface']['field_count']} "
        f"state_token_hits={census['proto_surface']['state_token_hit_count']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
