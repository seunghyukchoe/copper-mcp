"""Dependency-light command-line interface for local automation and CI."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from copper_mcp.config import ConfigurationError, Settings
from copper_mcp.kicad_cli import KiCadCliError
from copper_mcp.kicad_file import BoardFormatError, load_json_file
from copper_mcp.models import candidate_from_dict, rank_candidates
from copper_mcp.security import WorkspaceViolationError
from copper_mcp.tools import inspect_board, run_board_drc, server_info


def _json_dump(payload: object) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def _load_candidate(path: str, settings: Settings) -> dict[str, Any]:
    raw = load_json_file(path, settings)
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("candidate file must contain valid UTF-8 JSON") from error
    if not isinstance(payload, dict):
        raise ValueError("candidate document must be a JSON object")
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="copper-mcp", description="CopperMCP developer CLI")
    parser.add_argument("--workspace", type=Path, help="Allowed project directory")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("info", help="Show version and capability information")

    inspect_parser = subparsers.add_parser("inspect", help="Inspect a KiCad board read-only")
    inspect_parser.add_argument("path", help="Board path relative to the workspace")

    drc_parser = subparsers.add_parser("drc", help="Run authoritative KiCad DRC read-only")
    drc_parser.add_argument("path", help="Board path relative to the workspace")

    validate_parser = subparsers.add_parser("validate-candidate", help="Validate candidate JSON")
    validate_parser.add_argument("path", help="Candidate path relative to the workspace")

    compare_parser = subparsers.add_parser("compare", help="Rank candidate JSON files")
    compare_parser.add_argument(
        "paths", nargs="+", help="Candidate paths relative to the workspace"
    )

    serve_parser = subparsers.add_parser("serve", help="Run the MCP gateway")
    serve_parser.add_argument("--transport", choices=("stdio", "streamable-http"), default="stdio")
    return parser


def _settings_for(workspace: Path | None) -> Settings:
    if workspace is not None:
        os.environ["COPPER_MCP_WORKSPACE"] = str(workspace)
    return Settings.from_env()


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "info":
            _json_dump(server_info())
            return 0

        settings = _settings_for(args.workspace)
        if args.command == "inspect":
            _json_dump(inspect_board(args.path, settings))
            return 0
        if args.command == "drc":
            _json_dump(run_board_drc(args.path, settings))
            return 0
        if args.command == "validate-candidate":
            _json_dump(candidate_from_dict(_load_candidate(args.path, settings)).to_dict())
            return 0
        if args.command == "compare":
            candidates = [
                candidate_from_dict(_load_candidate(path, settings)) for path in args.paths
            ]
            _json_dump([candidate.to_dict() for candidate in rank_candidates(candidates)])
            return 0
        if args.command == "serve":
            os.environ["COPPER_MCP_TRANSPORT"] = args.transport
            from copper_mcp.mcp_server import main as serve

            serve()
            return 0
    except (
        BoardFormatError,
        ConfigurationError,
        KiCadCliError,
        OSError,
        ValueError,
        WorkspaceViolationError,
    ) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    parser.error("unsupported command")
