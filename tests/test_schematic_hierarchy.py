from __future__ import annotations

import dataclasses
import hashlib
import uuid

import pytest

from copper_mcp.engineering.capture import CaptureLimits
from copper_mcp.engineering.schematic_hierarchy import (
    SchematicFileEdge,
    SchematicHierarchyError,
    SchematicInstancePath,
    SchematicSource,
    _resolve_reference,
    _SheetReference,
    derive_schematic_hierarchy,
)

ROOT_UUID = "00000000-0000-0000-0000-000000000001"
CHILD_ROOT_UUID = "00000000-0000-0000-0000-000000000002"
SHEET_A_UUID = "00000000-0000-0000-0000-00000000000a"
SHEET_B_UUID = "00000000-0000-0000-0000-00000000000b"
SHARED_SHEET_UUID = "00000000-0000-0000-0000-00000000000c"


def _sheet(
    sheet_uuid: str,
    target: str,
    *,
    name: str = "Child",
    name_key: str = "Sheetname",
    file_key: str = "Sheetfile",
    name_head: str = "property",
    file_head: str = "property",
) -> str:
    quoted_target = target.replace("\\", "\\\\").replace('"', '\\"')
    return f"""
    (sheet
      (uuid {sheet_uuid})
      ({name_head} \"{name_key}\" \"{name}\")
      ({file_head} \"{file_key}\" \"{quoted_target}\")
    )
    """


def _source(root_uuid: str, *sheets: str, version: str = "20250114") -> bytes:
    return (
        "(kicad_sch\n"
        f"  (version {version})\n"
        '  (generator "copper_mcp_test")\n'
        f"  (uuid {root_uuid})\n" + "".join(sheets) + ")\n"
    ).encode()


def _item(path: str, content: bytes) -> SchematicSource:
    return SchematicSource(path=path, content=content)


@pytest.mark.parametrize("atom", ("private", "junk", '"private"'))
def test_rejects_bare_sheet_atoms_even_with_valid_properties(atom: str) -> None:
    sheet = _sheet(SHEET_A_UUID, "child.kicad_sch").rstrip()
    malformed = sheet[:-1] + f" {atom})"
    with pytest.raises(SchematicHierarchyError, match="sheet fields"):
        derive_schematic_hierarchy(
            "root.kicad_sch",
            (
                _item("root.kicad_sch", _source(ROOT_UUID, malformed)),
                _item("child.kicad_sch", _source(CHILD_ROOT_UUID)),
            ),
        )


def test_derives_root_child_digests_edge_and_instance_paths() -> None:
    root = _source(ROOT_UUID, _sheet(SHEET_A_UUID, "child.kicad_sch", name="Power"))
    child = _source(CHILD_ROOT_UUID)

    hierarchy = derive_schematic_hierarchy(
        "root.kicad_sch",
        (_item("root.kicad_sch", root), _item("child.kicad_sch", child)),
    )

    assert [(item.path, item.digest) for item in hierarchy.source_digests] == [
        ("child.kicad_sch", "sha256:" + hashlib.sha256(child).hexdigest()),
        ("root.kicad_sch", "sha256:" + hashlib.sha256(root).hexdigest()),
    ]
    assert [
        (edge.parent_path, edge.child_path, edge.sheet_name, edge.sheet_uuid)
        for edge in hierarchy.file_edges
    ] == [("root.kicad_sch", "child.kicad_sch", "Power", SHEET_A_UUID)]
    assert {(item.source_path, item.uuid_path) for item in hierarchy.instance_paths} == {
        ("root.kicad_sch", f"/{ROOT_UUID}"),
        ("child.kicad_sch", f"/{ROOT_UUID}/{SHEET_A_UUID}"),
    }


def test_nested_reused_source_expands_each_distinct_instance_path() -> None:
    root = _source(
        ROOT_UUID,
        _sheet(SHEET_A_UUID, "a.kicad_sch", name="A"),
        _sheet(SHEET_B_UUID, "b.kicad_sch", name="B"),
    )
    a = _source(
        "10000000-0000-0000-0000-000000000001",
        _sheet(SHARED_SHEET_UUID, "shared.kicad_sch", name="Shared"),
    )
    b = _source(
        "20000000-0000-0000-0000-000000000001",
        _sheet(SHARED_SHEET_UUID, "shared.kicad_sch", name="Shared"),
    )
    shared = _source("30000000-0000-0000-0000-000000000001")

    hierarchy = derive_schematic_hierarchy(
        "root.kicad_sch",
        tuple(
            _item(path, content)
            for path, content in (
                ("shared.kicad_sch", shared),
                ("b.kicad_sch", b),
                ("root.kicad_sch", root),
                ("a.kicad_sch", a),
            )
        ),
    )

    shared_paths = {
        item.uuid_path
        for item in hierarchy.instance_paths
        if item.source_path == "shared.kicad_sch"
    }
    assert shared_paths == {
        f"/{ROOT_UUID}/{SHEET_A_UUID}/{SHARED_SHEET_UUID}",
        f"/{ROOT_UUID}/{SHEET_B_UUID}/{SHARED_SHEET_UUID}",
    }
    assert len(hierarchy.instance_paths) == 5


