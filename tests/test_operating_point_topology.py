"""Bounded DC topology preflight controls; no simulator or physics verdict."""

from __future__ import annotations

import inspect
import time

import pytest

from copper_mcp.engineering import _operating_point_topology as topology

_DIVIDER = b""".subckt DIV IN OUT
R1 IN OUT 1k
R2 OUT 0 2k
.ends DIV
"""
_NESTED = b""".subckt INNER A B
R1 A MID 1k
R2 MID B 2k
.ends INNER
.subckt OUTER P Q
X1 P Q INNER
.ends OUTER
"""
_DIODE = b".model DM D(IS=1e-14 N=1)\n"
_CELL = b""".subckt CELL A
L1 A MID 1m
R1 MID 0 1k
.ends CELL
"""
_NESTED_CELL = (
    _CELL
    + b""".subckt OUTER P
XCHILD P CELL
.ends OUTER
"""
)


def _validate(
    rows,
    model_sources=(_DIVIDER,),
    voltage_edges=(),
    current_edges=(),
    *,
    deadline=None,
):
    return topology.validate_dc_topology(
        rows,
        model_sources,
        voltage_edges,
        current_edges,
        deadline=time.monotonic() + 30 if deadline is None else deadline,
    )


def _refuses(*args, **kwargs):
    with pytest.raises(topology.OperatingPointTopologyError) as error:
        _validate(*args, **kwargs)
    assert error.value.__context__ is None
    assert "PRIVATE" not in str(error.value)


def test_grounded_divider_has_dc_paths_and_two_connections():
    assert _validate((("X1", "VIN", "VOUT", "div"),), voltage_edges=(("VIN", "0"),)) is None


def test_nested_subcircuit_expands_iteratively():
    assert (
        _validate(
            (("XTOP", "VIN", "GND", "outer"),),
            (_NESTED,),
            voltage_edges=(("VIN", "0"),),
        )
        is None
    )


def test_diode_provides_dc_path_but_current_source_only_adds_connection():
    assert (
        _validate(
            (("D1", "OUT", "gNd", "dm"),),
            (_DIODE,),
            current_edges=(("OUT", "0"),),
        )
        is None
    )


def test_subcircuit_diode_dependency_is_reparsed_and_grounded():
    model = b".model DM D(IS=1e-14)\n.subckt WRAP A B\nD1 A B DM\n.ends WRAP\n"
    assert (
        _validate(
            (("X1", "OUT", "0", "WRAP"),),
            (model,),
            voltage_edges=(("OUT", "GND"),),
        )
        is None
    )


def test_top_level_node_cannot_alias_internal_hierarchical_node():
    rows = (
        ("X1", "VIN", "CELL"),
        ("D1", "x1.mid", "0", "DM"),
    )
    _refuses(
        rows,
        (_CELL, _DIODE),
        voltage_edges=(("VIN", "x1.mid"),),
    )


def test_top_level_node_cannot_alias_nested_hierarchical_node():
    rows = (
        ("XTOP", "VIN", "OUTER"),
        ("D1", "xtop.xchild.mid", "0", "DM"),
    )
    _refuses(
        rows,
        (_NESTED_CELL, _DIODE),
        voltage_edges=(("VIN", "xtop.xchild.mid"),),
    )


def test_casefolded_root_and_nested_instance_paths_cannot_collide():
    rows = (
        ("XTOP", "VIN", "OUTER"),
        ("XTOP.XCHILD", "OTHER", "CELL"),
    )
    _refuses(
        rows,
        (_NESTED_CELL,),
        voltage_edges=(("VIN", "0"), ("OTHER", "0")),
    )


def test_noncolliding_hierarchical_names_preserve_valid_topology():
    rows = (
        ("X1", "VIN", "CELL"),
        ("D1", "SENSE", "0", "DM"),
    )
    assert (
        _validate(
            rows,
            (_CELL, _DIODE),
            voltage_edges=(("VIN", "SENSE"),),
        )
        is None
    )


def test_floating_current_source_case_refuses_despite_resistive_connection():
    model = b".subckt FLOAT A B\nR1 A B 1k\n.ends FLOAT\n"
    _refuses((("X1", "A", "B", "FLOAT"),), (model,), current_edges=(("A", "B"),))


def test_capacitor_current_cutset_does_not_create_dc_path():
    model = b".subckt CUT OUT\nC1 OUT 0 1u\n.ends CUT\n"
    _refuses((("X1", "OUT", "CUT"),), (model,), current_edges=(("OUT", "0"),))


@pytest.mark.parametrize(
    ("model", "rows", "voltage_edges"),
    (
        (_DIVIDER, (("X1", "VIN", "VOUT", "DIV"),), (("VIN", "0"), ("VIN", "GND"))),
        (
            b".subckt COIL A B\nL1 A B 1m\n.ends COIL\n",
            (("X1", "A", "B", "COIL"),),
            (("B", "A"),),
        ),
        (_DIVIDER, (("X1", "VIN", "VOUT", "DIV"),), (("VIN", "VIN"),)),
    ),
)
def test_parallel_voltage_inductor_voltage_and_self_loops_refuse(model, rows, voltage_edges):
    _refuses(rows, (model,), voltage_edges=voltage_edges)


