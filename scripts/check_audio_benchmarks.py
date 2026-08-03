#!/usr/bin/env python3
"""Validate the local, licence-aware audio benchmark catalog."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG = Path("benchmarks/audio/catalog.json")
SCHEMA_PATH = Path("schemas/audio-benchmark-catalog/0.1.0.schema.json")
MAX_JSON_BYTES = 256_000
MAX_ARTIFACT_BYTES = 2_000_000
MAX_LICENSE_BYTES = 1_000_000

_LICENSE_MARKERS = {
    "Apache-2.0": (b"Apache License", b"Version 2.0, January 2004"),
    "CERN-OHL-S-2.0": (
        b"CERN Open Hardware Licence Version 2 - Strongly Reciprocal",
        b"CERN-OHL-S",
    ),
}


class CatalogError(ValueError):
    """The catalog or one of its committed artifacts is invalid."""


@dataclass(frozen=True)
class ValidatedFixture:
    """One fixture bound to the exact artifact and licence bytes that were checked."""

    document: dict[str, Any]
    artifact_bytes: bytes
    artifact_sha256: str
    license_bytes: bytes
    license_sha256: str


@dataclass(frozen=True)
class ValidatedCatalog:
    """A catalog and its fixtures captured by one bounded validation pass."""

    document: dict[str, Any]
    catalog_bytes: bytes
    catalog_sha256: str
    schema_sha256: str
    fixtures: tuple[ValidatedFixture, ...]


def _reject_constant(value: str) -> Any:
    raise CatalogError(f"unsupported JSON constant: {value}")


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise CatalogError("duplicate JSON object key")
        result[key] = value
    return result


def _read_bounded_path(path: Path, *, label: str, max_bytes: int) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise CatalogError(f"{label} cannot be read") from error
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise CatalogError(f"{label} must identify one regular file")
        with os.fdopen(descriptor, "rb", closefd=True) as handle:
            descriptor = -1
            payload = handle.read(max_bytes + 1)
    except OSError as error:
        raise CatalogError(f"{label} cannot be read") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if len(payload) > max_bytes:
        raise CatalogError(f"{label} exceeds {max_bytes} bytes")
    return payload


def _load_json(path: Path, *, label: str) -> tuple[dict[str, Any], bytes]:
    payload = _read_bounded_path(path, label=label, max_bytes=MAX_JSON_BYTES)
    try:
        document = json.loads(
            payload,
            object_pairs_hook=_strict_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CatalogError(f"{label} is not strict JSON") from error
    if not isinstance(document, dict):
        raise CatalogError(f"{label} must contain one JSON object")
    return document, payload


def _schema_error_path(error: Any) -> str:
    path = "/".join(str(part) for part in error.absolute_path)
    return path or "<root>"


def _validate_schema(document: dict[str, Any], schema: dict[str, Any]) -> None:
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as error:
        raise CatalogError("audio benchmark schema is invalid") from error
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(document), key=lambda error: list(error.absolute_path))
    if errors:
        first = errors[0]
        raise CatalogError(
            f"catalog schema violation at {_schema_error_path(first)}: {first.message}"
        )


def _read_confined_file(root: Path, relative: str, *, label: str, max_bytes: int) -> bytes:
    if not relative or Path(relative).is_absolute():
        raise CatalogError(f"{label} must be repository-relative")
    try:
        resolved = (root / relative).resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError) as error:
        raise CatalogError(f"{label} escapes the repository or is missing") from error
    if not resolved.is_file():
        raise CatalogError(f"{label} must identify one file")
    payload = _read_bounded_path(resolved, label=label, max_bytes=max_bytes)
    try:
        post_read_path = resolved.resolve(strict=True)
        post_read_path.relative_to(root)
    except (OSError, ValueError) as error:
        raise CatalogError(f"{label} changed or escaped during validation") from error
    if post_read_path != resolved:
        raise CatalogError(f"{label} changed during validation")
    return payload


def _validate_https_url(value: str, *, label: str) -> None:
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in value):
        raise CatalogError(f"{label} must not contain control characters")
    try:
        parsed = urlsplit(value)
        _ = parsed.port
    except ValueError as error:
        raise CatalogError(f"{label} must be a valid HTTPS URL") from error
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise CatalogError(f"{label} must be an HTTPS URL without credentials or a fragment")


def _require_unique_ids(entries: list[dict[str, Any]], *, label: str) -> None:
    identifiers = [entry["id"] for entry in entries]
    if len(identifiers) != len(set(identifiers)):
        raise CatalogError(f"{label} IDs must be unique")


def _validate_reference(reference: dict[str, Any]) -> None:
    _validate_https_url(reference["index_url"], label="external reference index_url")
    _validate_https_url(reference["terms_url"], label="external reference terms_url")
    try:
        date.fromisoformat(reference["reviewed_on"])
    except ValueError as error:
        raise CatalogError("external reference reviewed_on must be an ISO date") from error


def _expected_claims(fixture: dict[str, Any]) -> set[str]:
    claims: set[str] = set()
    if fixture["inspection"]["expected_supported"]:
        claims.add("board-ir-inspection")
    statuses = [route["expected_status"] for route in fixture["routes"]]
    if any(
        route["expected_status"] == "routed" and route["expected_pad_count"] == 2
        for route in fixture["routes"]
    ):
        claims.add("two-pin-route-preview")
    if any(
        route["expected_status"] == "routed" and route["expected_pad_count"] > 2
        for route in fixture["routes"]
    ):
        claims.add("multi-pin-route-preview")
    if any(status != "routed" for status in statuses):
        claims.add("typed-route-refusal")
    return claims


def _validate_fixture(root: Path, fixture: dict[str, Any]) -> ValidatedFixture:
    artifact_relative = fixture["artifact_path"]
    if not artifact_relative.endswith(".kicad_pcb"):
        raise CatalogError("fixture artifact_path must end with .kicad_pcb")
    artifact_payload = _read_confined_file(
        root,
        artifact_relative,
        label="fixture artifact_path",
        max_bytes=MAX_ARTIFACT_BYTES,
    )
    actual_digest = hashlib.sha256(artifact_payload).hexdigest()
    if actual_digest != fixture["artifact_sha256"]:
        raise CatalogError("fixture artifact_sha256 does not match artifact bytes")
    license_payload = _read_confined_file(
        root,
        fixture["license_path"],
        label="fixture license_path",
        max_bytes=MAX_LICENSE_BYTES,
    )
    license_digest = hashlib.sha256(license_payload).hexdigest()
    if license_digest != fixture["license_sha256"]:
        raise CatalogError("fixture license_sha256 does not match licence bytes")
    markers = _LICENSE_MARKERS[fixture["license_spdx"]]
    if any(marker not in license_payload for marker in markers):
        raise CatalogError("fixture license_spdx does not match licence evidence")
    route_keys = [(route["net"], route["layer"]) for route in fixture["routes"]]
    if len(route_keys) != len(set(route_keys)):
        raise CatalogError("fixture route declarations must use unique net/layer pairs")
    claims = set(fixture["claims"])
    not_claimed = set(fixture["not_claimed"])
    if claims & not_claimed:
        raise CatalogError("fixture claims and not_claimed must be disjoint")
    expected_claims = _expected_claims(fixture)
    if claims != expected_claims:
        raise CatalogError("fixture claims must exactly match declared observable evidence")
    return ValidatedFixture(
        document=fixture,
        artifact_bytes=artifact_payload,
        artifact_sha256=actual_digest,
        license_bytes=license_payload,
        license_sha256=license_digest,
    )


def load_and_validate_catalog(
    catalog_path: Path = ROOT / DEFAULT_CATALOG,
    *,
    root: Path = ROOT,
) -> ValidatedCatalog:
    """Load one bounded catalog and verify schemas, rights metadata, paths, and hashes."""

    resolved_root = root.resolve(strict=True)
    schema_payload = _read_confined_file(
        resolved_root,
        str(SCHEMA_PATH),
        label="audio benchmark schema",
        max_bytes=MAX_JSON_BYTES,
    )
    try:
        schema = json.loads(
            schema_payload,
            object_pairs_hook=_strict_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CatalogError("audio benchmark schema is not strict JSON") from error
    if not isinstance(schema, dict):
        raise CatalogError("audio benchmark schema must contain one JSON object")
    document, catalog_bytes = _load_json(catalog_path, label="audio benchmark catalog")
    _validate_schema(document, schema)
    try:
        date.fromisoformat(document["reviewed_on"])
    except ValueError as error:
        raise CatalogError("catalog reviewed_on must be an ISO date") from error
    references = document["external_references"]
    fixtures = document["fixtures"]
    _require_unique_ids(references, label="external reference")
    _require_unique_ids(fixtures, label="fixture")
    for reference in references:
        _validate_reference(reference)
    validated_fixtures = tuple(_validate_fixture(resolved_root, fixture) for fixture in fixtures)
    return ValidatedCatalog(
        document=document,
        catalog_bytes=catalog_bytes,
        catalog_sha256=hashlib.sha256(catalog_bytes).hexdigest(),
        schema_sha256=hashlib.sha256(schema_payload).hexdigest(),
        fixtures=validated_fixtures,
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=ROOT / DEFAULT_CATALOG)
    parser.add_argument("--root", type=Path, default=ROOT)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        catalog = load_and_validate_catalog(args.catalog, root=args.root)
    except CatalogError as error:
        raise SystemExit(f"Audio benchmark catalog check failed: {error}") from error
    print(
        "Audio benchmark catalog check passed: "
        f"{len(catalog.fixtures)} fixtures, "
        f"{len(catalog.document['external_references'])} reference-only sources."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