def test_rejects_file_cycle() -> None:
    root = _source(ROOT_UUID, _sheet(SHEET_A_UUID, "child.kicad_sch"))
    child = _source(CHILD_ROOT_UUID, _sheet(SHEET_B_UUID, "root.kicad_sch"))

    with pytest.raises(SchematicHierarchyError, match="cycle"):
        derive_schematic_hierarchy(
            "root.kicad_sch",
            (_item("root.kicad_sch", root), _item("child.kicad_sch", child)),
        )


def test_rejects_missing_target_unreachable_extra_and_missing_root() -> None:
    root = _source(ROOT_UUID, _sheet(SHEET_A_UUID, "missing.kicad_sch"))
    with pytest.raises(SchematicHierarchyError, match="target is missing"):
        derive_schematic_hierarchy("root.kicad_sch", (_item("root.kicad_sch", root),))

    leaf = _source(CHILD_ROOT_UUID)
    with pytest.raises(SchematicHierarchyError, match="unreachable"):
        derive_schematic_hierarchy(
            "root.kicad_sch",
            (_item("root.kicad_sch", _source(ROOT_UUID)), _item("extra.kicad_sch", leaf)),
        )

    with pytest.raises(SchematicHierarchyError, match="root source is missing"):
        derive_schematic_hierarchy("root.kicad_sch", (_item("other.kicad_sch", leaf),))


@pytest.mark.parametrize(
    "paths",
    [
        ("A.kicad_sch", "a.kicad_sch"),
        (
            "caf\N{LATIN SMALL LETTER E WITH ACUTE}.kicad_sch",
            "cafe\N{COMBINING ACUTE ACCENT}.kicad_sch",
        ),
    ],
)
def test_rejects_portable_path_aliases(paths: tuple[str, str]) -> None:
    content = _source(ROOT_UUID)
    with pytest.raises(SchematicHierarchyError, match="ambiguous"):
        derive_schematic_hierarchy(
            paths[0],
            (_item(paths[0], content), _item(paths[1], content)),
        )


@pytest.mark.parametrize(
    "root_path,files",
    [
        (1, ()),
        ("root.kicad_sch", []),
        ("root.kicad_sch", (object(),)),
        (
            "root.kicad_sch",
            (SchematicSource("root.kicad_sch", bytearray(b"x")),),  # type: ignore[arg-type]
        ),
        ("./root.kicad_sch", (SchematicSource("./root.kicad_sch", b"x"),)),
        ("root.sch", (SchematicSource("root.sch", b"x"),)),
        ("root.kicad_sch", (SchematicSource("root\\child.kicad_sch", b"x"),)),
    ],
)
def test_rejects_non_exact_or_noncanonical_inputs(root_path: object, files: object) -> None:
    with pytest.raises(SchematicHierarchyError):
        derive_schematic_hierarchy(root_path, files)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "content",
    [
        b"not an s-expression",
        _source(ROOT_UUID, version="20211122"),
        _source(ROOT_UUID, version="20260307"),
        _source(ROOT_UUID, version="20250231"),
        b'("kicad_sch" (version 20250114) (uuid 00000000-0000-0000-0000-000000000001))',
        b'(kicad_sch (version "20250114") (uuid 00000000-0000-0000-0000-000000000001))',
        b"(kicad_sch (version 20250114))",
        b"(kicad_sch (version 20250114) (version 20250114) "
        b"(uuid 00000000-0000-0000-0000-000000000001))",
        b'(kicad_sch (version 20250114) ("uuid" 00000000-0000-0000-0000-000000000001))',
    ],
)
def test_rejects_malformed_source_and_header(content: bytes) -> None:
    with pytest.raises(SchematicHierarchyError):
        derive_schematic_hierarchy("root.kicad_sch", (_item("root.kicad_sch", content),))


def test_accepts_quoted_uuid_case_insensitive_field_aliases_and_private_fields() -> None:
    sheet = f"""
    (sheet
      (uuid \"{SHEET_A_UUID}\")
      (property private \"sHeEt NaMe\" \"Power\")
      (property private \"SHEET FILE\" \"child.kicad_sch\")
    )
    """
    root = _source(ROOT_UUID, sheet).replace(
        f"(uuid {ROOT_UUID})".encode(), f'(uuid "{ROOT_UUID}")'.encode(), 1
    )

    hierarchy = derive_schematic_hierarchy(
        "root.kicad_sch",
        (_item("root.kicad_sch", root), _item("child.kicad_sch", _source(CHILD_ROOT_UUID))),
    )

    assert hierarchy.file_edges[0].sheet_name == "Power"
    assert hierarchy.file_edges[0].sheet_uuid == SHEET_A_UUID


