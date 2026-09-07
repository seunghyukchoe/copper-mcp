"""Pure native-export controls; successful matching is not simulator or physics evidence."""

from __future__ import annotations

import dataclasses
import time
import tracemalloc

import pytest
from test_project_spice_model_binding import _case, _run

from copper_mcp.engineering import spice_export as export

ROWS = (("XC1", "/NET_1", "/NET_2", "CAP"), ("XR1", "/NET_1", "/NET_2", "RES"))
INCLUDE = "/sealed/input/.copper-spice-models/model-000.lib"
RELATIVE = ".copper-spice-models/model-000.lib"


def _payload(rows=ROWS, include=INCLUDE):
    return (
        f'.title KiCad schematic\n.include "{include}"\n'
        + "\n".join(" ".join(row) for row in rows)
        + "\n.end\n"
    ).encode()


def _parse(payload=None, **kwargs):
    return export.parse_spice_export(
        _payload() if payload is None else payload,
        expected_rows=ROWS,
        expected_includes=((INCLUDE, RELATIVE),),
        deadline=time.monotonic() + 30,
        max_bytes=4096,
        **kwargs,
    )


def test_exact_complete_export_has_stable_redacted_identity():
    first = _parse()
    second = export.parse_spice_export(
        _payload(include="/other/input/.copper-spice-models/model-000.lib"),
        expected_rows=ROWS,
        expected_includes=(("/other/input/.copper-spice-models/model-000.lib", RELATIVE),),
        deadline=time.monotonic() + 30,
        max_bytes=4096,
    )
    assert first == second
    assert first.rows == ROWS and first.model_paths == (RELATIVE,)
    assert first.digest(deadline=time.monotonic() + 30) == second.digest(
        deadline=time.monotonic() + 30
    )
    assert "NET_1" not in repr(first) and "model-000" not in repr(first)
    with pytest.raises(dataclasses.FrozenInstanceError):
        first.rows = ()


def test_late_identity_error_cannot_return_an_observation(monkeypatch):
    def failed(*args, **kwargs):
        raise ValueError("owned late failure")

    monkeypatch.setattr(export.SpiceExportObservation, "digest", failed)
    with pytest.raises(export.SpiceExportError) as error:
        _parse()
    assert error.value.__context__ is None


@pytest.mark.parametrize("kind", ("line-flood", "token-flood"))
def test_structural_limits_precede_expansive_splitting(kind):
    body = b"\n" * 1_000_000 if kind == "line-flood" else b"x " * 500_000
    payload = b".title KiCad schematic\n" + f'.include "{INCLUDE}"\n'.encode() + body + b"\n.end\n"
    tracemalloc.start()
    try:
        with pytest.raises(export.SpiceExportError):
            export.parse_spice_export(
                payload,
                expected_rows=ROWS,
                expected_includes=((INCLUDE, RELATIVE),),
                deadline=time.monotonic() + 30,
                max_bytes=len(payload),
            )
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    # Deliberately broad: bounded framing needs far less than this; unbounded splitting
    # of these owned 1 MB payloads allocates millions of token/line slots.
    assert peak < 3_000_000


def test_expected_row_sort_must_finish_inside_deadline(tmp_path, monkeypatch):
    report = _run(_case(tmp_path, monkeypatch))
    clock = [0.0]
    original = sorted

    def expired(values):
        result = original(values)
        clock[0] = 2.0
        return result

    monkeypatch.setattr(export.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(export, "sorted", expired, raising=False)
    with pytest.raises(export.SpiceExportError):
        export.expected_spice_rows(report, deadline=1.0)


@pytest.mark.parametrize(
    "payload",
    (
        _payload(rows=ROWS[:1]),
        _payload(rows=(*ROWS, ROWS[0])),
        _payload(rows=(*ROWS, ("DR99", "A", "K", "EXTRA"))),
        _payload(include="/etc/other.lib"),
        _payload().replace(b".end\n", b".control\nquit\n.endc\n.end\n"),
        _payload().replace(b".end\n", b'.include "/extra.lib"\n.end\n'),
        _payload().replace(b".end\n", b".model EXTRA D(IS=1e-14)\n.end\n"),
        _payload().replace(b".end\n", b".end\n* hidden trailing data\n"),
        _payload().replace(b"XR1 /NET_1 /NET_2", b"XR1 /NET_2 /NET_1"),
        _payload().replace(b".include", b"* hidden include\n.include"),
    ),
)
def test_refuses_missing_extra_duplicate_or_different_observations(payload):
    with pytest.raises(export.SpiceExportError) as error:
        _parse(payload)
    assert error.value.__context__ is None
    assert "EXTRA" not in str(error.value) and "/etc" not in str(error.value)


@pytest.mark.parametrize("separator", ("\u2028", "\u0085", "\v", "\f"))
def test_unicode_or_control_line_separators_cannot_hide_non_native_syntax(separator):
    payload = _payload().replace(b"CAP\nXR1", ("CAP" + separator + "XR1").encode())
    with pytest.raises(export.SpiceExportError):
        _parse(payload)


@pytest.mark.parametrize("deadline", (True, float("nan"), float("inf"), 10**1000, 0))
def test_explicit_digest_deadline_is_finite(deadline):
    observation = _parse()
    with pytest.raises(export.SpiceExportError):
        observation.digest(deadline=deadline)


def test_expected_rows_follow_model_port_order_and_detect_name_collisions(tmp_path, monkeypatch):
    report = _run(_case(tmp_path, monkeypatch))
    assert export.expected_spice_rows(report, deadline=time.monotonic() + 30) == ROWS
    first, second = report.references
    first_pin = dataclasses.replace(
        first.pins[0], node=dataclasses.replace(first.pins[0].node, net_name="/S A")
    )
    second_pin = dataclasses.replace(
        second.pins[0],
        node=dataclasses.replace(second.pins[0].node, net_name="/S_A", net_code="99"),
    )
    changed = dataclasses.replace(
        report,
        references=(
            dataclasses.replace(first, pins=(first_pin, *first.pins[1:])),
            dataclasses.replace(second, pins=(second_pin, *second.pins[1:])),
        ),
    )
    with pytest.raises(export.SpiceExportError):
        export.expected_spice_rows(changed, deadline=time.monotonic() + 30)


@pytest.mark.parametrize(
    "name,expected",
    (("/foo/0", "0"), ("//0", "/root/0"), ("/gNd", "gNd"), ("Net-(U1-Pad1)", "Net-_U1-Pad1_")),
)
def test_supported_native_name_conversion_is_explicit(name, expected):
    assert export._net_name(name) == expected


@pytest.mark.parametrize("name", ("~{RESET}", "V_{OUT}", "한글", "A\\ B", "A\tB"))
def test_unsupported_markup_or_names_refuse(name):
    with pytest.raises(export.SpiceExportError):
        export._net_name(name)
