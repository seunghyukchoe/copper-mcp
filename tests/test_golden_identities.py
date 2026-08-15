"""Committed golden identities for every content-addressed surface in the repository.

Every value pinned in this module is a *published content address*: it appears in a durable
routing job ledger (ADR-0043), a redacted candidate manifest store (ADR-0047), a durable
request/result export (ADR-0048), an unsigned in-toto DRC statement (ADR-0052), or an MCP
response a caller is invited to store and later re-bind against.  A caller that persisted one
of these values and finds it no longer reproducible has a corrupted artifact, not a cosmetic
diff.

**Changing any value in this file is a breaking change.**  It requires, in the same change:

1. a deliberate version bump of the surface's own version constant (``ROUTER_VERSION``,
   ``LAYERED_ROUTER_VERSION``, ``PLACEMENT_VERSION``, the schema string embedded in the
   canonical payload, or the Board IR / Circuit IR schema version), and
2. a migration note for persisted artifacts in ``CHANGELOG.md`` and the relevant ADR,
   stating what previously stored artifacts stop verifying and what a caller must do.

Updating a constant here to make a red test green is exactly the failure this module exists to
prevent.  The gap it closes is real: a change to ``layered_board_adapter.py`` that normalized
via layer direction altered two-layer candidate IDs while 1,506 tests passed, because no
committed candidate-ID pin existed anywhere in the layered suite (issue #80).

Why digests *and* byte counts.  Two canonical payloads that differ only in the value of one
fixed-width field have identical length, so a length assertion alone cannot detect a changed
value - but a *reordered* or *renamed* field, or a field that gained or lost a member, moves
the length.  Pinning both makes the failure message say which kind of drift happened instead of
only that a hex string moved.

Surfaces already pinned elsewhere, deliberately not duplicated here:

- layered route candidate IDs (two-, three- and four-layer) -
  ``tests/test_layered_board_adapter.py``
- the negotiated-congestion policy binding digest -
  ``tests/test_routing_congestion.py``
- the eight NE5532 benchmark route candidate IDs - ``tests/test_ne5532_audio_routing.py``
- the Circuit Intent snapshot digest as it is stored inside its own fixture -
  ``tests/test_circuit_ir.py`` (re-pinned here against the schematic build path, which is a
  different consumer of the same address)
- the exact bytes of the two **frozen published JSON Schemas**, ``board-ir/0.1.0`` and
  ``board-ir/0.2.0`` - ``tests/test_board_ir_schema.py``.  Deliberately not here: a schema file
  is a published artifact but not a content address, so nothing binds to its digest and no
  caller re-derives it.  What it needs is byte permanence (ADR-0105), which is a different
  promise checked in the module that owns the freeze.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from copper_mcp.adapters import KiCadConstraintProfile, net_id_for_name, parse_kicad_bytes
from copper_mcp.attestation import canonical_statement_bytes
from copper_mcp.board_ir import NetClass, PointNM, encode_snapshot
from copper_mcp.board_ir.limits import ParseLimits
from copper_mcp.circuit_intent_service import build_schematic_from_snapshot_json
from copper_mcp.circuit_scene import observe_board_scene
from copper_mcp.config import Settings
from copper_mcp.kicad_cli import RouteCandidateDrcEvidence
from copper_mcp.kicad_ipc import LiveEditorContextSnapshot, LiveEditorSelection
from copper_mcp.live_editor_context import _context_digest
from copper_mcp.models import DrcSummary
from copper_mcp.placement import build_placement_view, evaluate_placement, parse_placement_intent
from copper_mcp.placement.contracts import canonical_candidate_bytes as canonical_placement_bytes
from copper_mcp.placement.contracts import verify_placement_id
from copper_mcp.route_bundle import preview_route_bundle
from copper_mcp.route_preview import preview_route
from copper_mcp.routing import (
    LayeredAStarSettings,
    LayeredBoardRouter,
    LayeredRouteRequest,
    RoutingJobRepository,
    canonical_candidate_bytes,
    verify_candidate_id,
)
from copper_mcp.routing.candidate_store import CandidateManifest
from copper_mcp.routing.job_repository import _candidate_document, _candidate_document_digest
from copper_mcp.routing.jobs import RoutingJobKind, RoutingJobSpec
from copper_mcp.routing.policy import (
    DeterministicReferencePolicy,
    PolicyBounds,
    PolicyNet,
    RepairWindowCandidate,
    RoutingPolicyInput,
    canonical_policy_decision_bytes,
    canonical_policy_input_bytes,
    decode_policy_input_json,
    evaluate_policy,
    policy_decision_digest,
    policy_input_digest,
)
from copper_mcp.routing.policy_worker_protocol import (
    PolicyWorkerRequest,
    canonical_policy_worker_request_bytes,
    policy_worker_request_digest,
)
from copper_mcp.routing.repair import LocalRepairRequest, LocalRepairStatus, exact_local_repair
from copper_mcp.routing_job_service import start_routing_job
from copper_mcp.scene_render import SVG_CANONICALIZATION, canonicalize_svg, render_digest
from copper_mcp.zone_fill import FillIsland, fill_digest

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
ROUTE_FIXTURES = FIXTURES / "route-candidate"
SCENE_FIXTURES = FIXTURES / "circuit-scene-v0.1"
BOARD_IR_V02 = FIXTURES / "board-ir-v0.2" / "schema-valid.json"
BOARD_IR_V01 = FIXTURES / "board-ir-v0.1" / "schema-valid.json"
SUBSET_BOARD = FIXTURES / "board-ir-v0.1" / "subset.kicad_pcb"
POLICY_INPUT_FIXTURE = FIXTURES / "routing-policy" / "reference-input.json"
INTENT_FIXTURE = ROOT / "benchmarks" / "audio" / "fixtures" / "rc-low-pass-intent-v1.json"
BUNDLE_FIXTURE = ROOT / "benchmarks" / "audio" / "fixtures" / "negotiated-crossing-v1.kicad_pcb"
PLACEMENT_FIXTURE = FIXTURES / "placement-v0.1" / "placement-legal.kicad_pcb"


def _fill(character: str) -> str:
    return f"sha256:{character * 64}"


# ---------------------------------------------------------------------------
# Board IR snapshot digests
# ---------------------------------------------------------------------------

# The Board IR snapshot digest is the base revision every route, layered route, placement and
# bundle candidate binds to, so it is the root of the whole identity graph.  Changing it
# requires a Board IR schema version bump and a migration note: every persisted candidate
# carrying the old base revision stops binding.
BOARD_IR_V02_SNAPSHOT_DIGEST = (
    "sha256:157661bfa1c007a8d54ee2cc591eddabb2dbcda2925a62348a283f467ea27878"
)
BOARD_IR_V02_CONSTRAINT_DIGEST = (
    "sha256:5b673432b26401b123bc4044d0d3c87796ec8165ec16d444e186a3c33229acd9"
)
BOARD_IR_V02_SOURCE_REVISION = (
    "sha256:58f35401a1bb75bbf33a47009dda92720284d8c67561c87fe9e2698a9f84fd06"
)
BOARD_IR_V02_ENCODED_BYTES = 4_280

# **Did not move for ADR-0105** (issue #172), which bumped `BOARD_IR_SCHEMA_VERSION` from `0.2.0`
# to `0.3.0`, and remains unchanged for the active 0.4.0 envelope. Not one of
# the four pins above moved, and that is the finding the decision rests
# on rather than a coincidence: the digest is taken over `_content_payload`, which carries no
# schema version, and the version appears only in the envelope.  The byte count is unchanged too,
# because all three version strings are the same width. The `V02` in these names records the
# release the pins were taken in; it is not a claim about the envelope's declared version.
#
# What did move is the committed envelope's *bytes*, at exactly one version index for each
# publication (`2` -> `3`, then `3` -> `4`). `tests/test_board_ir_schema.py` proves each move by
# construction, while this test keeps the active fixture's encoded bytes bound to its digest.

# The 0.1 snapshot digest can no longer be recomputed: the active codec refuses a 0.1 envelope
# by design (see tests/test_board_ir_schema.py).  The pin therefore guards the committed legacy
# fixture itself, which is what a 0.1-era persisted artifact would have to be replayed against.
BOARD_IR_V01_SNAPSHOT_DIGEST = (
    "sha256:56ad8d48ab604362e823e5c21ce601f805590f04c96a19adf112054820f1b699"
)
BOARD_IR_V01_CONSTRAINT_DIGEST = (
    "sha256:5b673432b26401b123bc4044d0d3c87796ec8165ec16d444e186a3c33229acd9"
)
BOARD_IR_V01_FIXTURE_BYTES = 3_979

# Net reference IDs are content-addressed over the net name, and they are the public handles a
# caller passes back in every routing and bundle request.
NET_REF_ID_GND = "net:name:ccdd8d41edbac58e6d53310d1fccffd0"
NET_REF_ID_SIG = "net:name:08d61b0ef81fc97b359093a799d2cf7c"


def _board_ir_profile() -> KiCadConstraintProfile:
    default = NetClass(
        id="class:default",
        name="Default",
        clearance_nm=250_000,
        track_width_nm=250_000,
        via_diameter_nm=800_000,
        via_drill_nm=400_000,
    )
    audio = NetClass(
        id="class:audio",
        name="Audio",
        clearance_nm=300_000,
        track_width_nm=300_000,
        via_diameter_nm=900_000,
        via_drill_nm=450_000,
    )
    return KiCadConstraintProfile(
        net_classes=(default, audio),
        default_net_class_id=default.id,
        net_class_by_name=(("SIG_µ", audio.id),),
    )


def test_board_ir_v0_2_snapshot_digest_matches_its_committed_golden_value() -> None:
    result = parse_kicad_bytes(SUBSET_BOARD.read_bytes(), _board_ir_profile())

    assert result.diagnostics == ()
    assert result.snapshot is not None
    assert result.snapshot.snapshot_digest == BOARD_IR_V02_SNAPSHOT_DIGEST
    assert result.snapshot.content.constraint_digest == BOARD_IR_V02_CONSTRAINT_DIGEST
    assert result.snapshot.content.source.revision == BOARD_IR_V02_SOURCE_REVISION
    assert len(encode_snapshot(result.snapshot)) == BOARD_IR_V02_ENCODED_BYTES
    assert encode_snapshot(result.snapshot) == BOARD_IR_V02.read_bytes()


def test_legacy_board_ir_v0_1_fixture_still_carries_its_committed_golden_digest() -> None:
    payload = json.loads(BOARD_IR_V01.read_text(encoding="utf-8"))

    assert payload["schema_version"] == "0.1.0"
    assert payload["snapshot_digest"] == BOARD_IR_V01_SNAPSHOT_DIGEST
    assert payload["content"]["constraint_digest"] == BOARD_IR_V01_CONSTRAINT_DIGEST
    assert len(BOARD_IR_V01.read_bytes()) == BOARD_IR_V01_FIXTURE_BYTES


def test_net_reference_ids_are_content_addressed_over_the_net_name() -> None:
    assert net_id_for_name("GND") == NET_REF_ID_GND
    assert net_id_for_name("SIG_µ") == NET_REF_ID_SIG


# ---------------------------------------------------------------------------
# Route candidate identity (single-layer A*)
# ---------------------------------------------------------------------------

# A RouteCandidate ID is the address a caller stores, hands to `apply_candidate`, and exports
# under ADR-0048.  Changing it requires bumping ROUTER_VERSION and a migration note.
#
# Moved once, deliberately, by ADR-0087 (issue #128): `max_obstacles` was re-derived from
# 256 to 4,096 and `ROUTER_VERSION` advanced to `astar-grid/0.7.0`.  The *geometry* did not
# move — this fixture's path is the same two vertices, the same length, and the same bend
# count as before — so the payload grew by exactly the one character that separates "256"
# from "4096".  The migration note is in CHANGELOG.md under 0.6.1.
#
# **Did not move for ADR-0103** (issue #163), which added `fill_binding` to `RouteCandidate`.
# The field is emitted into the canonical payload only when it is not `None`, so a candidate
# routed against the conservative zone envelope — which is every candidate this fixture, the
# corpus, and every caller has ever produced — is addressed by byte-identical content.  The
# test below asserts `fill_binding is None` here, so this pin's stability is a stated
# consequence rather than a coincidence, and `tests/test_routing_astar.py`'s
# `test_an_envelope_candidate_carries_no_fill_key_in_its_canonical_identity` proves the
# absence at the byte level.  A *fill*-routed candidate's address does move; it is
# pinned in `tests/test_routing_astar.py` and had no pin anywhere before.
ROUTE_CANDIDATE_ID = "sha256:7dbbc4b034238d61c9f002163a3af9a91022942785bf97eee8db37c6bf564784"
ROUTE_CANDIDATE_PAYLOAD_BYTES = 1_043
ROUTE_CANDIDATE_ROUTER_VERSION = "astar-grid/0.7.0"
ROUTE_CANDIDATE_BOARD_REVISION = (
    "sha256:5f88ebcf52cf8f1548990bdbdc1c52ac7a30f39c013366f79b161ec15e1caae2"
)
ROUTE_CANDIDATE_SNAPSHOT_DIGEST = (
    "sha256:e57e679dc80e2d413c59c186db4ff520a5dc526bb025fde32f3b9eaa8d1e469f"
)


def _route_request() -> dict[str, Any]:
    return {
        "board": ROUTE_FIXTURES.joinpath("two-pad.kicad_pcb").name,
        "net": "AUDIO",
        "layer": "F.Cu",
        "seed": 23,
        "constraints": {
            "clearance_nm": 250_000,
            "track_width_nm": 250_000,
            "via_diameter_nm": 800_000,
            "via_drill_nm": 400_000,
        },
    }


def test_route_candidate_identity_matches_its_committed_golden_value(tmp_path: Path) -> None:
    fixture = ROUTE_FIXTURES / "two-pad.kicad_pcb"
    shutil.copy2(fixture, tmp_path / fixture.name)

    preview = preview_route(_route_request(), Settings(workspace=tmp_path))

    assert preview.candidate is not None
    assert verify_candidate_id(preview.candidate)
    assert preview.candidate.router_version == ROUTE_CANDIDATE_ROUTER_VERSION
    # The reason this pin did not move for ADR-0103; see the note above.
    assert preview.candidate.fill_binding is None
    assert len(canonical_candidate_bytes(preview.candidate)) == ROUTE_CANDIDATE_PAYLOAD_BYTES
    assert preview.candidate.candidate_id == ROUTE_CANDIDATE_ID
    assert preview.board_revision == ROUTE_CANDIDATE_BOARD_REVISION
    assert preview.snapshot_digest == ROUTE_CANDIDATE_SNAPSHOT_DIGEST


# ---------------------------------------------------------------------------
# Placement candidate identity
# ---------------------------------------------------------------------------

# Placement candidates are separately authorized for apply (ADR-0059), so a stored placement
# candidate that no longer verifies is an apply that silently cannot be replayed.  Changing this
# value requires bumping PLACEMENT_VERSION and a migration note.
PLACEMENT_CANDIDATE_ID = "sha256:adaf67a37f12f4a51850f92f658ad49dc3c811fee480813d19757b2d3a56d1e6"
PLACEMENT_CANDIDATE_PAYLOAD_BYTES = 801
PLACEMENT_BASE_REVISION = "sha256:ae40b5e32f403a6df137fa7b1128ddc303c0bbf323091990329e43dfe243395e"
PLACEMENT_VIEW_REVISION = "sha256:4396686c92d63969b8c9282530d85b3a220ee38cfc23f16966eacb689e80add3"

_PLACEMENT_CONSTRAINTS = {
    "clearance_nm": 200_000,
    "track_width_nm": 250_000,
    "via_diameter_nm": 600_000,
    "via_drill_nm": 300_000,
}


def test_placement_candidate_identity_matches_its_committed_golden_value() -> None:
    net_class = NetClass(id="class:request", name="Request", **_PLACEMENT_CONSTRAINTS)
    profile = KiCadConstraintProfile(net_classes=(net_class,), default_net_class_id=net_class.id)
    source = PLACEMENT_FIXTURE.read_bytes()
    parsed = parse_kicad_bytes(source, profile, ParseLimits())
    assert parsed.snapshot is not None
    view = build_placement_view(source, parsed.snapshot)
    intent = parse_placement_intent(
        {
            "board": PLACEMENT_FIXTURE.name,
            "constraints": dict(_PLACEMENT_CONSTRAINTS),
            "subjects": sorted(view.footprints),
        }
    )

    result = evaluate_placement(intent, parsed.snapshot, view)

    assert result.candidate is not None
    assert verify_placement_id(result.candidate)
    assert len(canonical_placement_bytes(result.candidate)) == PLACEMENT_CANDIDATE_PAYLOAD_BYTES
    assert result.candidate.candidate_id == PLACEMENT_CANDIDATE_ID
    assert result.candidate.base_revision == PLACEMENT_BASE_REVISION
    assert result.candidate.view_revision == PLACEMENT_VIEW_REVISION


# ---------------------------------------------------------------------------
# Route bundle identity (ADR-0066)
# ---------------------------------------------------------------------------

# A bundle is all-or-nothing: its ID binds the member candidate IDs *and* the coordinator policy
# envelope digest, so a caller replaying a stored bundle_id is asserting both.  Changing it is a
# route-bundle schema bump plus a migration note.
# Moved by ADR-0087 with every member candidate ID below; see the note on ROUTE_CANDIDATE_ID.
ROUTE_BUNDLE_ID = "sha256:38941a989de4725fa4e5be81e163caffa80ad40401e46f06973befce263a9095"
ROUTE_BUNDLE_BASE_REVISION = (
    "sha256:2f82b6a51792ef5e93b9834419c18639d6c13ebe974176447be037644e531c70"
)
ROUTE_BUNDLE_POLICY_DIGEST = (
    "sha256:78554a759c21b55d4b8e0dc7bcc809cacc7200086684bd27d1516bbc73387e9d"
)
ROUTE_BUNDLE_CANDIDATE_IDS = (
    "sha256:2d0c36064fe87b6129780acc682767e409683ab188a126c604f48f7d2d326449",
    "sha256:e93295520e0ded40fcec9a3be68c3bd820e0b18afc7b7b2104cf659639522948",
)

_BUNDLE_CONSTRAINTS = {
    "clearance_nm": 100_000,
    "track_width_nm": 200_000,
    "via_diameter_nm": 600_000,
    "via_drill_nm": 300_000,
}


def test_route_bundle_identity_matches_its_committed_golden_value(tmp_path: Path) -> None:
    source = BUNDLE_FIXTURE.read_bytes()
    (tmp_path / BUNDLE_FIXTURE.name).write_bytes(source)
    net_class = NetClass(id="class:request", name="Request", **_BUNDLE_CONSTRAINTS)
    converted = parse_kicad_bytes(
        source,
        KiCadConstraintProfile(net_classes=(net_class,), default_net_class_id=net_class.id),
    )
    assert converted.snapshot is not None

    preview = preview_route_bundle(
        {
            "board": BUNDLE_FIXTURE.name,
            "layer": "F.Cu",
            "constraints": dict(_BUNDLE_CONSTRAINTS),
            "net_ref_ids": [net_id_for_name("HORIZONTAL"), net_id_for_name("VERTICAL")],
            "expect_board_revision": f"sha256:{hashlib.sha256(source).hexdigest()}",
            "expect_snapshot_digest": converted.snapshot.snapshot_digest,
            "seed": 7,
            "settings": {
                "grid_step_nm": 1_000_000,
                "bend_penalty_nm": 500_000,
                "proximity_penalty_nm": 0,
                "max_grid_nodes": 512,
                "max_expansions": 20_000,
                "max_obstacles": 128,
                "max_obstacle_checks": 200_000,
            },
        },
        Settings(workspace=tmp_path),
    )

    assert preview.plan is not None
    assert preview.plan.bundle_id == ROUTE_BUNDLE_ID
    assert preview.plan.base_revision == ROUTE_BUNDLE_BASE_REVISION
    assert preview.plan.policy_digest == ROUTE_BUNDLE_POLICY_DIGEST
    assert (
        tuple(candidate.candidate_id for candidate in preview.plan.candidates)
        == ROUTE_BUNDLE_CANDIDATE_IDS
    )


# ---------------------------------------------------------------------------
# Circuit Intent snapshot and schematic artifact digests
# ---------------------------------------------------------------------------

# The intent digest is embedded in the rendered schematic itself, so the two move together.  A
# change to either requires a Circuit IR schema version bump and a migration note: a caller
# holding a schematic artifact can no longer prove which intent produced it.
#
# ``SCHEMATIC_ARTIFACT_DIGEST`` is the one pin in this module that is **version-coupled**: the
# renderer writes ``(generator_version "<package version>")`` into the schematic as provenance, so
# the artifact bytes — and therefore this digest — change on every release.  Re-pin it in the
# release commit that bumps the version; that is not a contract change, and it is why the byte
# length is pinned separately (it stays 7,715 only while the version string keeps its length).
# Pinned at CopperMCP 0.9.0.
CIRCUIT_INTENT_SNAPSHOT_DIGEST = (
    "sha256:06383cabd428aa52585b1e0f0c82dea6e6f434e55d154f6d2ee87e3879f49795"
)
SCHEMATIC_ARTIFACT_DIGEST = (
    "sha256:1a5232edc34359f3274b67a89d94834fadeaf404bbd5752a53c12e9f25c35dce"
)
SCHEMATIC_ARTIFACT_BYTES = 7_715


def test_circuit_intent_and_schematic_artifact_digests_match_their_golden_values() -> None:
    build = build_schematic_from_snapshot_json(INTENT_FIXTURE.read_bytes())

    assert build.artifact.intent_digest == CIRCUIT_INTENT_SNAPSHOT_DIGEST
    assert build.artifact.artifact_digest == SCHEMATIC_ARTIFACT_DIGEST
    assert len(build.artifact.content) == SCHEMATIC_ARTIFACT_BYTES
    assert (
        f"sha256:{hashlib.sha256(build.artifact.content).hexdigest()}" == SCHEMATIC_ARTIFACT_DIGEST
    )


# ---------------------------------------------------------------------------
# Circuit Scene revisions and annotation reference IDs
# ---------------------------------------------------------------------------

# A scene's snapshot_digest is what a caller re-binds a follow-up request against, and an
# annotation ref_id embeds a truncated digest of the author-controlled text so a caller can
# refer to a quoted string without echoing it.  Changing either is a SCENE_VERSION bump plus a
# migration note.
SCENE_BOARD_REVISION = "sha256:c69298f27512becfe4b765b99e75628426711103837ab04f4ea424cc48580a1c"
SCENE_SNAPSHOT_DIGEST = "sha256:e21e0eb1211cda221a94359805955b6aa5c889173e7085f8728070d4e51e7e4a"
SCENE_HOSTILE_SNAPSHOT_DIGEST = (
    "sha256:3b54e6ea601f7a99fe6c6c93e7fddd073f1c95db39c03418a8f0483683759274"
)
SCENE_ANNOTATION_COUNT = 10
SCENE_ANNOTATION_REF_IDS = (
    "annotation:gr_text:0000:0:f330f9581d46e5ac",
    "annotation:gr_text:0001:0:c399f17c76554934",
    "annotation:fp0:0002:0:04f8996da763b7a9",
    "annotation:fp0:0003:1:76bb562895fd6d86",
)

_SCENE_CONSTRAINTS = {
    "clearance_nm": 200_000,
    "track_width_nm": 250_000,
    "via_diameter_nm": 600_000,
    "via_drill_nm": 300_000,
}
_WHOLE_BOARD = {
    "min_x_nm": -1_000_000_000,
    "min_y_nm": -1_000_000_000,
    "max_x_nm": 1_000_000_000,
    "max_y_nm": 1_000_000_000,
}


def test_circuit_scene_revisions_match_their_committed_golden_values(tmp_path: Path) -> None:
    fixture = SCENE_FIXTURES / "scene-region.kicad_pcb"
    shutil.copy2(fixture, tmp_path / fixture.name)

    scene = observe_board_scene(
        {
            "board": fixture.name,
            "constraints": dict(_SCENE_CONSTRAINTS),
            "region": dict(_WHOLE_BOARD),
        },
        Settings(workspace=tmp_path.resolve()),
    )

    assert scene.board_revision == SCENE_BOARD_REVISION
    assert scene.snapshot_digest == SCENE_SNAPSHOT_DIGEST


def test_scene_annotation_reference_ids_match_their_committed_golden_values(
    tmp_path: Path,
) -> None:
    fixture = SCENE_FIXTURES / "scene-hostile-text.kicad_pcb"
    shutil.copy2(fixture, tmp_path / fixture.name)

    scene = observe_board_scene(
        {
            "board": fixture.name,
            "constraints": dict(_SCENE_CONSTRAINTS),
            "region": dict(_WHOLE_BOARD),
            "include_annotations": True,
        },
        Settings(workspace=tmp_path.resolve()),
    )

    assert scene.snapshot_digest == SCENE_HOSTILE_SNAPSHOT_DIGEST
    assert len(scene.annotations) == SCENE_ANNOTATION_COUNT
    assert (
        tuple(item.ref_id for item in scene.annotations[: len(SCENE_ANNOTATION_REF_IDS)])
        == SCENE_ANNOTATION_REF_IDS
    )


# ---------------------------------------------------------------------------
# Deterministic render digest (title-line-v1 canonicalization)
# ---------------------------------------------------------------------------

# The whole point of title-line-v1 is that two exports of an unchanged board digest equally.
# The pin is taken over synthetic bytes in KiCad 10.0.5's exact shape so it stays a pure,
# reproducible statement about the canonicalization rule rather than about a local KiCad.
# Changing it means changing SVG_CANONICALIZATION and writing a migration note: stored render
# evidence recorded under the old rule is no longer comparable.
RENDER_CANONICALIZATION = "title-line-v1"
RENDER_CANONICAL_BYTES = 122
RENDER_DIGEST = "sha256:0b6187c9c76ff75b54fd3241669a61d52c16207f5dd77f465ef5cf5942607500"

_EXPORT_HEAD = b'<?xml version="1.0" standalone="no"?>\n<svg>\n'
_EXPORT_TAIL = b'\n<path d="M 0 0"/>\n</svg>\n'


def _synthetic_export(name: str, when: str) -> bytes:
    return (
        _EXPORT_HEAD
        + f"<title>SVG Image created as {name} date {when} </title>".encode()
        + _EXPORT_TAIL
    )


def test_deterministic_render_digest_matches_its_committed_golden_value() -> None:
    first = canonicalize_svg(_synthetic_export("scene.svg", "2026-08-04T01:02:03"))
    second = canonicalize_svg(_synthetic_export("other.svg", "2027-01-31T23:59:59"))

    assert SVG_CANONICALIZATION == RENDER_CANONICALIZATION
    assert first == second
    assert len(first) == RENDER_CANONICAL_BYTES
    assert render_digest(first) == RENDER_DIGEST


# ---------------------------------------------------------------------------
# Durable routing job identity (ADR-0043)
# ---------------------------------------------------------------------------

# A job ID is the handle a caller polls, cancels and exports against after a process restart.
# Changing it orphans every queued and running job in a persisted ledger, so it requires a job
# schema version bump and a migration note.
ROUTING_JOB_SPEC_ID = "sha256:3a95c9906277136c9c3b2242ed8f6bba48f356f13a0cd38c9d660e1406703d63"
ROUTING_JOB_SERVICE_ID = "sha256:9099378db53a0f6c4120861f7f9580cb7aaed8b47d755e53f8c455c3bc1fcbd1"
ROUTING_JOB_SERVICE_REQUEST_DIGEST = (
    "sha256:cdf7ad90e118f001364a6f78d40ca4a3ba7f6d89162c84f1b91e743ce2f90334"
)


def test_routing_job_specification_identity_matches_its_committed_golden_value() -> None:
    spec = RoutingJobSpec.create(
        board_revision=_fill("b"),
        snapshot_digest=None,
        start_pad_id="pad:start",
        end_pad_id="pad:end",
        request_digest=_fill("c"),
        request_kind=RoutingJobKind.SINGLE_LAYER,
        backend="astar-v1",
        router_version="astar-v1",
        policy="deterministic",
        seed=0,
    )

    assert spec.job_id == ROUTING_JOB_SPEC_ID


def test_started_routing_job_identity_and_request_digest_match_their_golden_values(
    tmp_path: Path,
) -> None:
    fixture = ROUTE_FIXTURES / "two-pad.kicad_pcb"
    source = fixture.read_bytes()
    (tmp_path / fixture.name).write_bytes(source)
    constraints = NetClass(
        id="class:request",
        name="Request",
        clearance_nm=250_000,
        track_width_nm=250_000,
        via_diameter_nm=800_000,
        via_drill_nm=400_000,
    )
    converted = parse_kicad_bytes(
        source,
        KiCadConstraintProfile(net_classes=(constraints,), default_net_class_id=constraints.id),
    )
    assert converted.snapshot is not None
    pads = converted.snapshot.content.pads
    request = {
        "board": fixture.name,
        "start_pad_id": pads[0].id,
        "end_pad_id": pads[1].id,
        "expect_board_revision": f"sha256:{hashlib.sha256(source).hexdigest()}",
        "expect_snapshot_digest": converted.snapshot.snapshot_digest,
        "constraints": {
            "clearance_nm": constraints.clearance_nm,
            "track_width_nm": constraints.track_width_nm,
            "via_diameter_nm": constraints.via_diameter_nm,
            "via_drill_nm": constraints.via_drill_nm,
        },
        "grid_step_nm": 250_000,
        "seed": 0,
        "settings": {
            "move_cost": 1,
            "via_cost": 10,
            "max_expansions": 100_000,
            "max_nodes": 250_000,
            "max_obstacles": 256,
            "max_obstacle_checks": 2_000_000,
        },
    }
    authorization = f"sha256:{hashlib.sha256(b'caller-context').hexdigest()}"

    repository = RoutingJobRepository(tmp_path / "jobs.sqlite3")
    try:
        document = start_routing_job(
            {"request": request, "authorization_digest": authorization},
            Settings(workspace=tmp_path),
            repository,
        )
    finally:
        repository.close()

    assert document["job_id"] == ROUTING_JOB_SERVICE_ID
    assert document["request_digest"] == ROUTING_JOB_SERVICE_REQUEST_DIGEST


# ---------------------------------------------------------------------------
# Redacted candidate manifest digest (ADR-0047)
# ---------------------------------------------------------------------------

# The manifest digest is re-derived on every read and a mismatch discards the row, so a change
# here silently empties a persisted manifest store.  Version bump plus migration note required.
CANDIDATE_MANIFEST_DIGEST = (
    "sha256:05fcd42562efbba25efd8364d44011c6450bb37c4dd00719c2a76eb3633f019c"
)


def test_candidate_manifest_digest_matches_its_committed_golden_value() -> None:
    manifest = CandidateManifest.create(
        candidate_id=_fill("a"),
        base_revision=_fill("b"),
        start_pad_id="pad:start",
        end_pad_id="pad:end",
        kind="single-layer",
        router="astar-v1",
        policy="deterministic",
        path_count=1,
        via_count=0,
        cost=123,
        metrics={"wire_length_nm": 1000, "bend_count": 2},
        job_id=None,
    )

    assert manifest.manifest_digest == CANDIDATE_MANIFEST_DIGEST
    assert manifest.expected_digest == CANDIDATE_MANIFEST_DIGEST


# ---------------------------------------------------------------------------
# Durable candidate export digest (ADR-0048)
# ---------------------------------------------------------------------------

# A stored export is re-digested on read without trusting its own ID field, and the recomputed
# value must equal the candidate ID itself - that equality is the export contract.  The pinned
# candidate ID is the same two-layer identity pinned in tests/test_layered_board_adapter.py;
# pinning it again here states that the *export encoding* has not drifted away from it.
EXPORT_CANDIDATE_ID = "sha256:5ea134fc319c5a7fa4b7d64b9e6cc47b8439f60c821391c3c3e4c46678f82818"
EXPORT_RENDERED_BYTES = 1_616
EXPORT_KIND = "layered"


def test_persisted_candidate_export_digest_matches_its_committed_golden_value() -> None:
    fixture = ROUTE_FIXTURES / "blocked-pad.kicad_pcb"
    net_class = NetClass(
        id="class:default",
        name="Default",
        clearance_nm=250_000,
        track_width_nm=250_000,
        via_diameter_nm=800_000,
        via_drill_nm=400_000,
    )
    converted = parse_kicad_bytes(
        fixture.read_bytes(),
        KiCadConstraintProfile(net_classes=(net_class,), default_net_class_id=net_class.id),
    )
    assert converted.diagnostics == ()
    assert converted.snapshot is not None
    snapshot = converted.snapshot
    pads = tuple(
        pad for pad in snapshot.content.pads if pad.net_id == snapshot.content.pads[0].net_id
    )
    assert pads[0].net_id is not None
    result = LayeredBoardRouter().propose(
        snapshot,
        LayeredRouteRequest(
            board_revision=snapshot.snapshot_digest,
            net_id=pads[0].net_id,
            start_pad_id=pads[0].id,
            end_pad_id=pads[1].id,
            start_layer_id="layer:F.Cu",
            end_layer_id="layer:F.Cu",
            grid_step_nm=1_000,
            settings=LayeredAStarSettings(via_cost=2),
        ),
    )
    assert result.candidate is not None

    kind, rendered, document = _candidate_document(result.candidate)

    assert kind == EXPORT_KIND
    assert len(rendered) == EXPORT_RENDERED_BYTES
    assert result.candidate.candidate_id == EXPORT_CANDIDATE_ID
    assert _candidate_document_digest(document) == EXPORT_CANDIDATE_ID
    # **Did not move for ADR-0106** (issue #164), which added `fill_binding` to
    # `LayeredRouteCandidate`. This fixture routes under the conservative zone envelopes, so its
    # binding is `None` and the canonical identity payload omits the key entirely -- exactly as
    # ADR-0103 arranged for the single-layer candidate. The stability of this pin and its byte
    # count is therefore a stated consequence rather than a coincidence, and the two assertions
    # below are what make it one.
    assert result.candidate.fill_binding is None
    assert "fill_binding" not in document


# ---------------------------------------------------------------------------
# Advisory routing policy digests (ADR-0064)
# ---------------------------------------------------------------------------

# Policy input and decision digests are recorded in benchmark artifacts and redacted traces that
# are compared across runs and releases, so a silent change invalidates published evidence.
POLICY_INPUT_DIGEST = "sha256:4dbe989cdffab06752bca92eb8cb2e87bbb4d84977a82b2c44367ccdea97c3fa"
POLICY_INPUT_PAYLOAD_BYTES = 947
POLICY_DECISION_DIGEST = "sha256:f32c14376c8ced7c110199ba139dd94d3ca7f6a386213d56b4d3f3dc5e07ece7"
POLICY_DECISION_PAYLOAD_BYTES = 801
POLICY_WORKER_REQUEST_DIGEST = (
    "sha256:28fb887106f475efe6cb34c5f5b0735369ddbf8833a825911bbd44abd71094b9"
)
POLICY_WORKER_FRAME_BYTES = 590


def test_policy_input_and_decision_digests_match_their_committed_golden_values() -> None:
    policy_input = decode_policy_input_json(POLICY_INPUT_FIXTURE.read_bytes())
    decision = evaluate_policy(DeterministicReferencePolicy(), policy_input)

    assert len(canonical_policy_input_bytes(policy_input)) == POLICY_INPUT_PAYLOAD_BYTES
    assert policy_input_digest(policy_input) == POLICY_INPUT_DIGEST
    assert len(canonical_policy_decision_bytes(decision)) == POLICY_DECISION_PAYLOAD_BYTES
    assert policy_decision_digest(decision) == POLICY_DECISION_DIGEST


def test_policy_worker_request_frame_digest_matches_its_committed_golden_value() -> None:
    request = PolicyWorkerRequest(
        nonce="b" * 64,
        policy_input=RoutingPolicyInput(
            board_revision=_fill("a"),
            bounds=PolicyBounds(0, 0, 0, 0),
            nets=(
                PolicyNet("net:audio-left", 3, 8, 1),
                PolicyNet("net:audio-right", 2, 9, 2),
            ),
        ),
    )

    assert len(canonical_policy_worker_request_bytes(request)) == POLICY_WORKER_FRAME_BYTES
    assert policy_worker_request_digest(request) == POLICY_WORKER_REQUEST_DIGEST


# ---------------------------------------------------------------------------
# Exact local repair digests
# ---------------------------------------------------------------------------

# The repair route digest binds a proposed route to the exact request that produced it, and
# verify_local_repair_result rejects anything else.  A change breaks stored repair proposals.
REPAIR_INPUT_DIGEST = "sha256:4181c8f988758884364eb7dad353eaa6995a91afb71aaf436e1c0c92bdf78d3b"
REPAIR_ROUTE_DIGEST = "sha256:cd44151584f02d2721b87f7f48419bbf132b762497c652955b074eeb31ce1c3e"


def test_local_repair_digests_match_their_committed_golden_values() -> None:
    request = LocalRepairRequest(
        repair_window=RepairWindowCandidate(
            net_id="net:audio",
            bounds=PolicyBounds(0, 0, 4, 4),
            conflict_score=7,
        ),
        start=(0, 0),
        end=(4, 4),
        blocked_cells=((2, 0), (2, 1), (2, 2)),
    )

    result = exact_local_repair(request)

    assert result.status is LocalRepairStatus.COMPLETED
    assert request.input_digest == REPAIR_INPUT_DIGEST
    assert result.route_digest == REPAIR_ROUTE_DIGEST


# ---------------------------------------------------------------------------
# Zone fill digest
# ---------------------------------------------------------------------------

# The fill digest is the authority a route preview binds to when it claims a zone fill was
# fresh.  Changing it invalidates every recorded fill provenance claim (ADR-0040).
ZONE_FILL_DIGEST = "sha256:33fd8f9ccf69e1c62620c632ddaaf3c6f5cff1b44616c65611af0747012eef27"
ZONE_FILL_EMPTY_DIGEST = "sha256:4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"


def test_zone_fill_digest_matches_its_committed_golden_value() -> None:
    first = FillIsland(
        net_id="net:name:abc",
        layer_id="layer:F.Cu",
        points=(PointNM(0, 0), PointNM(10, 0), PointNM(10, 10)),
    )
    second = FillIsland(
        net_id="net:name:abc",
        layer_id="layer:B.Cu",
        points=(PointNM(5, 5), PointNM(20, 5), PointNM(20, 20)),
    )

    assert fill_digest((first, second)) == ZONE_FILL_DIGEST
    assert fill_digest((second, first)) == ZONE_FILL_DIGEST
    assert fill_digest(()) == ZONE_FILL_EMPTY_DIGEST


# ---------------------------------------------------------------------------
# Unsigned in-toto DRC statement (ADR-0052)
# ---------------------------------------------------------------------------

# The statement is an attestation payload a caller may archive and later verify.  A change to
# its canonical bytes silently invalidates every archived statement.
ATTESTATION_STATEMENT_BYTES = 1_457
ATTESTATION_STATEMENT_DIGEST = (
    "sha256:3cc163adbfe255ed144c94c3558a41a7340f894bf263ffe57d3a2a9e9fba14b7"
)


def test_candidate_drc_statement_digest_matches_its_committed_golden_value() -> None:
    evidence = RouteCandidateDrcEvidence(
        candidate_id=_fill("e"),
        candidate_base_revision=_fill("a"),
        source_revision=_fill("b"),
        patched_board_revision=_fill("c"),
        patched_drc_context_revision=_fill("d"),
        summary=DrcSummary(
            base_revision=_fill("c"),
            drc_context_revision=_fill("d"),
            kicad_version="10.0.5",
            drc_schema="https://schemas.kicad.org/drc.v1.json",
            coordinate_units="mm",
            error_count=1,
            warning_count=1,
            exclusion_count=0,
            ignored_check_count=0,
            unconnected_count=0,
            violation_type_counts={"clearance": 1, "silk_overlap": 1},
            passed=False,
        ),
    )

    payload = canonical_statement_bytes(evidence.to_statement())

    assert len(payload) == ATTESTATION_STATEMENT_BYTES
    assert f"sha256:{hashlib.sha256(payload).hexdigest()}" == ATTESTATION_STATEMENT_DIGEST


# ---------------------------------------------------------------------------
# Live editor context digest (ADR-0044)
# ---------------------------------------------------------------------------

# A caller binds a follow-up live request to this digest, so a change turns every held context
# handle into a stale-context refusal.
LIVE_EDITOR_CONTEXT_DIGEST = (
    "sha256:872707153917ace1b174e5319eaafe99147316b8bf496c88f59dc4fdecbff3dd"
)


def test_live_editor_context_digest_matches_its_committed_golden_value() -> None:
    snapshot = LiveEditorContextSnapshot(
        board_digest=_fill("a"),
        board_bytes=2_048,
        active_layer_index=0,
        active_layer_name="F.Cu",
        selection=(
            LiveEditorSelection(
                kind="pad", ref_id="pad:kicad:00000000-0000-0000-0000-000000000001"
            ),
            LiveEditorSelection(
                kind="segment", ref_id="segment:kicad:00000000-0000-0000-0000-000000000002"
            ),
        ),
    )

    assert _context_digest(snapshot) == LIVE_EDITOR_CONTEXT_DIGEST


# ---------------------------------------------------------------------------
# Guard the guards
# ---------------------------------------------------------------------------


def test_every_pinned_identity_is_a_distinct_well_formed_content_address() -> None:
    """A pin that silently became a constant shared with another surface would prove nothing."""

    pinned = {
        "board_ir_v0_2_snapshot": BOARD_IR_V02_SNAPSHOT_DIGEST,
        "board_ir_v0_1_snapshot": BOARD_IR_V01_SNAPSHOT_DIGEST,
        "route_candidate": ROUTE_CANDIDATE_ID,
        "placement_candidate": PLACEMENT_CANDIDATE_ID,
        "route_bundle": ROUTE_BUNDLE_ID,
        "circuit_intent_snapshot": CIRCUIT_INTENT_SNAPSHOT_DIGEST,
        "schematic_artifact": SCHEMATIC_ARTIFACT_DIGEST,
        "scene_snapshot": SCENE_SNAPSHOT_DIGEST,
        "render": RENDER_DIGEST,
        "routing_job_spec": ROUTING_JOB_SPEC_ID,
        "routing_job_service": ROUTING_JOB_SERVICE_ID,
        "routing_job_request": ROUTING_JOB_SERVICE_REQUEST_DIGEST,
        "candidate_manifest": CANDIDATE_MANIFEST_DIGEST,
        "candidate_export": EXPORT_CANDIDATE_ID,
        "policy_input": POLICY_INPUT_DIGEST,
        "policy_decision": POLICY_DECISION_DIGEST,
        "policy_worker_request": POLICY_WORKER_REQUEST_DIGEST,
        "repair_input": REPAIR_INPUT_DIGEST,
        "repair_route": REPAIR_ROUTE_DIGEST,
        "zone_fill": ZONE_FILL_DIGEST,
        "attestation_statement": ATTESTATION_STATEMENT_DIGEST,
        "live_editor_context": LIVE_EDITOR_CONTEXT_DIGEST,
    }

    for name, value in pinned.items():
        assert value.startswith("sha256:"), name
        assert len(value) == 71, name
        assert set(value.removeprefix("sha256:")) <= set("0123456789abcdef"), name
    assert len(set(pinned.values())) == len(pinned)


@pytest.mark.parametrize(
    ("name", "digest"),
    [
        ("board_ir_constraints", BOARD_IR_V02_CONSTRAINT_DIGEST),
        ("route_candidate_base", ROUTE_CANDIDATE_SNAPSHOT_DIGEST),
        ("placement_base", PLACEMENT_BASE_REVISION),
        ("route_bundle_policy", ROUTE_BUNDLE_POLICY_DIGEST),
    ],
)
def test_supporting_pinned_bindings_are_well_formed(name: str, digest: str) -> None:
    assert digest.startswith("sha256:"), name
    assert len(digest) == 71, name