def test_rejects_standalone_private_sheet_field_replacement() -> None:
    sheet = f"""
    (sheet
      (uuid {SHEET_A_UUID})
      (property \"Sheetname\" \"Power\")
      (private \"Sheetfile\" \"child.kicad_sch\")
    )
    """
    with pytest.raises(SchematicHierarchyError, match="sheet"):
        derive_schematic_hierarchy(
            "root.kicad_sch",
            (
                _item("root.kicad_sch", _source(ROOT_UUID, sheet)),
                _item("child.kicad_sch", _source(CHILD_ROOT_UUID)),
            ),
        )


@pytest.mark.parametrize(
    "sheet",
    [
        '(sheet (property "Sheetname" "A") (property "Sheetfile" "child.kicad_sch"))',
        f'(sheet (uuid {SHEET_A_UUID}) (property "Sheetfile" "child.kicad_sch"))',
        f'(sheet (uuid {SHEET_A_UUID}) (property "Sheetname" "A"))',
        f'(sheet (uuid {SHEET_A_UUID}) (property "Sheetname" "A") '
        '(property "Sheet name" "B") (property "Sheetfile" "child.kicad_sch"))',
        f'(sheet (uuid {SHEET_A_UUID}) (property "Sheetname" "A") '
        '(property "Sheetfile" "child.kicad_sch") '
        '(property "Sheet file" "other.kicad_sch"))',
        f"(sheet (uuid {SHEET_A_UUID}) (uuid {SHEET_B_UUID}) "
        '(property "Sheetname" "A") (property "Sheetfile" "child.kicad_sch"))',
        f'(sheet (uuid {SHEET_A_UUID}) (property Sheetname "A") '
        '(property "Sheetfile" "child.kicad_sch"))',
    ],
)
def test_rejects_missing_ambiguous_or_malformed_sheet_fields(sheet: str) -> None:
    root = _source(ROOT_UUID, sheet)
    with pytest.raises(SchematicHierarchyError, match="sheet"):
        derive_schematic_hierarchy(
            "root.kicad_sch",
            (
                _item("root.kicad_sch", root),
                _item("child.kicad_sch", _source(CHILD_ROOT_UUID)),
                _item("other.kicad_sch", _source("40000000-0000-0000-0000-000000000001")),
            ),
        )


def test_rejects_duplicate_sibling_sheet_uuids() -> None:
    root = _source(
        ROOT_UUID,
        _sheet(SHEET_A_UUID, "a.kicad_sch", name="A"),
        _sheet(SHEET_A_UUID, "b.kicad_sch", name="B"),
    )
    with pytest.raises(SchematicHierarchyError, match="UUID"):
        derive_schematic_hierarchy(
            "root.kicad_sch",
            (
                _item("root.kicad_sch", root),
                _item("a.kicad_sch", _source("10000000-0000-0000-0000-000000000001")),
                _item("b.kicad_sch", _source("20000000-0000-0000-0000-000000000001")),
            ),
        )


@pytest.mark.parametrize(
    "sheet_uuid,extra",
    [
        (ROOT_UUID, ""),
        (SHEET_A_UUID, f'(wire (uuid "{ROOT_UUID}"))'),
        (SHEET_A_UUID, f'(symbol (pin "1" (uuid "{SHEET_A_UUID.upper()}")))'),
        (SHEET_A_UUID, f"(wire (uuid {SHEET_B_UUID})) (symbol (uuid {SHEET_B_UUID}))"),
    ],
)
def test_rejects_uuid_collisions_anywhere_within_one_source(sheet_uuid: str, extra: str) -> None:
    root = _source(ROOT_UUID, _sheet(sheet_uuid, "child.kicad_sch"), extra)
    with pytest.raises(SchematicHierarchyError, match="UUID"):
        derive_schematic_hierarchy(
            "root.kicad_sch",
            (_item("root.kicad_sch", root), _item("child.kicad_sch", _source(CHILD_ROOT_UUID))),
        )


@pytest.mark.parametrize("uuid_field", ["(uuid invalid)", "(uuid)", f"(uuid {SHEET_B_UUID} junk)"])
def test_rejects_malformed_nonhierarchy_uuid_fields(uuid_field: str) -> None:
    root = _source(ROOT_UUID, f'(symbol (pin "1" {uuid_field}))')
    with pytest.raises(SchematicHierarchyError):
        derive_schematic_hierarchy("root.kicad_sch", (_item("root.kicad_sch", root),))


