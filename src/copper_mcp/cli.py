"""Dependency-light command-line interface for local automation and CI."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from copper_mcp.adapters.kicad_schematic import MAX_RENDERED_SCHEMATIC_BYTES
from copper_mcp.circuit_intent_service import build_schematic_from_snapshot_json
from copper_mcp.circuit_ir.limits import MAX_CIRCUIT_INPUT_BYTES
from copper_mcp.config import ConfigurationError, Settings
from copper_mcp.kicad_cli import KiCadCliError
from copper_mcp.kicad_file import BoardFormatError, load_json_file
from copper_mcp.models import candidate_from_dict, rank_candidates
from copper_mcp.request_boundary import CONSTRAINT_FIELDS, RequestError
from copper_mcp.routing import AStarSettings
from copper_mcp.security import (
    WorkspaceViolationError,
    create_workspace_file,
    read_workspace_file,
)
from copper_mcp.tools import (
    inspect_board,
    inspect_board_ir,
    observe_board_scene_raw,
    preview_route,
    run_board_drc,
    server_info,
)

_ROUTER_SETTING_OPTIONS = tuple(AStarSettings.__dataclass_fields__)
_CONSTRAINT_OPTIONS = CONSTRAINT_FIELDS


def _json_dump(payload: object) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def _constraints(args: argparse.Namespace) -> dict[str, Any]:
    return {option: getattr(args, option) for option in _CONSTRAINT_OPTIONS}


def _preview_request(args: argparse.Namespace) -> dict[str, Any]:
    """Build a preview request from flags; the service still validates every field."""

    overrides = {
        option: getattr(args, option)
        for option in _ROUTER_SETTING_OPTIONS
        if getattr(args, option) is not None
    }
    return {
        "board": args.path,
        "net": args.net,
        "layer": args.layer,
        "seed": args.seed,
        "include_drc": args.drc,
        "constraints": _constraints(args),
        "settings": overrides,
    }


def _scene_request(args: argparse.Namespace) -> dict[str, Any]:
    """Build a scene request from flags; the service still validates every field.

    Only the two documented region forms are constructible here. Argparse rejects supplying
    both, and the service independently rejects a mixed region, so a malformed window cannot
    reach the reader through either path.
    """

    if args.around_ref is not None:
        region: dict[str, Any] = {"around_ref_id": args.around_ref}
        if args.radius_nm is not None:
            region["radius_nm"] = args.radius_nm
    else:
        minimum_x, minimum_y, maximum_x, maximum_y = args.region
        region = {
            "min_x_nm": minimum_x,
            "min_y_nm": minimum_y,
            "max_x_nm": maximum_x,
            "max_y_nm": maximum_y,
        }
    return {
        "board": args.path,
        "constraints": _constraints(args),
        "region": region,
        "layers": list(args.scene_layers or ()),
        "include_annotations": args.include_annotations,
        "include_render": args.render is not None,
    }


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

    board_ir_parser = subparsers.add_parser(
        "board-ir", help="Report whether a board converts to the supported Board IR"
    )
    board_ir_parser.add_argument("path", help="Board path relative to the workspace")
    for option in _CONSTRAINT_OPTIONS:
        board_ir_parser.add_argument(f"--{option.replace('_', '-')}", type=int, required=True)

    preview_parser = subparsers.add_parser(
        "preview-route", help="Preview one two-pin route candidate without modifying files"
    )
    preview_parser.add_argument("path", help="Board path relative to the workspace")
    preview_parser.add_argument("--net", required=True, help="KiCad net name to route")
    preview_parser.add_argument("--layer", required=True, help="Copper layer name, such as F.Cu")
    for option in _CONSTRAINT_OPTIONS:
        preview_parser.add_argument(f"--{option.replace('_', '-')}", type=int, required=True)
    preview_parser.add_argument("--seed", type=int, default=0)
    for option in _ROUTER_SETTING_OPTIONS:
        preview_parser.add_argument(f"--{option.replace('_', '-')}", type=int, default=None)
    preview_parser.add_argument(
        "--drc",
        action="store_true",
        help="Bind the candidate to authoritative KiCad DRC evidence",
    )

    scene_parser = subparsers.add_parser(
        "observe-scene",
        help="Observe a region of a board as a bounded Circuit Scene without modifying it",
    )
    scene_parser.add_argument("path", help="Board path relative to the workspace")
    for option in _CONSTRAINT_OPTIONS:
        scene_parser.add_argument(f"--{option.replace('_', '-')}", type=int, required=True)
    scene_region = scene_parser.add_mutually_exclusive_group(required=True)
    scene_region.add_argument(
        "--region",
        nargs=4,
        type=int,
        metavar=("MIN_X_NM", "MIN_Y_NM", "MAX_X_NM", "MAX_Y_NM"),
        help="Observation window in absolute board nanometres",
    )
    scene_region.add_argument("--around-ref", help="Observe around one object reference id")
    scene_parser.add_argument(
        "--radius-nm", type=int, default=None, help="Required with --around-ref"
    )
    scene_parser.add_argument(
        "--layer",
        action="append",
        default=None,
        dest="scene_layers",
        help="Restrict to a copper layer; repeatable",
    )
    scene_parser.add_argument(
        "--render",
        default=None,
        metavar="OUTPUT_SVG",
        help=(
            "Also render the board's copper to this new .svg path inside the workspace; "
            "existing files are never replaced"
        ),
    )
    scene_parser.add_argument(
        "--include-annotations",
        action="store_true",
        help="Also return board text, quarantined and marked untrusted",
    )

    validate_parser = subparsers.add_parser("validate-candidate", help="Validate candidate JSON")
    validate_parser.add_argument("path", help="Candidate path relative to the workspace")

    compare_parser = subparsers.add_parser("compare", help="Rank candidate JSON files")
    compare_parser.add_argument(
        "paths", nargs="+", help="Candidate paths relative to the workspace"
    )

    render_parser = subparsers.add_parser(
        "render-schematic",
        help="Validate Circuit Intent and create one new deterministic KiCad schematic",
    )
    render_parser.add_argument("path", help="Circuit Intent snapshot JSON inside the workspace")
    render_parser.add_argument(
        "--output",
        required=True,
        help="New .kicad_sch path inside the workspace; existing files are never replaced",
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
        if args.command == "board-ir":
            _json_dump(
                inspect_board_ir({"board": args.path, "constraints": _constraints(args)}, settings)
            )
            return 0
        if args.command == "preview-route":
            _json_dump(preview_route(_preview_request(args), settings))
            return 0
        if args.command == "observe-scene":
            scene = observe_board_scene_raw(_scene_request(args), settings)
            document = scene.to_dict()
            if args.render is not None:
                if scene.render_bytes is None:
                    raise ValueError("the board produced no render; it is outside Board IR")
                # Create-only, exact lowercase suffix, workspace-confined: the same discipline
                # as the schematic export. Observation must never overwrite a caller's file.
                output_path = create_workspace_file(
                    settings.workspace,
                    args.render,
                    scene.render_bytes,
                    allowed_suffixes={".svg"},
                    max_bytes=settings.max_render_bytes,
                )
                document["export"] = {
                    "created": True,
                    "output_path": output_path.relative_to(
                        settings.workspace.resolve(strict=True)
                    ).as_posix(),
                }
            _json_dump(document)
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
        if args.command == "render-schematic":
            intent_snapshot = read_workspace_file(
                settings.workspace,
                args.path,
                allowed_suffixes={".json"},
                max_bytes=MAX_CIRCUIT_INPUT_BYTES,
            )
            build = build_schematic_from_snapshot_json(intent_snapshot.content)
            output_path = create_workspace_file(
                settings.workspace,
                args.output,
                build.artifact.content,
                allowed_suffixes={".kicad_sch"},
                max_bytes=MAX_RENDERED_SCHEMATIC_BYTES,
            )
            response = build.to_dict()
            response["export"] = {
                "created": True,
                "output_path": output_path.relative_to(
                    settings.workspace.resolve(strict=True)
                ).as_posix(),
            }
            _json_dump(response)
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
        RequestError,
        ValueError,
        WorkspaceViolationError,
    ) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    parser.error("unsupported command")
