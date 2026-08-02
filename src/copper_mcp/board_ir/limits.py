"""Deterministic structural budgets for untrusted Board IR and KiCad inputs."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ParseLimits:
    """Independent caps that prevent parser memory and structure amplification."""

    max_input_bytes: int = 16 * 1024 * 1024
    max_depth: int = 128
    max_tokens: int = 1_000_000
    max_nodes: int = 500_000
    max_atom_chars: int = 4096
    max_children_per_list: int = 100_000
    max_objects: int = 250_000
    max_vertices_per_ring: int = 100_000
    max_total_vertices: int = 1_000_000
    max_intersection_tests: int = 2_000_000
    max_diagnostics: int = 100

    def __post_init__(self) -> None:
        for name in self.__dataclass_fields__:
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer")