def test_global_zero_and_gnd_are_one_canonical_ground():
    model = b".subckt LOAD A\nR1 A GND 1meg\n.ends LOAD\n"
    assert _validate((("X1", "NODE", "LOAD"),), (model,), voltage_edges=(("NODE", "0"),)) is None


@pytest.mark.parametrize(
    "model",
    (
        b".subckt BAD GND\nR1 GND 0 1k\n.ends BAD\n",
        b".subckt UNUSED A B\nR1 A 0 1k\n.ends UNUSED\n",
    ),
)
def test_formal_ground_and_unused_formal_nodes_refuse(model):
    name = "BAD" if b" BAD " in model else "UNUSED"
    nodes = ("N",) if name == "BAD" else ("N1", "N2")
    _refuses((("X1", *nodes, name),), (model,), voltage_edges=((nodes[0], "0"),))


@pytest.mark.parametrize("kind", ("R", "C", "L"))
@pytest.mark.parametrize("value", ("0", "-1k", "-1e-3"))
def test_runtime_passives_require_positive_finite_values(kind, value):
    model = f".subckt BAD A\n{kind}1 A 0 {value}\n.ends BAD\n".encode()
    _refuses((("X1", "N", "BAD"),), (model,), voltage_edges=(("N", "0"),))


@pytest.mark.parametrize(
    "sources",
    (
        (
            b".model SAME D(IS=1e-14)\n",
            b".model same D(IS=2e-14)\n",
        ),
        (b".subckt BAD A B\nX1 A B MISSING\n.ends BAD\n",),
        (b".subckt CHILD A B\nR1 A B 1k\n.ends CHILD\n.subckt BAD A\nX1 A CHILD\n.ends BAD\n",),
    ),
)
def test_global_model_collisions_dependencies_and_call_arity_refuse(sources):
    _refuses((("X1", "A", "B", "BAD"),), sources, voltage_edges=(("A", "0"),))


def test_unknown_stimulus_nodes_same_ends_and_duplicate_instances_refuse():
    rows = (("X1", "VIN", "VOUT", "DIV"),)
    _refuses(rows, voltage_edges=(("MISSING", "0"),))
    _refuses(rows, current_edges=(("VIN", "vin"),))
    _refuses((*rows, ("x1", "VIN", "VOUT", "DIV")), voltage_edges=(("VIN", "0"),))


def test_exact_types_and_bounds_precede_hash_or_lookup():
    operations = []

    class Hostile(str):
        def __hash__(self):
            operations.append("hash")
            return super().__hash__()

        def casefold(self):
            operations.append("casefold")
            return super().casefold()

    _refuses((("X1", "VIN", "VOUT", Hostile("DIV")),), voltage_edges=(("VIN", "0"),))
    assert operations == []
    _refuses([("X1", "VIN", "VOUT", "DIV")], voltage_edges=(("VIN", "0"),))
    _refuses((("X1", "VIN", "VOUT", "DIV"),), model_sources=[_DIVIDER])
    _refuses((("X1", "VIN", "VOUT", "DIV"),), model_sources=(_DIVIDER,) * 17)


def test_work_node_depth_and_deadline_budgets(monkeypatch):
    rows = (("X1", "VIN", "VOUT", "DIV"),)
    monkeypatch.setattr(topology, "_MAX_WORK", 2)
    _refuses(rows, voltage_edges=(("VIN", "0"),))
    monkeypatch.setattr(topology, "_MAX_WORK", 100_000)
    monkeypatch.setattr(topology, "_MAX_NODES", 2)
    _refuses(rows, voltage_edges=(("VIN", "0"),))
    monkeypatch.setattr(topology, "_MAX_NODES", 100_000)
    monkeypatch.setattr(topology, "_MAX_DEPTH", 1)
    _refuses((("X1", "VIN", "0", "OUTER"),), (_NESTED,), voltage_edges=(("VIN", "0"),))
    monkeypatch.setattr(topology, "_MAX_DEPTH", 128)
    monkeypatch.setattr(topology, "_MAX_PATH_BYTES", len("xtop.xchild") - 1)
    _refuses(
        (("XTOP", "VIN", "OUTER"),),
        (_NESTED_CELL,),
        voltage_edges=(("VIN", "0"),),
    )
    monkeypatch.setattr(topology, "_MAX_PATH_BYTES", 4096)
    monkeypatch.setattr(topology, "_MAX_RETAINED_PATH_BYTES", 1)
    _refuses(rows, voltage_edges=(("VIN", "0"),))
    _refuses(rows, voltage_edges=(("VIN", "0"),), deadline=0)


def test_reflection_exposes_only_preflight_without_execution_authority():
    signature = inspect.signature(topology.validate_dc_topology)
    assert tuple(signature.parameters) == (
        "rows",
        "model_sources",
        "voltage_edges",
        "current_edges",
        "deadline",
    )
    assert signature.parameters["deadline"].kind is inspect.Parameter.KEYWORD_ONLY
    assert "subprocess" not in topology.__dict__ and "receipt" not in topology.__dict__
    assert repr(topology.OperatingPointTopologyError("PRIVATE")) == (
        "OperatingPointTopologyError('PRIVATE')"
    )