def test_allows_uuid_reuse_across_sources_and_shared_instances() -> None:
    root = _source(
        ROOT_UUID,
        _sheet(SHEET_A_UUID, "child.kicad_sch"),
        _sheet(SHEET_B_UUID, "child.kicad_sch"),
    )
    child = _source(ROOT_UUID, _sheet(SHEET_A_UUID, "leaf.kicad_sch"))
    hierarchy = derive_schematic_hierarchy(
        "root.kicad_sch",
        (
            _item("root.kicad_sch", root),
            _item("child.kicad_sch", child),
            _item("leaf.kicad_sch", _source(ROOT_UUID)),
        ),
    )
    assert {item.uuid_path for item in hierarchy.instance_paths} == {
        f"/{ROOT_UUID}",
        f"/{ROOT_UUID}/{SHEET_A_UUID}",
        f"/{ROOT_UUID}/{SHEET_B_UUID}",
        f"/{ROOT_UUID}/{SHEET_A_UUID}/{SHEET_A_UUID}",
        f"/{ROOT_UUID}/{SHEET_B_UUID}/{SHEET_A_UUID}",
    }


@pytest.mark.parametrize(
    "tail",
    [
        "trailing",
        '"trailing"',
        "(unknown yes)",
        "(id)",
        "(id 1.5)",
        '(id "1")',
        "(id 1 2)",
        "(id 2147483648)",
        "(at 1 2)",
        "(at 1 2 3 4)",
        '(at "1" 2 3)',
        "(at 1 NaN 3)",
        "(at 1 2 1e9999)",
        "(at 1 2 1_0)",
        "(at 1 (x 2) 3)",
        "(hide)",
        "(hide true)",
        '(hide "yes")',
        "(hide yes no)",
        "show_name",
        "(show_name maybe)",
        '(show_name "no")',
        "(do_not_autoplace yes no)",
        "(do_not_autoplace (yes))",
        "(effects trailing)",
        "(effects (unknown yes))",
        "(effects (font trailing))",
        "(effects (font (unknown 1)))",
        "(effects (font (size 1)))",
        "(effects (font (size 1 2 3)))",
        '(effects (font (size "1" 2)))',
        "(effects (font (size 1 inf)))",
        "(effects (font (thickness 1 2)))",
        "(effects (font (thickness NaN)))",
        "(effects (font (face)))",
        '(effects (font (face "A" "B")))',
        "(effects (font (bold maybe)))",
        '(effects (font "italic"))',
        "(effects (font (italic yes no)))",
        "(effects (font (color 1 2 3)))",
        "(effects (font (color 1.5 2 3 1)))",
        "(effects (font (color 1 2 3 NaN)))",
        "(effects (font (line_spacing)))",
        "(effects (font (line_spacing nan)))",
        "(effects (justify center))",
        '(effects (justify "left"))',
        "(effects (justify (left)))",
        "(effects (hide maybe))",
        '(effects "hide")',
        '(effects (href "https://example.com"))',
    ],
)
def test_rejects_malformed_or_unsupported_sheet_property_tails(tail: str) -> None:
    sheet = f"""(sheet (uuid {SHEET_A_UUID})
        (property private "Sheetname" "Good" {tail})
        (property "Sheetfile" "child.kicad_sch"))"""
    with pytest.raises(SchematicHierarchyError, match="sheet"):
        derive_schematic_hierarchy(
            "root.kicad_sch",
            (
                _item("root.kicad_sch", _source(ROOT_UUID, sheet)),
                _item("child.kicad_sch", _source(CHILD_ROOT_UUID)),
            ),
        )


@pytest.mark.parametrize(
    "tail",
    [
        "(id 0) (at 1.27 -2.54 90) (hide no) (show_name) (do_not_autoplace)",
        "(id -1) (at .5 -2e-1 0) (hide yes) (show_name yes) (do_not_autoplace no)",
        "(show_name no) (do_not_autoplace yes)",
        "(effects)",
        "(effects (font (size 1.27 1.27) (thickness 0.1) bold italic) "
        "(justify left top mirror) hide)",
        '(effects (font (face "KiCad Font") (size 1 2) (bold no) (italic yes) '
        "(color 10 20 30 0.5) (line_spacing 1.2)) (justify right bottom) (hide no))",
        "(effects (font (bold) (italic)) (justify) (hide))",
    ],
)
def test_accepts_supported_native_sheet_property_tails(tail: str) -> None:
    sheet = f"""(sheet (uuid {SHEET_A_UUID})
        (property private "Sheet name" "Good" {tail})
        (property "Sheet file" "child.kicad_sch" {tail}))"""
    root = _source(ROOT_UUID, sheet)
    hierarchy = derive_schematic_hierarchy(
        "root.kicad_sch",
        (_item("root.kicad_sch", root), _item("child.kicad_sch", _source(CHILD_ROOT_UUID))),
    )
    assert hierarchy.file_edges[0].sheet_name == "Good"


