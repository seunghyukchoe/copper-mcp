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
from copper_mcp.schematic_erc_service import verify_schematic_erc_from_snapshot_json
from copper_mcp.security import (
    WorkspaceViolationError,
    create_workspace_file,
    read_workspace_file,
)
from copper_mcp.source_to_board_parity_service import (
    verify_source_to_board_parity_from_snapshot_json,
)
from copper_mcp.tools import (
    apply_candidate,
    inspect_board,
    inspect_board_ir,
    observe_board_scene_raw,
    preview_placement,
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
    request: dict[str, Any] = {
        "board": args.path,
        "layer": args.layer,
        "seed": args.seed,
        "include_drc": args.drc,
        "constraints": _constraints(args),
        "settings": overrides,
    }
    if args.net is not None:
        request["net"] = args.net
    else:
        request["net_ref_id"] = args.net_ref_id
    if args.expect_board_revision is not None:
        request["expect_board_revision"] = args.expect_board_revision
    if args.expect_snapshot_digest is not None:
        request["expect_snapshot_digest"] = args.expect_snapshot_digest
    return request


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


def _cli_apply_authorization(
    board: str, manifest: dict[str, Any], expected_revision: str
) -> tuple[Any, str]:
    """Authorize a CLI apply and return the authority plus a token bound to this request.

    The MCP surface uses a single-use token because a *model* drives it and must not be able
    to apply anything the operator has not previewed. The CLI is different: the operator is
    the one typing the command, and the apply token's signing key exists only inside the
    issuing process, so a token minted by an earlier `preview-route` run could never verify
    here. Requiring one would therefore be theatre - a flag that can only ever be satisfied by
    a value this process just made up.

    The CLI's real authorization is the operator flag plus `--expect-board-revision`, which is
    the compare-and-swap the operator states explicitly. The token is minted here for exactly
    that binding so the service keeps one code path and its token check stays a genuine
    internal invariant rather than a branch that is skipped on this route.
    """

    from copper_mcp.apply.tokens import ApplyBinding, ApplyTokenAuthority

    authority = ApplyTokenAuthority()
    token = authority.issue(
        ApplyBinding(
            candidate_id=str(manifest.get("candidate_id", "")),
            base_revision=str(manifest.get("base_revision", "")),
            board_revision=expected_revision,
            relative_path=board,
        )
    )
    return authority, token


def _placement_request(args: argparse.Namespace, settings: Settings) -> dict[str, Any]:
    """Build a placement request from flags; the service still validates every field.

    Rules and proposals are structured enough that flags would be a worse interface than a
    document, so they come from an optional workspace-confined JSON file. The board,
    constraints and subjects always come from the flags, so the file cannot redirect the
    request at a different board.
    """

    request: dict[str, Any] = {}
    if args.intent is not None:
        raw = load_json_file(args.intent, settings)
        try:
            document = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("intent file must contain valid UTF-8 JSON") from error
        if not isinstance(document, dict):
            raise ValueError("intent document must be a JSON object")
        for key in ("rules", "proposals"):
            if key in document:
                request[key] = document[key]
        extra = set(document) - {"rules", "proposals"}
        if extra:
            raise ValueError(
                f"intent document has {len(extra)} unsupported field(s); "
                "supported fields are: proposals, rules"
            )
    request["board"] = args.path
    request["constraints"] = _constraints(args)
    request["subjects"] = list(args.placement_subjects)
    if args.placement_grid_nm is not None:
        request["placement_grid_nm"] = args.placement_grid_nm
    return request


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
        "preview-route", help="Preview one route candidate without modifying files"
    )
    preview_parser.add_argument("path", help="Board path relative to the workspace")
    preview_selector = preview_parser.add_mutually_exclusive_group(required=True)
    preview_selector.add_argument("--net", help="KiCad net name to route")
    preview_selector.add_argument(
        "--net-ref-id", help="Opaque net reference copied from Circuit Scene"
    )
    preview_parser.add_argument(
        "--expect-board-revision",
        help="Scene board revision; required with --net-ref-id",
    )
    preview_parser.add_argument(
        "--expect-snapshot-digest",
        help="Scene snapshot digest; required with --net-ref-id",
    )
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

    placement_parser = subparsers.add_parser(
        "preview-placement",
        help="Validate a proposed footprint placement without modifying the board",
    )
    placement_parser.add_argument("path", help="Board path relative to the workspace")
    for option in _CONSTRAINT_OPTIONS:
        placement_parser.add_argument(f"--{option.replace('_', '-')}", type=int, required=True)
    placement_parser.add_argument(
        "--subject",
        action="append",
        required=True,
        dest="placement_subjects",
        help="Footprint reference the proposal may move; repeatable",
    )
    placement_parser.add_argument(
        "--intent",
        default=None,
        help=(
            "Optional JSON document inside the workspace supplying rules and proposals; "
            "its board, constraints and subjects come from the flags above"
        ),
    )
    placement_parser.add_argument(
        "--placement-grid-nm",
        type=int,
        default=None,
        help="Snap proposed origins to this grid",
    )

    apply_parser = subparsers.add_parser(
        "apply-candidate",
        help="Apply a previewed route candidate to a board (requires COPPER_MCP_ALLOW_APPLY=1)",
    )
    apply_parser.add_argument("path", help="Board path relative to the workspace")
    apply_parser.add_argument(
        "--candidate",
        required=True,
        help="Candidate manifest JSON inside the workspace, as returned by preview-route",
    )
    apply_parser.add_argument(
        "--expect-board-revision",
        required=True,
        help="The sha256 board revision the candidate was previewed against",
    )
    for option in _CONSTRAINT_OPTIONS:
        apply_parser.add_argument(f"--{option.replace('_', '-')}", type=int, required=True)

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

    erc_parser = subparsers.add_parser(
        "schematic-erc",
        help="Run authoritative KiCad ERC and round-trip verification on a Circuit Intent",
    )
    erc_parser.add_argument("path", help="Circuit Intent snapshot JSON inside the workspace")

    parity_parser = subparsers.add_parser(
        "source-to-board-parity",
        help="Check whether a workspace board implements a Circuit Intent's connectivity",
    )
    parity_parser.add_argument("path", help="Circuit Intent snapshot JSON inside the workspace")
    parity_parser.add_argument(
        "--board",
        required=True,
        help="Existing .kicad_pcb path inside the workspace; read but never written",
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
        if args.command == "preview-placement":
            _json_dump(preview_placement(_placement_request(args, settings), settings))
            return 0
        if args.command == "apply-candidate":
            document = _load_candidate(args.candidate, settings)
            # The manifest may be a whole preview response; take just the candidate so a
            # caller can pass either shape without editing the file.
            manifest = document.get("candidate", document)
            if not isinstance(manifest, dict):
                raise ValueError("candidate document must contain a candidate object")
            authority, token = _cli_apply_authorization(
                args.path, manifest, args.expect_board_revision
            )
            _json_dump(
                apply_candidate(
                    {
                        "board": args.path,
                        "candidate": manifest,
                        "apply_token": token,
                        "expect_board_revision": args.expect_board_revision,
                        "constraints": _constraints(args),
                    },
                    settings,
                    authority,
                )
            )
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
        if args.command == "schematic-erc":
            intent_snapshot = read_workspace_file(
                settings.workspace,
                args.path,
                allowed_suffixes={".json"},
                max_bytes=MAX_CIRCUIT_INPUT_BYTES,
            )
            _json_dump(
                verify_schematic_erc_from_snapshot_json(intent_snapshot.content, settings).to_dict()
            )
            return 0
        if args.command == "source-to-board-parity":
            intent_snapshot = read_workspace_file(
                settings.workspace,
                args.path,
                allowed_suffixes={".json"},
                max_bytes=MAX_CIRCUIT_INPUT_BYTES,
            )
            _json_dump(
                verify_source_to_board_parity_from_snapshot_json(
                    intent_snapshot.content,
                    args.board,
                    settings,
                ).to_dict()
            )
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
