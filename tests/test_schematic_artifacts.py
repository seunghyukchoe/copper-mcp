from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from copper_mcp.circuit_intent_service import build_schematic_from_snapshot_json
from copper_mcp.schematic_artifacts import (
    MAX_SCHEMATIC_ARTIFACT_STORE_BYTES,
    MAX_SCHEMATIC_ARTIFACTS,
    SCHEMATIC_ARTIFACT_TTL_SECONDS,
    SchematicArtifactStore,
    SchematicArtifactUnavailableError,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "benchmarks" / "audio" / "fixtures" / "rc-low-pass-intent-v1.json"
TOKEN_PATTERN = re.compile(
    r"^pcb://artifacts/schematic/([A-Za-z0-9_-]{43})/circuit\.kicad_sch$"
)


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _tokens() -> Iterator[str]:
    for character in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        yield character * 43


def _token(uri: str) -> str:
    match = TOKEN_PATTERN.fullmatch(uri)
    assert match is not None
    return match.group(1)


def _artifact():  # type: ignore[no-untyped-def]
    return build_schematic_from_snapshot_json(FIXTURE.read_bytes()).artifact


def test_default_store_issues_distinct_opaque_capabilities_and_exact_bytes() -> None:
    artifact = _artifact()
    store = SchematicArtifactStore()

    first_uri = store.put(artifact)
    second_uri = store.put(artifact)
    first_token = _token(first_uri)
    second_token = _token(second_uri)

    assert first_token != second_token
    assert artifact.artifact_digest not in first_uri
    assert artifact.intent_digest not in first_uri
    assert store.read(first_token) == artifact.content
    assert store.read(second_token) == artifact.content
    assert not hasattr(store, "list")


def test_concurrent_capability_creation_and_reads_remain_exact() -> None:
    artifact = _artifact()
    store = SchematicArtifactStore()

    def round_trip(_: int) -> bytes:
        return store.read(_token(store.put(artifact)))

    with ThreadPoolExecutor(max_workers=8) as executor:
        observed = list(executor.map(round_trip, range(8)))

    assert observed == [artifact.content] * 8


def test_ttl_is_absolute_and_reads_do_not_extend_it() -> None:
    clock = _Clock()
    tokens = _tokens()
    store = SchematicArtifactStore(
        ttl_seconds=10,
        clock=clock,
        token_factory=lambda: next(tokens),
    )
    artifact = _artifact()
    token = _token(store.put(artifact))

    clock.now = 9.999
    assert store.read(token) == artifact.content
    clock.now = 10.0
    with pytest.raises(
        SchematicArtifactUnavailableError,
        match=r"^schematic artifact is unavailable$",
    ):
        store.read(token)


def test_entry_bound_uses_deterministic_read_refreshed_lru_eviction() -> None:
    tokens = _tokens()
    store = SchematicArtifactStore(
        max_artifacts=2,
        token_factory=lambda: next(tokens),
    )
    artifact = _artifact()
    first = _token(store.put(artifact))
    second = _token(store.put(artifact))

    assert store.read(first) == artifact.content
    third = _token(store.put(artifact))

    assert store.read(first) == artifact.content
    assert store.read(third) == artifact.content
    with pytest.raises(SchematicArtifactUnavailableError) as raised:
        store.read(second)
    assert str(raised.value) == "schematic artifact is unavailable"


def test_total_byte_bound_evicts_before_inserting() -> None:
    tokens = _tokens()
    artifact = _artifact()
    store = SchematicArtifactStore(
        max_total_bytes=len(artifact.content) * 2 - 1,
        token_factory=lambda: next(tokens),
    )
    first = _token(store.put(artifact))
    second = _token(store.put(artifact))

    with pytest.raises(SchematicArtifactUnavailableError):
        store.read(first)
    assert store.read(second) == artifact.content


def test_invalid_expired_and_evicted_tokens_have_one_failure_surface() -> None:
    clock = _Clock()
    tokens = _tokens()
    store = SchematicArtifactStore(
        max_artifacts=1,
        ttl_seconds=2,
        clock=clock,
        token_factory=lambda: next(tokens),
    )
    artifact = _artifact()
    evicted = _token(store.put(artifact))
    live = _token(store.put(artifact))
    invalid = "not-a-valid-capability"

    failures: list[tuple[type[BaseException], str]] = []
    for token in (invalid, evicted):
        with pytest.raises(SchematicArtifactUnavailableError) as raised:
            store.read(token)
        failures.append((type(raised.value), str(raised.value)))
    clock.now = 2.0
    with pytest.raises(SchematicArtifactUnavailableError) as raised:
        store.read(live)
    failures.append((type(raised.value), str(raised.value)))

    assert failures == [
        (SchematicArtifactUnavailableError, "schematic artifact is unavailable"),
        (SchematicArtifactUnavailableError, "schematic artifact is unavailable"),
        (SchematicArtifactUnavailableError, "schematic artifact is unavailable"),
    ]


def test_read_reverifies_content_digest_and_discards_tampered_entry() -> None:
    tokens = _tokens()
    artifact = _artifact()
    store = SchematicArtifactStore(token_factory=lambda: next(tokens))
    token = _token(store.put(artifact))
    object.__setattr__(artifact, "content", artifact.content + b"tampered")

    with pytest.raises(
        SchematicArtifactUnavailableError,
        match=r"^schematic artifact is unavailable$",
    ):
        store.read(token)
    with pytest.raises(SchematicArtifactUnavailableError):
        store.read(token)


def test_coordinated_tamper_cannot_corrupt_store_byte_accounting() -> None:
    tokens = _tokens()
    artifact = _artifact()
    size = len(artifact.content)
    store = SchematicArtifactStore(
        max_total_bytes=size,
        token_factory=lambda: next(tokens),
    )
    tampered_token = _token(store.put(artifact))
    tampered_content = artifact.content + (b"x" * size)
    object.__setattr__(artifact, "content", tampered_content)
    object.__setattr__(
        artifact,
        "artifact_digest",
        f"sha256:{hashlib.sha256(tampered_content).hexdigest()}",
    )

    with pytest.raises(SchematicArtifactUnavailableError):
        store.read(tampered_token)

    first_artifact = _artifact()
    second_artifact = _artifact()
    first = _token(store.put(first_artifact))
    second = _token(store.put(second_artifact))
    with pytest.raises(SchematicArtifactUnavailableError):
        store.read(first)
    assert store.read(second) == second_artifact.content


@pytest.mark.parametrize(
    "settings",
    [
        {"max_artifacts": True},
        {"max_artifacts": 0},
        {"max_artifacts": MAX_SCHEMATIC_ARTIFACTS + 1},
        {"max_total_bytes": MAX_SCHEMATIC_ARTIFACT_STORE_BYTES + 1},
        {"ttl_seconds": SCHEMATIC_ARTIFACT_TTL_SECONDS + 1},
    ],
)
def test_store_limits_are_positive_and_tighten_only(settings: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="positive and tighten-only"):
        SchematicArtifactStore(**settings)  # type: ignore[arg-type]