def test_rejects_standalone_private_even_beside_valid_properties() -> None:
    sheet = f"""(sheet (uuid {SHEET_A_UUID})
        (property "Sheetname" "Good") (property "Sheetfile" "child.kicad_sch")
        (private "Other field" "Ignored"))"""
    with pytest.raises(SchematicHierarchyError, match="sheet"):
        derive_schematic_hierarchy(
            "root.kicad_sch",
            (
                _item("root.kicad_sch", _source(ROOT_UUID, sheet)),
                _item("child.kicad_sch", _source(CHILD_ROOT_UUID)),
            ),
        )


def test_resolves_confined_dot_parent_and_declared_variables_from_current_sheet() -> None:
    root = _source(ROOT_UUID, _sheet(SHEET_A_UUID, "./sub/child.kicad_sch"))
    child = _source(
        CHILD_ROOT_UUID,
        _sheet(SHEET_B_UUID, "../shared.kicad_sch", name="Shared"),
        _sheet(SHARED_SHEET_UUID, "${KIPRJMOD}/leaf.kicad_sch", name="Leaf"),
    )
    files = (
        _item("project/root.kicad_sch", root),
        _item("project/sub/child.kicad_sch", child),
        _item("project/shared.kicad_sch", _source("30000000-0000-0000-0000-000000000001")),
        _item("project/leaf.kicad_sch", _source("40000000-0000-0000-0000-000000000001")),
    )

    hierarchy = derive_schematic_hierarchy(
        "project/root.kicad_sch", files, project_variables={"KIPRJMOD": ".."}
    )

    assert {edge.child_path for edge in hierarchy.file_edges} == {
        "project/sub/child.kicad_sch",
        "project/shared.kicad_sch",
        "project/leaf.kicad_sch",
    }


def test_projectname_is_builtin_and_overrides_a_declared_value() -> None:
    root = _source(ROOT_UUID, _sheet(SHEET_A_UUID, "${PROJECTNAME}.child.kicad_sch"))
    hierarchy = derive_schematic_hierarchy(
        "project/root.kicad_sch",
        (
            _item("project/root.kicad_sch", root),
            _item("project/root.child.kicad_sch", _source(CHILD_ROOT_UUID)),
        ),
        project_variables={"PROJECTNAME": "not-the-root-stem"},
    )
    assert hierarchy.file_edges[0].child_path == "project/root.child.kicad_sch"


@pytest.mark.parametrize(
    "variables,reference",
    [
        ({"A": "${B}", "B": "${A}"}, "${A}/child.kicad_sch"),
        (
            {
                "A": "${B}",
                "B": "${C}",
                "C": "${D}",
                "D": "${E}",
                "E": "${F}",
                "F": "${G}",
                "G": "${H}",
                "H": "${I}",
                "I": "leaf",
            },
            "${A}/child.kicad_sch",
        ),
        ({"A": "x" * 4096}, "${A}.kicad_sch"),
        ({}, "${CURRENT_DATE}.kicad_sch"),
        ({}, "${CURRENT_TIME_HH_MM_SS}.kicad_sch"),
        ({}, "${VCSHASH}.kicad_sch"),
        ({}, "$HOME/child.kicad_sch"),
        ({}, "${MISSING}.kicad_sch"),
        ({}, "@{1}.kicad_sch"),
        ({"A": "@{1}"}, "${A}.kicad_sch"),
    ],
)
def test_refuses_cyclic_limited_builtin_and_unsupported_variable_references(
    variables: dict[str, str], reference: str
) -> None:
    root = _source(ROOT_UUID, _sheet(SHEET_A_UUID, reference))
    with pytest.raises(SchematicHierarchyError, match="reference"):
        derive_schematic_hierarchy(
            "root.kicad_sch",
            (_item("root.kicad_sch", root),),
            project_variables=variables,
        )


def test_does_not_consult_environment_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COPPER_MCP_HIERARCHY_ENV_SENTINEL", "child")
    root = _source(
        ROOT_UUID,
        _sheet(SHEET_A_UUID, "${COPPER_MCP_HIERARCHY_ENV_SENTINEL}.kicad_sch"),
    )
    with pytest.raises(SchematicHierarchyError, match="reference"):
        derive_schematic_hierarchy(
            "root.kicad_sch",
            (
                _item("root.kicad_sch", root),
                _item("child.kicad_sch", _source(CHILD_ROOT_UUID)),
            ),
        )


