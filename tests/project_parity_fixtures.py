"""CopperMCP-authored assigned-footprint project/board controls; not held-out evidence."""

import hashlib
import json
import uuid
from pathlib import Path

from test_project_erc import build_project

from copper_mcp.adapters.sexpr import child, children, parse_sexpr
from copper_mcp.engineering.project_erc_inputs import SymbolLibraryInput
from copper_mcp.engineering.schematic_project_capture import (
    ProjectFileBinding,
    capture_schematic_project,
)

ROOT = Path(__file__).resolve().parents[1]


def digest(content):
    return "sha256:" + hashlib.sha256(content).hexdigest()


def assigned_project(tmp_path):
    capture, libraries, files = build_project(tmp_path)
    source = files[capture.root_path].replace(b"(on_board no)", b"(on_board yes)")
    text = source.decode()
    fields = {}
    changes = []
    for symbol in children(parse_sexpr(source), "symbol"):
        library = child(symbol, "lib_id").items[1]
        fields[library] = {node.items[1]: node.items[2] for node in children(symbol, "property")}
        prop = next(node for node in children(symbol, "property") if node.items[1] == "Footprint")
        old = '(property "Footprint" ""'
        assert text.startswith(old, prop.offset)
        changes.append((prop.offset, len(old), '(property "Footprint" ' + json.dumps(library)))
    for offset, length, replacement in sorted(changes, reverse=True):
        text = text[:offset] + replacement + text[offset + length :]
    files[capture.root_path] = text.encode()
    libraries = tuple(
        SymbolLibraryInput(item.name, content, digest(content))
        for item in libraries
        for content in [item.content.replace(b"(on_board no)", b"(on_board yes)")]
    )
    for name, content in files.items():
        (tmp_path / name).write_bytes(content)
    capture = capture_schematic_project(
        tmp_path,
        capture.root_path,
        tuple(ProjectFileBinding(name, digest(content)) for name, content in files.items()),
    )
    boards = {}
    for name in ("matching", "net-mismatch"):
        board = (ROOT / "tests/fixtures/source-to-board-parity" / f"{name}.kicad_pcb").read_bytes()
        text = board.decode()
        changes = []
        for index, footprint in enumerate(children(parse_sexpr(board), "footprint")):
            library = footprint.items[1]
            prefix = "(footprint " + json.dumps(library)
            assert text.startswith(prefix, footprint.offset)
            added = (
                '\n    (uuid "'
                + str(uuid.uuid5(uuid.NAMESPACE_DNS, f"parity-probe-fp-{index}"))
                + '")'
            )
            for key in ("Datasheet", "Description"):
                value = fields[library][key]
                # Native schematic field expansion treats '~' as an empty placeholder.
                if key == "Datasheet" and value == "~":
                    value = ""
                identifier = uuid.uuid5(uuid.NAMESPACE_DNS, f"parity-probe-{index}-{key}")
                added += (
                    f"\n    (property {json.dumps(key)} {json.dumps(value)}"
                    f' (at 0 0 0) (layer "F.Fab") (uuid "{identifier}")'
                    " (effects (font (size 1 1) (thickness 0.15)) (hide yes)))"
                )
            changes.append((footprint.offset + len(prefix), added))
        for offset, added in sorted(changes, reverse=True):
            text = text[:offset] + added + text[offset:]
        boards[name] = text.encode()
    return capture, libraries, files, boards
