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
record. Two further clauses exist because adversarial review defeated the first
draft with them: an exemption's tag **must be a release tag**, so a live break
cannot be waved through by keying an entry to the working tree; and an
exemption's recorded **direction** must be one the comparison actually observed,
so `narrowing`/`widening` in a reason is a checked claim rather than prose. What
stays unchecked is whether the ledger row an entry cites exists or says what the
entry claims -- that is reviewer-owned, exactly as it is in `check_doc_links.py`.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = "schemas"

# Every published release, oldest first. The sweep walks consecutive pairs, so a
# file first seen at `v0.5.0` is compared from there and never against absence.
#
# This list is explicit rather than derived, so that adding a release is a
# reviewed edit. During a release cut the final entry may be the one pending tag
# named by `pyproject.toml`; every earlier listed tag must already exist, and any
# repository tag not listed still fails. That lets pre-tag CI compare against the
# newest *published* tag without making the tag-triggered gate stale.
RELEASE_TAGS = (
    "v0.1.0",
    "v0.2.0",
    "v0.3.0",
    "v0.4.0",
    "v0.5.0",
    "v0.6.0",
    "v0.7.0",
    "v0.8.0",
    "v0.9.0",
)

# The four in-place accepted-set changes that were published before this checker
# existed. They cannot be repaired: the bytes are in eight release tags, on PyPI
# and in whatever a consumer downloaded. ADR-0105 records the decision to freeze
# rather than correct them, and `D-197` is the row.
#
# Keyed `(path, declared version, the tag at which the set moved)`. The value
# names the record and the direction. Three things are enforced about an entry,
# and one is not:
#
# * **Its tag must be a release tag.** An exemption keyed to the working tree
#   would let a live break be waved through by adding a line here, which is a
#   suppression mechanism wearing an exemption's clothes. Only history can be
#   exempted, because only history cannot be repaired.
# * **It must match real drift.** An entry matching nothing fails the run, so it
#   cannot be added and then quietly forgotten.
# * **Its recorded direction must be one the gate observed** (see
#   `_check_recorded_direction`). Not exhaustive -- see that function for why.
#
# **Not enforced:** that the record it names exists or says what the entry claims
# it says. `D-197` is a citation, and citation truth is reviewer-owned here
# exactly as it is in `check_doc_links.py`.
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


def _current_project_tag() -> str:
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return f"v{metadata['project']['version']}"


def _published_tags_for_comparison(failures: list[str]) -> tuple[str, ...]:
    """Return listed tags that exist, allowing only the current final tag to be pending.

    Pre-tag CI has to know the release tag it is preparing, while the tag-triggered
    release gate has to compare that same tag once it exists. The only missing
    listed tag therefore permitted is the final entry, and it must match the
    project version being cut. A deleted historical tag or an unlisted new tag is
    still a hard failure.
    """

    repository_tags = _repository_release_tags()
    unlisted = sorted(repository_tags - set(RELEASE_TAGS))
    if unlisted:
        failures.append(
            f"{Path(__file__).name}: RELEASE_TAGS omits {', '.join(unlisted)}; the working-tree "
            "comparison would run against a tag that is no longer the newest"
        )

    missing = [tag for tag in RELEASE_TAGS if tag not in repository_tags]
    pending = _current_project_tag()
    if missing and missing != [RELEASE_TAGS[-1]]:
        failures.append(
            f"{Path(__file__).name}: listed historical release tag(s) are missing: "
            f"{', '.join(missing)}"
        )
    elif missing and missing[0] != pending:
        failures.append(
            f"{Path(__file__).name}: pending final tag {missing[0]} does not match "
            f"project version tag {pending}"
        )

    return tuple(tag for tag in RELEASE_TAGS if tag in repository_tags)


def _check_exemptions_are_keyed_to_history(failures: list[str]) -> None:
    """An exemption may only name a release tag, never the working tree.

    Without this, a live break is waved through by adding one line: real drift
    plus an entry keyed `(file, version, "the working tree")` and the run goes
    green. Only a *published* break is unrepairable, so only a published break
    is exemptible; anything in the working tree can simply be fixed. Rejecting
    the key makes the smuggled entry inexpressible rather than merely something
    a reviewer is supposed to notice.
    """

    for key in sorted(EXEMPT_DRIFT):
        path, version, tag = key
        if tag not in RELEASE_TAGS:
            failures.append(
                f"{path}: drift exemption for {version!r} names {tag!r}, which is not a release "
                "tag; only a published break can be exempted"
            )