@pytest.mark.parametrize(
    "provided_path,reference",
    [
        ("child.kicad_sch", "CHILD.kicad_sch"),
        (
            "caf\N{LATIN SMALL LETTER E WITH ACUTE}.kicad_sch",
            "cafe\N{COMBINING ACUTE ACCENT}.kicad_sch",
        ),
    ],
)
def test_rejects_portable_alias_only_reference_spelling(provided_path: str, reference: str) -> None:
    root = _source(ROOT_UUID, _sheet(SHEET_A_UUID, reference))
    with pytest.raises(SchematicHierarchyError, match="target"):
        derive_schematic_hierarchy(
            "root.kicad_sch",
            (_item("root.kicad_sch", root), _item(provided_path, _source(CHILD_ROOT_UUID))),
        )


@pytest.mark.parametrize(
    "reference",
    [
        "/absolute.kicad_sch",
        "C:/absolute.kicad_sch",
        "..\\child.kicad_sch",
        "${OTHER}/child.kicad_sch",
        "sub/${KIPRJMOD}/child.kicad_sch",
        "${KIPRJMOD}/../../escape.kicad_sch",
        "../../escape.kicad_sch",
        "child\x00.kicad_sch",
        "child.sch",
        "child//nested.kicad_sch",
    ],
)
def test_rejects_absolute_variable_malformed_and_escaping_references(reference: str) -> None:
    root = _source(ROOT_UUID, _sheet(SHEET_A_UUID, reference))
    with pytest.raises(SchematicHierarchyError, match="reference"):
        derive_schematic_hierarchy("root.kicad_sch", (_item("root.kicad_sch", root),))


def test_rejects_source_and_raw_reference_over_4096_characters() -> None:
    long_path = "a" * 4087 + ".kicad_sch"
    with pytest.raises(SchematicHierarchyError, match="source path"):
        derive_schematic_hierarchy(long_path, (_item(long_path, _source(ROOT_UUID)),))

    long_reference = "./" * 2048 + "child.kicad_sch"
    root = _source(ROOT_UUID, _sheet(SHEET_A_UUID, long_reference))
    with pytest.raises(SchematicHierarchyError):
        derive_schematic_hierarchy(
            "root.kicad_sch",
            (
                _item("root.kicad_sch", root),
                _item("child.kicad_sch", _source(CHILD_ROOT_UUID)),
            ),
        )
    with pytest.raises(SchematicHierarchyError, match="reference"):
        _resolve_reference(
            "root.kicad_sch",
            long_reference,
            frozenset({"child.kicad_sch"}),
            {},
            "root",
            float("inf"),
        )


def test_enforces_file_file_byte_total_byte_and_deadline_budgets() -> None:
    root = _source(ROOT_UUID)
    with pytest.raises(SchematicHierarchyError, match="file budget"):
        derive_schematic_hierarchy(
            "root.kicad_sch",
            tuple(_item(f"f{index}.kicad_sch", root) for index in range(65)),
        )

    with pytest.raises(SchematicHierarchyError, match="source byte"):
        derive_schematic_hierarchy(
            "root.kicad_sch",
            (_item("root.kicad_sch", root),),
            limits=CaptureLimits(max_file_bytes=len(root) - 1),
        )

    child = _source(CHILD_ROOT_UUID)
    linked_root = _source(ROOT_UUID, _sheet(SHEET_A_UUID, "child.kicad_sch"))
    with pytest.raises(SchematicHierarchyError, match="total byte"):
        derive_schematic_hierarchy(
            "root.kicad_sch",
            (_item("root.kicad_sch", linked_root), _item("child.kicad_sch", child)),
            limits=CaptureLimits(
                max_file_bytes=max(len(linked_root), len(child)),
                max_total_bytes=len(linked_root) + len(child) - 1,
            ),
        )

    with pytest.raises(SchematicHierarchyError, match="deadline expired"):
        derive_schematic_hierarchy("root.kicad_sch", (_item("root.kicad_sch", root),), deadline=0)


@pytest.mark.parametrize("deadline", [True, float("inf"), float("nan"), "soon"])
def test_rejects_malformed_deadline(deadline: object) -> None:
    with pytest.raises(SchematicHierarchyError, match="deadline is malformed"):
        derive_schematic_hierarchy(
            "root.kicad_sch",
            (_item("root.kicad_sch", _source(ROOT_UUID)),),
            deadline=deadline,  # type: ignore[arg-type]
        )


def test_deadline_is_checked_cooperatively_during_hashing(monkeypatch: pytest.MonkeyPatch) -> None:
    ticks = iter((0.0, 0.0, 0.0, 1.0))

    def monotonic() -> float:
        return next(ticks, 1.0)

    monkeypatch.setattr("copper_mcp.engineering.schematic_hierarchy.time.monotonic", monotonic)
    with pytest.raises(SchematicHierarchyError, match="deadline expired"):
        derive_schematic_hierarchy(
            "root.kicad_sch",
            (_item("root.kicad_sch", _source(ROOT_UUID)),),
            deadline=0.5,
        )


