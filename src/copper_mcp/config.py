"""Validated process configuration.

The project intentionally reads the process environment directly and never loads
``.env`` files on behalf of callers. Secret-file loading belongs to the host or a
dedicated secret manager.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

_ALLOWED_TRANSPORTS = frozenset({"stdio", "streamable-http"})


class ConfigurationError(ValueError):
    """Raised when process configuration is unsafe or invalid."""


#: One optional sign and ASCII digits, nothing else. ``int()`` alone is looser than it looks: it
#: accepts ``"1_000"`` as 1000 and the Arabic-Indic ``"٤"`` as 4, and it strips surrounding
#: whitespace. Every one of those is a value whose *appearance* in a deployment's environment
#: differs from the ceiling the process would then enforce, which is exactly the confusion the
#: exact-membership rule on the allow-flags exists to prevent. A refused spelling is cheap to fix;
#: a silently reinterpreted one is not.
_INTEGER_SPELLING = re.compile(r"\A-?[0-9]+\Z")


def _bounded_int(name: str, raw: str, minimum: int, maximum: int) -> int:
    if not isinstance(raw, str) or not _INTEGER_SPELLING.match(raw):
        raise ConfigurationError(f"{name} must be an integer")
    value = int(raw)
    if not minimum <= value <= maximum:
        raise ConfigurationError(f"{name} must be between {minimum} and {maximum}")
    return value


@dataclass(frozen=True, slots=True)
class Settings:
    """Runtime settings shared by the CLI and MCP gateway."""

    workspace: Path
    transport: str = "stdio"
    host: str = "127.0.0.1"
    port: int = 8765
    max_board_bytes: int = 64 * 1024 * 1024
    # Structural parse budgets. Every default mirrors the matching ``ParseLimits`` field, and the
    # measurement each is derived from is recorded in
    # docs/research/parse-budget-calibration-v1.md. They are settings rather than constants
    # because ``COPPER_MCP_MAX_BOARD_BYTES`` alone could not admit an ordinary large board: the
    # node ceiling bound first and had no knob at all (issue #112).
    #
    # Upper bounds are not decoration. ``max_parse_tokens`` is the parser's memory control at a
    # measured ~61 bytes of peak parse arena per admitted token, so its ceiling of 32,000,000 is
    # also a ~2 GiB residency ceiling; ``max_parse_objects`` cannot usefully exceed the Board IR
    # schema's own 250,000-object limit, because validation takes the minimum of the two; and
    # ``max_parse_intersection_tests`` buys O(n^2) work at ~0.83 us per test, so its ceiling of
    # 50,000,000 is a ~41 s ceiling. None of these may be raised without accepting that cost.
    max_parse_tokens: int = 4_000_000
    max_parse_nodes: int = 3_000_000
    max_parse_children_per_list: int = 500_000
    max_parse_objects: int = 250_000
    max_parse_total_vertices: int = 2_000_000
    max_parse_intersection_tests: int = 2_000_000
    kicad_cli: Path | None = None
    kicad_timeout_seconds: int = 120
    max_drc_report_bytes: int = 8 * 1024 * 1024
    max_drc_context_bytes: int = 128 * 1024 * 1024
    max_drc_context_files: int = 10_000
    max_drc_context_scan_seconds: int = 10
    max_route_preview_seconds: int = 30
    # Cached zone-fill vertices admitted from one board, summed across every island. Derived in
    # docs/research/fill-vertex-budget-calibration-v1.md by ADR-0079's rule -- a board that fits
    # inside the parser's 16 MiB input ceiling should fit inside every scale budget -- applied at
    # the densest observed pour, 29,503 vertices per mebibyte: 16 x 29,503 = 472,048, rounded up.
    #
    # It is not the control it looks like, and ADR-0104 records why. `read_fill_islands` parses
    # the whole document before it counts a single vertex, so a refusal here is paid for at full
    # parse price: refusing a 9.1 MB board at a budget of 3 costs 20.9 s of the 24.2 s a complete
    # read costs. What this budget meters is only the materialisation of already-parsed atoms
    # into points, measured at ~25 us and ~113 bytes per vertex. The defence against an unbounded
    # vertex list is `ParseLimits` (ADR-0079), which also caps how many vertices can be offered at
    # all: at the shipped parse defaults a document cannot carry more than 741,375 of them before
    # `budget.exceeded.nodes` refuses it.
    max_fill_vertices: int = 500_000
    # Provisional: CopperTone's whole board is ~120 objects. Raise after measuring a genuinely
    # dense board rather than guessing upward now.
    max_scene_objects: int = 2_000
    max_scene_vertices: int = 200_000
    max_render_bytes: int = 4 * 1024 * 1024
    max_scene_annotations: int = 5_000
    max_placement_subjects: int = 64
    max_placement_rules: int = 256
    max_placement_checks: int = 2_000_000
    max_placement_seconds: int = 10
    allow_apply: bool = False
    allow_live_ipc: bool = False
    #: Consent to mutate the *running editor's* in-memory document. Deliberately its own flag
    #: rather than the conjunction of the two above: ADR-0069 recorded that the live opt-in
    #: "enables observation only", and ADR-0025's flag is documented as replacing a file on
    #: disk. Reading an already-granted pair as mutation consent would retroactively widen what
    #: past operators agreed to. See ADR-0074.
    allow_live_apply: bool = False

    @classmethod
    def from_env(cls) -> Settings:
        workspace = Path(os.environ.get("COPPER_MCP_WORKSPACE", str(Path.cwd()))).expanduser()
        workspace = workspace.resolve(strict=True)
        if not workspace.is_dir():
            raise ConfigurationError("COPPER_MCP_WORKSPACE must be a directory")

        transport = os.environ.get("COPPER_MCP_TRANSPORT", "stdio")
        if transport not in _ALLOWED_TRANSPORTS:
            allowed = ", ".join(sorted(_ALLOWED_TRANSPORTS))
            raise ConfigurationError(f"COPPER_MCP_TRANSPORT must be one of: {allowed}")

        host = os.environ.get("COPPER_MCP_HOST", "127.0.0.1").strip()
        if not host or any(character.isspace() for character in host):
            raise ConfigurationError("COPPER_MCP_HOST is invalid")

        port = _bounded_int("COPPER_MCP_PORT", os.environ.get("COPPER_MCP_PORT", "8765"), 1, 65535)
        max_board_bytes = _bounded_int(
            "COPPER_MCP_MAX_BOARD_BYTES",
            os.environ.get("COPPER_MCP_MAX_BOARD_BYTES", str(64 * 1024 * 1024)),
            1024,
            1024 * 1024 * 1024,
        )
        # Each range runs up to the point where the budget stops being reachable, so no accepted
        # value is inert. Three different things set that point: the parser's own 16 MiB input
        # ceiling (a token costs at least one byte, a node or a list child at least two, a vertex
        # at least ten), the Board IR schema's fixed object limit, and — for the one budget whose
        # cost is superlinear in input size — an explicit wall-clock ceiling.
        max_parse_tokens = _bounded_int(
            "COPPER_MCP_MAX_PARSE_TOKENS",
            os.environ.get("COPPER_MCP_MAX_PARSE_TOKENS", "4000000"),
            1,
            # Also a memory ceiling: peak parse arena measures ~61 bytes per admitted token, so
            # authorizing the maximum here authorizes roughly 1 GiB of transient residency.
            16 * 1024 * 1024,
        )
        max_parse_nodes = _bounded_int(
            "COPPER_MCP_MAX_PARSE_NODES",
            os.environ.get("COPPER_MCP_MAX_PARSE_NODES", "3000000"),
            1,
            8 * 1024 * 1024,
        )
        max_parse_children_per_list = _bounded_int(
            "COPPER_MCP_MAX_PARSE_CHILDREN_PER_LIST",
            os.environ.get("COPPER_MCP_MAX_PARSE_CHILDREN_PER_LIST", "500000"),
            1,
            8 * 1024 * 1024,
        )
        max_parse_objects = _bounded_int(
            "COPPER_MCP_MAX_PARSE_OBJECTS",
            os.environ.get("COPPER_MCP_MAX_PARSE_OBJECTS", "250000"),
            1,
            # The Board IR schema's own object ceiling. Validation refuses at the minimum of this
            # setting and that constant, so accepting a larger value here would be accepting a
            # number that changes nothing — a setting that lies is worse than one that refuses.
            250_000,
        )
        max_parse_total_vertices = _bounded_int(
            "COPPER_MCP_MAX_PARSE_TOTAL_VERTICES",
            os.environ.get("COPPER_MCP_MAX_PARSE_TOTAL_VERTICES", "2000000"),
            3,
            2_000_000,
        )
        max_parse_intersection_tests = _bounded_int(
            "COPPER_MCP_MAX_PARSE_INTERSECTION_TESTS",
            os.environ.get("COPPER_MCP_MAX_PARSE_INTERSECTION_TESTS", "2000000"),
            1,
            # ~0.83 us per test, so this ceiling is a ~17 s ceiling. Raising the default trades a
            # refusal for a slow refusal, which is why it is not derived from the byte ceiling.
            20_000_000,
        )
        raw_kicad_cli = os.environ.get("COPPER_MCP_KICAD_CLI", "").strip()
        kicad_cli = Path(raw_kicad_cli).expanduser() if raw_kicad_cli else None
        kicad_timeout_seconds = _bounded_int(
            "COPPER_MCP_KICAD_TIMEOUT_SECONDS",
            os.environ.get("COPPER_MCP_KICAD_TIMEOUT_SECONDS", "120"),
            1,
            3600,
        )
        max_drc_report_bytes = _bounded_int(
            "COPPER_MCP_MAX_DRC_REPORT_BYTES",
            os.environ.get("COPPER_MCP_MAX_DRC_REPORT_BYTES", str(8 * 1024 * 1024)),
            1024,
            64 * 1024 * 1024,
        )
        max_drc_context_bytes = _bounded_int(
            "COPPER_MCP_MAX_DRC_CONTEXT_BYTES",
            os.environ.get("COPPER_MCP_MAX_DRC_CONTEXT_BYTES", str(128 * 1024 * 1024)),
            1024,
            1024 * 1024 * 1024,
        )
        max_drc_context_files = _bounded_int(
            "COPPER_MCP_MAX_DRC_CONTEXT_FILES",
            os.environ.get("COPPER_MCP_MAX_DRC_CONTEXT_FILES", "10000"),
            1,
            100_000,
        )
        max_drc_context_scan_seconds = _bounded_int(
            "COPPER_MCP_MAX_DRC_CONTEXT_SCAN_SECONDS",
            os.environ.get("COPPER_MCP_MAX_DRC_CONTEXT_SCAN_SECONDS", "10"),
            1,
            300,
        )
        max_route_preview_seconds = _bounded_int(
            "COPPER_MCP_MAX_ROUTE_PREVIEW_SECONDS",
            os.environ.get("COPPER_MCP_MAX_ROUTE_PREVIEW_SECONDS", "30"),
            1,
            600,
        )
        max_fill_vertices = _bounded_int(
            "COPPER_MCP_MAX_FILL_VERTICES",
            os.environ.get("COPPER_MCP_MAX_FILL_VERTICES", "500000"),
            3,
            # The ceiling stays at 1,000,000 rather than moving with the default. At the shipped
            # parse budgets nothing above 741,375 is reachable, but `COPPER_MCP_MAX_PARSE_NODES`
            # can be raised, and the shortest legal fill vertex `(xy 0 0)` is 8 bytes, so the
            # parser's fixed 16 MiB input ceiling admits at most 2,097,152 of them under any
            # configuration. 1,000,000 therefore is not an inert setting -- an operator who has
            # raised the node budget can reach it -- and it is deliberately left below the byte
            # ceiling, because raising a DoS ceiling that no measured board needs buys nothing.
            1_000_000,
        )
        max_scene_objects = _bounded_int(
            "COPPER_MCP_MAX_SCENE_OBJECTS",
            os.environ.get("COPPER_MCP_MAX_SCENE_OBJECTS", "2000"),
            1,
            200_000,
        )
        max_scene_vertices = _bounded_int(
            "COPPER_MCP_MAX_SCENE_VERTICES",
            os.environ.get("COPPER_MCP_MAX_SCENE_VERTICES", "200000"),
            3,
            5_000_000,
        )
        max_render_bytes = _bounded_int(
            "COPPER_MCP_MAX_RENDER_BYTES",
            os.environ.get("COPPER_MCP_MAX_RENDER_BYTES", str(4 * 1024 * 1024)),
            1024,
            64 * 1024 * 1024,
        )
        max_scene_annotations = _bounded_int(
            "COPPER_MCP_MAX_SCENE_ANNOTATIONS",
            os.environ.get("COPPER_MCP_MAX_SCENE_ANNOTATIONS", "5000"),
            1,
            1_000_000,
        )
        max_placement_subjects = _bounded_int(
            "COPPER_MCP_MAX_PLACEMENT_SUBJECTS",
            os.environ.get("COPPER_MCP_MAX_PLACEMENT_SUBJECTS", "64"),
            1,
            4_096,
        )
        max_placement_rules = _bounded_int(
            "COPPER_MCP_MAX_PLACEMENT_RULES",
            os.environ.get("COPPER_MCP_MAX_PLACEMENT_RULES", "256"),
            0,
            16_384,
        )
        max_placement_checks = _bounded_int(
            "COPPER_MCP_MAX_PLACEMENT_CHECKS",
            os.environ.get("COPPER_MCP_MAX_PLACEMENT_CHECKS", "2000000"),
            1,
            100_000_000,
        )
        max_placement_seconds = _bounded_int(
            "COPPER_MCP_MAX_PLACEMENT_SECONDS",
            os.environ.get("COPPER_MCP_MAX_PLACEMENT_SECONDS", "10"),
            1,
            600,
        )
        raw_allow_apply = os.environ.get("COPPER_MCP_ALLOW_APPLY", "0")
        if raw_allow_apply not in {"0", "1"}:
            # Exact membership, no case folding and no truthiness. "false", "no" and "" would
            # all be truthy under bool(), and a flag that enables board mutation must never be
            # switched on by an ambiguous spelling.
            raise ConfigurationError('COPPER_MCP_ALLOW_APPLY must be exactly "0" or "1"')
        raw_allow_live_ipc = os.environ.get("COPPER_MCP_ALLOW_LIVE_IPC", "0")
        if raw_allow_live_ipc not in {"0", "1"}:
            # Same exact-membership rule as the apply flag. Connecting to whatever socket the
            # official binding defaults to is an outbound action against the operator's running
            # editor, so it must never be switched on by an ambiguous spelling either.
            raise ConfigurationError('COPPER_MCP_ALLOW_LIVE_IPC must be exactly "0" or "1"')
        raw_allow_live_apply = os.environ.get("COPPER_MCP_ALLOW_LIVE_APPLY", "0")
        if raw_allow_live_apply not in {"0", "1"}:
            # Same exact-membership rule again, for the same reason: this flag is the only
            # consent that authorizes mutating a document the operator has open in front of
            # them, and no ambiguous spelling may switch it on.
            raise ConfigurationError('COPPER_MCP_ALLOW_LIVE_APPLY must be exactly "0" or "1"')
        return cls(
            workspace=workspace,
            transport=transport,
            host=host,
            port=port,
            max_board_bytes=max_board_bytes,
            max_parse_tokens=max_parse_tokens,
            max_parse_nodes=max_parse_nodes,
            max_parse_children_per_list=max_parse_children_per_list,
            max_parse_objects=max_parse_objects,
            max_parse_total_vertices=max_parse_total_vertices,
            max_parse_intersection_tests=max_parse_intersection_tests,
            kicad_cli=kicad_cli,
            kicad_timeout_seconds=kicad_timeout_seconds,
            max_drc_report_bytes=max_drc_report_bytes,
            max_drc_context_bytes=max_drc_context_bytes,
            max_drc_context_files=max_drc_context_files,
            max_drc_context_scan_seconds=max_drc_context_scan_seconds,
            max_route_preview_seconds=max_route_preview_seconds,
            max_fill_vertices=max_fill_vertices,
            max_scene_objects=max_scene_objects,
            max_scene_vertices=max_scene_vertices,
            max_render_bytes=max_render_bytes,
            max_scene_annotations=max_scene_annotations,
            max_placement_subjects=max_placement_subjects,
            max_placement_rules=max_placement_rules,
            max_placement_checks=max_placement_checks,
            max_placement_seconds=max_placement_seconds,
            allow_apply=raw_allow_apply == "1",
            allow_live_ipc=raw_allow_live_ipc == "1",
            allow_live_apply=raw_allow_live_apply == "1",
        )