# The direction words a reason may claim. `shape` is a label the gate emits and
# never a word a reason carries, so it is deliberately not in this set.
_DIRECTION_WORDS = ("narrowing", "widening")


def _observed_directions(differences: list[str]) -> set[str]:
    return {
        word
        for word in _DIRECTION_WORDS
        if any(f"[{word}" in line or f"/{word}]" in line for line in differences)
    }


def _check_recorded_direction(
    key: tuple[str, str, str], differences: list[str], failures: list[str]
) -> None:
    """The direction an exemption claims must be one the gate actually saw.

    Without this the words `narrowing` and `widening` in a reason are unverified
    prose: flipping one changes nothing and no test notices.

    **The check is containment, not equality, and that is deliberate.** A
    required-key addition is simultaneously a widening of the object's property
    set and a narrowing of its `required` list -- `drc-summary` at `v0.7.0` is
    exactly that, and it is recorded by its *net* effect on a consumer, which is
    the narrowing. So a reason must claim only directions the gate observed; it
    is **not** required to enumerate all of them. What this catches is a reason
    naming a direction the change does not have.
    """

    path, version, tag = key
    observed = _observed_directions(differences)
    recorded = {word for word in _DIRECTION_WORDS if word in EXEMPT_DRIFT[key]}
    if not recorded:
        failures.append(
            f"{path}: drift exemption for {version!r} at {tag} records no direction; "
            f"name at least one of {', '.join(_DIRECTION_WORDS)}"
        )
        return
    unsupported = sorted(recorded - observed)
    if unsupported:
        failures.append(
            f"{path}: drift exemption for {version!r} at {tag} records "
            f"{', '.join(unsupported)}, which the accepted-set comparison does not show "
            f"(it shows {', '.join(sorted(observed)) or 'no direction at all'})"
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
        # `label in RELEASE_TAGS` is what makes a working-tree exemption
        # *inapplicable* rather than merely reported: without it the smuggled
        # entry still suppresses its drift, and the run fails without ever
        # naming what was waved through.
        key = (path, version, label)
        if label in RELEASE_TAGS and key in EXEMPT_DRIFT:
            used.add(key)
            _check_recorded_direction(key, differences, failures)
            continue
        detail = "\n    ".join(differences)
        failures.append(
            f"{path}: accepted set changed at {label} while the declared version stayed "
            f"{version!r}\n    {detail}"
        )


def main() -> int:
    failures: list[str] = []
    used: set[tuple[str, str, str]] = set()

    published_tags = _published_tags_for_comparison(failures)
    _check_exemptions_are_keyed_to_history(failures)
    if not published_tags:
        failures.append("no listed release tag exists to anchor the working-tree comparison")
        working_tree = _working_tree_schemas()
    else:
        previous = _snapshot_at(published_tags[0])
        for tag in published_tags[1:]:
            current = _snapshot_at(tag)
            _compare(tag, previous, current, failures, used)
            previous = current
        working_tree = _working_tree_schemas()
        _compare(WORKING_TREE, previous, working_tree, failures, used)

    for key in sorted(set(EXEMPT_DRIFT) - used):
        path, version, tag = key
        if tag not in RELEASE_TAGS:
            # Already reported, and by the more specific reason of the two.
            continue
        failures.append(
            f"{path}: drift exemption for {version!r} at {tag} ({EXEMPT_DRIFT[key]}) "
            "matched no accepted-set change; remove it"
        )

    if failures:
        raise SystemExit("Schema accepted-set check failed:\n- " + "\n- ".join(failures))
    print(
        f"Schema accepted-set check passed ({len(working_tree)} schemas across "
        f"{len(published_tags)} published release tags, "
        f"{len(RELEASE_TAGS) - len(published_tags)} pending tag(s), and the working tree; "
        f"recorded published-break exemptions: {len(EXEMPT_DRIFT)})."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