def test_limits_are_copied_and_must_be_the_exact_capture_limits_type() -> None:
    limits = CaptureLimits()
    object.__setattr__(limits, "max_file_bytes", 0)
    with pytest.raises(SchematicHierarchyError, match="limits are malformed"):
        derive_schematic_hierarchy(
            "root.kicad_sch",
            (_item("root.kicad_sch", _source(ROOT_UUID)),),
            limits=limits,
        )

    with pytest.raises(SchematicHierarchyError, match="limits are malformed"):
        derive_schematic_hierarchy(
            "root.kicad_sch",
            (_item("root.kicad_sch", _source(ROOT_UUID)),),
            limits=object(),  # type: ignore[arg-type]
        )


def test_wide_fanout_refuses_before_edge_or_instance_queue_materialization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sheets = tuple(
        _sheet(str(uuid.UUID(int=index + 100)), "leaf.kicad_sch", name=f"Leaf {index}")
        for index in range(512)
    )
    edge_calls = 0
    instance_calls = 0

    def edge_factory(
        *, parent_path: str, child_path: str, sheet_name: str, sheet_uuid: str
    ) -> SchematicFileEdge:
        nonlocal edge_calls
        edge_calls += 1
        return SchematicFileEdge(parent_path, child_path, sheet_name, sheet_uuid)

    def instance_factory(*, source_path: str, uuid_path: str) -> SchematicInstancePath:
        nonlocal instance_calls
        instance_calls += 1
        return SchematicInstancePath(source_path, uuid_path)

    monkeypatch.setattr(
        "copper_mcp.engineering.schematic_hierarchy.SchematicFileEdge", edge_factory
    )
    monkeypatch.setattr(
        "copper_mcp.engineering.schematic_hierarchy.SchematicInstancePath", instance_factory
    )
    with pytest.raises(SchematicHierarchyError, match="file edge budget"):
        derive_schematic_hierarchy(
            "root.kicad_sch",
            (
                _item("root.kicad_sch", _source(ROOT_UUID, *sheets)),
                _item("leaf.kicad_sch", _source(CHILD_ROOT_UUID)),
            ),
        )
    assert edge_calls == 0
    assert instance_calls == 0


@pytest.mark.parametrize("sheet_counts", [(600,), (300, 300)])
def test_cumulative_file_edge_budget_precedes_512th_sheet_reference(
    monkeypatch: pytest.MonkeyPatch, sheet_counts: tuple[int, ...]
) -> None:
    reference_calls = 0

    def reference_factory(*, name: str, target_path: str, sheet_uuid: str) -> _SheetReference:
        nonlocal reference_calls
        reference_calls += 1
        return _SheetReference(name, target_path, sheet_uuid)

    monkeypatch.setattr(
        "copper_mcp.engineering.schematic_hierarchy._SheetReference", reference_factory
    )
    items = []
    for index, count in enumerate(sheet_counts):
        target = f"source-{index + 1}.kicad_sch"
        sheets = tuple(_sheet(str(uuid.UUID(int=number + 100)), target) for number in range(count))
        items.append(_item(f"source-{index}.kicad_sch", _source(ROOT_UUID, *sheets)))
    items.append(_item(f"source-{len(sheet_counts)}.kicad_sch", _source(ROOT_UUID)))
    with pytest.raises(SchematicHierarchyError, match="file edge budget"):
        derive_schematic_hierarchy("source-0.kicad_sch", tuple(items))
    assert reference_calls == 511


def test_accepts_exactly_511_file_edges_and_512_instances() -> None:
    sheets = tuple(
        _sheet(str(uuid.UUID(int=number + 100)), "child.kicad_sch") for number in range(511)
    )
    root = _source(ROOT_UUID, *sheets)
    hierarchy = derive_schematic_hierarchy(
        "root.kicad_sch",
        (_item("root.kicad_sch", root), _item("child.kicad_sch", _source(CHILD_ROOT_UUID))),
    )
    assert len(hierarchy.file_edges) == 511
    assert len(hierarchy.instance_paths) == 512


