from __future__ import annotations

import hashlib
import os
import re
import stat
import subprocess
import tomllib
import uuid
from dataclasses import replace
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from copper_mcp import __version__
from copper_mcp.adapters.kicad_schematic import (
    KICAD_SCHEMATIC_FORMAT_VERSION,
    KiCadSchematicArtifact,
    render_kicad_schematic,
)
from copper_mcp.adapters.kicad_schematic_parity import verify_kicad_schematic_parity
from copper_mcp.circuit_ir import (
    Component,
    ComponentKind,
    Connection,
    Net,
    decode_snapshot_json,
    make_content,
    make_snapshot,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "benchmarks" / "audio" / "fixtures" / "rc-low-pass-intent-v1.json"
KICAD_CLI = Path("/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli")


def _snapshot():  # type: ignore[no-untyped-def]
    return decode_snapshot_json(FIXTURE.read_bytes())


def _file_state(path: Path) -> tuple[bytes, int, int, int]:
    stat = path.stat()
    return path.read_bytes(), stat.st_ino, stat.st_size, stat.st_mtime_ns


def _uuid_values(content: bytes) -> list[uuid.UUID]:
    tokens = re.findall(rb'\(uuid "([0-9a-f-]{36})"\)', content)
    return [uuid.UUID(token.decode("ascii")) for token in tokens]


def test_render_is_byte_digest_uuid_v4_deterministic_and_source_immutable() -> None:
    fixture_before = _file_state(FIXTURE)
    snapshot = _snapshot()
    snapshot_before = snapshot

    first = render_kicad_schematic(snapshot)
    second = render_kicad_schematic(snapshot)

    assert first == second
    assert first.content == second.content
    assert first.artifact_digest == f"sha256:{hashlib.sha256(first.content).hexdigest()}"
    assert first.intent_digest == snapshot.snapshot_digest
    assert first.format_version == KICAD_SCHEMATIC_FORMAT_VERSION
    assert (first.component_count, first.net_count, first.port_count) == (2, 3, 3)
    assert f"Circuit Intent source: {snapshot.snapshot_digest}" in first.content.decode()

    identifiers = _uuid_values(first.content)
    assert identifiers
    assert len(identifiers) == len(set(identifiers))
    assert all(identifier.version == 4 for identifier in identifiers)
    assert all(identifier.variant == uuid.RFC_4122 for identifier in identifiers)
    assert snapshot == snapshot_before
    assert _file_state(FIXTURE) == fixture_before


def test_render_embeds_only_private_non_board_symbols() -> None:
    artifact = render_kicad_schematic(_snapshot())
    text = artifact.content.decode("utf-8")
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert text.startswith("(kicad_sch\n")
    assert text.endswith(")\n")
    assert '(generator "copper_mcp")' in text
    assert __version__ == metadata["project"]["version"]
    assert f'(generator_version "{__version__}")' in text
    assert "(on_board yes)" not in text
    assert text.count("(on_board no)") == 4
    assert set(re.findall(r'\(lib_id "([^"]+)"\)', text)) == {
        "CopperMCP:C",
        "CopperMCP:R",
    }
    assert "Device:" not in text
    assert "http://" not in text
    assert "https://" not in text
    assert "/Applications/" not in text


def test_fixture_layout_separates_symbols_properties_and_pin_labels() -> None:
    text = render_kicad_schematic(_snapshot()).content.decode("utf-8")
    instances = {
        kind: (float(x), float(y))
        for kind, x, y in re.findall(
            r'\(symbol\s+\(lib_id "CopperMCP:([CR])"\)\s+'
            r"\(at ([0-9.]+) ([0-9.]+) 0\)",
            text,
        )
    }
    label_anchors = [
        (name, float(x), float(y))
        for name, x, y in re.findall(
            r'\(global_label "([^"]+)"\s+\(shape [^)]+\)\s+'
            r"\(at ([0-9.]+) ([0-9.]+) 0\)",
            text,
        )
    ]

    assert instances == {"C": (20.32, 20.32), "R": (45.72, 20.32)}
    assert {name for name, _, _ in label_anchors} == {"AUDIO_IN", "AUDIO_OUT", "GND"}
    assert len(label_anchors) == 4
    for _, x, y in label_anchors:
        center = instances["C"] if x == instances["C"][0] else instances["R"]
        assert abs(y - center[1]) == pytest.approx(5.08)
    assert '(property "Reference" "C1"\n      (at 22.86 19.05 0)' in text
    assert '(property "Value" "100n"\n      (at 22.86 21.59 0)' in text
    assert '(property "Reference" "R1"\n      (at 48.26 19.05 0)' in text
    assert '(property "Value" "1k"\n      (at 48.26 21.59 0)' in text
    assert text.count("(at 0 -5.08 90)") == 2
    assert text.count("(at 0 5.08 270)") == 2
    assert text.count("(length 3.302)") == 2
    assert text.count("(length 4.572)") == 2


def test_render_escapes_untrusted_title_and_component_values() -> None:
    original = _snapshot()
    components = (
        replace(
            original.content.components[0],
            value='100n") (lib_id "Device:Injected',
        ),
        replace(original.content.components[1], value=r"1k\quoted"),
    )
    content = replace(
        original.content,
        title='Quoted "title" \\ path',
        components=components,
    )
    snapshot = make_snapshot(content)

    first = render_kicad_schematic(snapshot)
    second = render_kicad_schematic(snapshot)
    text = first.content.decode("utf-8")

    assert first == second
    assert '(title "Quoted \\"title\\" \\\\ path")' in text
    assert r"100n\") (lib_id \"Device:Injected" in text
    assert r"1k\\quoted" in text
    assert '(lib_id "Device:Injected")' not in text
    assert set(re.findall(r'\(lib_id "([^"]+)"\)', text)) == {
        "CopperMCP:C",
        "CopperMCP:R",
    }


def test_schema_ceiling_ring_has_unique_identities_and_bounded_output() -> None:
    components = tuple(
        Component(
            id=f"component:r{index:03d}",
            kind=ComponentKind.RESISTOR,
            reference=f"R{index + 1}",
            value="1k",
        )
        for index in range(64)
    )
    nets = tuple(
        Net(
            id=f"net:ring-{index:03d}",
            name=f"RING_{index:03d}",
            connections=(
                Connection(component_id=components[index].id, pin="2"),
                Connection(component_id=components[(index + 1) % len(components)].id, pin="1"),
            ),
        )
        for index in range(64)
    )
    snapshot = make_snapshot(
        make_content(
            circuit_id="circuit:maximum-ring",
            project_name="maximum-ring",
            title="Maximum v0.1 topology ring",
            components=components,
            nets=nets,
        )
    )

    artifact = render_kicad_schematic(snapshot)
    identifiers = _uuid_values(artifact.content)

    assert artifact.component_count == 64
    assert artifact.net_count == 64
    assert len(identifiers) == len(set(identifiers))
    assert len(artifact.content) < 1_000_000


def test_artifact_rejects_content_digest_mismatch() -> None:
    artifact = render_kicad_schematic(_snapshot())

    with pytest.raises(ValueError, match="digest does not match"):
        replace(artifact, content=artifact.content + b" ")


@pytest.mark.parametrize(
    "changes",
    [
        {"intent_digest": "sha256:" + "0" * 64},
        {"component_count": 3},
        {"net_count": 4},
        {"port_count": 2},
    ],
)
def test_artifact_rejects_provenance_metadata_drift(changes: dict[str, object]) -> None:
    artifact = render_kicad_schematic(_snapshot())

    with pytest.raises(ValueError, match="provenance does not match"):
        replace(artifact, **changes)  # type: ignore[arg-type]


def _private_kicad_environment(state_root: Path) -> dict[str, str]:
    locations = {
        "HOME": state_root / "home",
        "KICAD_CONFIG_HOME": state_root / "config",
        "KICAD_DOCUMENTS_HOME": state_root / "documents",
        "XDG_CONFIG_HOME": state_root / "xdg-config",
        "XDG_CACHE_HOME": state_root / "cache",
        "XDG_DATA_HOME": state_root / "data",
        "XDG_STATE_HOME": state_root / "state",
        "XDG_RUNTIME_DIR": state_root / "runtime",
        "TMPDIR": state_root / "tmp",
    }
    state_root.mkdir(mode=0o700)
    for location in locations.values():
        location.mkdir(mode=0o700)
    return {
        "PATH": os.defpath,
        "LANG": "C",
        "LC_ALL": "C",
        **{name: str(location) for name, location in locations.items()},
    }


def _assert_private_kicad_state(state_root: Path) -> None:
    file_count = 0
    total_bytes = 0
    for path in state_root.rglob("*"):
        file_state = path.lstat()
        assert not stat.S_ISLNK(file_state.st_mode)
        if stat.S_ISDIR(file_state.st_mode):
            continue
        assert stat.S_ISREG(file_state.st_mode)
        file_count += 1
        total_bytes += file_state.st_size
        assert file_count <= 1_024
        assert file_state.st_size <= 8 * 1024 * 1024
        assert total_bytes <= 32 * 1024 * 1024


def _run_kicad(
    arguments: list[str],
    *,
    environment: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    state_root = Path(environment["HOME"]).parent
    expected_keys = {
        "PATH",
        "LANG",
        "LC_ALL",
        "HOME",
        "KICAD_CONFIG_HOME",
        "KICAD_DOCUMENTS_HOME",
        "XDG_CONFIG_HOME",
        "XDG_CACHE_HOME",
        "XDG_DATA_HOME",
        "XDG_STATE_HOME",
        "XDG_RUNTIME_DIR",
        "TMPDIR",
    }
    assert set(environment) == expected_keys
    assert environment["PATH"] == os.defpath
    assert environment["LANG"] == "C"
    assert environment["LC_ALL"] == "C"
    for name in expected_keys - {"PATH", "LANG", "LC_ALL"}:
        Path(environment[name]).relative_to(state_root)
    result = subprocess.run(  # noqa: S603
        [str(KICAD_CLI), *arguments],
        cwd=environment["TMPDIR"],
        env=environment,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
        timeout=45,
    )
    _assert_private_kicad_state(state_root)
    return result


def _kicad_version(environment: dict[str, str]) -> str | None:
    if not KICAD_CLI.is_file():
        return None
    result = _run_kicad(["version"], environment=environment)
    if result.returncode != 0:
        return None
    return result.stdout.strip()


@pytest.mark.skipif(not KICAD_CLI.is_file(), reason="KiCad CLI is not installed")
def test_kicad_10_0_5_exports_svg_and_exact_logical_nets(tmp_path: Path) -> None:
    state_root = tmp_path / "process-state"
    environment = _private_kicad_environment(state_root)
    version = _kicad_version(environment)
    if version != "10.0.5":
        pytest.skip(f"verified integration requires KiCad 10.0.5, found {version!r}")

    fixture_before = _file_state(FIXTURE)
    artifact = render_kicad_schematic(_snapshot())
    schematic = tmp_path / "rc-low-pass-v1.kicad_sch"
    schematic.write_bytes(artifact.content)
    schematic_before = _file_state(schematic)
    svg_directory = tmp_path / "svg"
    svg_directory.mkdir()
    netlist = tmp_path / "rc-low-pass-v1.xml"

    svg_result = _run_kicad(
        [
            "sch",
            "export",
            "svg",
            "--black-and-white",
            "--exclude-drawing-sheet",
            "--no-background-color",
            "--output",
            str(svg_directory),
            str(schematic),
        ],
        environment=environment,
    )
    netlist_result = _run_kicad(
        [
            "sch",
            "export",
            "netlist",
            "--format",
            "kicadxml",
            "--output",
            str(netlist),
            str(schematic),
        ],
        environment=environment,
    )

    assert svg_result.returncode == 0, svg_result.stderr
    assert netlist_result.returncode == 0, netlist_result.stderr
    svg_files = sorted(svg_directory.glob("*.svg"))
    assert len(svg_files) == 1
    assert svg_files[0].stat().st_size > 0
    svg_root = ET.fromstring(svg_files[0].read_bytes())  # noqa: S314
    assert svg_root.tag.endswith("svg")

    parity = verify_kicad_schematic_parity(_snapshot(), artifact.content, netlist.read_bytes())
    assert parity.source_replay == "passed"
    assert parity.component_parity == "passed"
    assert parity.connectivity_parity == "passed"
    assert (parity.component_count, parity.net_count, parity.connection_count) == (2, 3, 4)
    assert _file_state(schematic) == schematic_before
    assert _file_state(FIXTURE) == fixture_before
    _assert_private_kicad_state(state_root)


def test_artifact_is_a_frozen_value_object() -> None:
    artifact = render_kicad_schematic(_snapshot())

    assert isinstance(artifact, KiCadSchematicArtifact)
    with pytest.raises(AttributeError):
        artifact.content = b"mutated"  # type: ignore[misc]
