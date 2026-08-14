#!/usr/bin/env python3
"""Fail when a published schema's *accepted set* changes without its version.

A JSON Schema file is a promise about which documents are acceptable. Two files
can carry the same `$id` and the same declared version and still accept
different documents, and nothing in this repository noticed that for three
releases running.

**What this checker owns.** For every `schemas/**/*.json`, it derives an
*accepted set* -- the property names of every object, its `additionalProperties`
setting, its `required` list, every `enum`, every `const`, and every union
`type` -- and fails when that set differs between two points at which the file's
*declared version* is the same. It sweeps two axes:

* **Every consecutive release tag**, so the four historical instances are on the
  record as exemptions rather than as folklore. This half never goes green by
  accident; it goes green because someone wrote down each break.
* **The newest release tag against the working tree**, which is the half that
  catches the next one before it merges.

It also fails when a published schema is **removed**. That is not an accepted-set
change, so the comparison would never see it, and it is the loudest way to break
a consumer -- including by silently undoing a freeze this checker exists to hold.

**Both directions fail, and the checker says which.** A `required` key added, a
property or `enum` member removed, `additionalProperties` closed, or a `const`
changed is a **narrowing**: a document that validated yesterday fails today.
The reverse is a **widening**: the consumer holding the older copy rejects a
document the project now calls valid. Issue #172 argued about widening only,
and two of the four historical instances are narrowings -- so a gate that fired
in one direction would have missed half of what it was built for. A whole
constraint *site* appearing or disappearing is labelled `[shape]` and given no
direction, because the direction is not readable locally; see `drift`.

**What it does not own.** It has no opinion about whether a change is *correct*,
only about whether the version moved with it. It does not validate a schema
against its meta-schema (`tests/test_schema_conformance.py` does), does not read
instance documents, and cannot see a semantic change expressed through keywords
outside the list above -- a tightened `pattern`, a lowered `maximum`, a changed
`$ref` target. Those are real drift and this checker is blind to them; it is a
floor, not a proof. Widening the extracted set is the way to raise the floor.

**Exemptions follow `check_doc_links.py`'s discipline exactly**, because the same
thing is true here: a published break cannot be repaired. `EXEMPT_DRIFT` is
keyed `(file, declared version, tag)`, each entry names the ledger row that
records the break, and an entry matching nothing **fails the run**. An exemption
that can be added and then quietly forgotten is a suppression mechanism, not a
record.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = "schemas"

# Every published release, oldest first. The sweep walks consecutive pairs, so a
# file first seen at `v0.5.0` is compared from there and never against absence.
#
# This list is explicit rather than derived, so that adding a release is a
# reviewed edit -- and `_check_tags_are_current` fails the run when a `v*` tag
# exists that is not listed, so it cannot go stale unnoticed. That matters: the
# working-tree half compares against `RELEASE_TAGS[-1]`, and a stale last entry
# would quietly mean "newest tag" is not the newest tag.
RELEASE_TAGS = (
    "v0.1.0",
    "v0.2.0",
    "v0.3.0",
    "v0.4.0",
    "v0.5.0",
    "v0.6.0",
    "v0.7.0",
    "v0.8.0",
)

# The four in-place accepted-set changes that were published before this checker
# existed. They cannot be repaired: the bytes are in eight release tags, on PyPI
# and in whatever a consumer downloaded. ADR-0105 records the decision to freeze
# rather than correct them, and `D-197` is the row.
#
# Keyed `(path, declared version, the tag at which the set moved)`. The value
# names the record. Adding an entry requires a ledger row; an entry that matches
# no real drift fails the run.
EXEMPT_DRIFT: dict[tuple[str, str, str], str] = {
    (
        "schemas/audio-benchmark-catalog/0.1.0.schema.json",
        "0.1.0",
        "v0.3.0",
    ): "ADR-0105 / D-197: `expected_pad_count` added as a required key (narrowing) "
    "and a `multi-pin-route-preview` claim enum member added (widening)",
    (
        "schemas/board-ir/0.2.0.schema.json",
        "0.2.0",
        "v0.7.0",
    ): "ADR-0105 / D-197: `courtyard_circles` and its `$def` added, and `net_id` widened "
    "to accept null on via, segment and arc (widening)",
    (
        "schemas/drc-summary.schema.json",
        "1.0",
        "v0.7.0",
    ): "ADR-0105 / D-197: `clean` added as a required key with an `allOf` pinning both "
    "derivations (narrowing)",
    (
        "schemas/board-ir/0.2.0.schema.json",
        "0.2.0",
        "v0.8.0",
    ): "ADR-0105 / D-197: `far_side_courtyards` and `far_side_courtyard_circles` added to "
    "`$defs/footprint` by ADR-0097 (widening) -- issue #172's instance",
}

WORKING_TREE = "the working tree"

# Keyword suffixes used to build accepted-set keys. Named so a failure message
# can say which kind of promise moved.
_PROPERTIES = "properties"
_ADDITIONAL = "additionalProperties"
_REQUIRED = "required"
_ENUM = "enum"
_CONST = "const"
_TYPE = "type"


def accepted_set(document: Any) -> dict[str, Any]:
    """Extract every promise this schema makes about which documents it accepts.

    The keys are JSON-pointer-ish paths suffixed with the keyword, so a diff
    names the exact site. The values are order-independent (sorted) wherever the
    keyword's own semantics are, so a reordered `required` list is not drift.
    """

    found: dict[str, Any] = {}

    def walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            properties = node.get(_PROPERTIES)
            if isinstance(properties, dict):
                found[f"{path} |{_PROPERTIES}"] = sorted(properties)
                # An absent `additionalProperties` is `true`: the object is open.
                found[f"{path} |{_ADDITIONAL}"] = node.get(_ADDITIONAL, True)
                found[f"{path} |{_REQUIRED}"] = sorted(node.get(_REQUIRED, []))
            if _ENUM in node:
                found[f"{path} |{_ENUM}"] = sorted(str(member) for member in node[_ENUM])
            if _CONST in node:
                found[f"{path} |{_CONST}"] = str(node[_CONST])
            declared_type = node.get(_TYPE)
            if declared_type is not None and not isinstance(declared_type, str):
                found[f"{path} |{_TYPE}"] = sorted(declared_type)
            for key, value in node.items():
                walk(value, f"{path}.{key}")
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, f"{path}[{index}]")

    walk(document, "$")
    return found


def declared_version(path: str, document: Any) -> str:
    """The version this file says it is, as a token to compare against itself.

    A schema that pins `schema_version` names its own version and that is the
    authority. Otherwise the filename does -- `0.2.0.schema.json` is `0.2.0` and
    `pcm.v1.schema.json` is `pcm.v1`, which is opaque but stable, and stability
    is the whole requirement: this value is only ever compared with itself.
    """

    if isinstance(document, dict):
        properties = document.get(_PROPERTIES)
        if isinstance(properties, dict):
            version = properties.get("schema_version")
            if isinstance(version, dict) and isinstance(version.get(_CONST), str):
                return version[_CONST]
    return Path(path).name.removesuffix(".json").removesuffix(".schema")


def _narrows(key: str, before: Any, after: Any) -> bool:
    """Does this one change reject a document the older copy accepted?"""

    if key.endswith(f"|{_ADDITIONAL}"):
        # `true` -> `false` closes an object that was open.
        return before is not False and after is False
    if key.endswith(f"|{_REQUIRED}"):
        return bool(set(after) - set(before))
    if key.endswith(f"|{_CONST}"):
        return True
    # Property sets, enums and union types: losing a member rejects documents.
    return bool(set(before) - set(after))


def _widens(key: str, before: Any, after: Any) -> bool:
    """Does this one change accept a document the older copy rejected?"""

    if key.endswith(f"|{_ADDITIONAL}"):
        return before is False and after is not False
    if key.endswith(f"|{_REQUIRED}"):
        return bool(set(before) - set(after))
    if key.endswith(f"|{_CONST}"):
        return True
    return bool(set(after) - set(before))


def drift(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    """Describe every accepted-set difference, each labelled with its direction.

    A `const` change is reported as **both** directions, because it rejects what
    it used to accept and accepts what it used to reject in the same edit.

    A whole constraint *site* appearing or disappearing -- a new `$def`, a
    deleted object -- is labelled `[shape]` and **not** given a direction, on
    purpose. The direction is not readable locally: a new closed object with a
    `required` list narrows the documents it governs, and the same object added
    beneath a new *optional* property widens the schema overall. Guessing here
    would put a confident wrong word in a failure message. It still fails the
    run; it just does not claim to know which way.
    """

    lines: list[str] = []
    for key in sorted(set(before) | set(after)):
        if key in before and key not in after:
            lines.append(f"removed {key} (was {before[key]!r}) [shape]")
            continue
        if key not in before:
            lines.append(f"added {key} = {after[key]!r} [shape]")
            continue
        if before[key] == after[key]:
            continue
        directions = []
        if _narrows(key, before[key], after[key]):
            directions.append("narrowing")
        if _widens(key, before[key], after[key]):
            directions.append("widening")
        label = "/".join(directions) if directions else "changed"
        lines.append(f"changed {key}: {before[key]!r} -> {after[key]!r} [{label}]")
    return lines


def _git(*arguments: str) -> subprocess.CompletedProcess[bytes]:
    git = shutil.which("git")
    if git is None:
        raise SystemExit("git is required to compare published schemas against release tags")
    return subprocess.run(  # noqa: S603
        [git, "-C", str(ROOT), *arguments], capture_output=True, check=False
    )


def _schema_paths_at(tag: str) -> list[str]:
    result = _git("ls-tree", "-r", "--name-only", tag, f"{SCHEMA_DIR}/")
    if result.returncode != 0:
        raise SystemExit(f"cannot read {SCHEMA_DIR}/ at {tag}: {result.stderr.decode().strip()}")
    return sorted(
        line for line in result.stdout.decode("utf-8").split("\n") if line.endswith(".json")
    )


def _document_at(tag: str, path: str) -> Any:
    result = _git("show", f"{tag}:{path}")
    if result.returncode != 0:
        raise SystemExit(f"cannot read {path} at {tag}")
    return json.loads(result.stdout.decode("utf-8"))


def _working_tree_schemas() -> dict[str, Any]:
    documents: dict[str, Any] = {}
    for path in sorted((ROOT / SCHEMA_DIR).rglob("*.json")):
        relative = path.relative_to(ROOT).as_posix()
        documents[relative] = json.loads(path.read_text(encoding="utf-8"))
    return documents


def _repository_release_tags() -> set[str]:
    result = _git("tag", "--list", "v*")
    if result.returncode != 0:
        raise SystemExit("cannot enumerate release tags")
    return {line for line in result.stdout.decode("utf-8").split("\n") if line}


def _check_tags_are_current(failures: list[str]) -> None:
    """A release tag this checker has never heard of is a hole, so it fails."""

    unlisted = sorted(_repository_release_tags() - set(RELEASE_TAGS))
    if unlisted:
        failures.append(
            f"{Path(__file__).name}: RELEASE_TAGS omits {', '.join(unlisted)}; the working-tree "
            "comparison would run against a tag that is no longer the newest"
        )


def _snapshot_at(tag: str) -> dict[str, Any]:
    return {path: _document_at(tag, path) for path in _schema_paths_at(tag)}


def _compare(
    label: str,
    before: dict[str, Any],
    after: dict[str, Any],
    failures: list[str],
    used: set[tuple[str, str, str]],
) -> None:
    """Compare one snapshot against the next, at `label`."""

    for path in sorted(set(before) - set(after)):
        # A published schema that simply vanishes is the loudest possible way to
        # break a consumer, and it is not an accepted-set change, so the loop
        # below would never see it. ADR-0105 freezes `board-ir/0.2.0` rather
        # than correcting it; a gate that stayed green when the file was deleted
        # would not be enforcing that freeze at all.
        failures.append(f"{path}: published schema removed at {label}; it may not be unpublished")

    for path, document in after.items():
        if path not in before:
            # First publication is not drift: there is no promise to break yet.
            continue
        version = declared_version(path, document)
        if declared_version(path, before[path]) != version:
            # The version moved with the change, which is the whole point.
            continue
        differences = drift(accepted_set(before[path]), accepted_set(document))
        if not differences:
            continue
        key = (path, version, label)
        if key in EXEMPT_DRIFT:
            used.add(key)
            continue
        detail = "\n    ".join(differences)
        failures.append(
            f"{path}: accepted set changed at {label} while the declared version stayed "
            f"{version!r}\n    {detail}"
        )


def main() -> int:
    failures: list[str] = []
    used: set[tuple[str, str, str]] = set()

    _check_tags_are_current(failures)
    previous = _snapshot_at(RELEASE_TAGS[0])
    for tag in RELEASE_TAGS[1:]:
        current = _snapshot_at(tag)
        _compare(tag, previous, current, failures, used)
        previous = current
    working_tree = _working_tree_schemas()
    _compare(WORKING_TREE, previous, working_tree, failures, used)

    for key in sorted(set(EXEMPT_DRIFT) - used):
        path, version, tag = key
        failures.append(
            f"{path}: drift exemption for {version!r} at {tag} ({EXEMPT_DRIFT[key]}) "
            "matched no accepted-set change; remove it"
        )

    if failures:
        raise SystemExit("Schema accepted-set check failed:\n- " + "\n- ".join(failures))
    print(
        f"Schema accepted-set check passed ({len(working_tree)} schemas across "
        f"{len(RELEASE_TAGS)} release tags and the working tree; "
        f"recorded published-break exemptions: {len(EXEMPT_DRIFT)})."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