def test_shared_dag_amplification_refuses_without_oversized_instance_materialization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    items = []
    for level in range(10):
        path = f"level-{level}.kicad_sch"
        root_uuid = str(uuid.UUID(int=30_000 + level))
        sheets = (
            ()
            if level == 9
            else (
                _sheet(SHEET_A_UUID, f"level-{level + 1}.kicad_sch", name="Left"),
                _sheet(SHEET_B_UUID, f"level-{level + 1}.kicad_sch", name="Right"),
            )
        )
        items.append(_item(path, _source(root_uuid, *sheets)))

    instance_calls = 0

    def instance_factory(*, source_path: str, uuid_path: str) -> SchematicInstancePath:
        nonlocal instance_calls
        instance_calls += 1
        return SchematicInstancePath(source_path, uuid_path)

    monkeypatch.setattr(
        "copper_mcp.engineering.schematic_hierarchy.SchematicInstancePath", instance_factory
    )
    with pytest.raises(SchematicHierarchyError, match="instance budget"):
        derive_schematic_hierarchy("level-0.kicad_sch", tuple(items))
    assert instance_calls <= 512


def test_nested_variable_values_are_not_recursively_expanded() -> None:
    root = _source(ROOT_UUID, _sheet(SHEET_A_UUID, "${A}.kicad_sch"))
    with pytest.raises(SchematicHierarchyError, match="reference"):
        derive_schematic_hierarchy(
            "root.kicad_sch",
            (_item("root.kicad_sch", root), _item("child.kicad_sch", _source(CHILD_ROOT_UUID))),
            project_variables={"A": "${B}", "B": "child"},
        )


def test_empty_nested_variable_expansion_is_refused_without_amplification() -> None:
    root = _source(ROOT_UUID, _sheet(SHEET_A_UUID, "${A}child.kicad_sch"))
    with pytest.raises(SchematicHierarchyError, match="reference"):
        derive_schematic_hierarchy(
            "root.kicad_sch",
            (_item("root.kicad_sch", root), _item("child.kicad_sch", _source(CHILD_ROOT_UUID))),
            project_variables={"A": "${B}" * 256, "B": "${C}" * 256, "C": ""},
        )


def test_variable_expansion_output_bound_is_inclusive() -> None:
    from copper_mcp.engineering import schematic_hierarchy as hierarchy

    exact = "x" * 4096
    assert hierarchy._expand_reference("${A}", {"A": exact}, "root", float("inf")) == exact
    with pytest.raises(SchematicHierarchyError, match="reference"):
        hierarchy._expand_reference("${A}x", {"A": exact}, "root", float("inf"))


def test_variable_expansion_obeys_shared_deadline(monkeypatch) -> None:
    from copper_mcp.engineering import schematic_hierarchy as hierarchy

    clock = iter((1.0, 2.0))
    monkeypatch.setattr(hierarchy.time, "monotonic", lambda: next(clock))
    with pytest.raises(SchematicHierarchyError, match="deadline"):
        hierarchy._expand_reference("${A}", {"A": "x"}, "root", 2.0)


def test_enforces_hierarchy_depth_budget() -> None:

    chain = []
    for index in range(17):
        path = f"level-{index}.kicad_sch"
        root_uuid = str(uuid.UUID(int=10_000 + index))
        if index == 16:
            content = _source(root_uuid)
        else:
            content = _source(
                root_uuid,
                _sheet(str(uuid.UUID(int=20_000 + index)), f"level-{index + 1}.kicad_sch"),
            )
        chain.append(_item(path, content))
    with pytest.raises(SchematicHierarchyError, match="depth budget"):
        derive_schematic_hierarchy("level-0.kicad_sch", tuple(chain))


def test_result_order_digest_and_redaction_are_deterministic_and_frozen() -> None:
    root = _source(
        ROOT_UUID,
        _sheet(SHEET_B_UUID, "b.kicad_sch", name="B"),
        _sheet(SHEET_A_UUID, "a.kicad_sch", name="A"),
    )
    items = (
        _item("root.kicad_sch", root),
        _item("b.kicad_sch", _source("20000000-0000-0000-0000-000000000001")),
        _item("a.kicad_sch", _source("10000000-0000-0000-0000-000000000001")),
    )

    first = derive_schematic_hierarchy("root.kicad_sch", items)
    second = derive_schematic_hierarchy("root.kicad_sch", tuple(reversed(items)))

    assert first == second
    assert [item.path for item in first.source_digests] == [
        "a.kicad_sch",
        "b.kicad_sch",
        "root.kicad_sch",
    ]
    assert [edge.sheet_uuid for edge in first.file_edges] == [SHEET_A_UUID, SHEET_B_UUID]
    assert repr(items[0]) == "<SchematicSource redacted>"
    assert repr(first) == "<SchematicHierarchy redacted>"
    assert all("root.kicad_sch" not in repr(item) for item in first.source_digests)
    assert all("root.kicad_sch" not in repr(item) for item in first.file_edges)
    assert all("root.kicad_sch" not in repr(item) for item in first.instance_paths)
    with pytest.raises(dataclasses.FrozenInstanceError):
        first.source_digests = ()  # type: ignore[misc]
