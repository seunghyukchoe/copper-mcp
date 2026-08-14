"""The repository gate on published DRC counts.

`scripts/check_drc_comparability.py` has three halves and each one is here because the others
cannot see what it sees: an artifact sweep that catches a runner nobody registered, a
differential prohibition that catches a delta computed from noise, and a runner registry that
catches a runner that stopped enforcing at emission.

So the tests are organised by what could make the gate useless:

* it could stop demanding the literal at all, or stop sweeping some artifacts;
* its exemption list could become a suppression mechanism, so an entry matching nothing has to
  fail the run **and** an entry whose section has since been qualified has to fail too;
* it could admit a differential over a count that is not comparable;
* the registry could name a runner that no longer imports the gate, or a file that is gone.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location(
    "check_drc_comparability", ROOT / "scripts" / "check_drc_comparability.py"
)
assert _SPEC is not None and _SPEC.loader is not None
check_drc_comparability = importlib.util.module_from_spec(_SPEC)
sys.modules["check_drc_comparability"] = check_drc_comparability
_SPEC.loader.exec_module(check_drc_comparability)

_QUALIFIED = {"error_count": 936, "warning_count": 3, "drc_comparability": "single_invocation"}


def _tree(tmp_path: Path, artifacts: dict[str, Any], runners: set[str] | None = None) -> Path:
    """A miniature repository: some artifacts, and every registered runner importing the gate."""

    for name, document in artifacts.items():
        path = tmp_path / "benchmarks" / "results" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    for runner in runners if runners is not None else check_drc_comparability.DRC_RECORDING_RUNNERS:
        path = tmp_path / runner
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"import {check_drc_comparability.MODULE}\n", encoding="utf-8")
    return tmp_path


def _run_against(
    monkeypatch: pytest.MonkeyPatch,
    tree: Path,
    pre_policy: dict[tuple[str, str], str] | None = None,
) -> None:
    monkeypatch.setattr(check_drc_comparability, "ROOT", tree)
    monkeypatch.setattr(check_drc_comparability, "PRE_POLICY", pre_policy or {})
    check_drc_comparability.main()


# ---------------------------------------------------------------------------
# The gate on this tree
# ---------------------------------------------------------------------------


def test_the_gate_is_green_on_this_tree() -> None:
    assert check_drc_comparability.main() == 0


def test_every_pre_policy_exemption_names_the_record_that_qualifies_it() -> None:
    """An exemption is a pointer at a record, or it is a suppression."""

    assert check_drc_comparability.PRE_POLICY
    for key, reason in check_drc_comparability.PRE_POLICY.items():
        assert "B-111" in reason, key


def test_every_registered_runner_exists_and_imports_the_gate() -> None:
    for runner in check_drc_comparability.DRC_RECORDING_RUNNERS:
        source = (ROOT / runner).read_text(encoding="utf-8")
        assert check_drc_comparability.MODULE in source, runner


def test_the_deferred_runner_still_has_the_reason_it_is_deferred() -> None:
    """A deferral is a record, so the fact it rests on has to still be true.

    `benchmark_route_bundle.py` is not wired to the emission gate because its committed artifact
    pins `script_sha256` of the runner itself: editing the runner would invalidate a published
    binding to buy a gate the artifact sweep already provides. If that pin ever goes away, the
    deferral has lost its reason and this test says so.
    """

    assert set(check_drc_comparability.DEFERRED_RUNNERS).isdisjoint(
        check_drc_comparability.DRC_RECORDING_RUNNERS
    )
    for runner, reason in check_drc_comparability.DEFERRED_RUNNERS.items():
        assert (ROOT / runner).is_file(), runner
        assert "script_sha256" in reason, runner
        assert check_drc_comparability.MODULE not in (ROOT / runner).read_text(encoding="utf-8")

    artifact = json.loads(
        (ROOT / "benchmarks/results/routing/2026-08-05-route-bundle-v1.json").read_text(
            encoding="utf-8"
        )
    )
    assert artifact["script"] == "scripts/benchmark_route_bundle.py"
    assert "script_sha256" in artifact


_FREEROUTING_RUNNER = "scripts/benchmark_freerouting_comparison.py"
_FREEROUTING_ARTIFACT = "benchmarks/results/routing/2026-08-05-freerouting-common-two-pad.json"


def test_the_freerouting_runner_is_wired_because_it_has_no_self_pin_to_protect() -> None:
    """The deferral is a cost paid for a real binding, so the reason has to be checked, not assumed.

    `benchmark_route_bundle.py` is deferred because its artifact pins `script_sha256` of the
    runner. This runner was checked against that same reason and does not have it: its artifact
    records no `script_sha256` and no `script` field at all, so editing the runner invalidates
    nothing that was published. If a future regeneration ever adds a self-pin here, this test
    fails and the wiring decision has to be re-taken rather than silently inherited.
    """

    assert _FREEROUTING_RUNNER in check_drc_comparability.DRC_RECORDING_RUNNERS
    assert _FREEROUTING_RUNNER not in check_drc_comparability.DEFERRED_RUNNERS

    artifact = json.loads((ROOT / _FREEROUTING_ARTIFACT).read_text(encoding="utf-8"))
    assert "script_sha256" not in artifact
    assert "script" not in artifact
    assert artifact["schema"] == "copper-mcp/benchmark/freerouting-comparison/v1"


def test_the_freerouting_artifacts_three_drc_sections_are_swept_and_exempted() -> None:
    """The sections the first version of the section table could not see.

    All three publish their counts under the second vocabulary. Their bytes are not edited --
    `run_id` is a digest of the artifact's own content -- so each is keyed into `PRE_POLICY`
    against `B-111` exactly as the six artifacts before them.
    """

    document = json.loads((ROOT / _FREEROUTING_ARTIFACT).read_text(encoding="utf-8"))
    paths = {path for path, _ in check_drc_comparability.drc_sections(document)}

    assert paths == {"/results[0]/drc", "/results[1]/drc", "/source_drc"}
    for path in paths:
        assert (_FREEROUTING_ARTIFACT, path) in check_drc_comparability.PRE_POLICY


# ---------------------------------------------------------------------------
# The artifact sweep
# ---------------------------------------------------------------------------


def test_an_unqualified_count_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    tree = _tree(tmp_path, {"new/run.json": {"drc": {"error_count": 941}}})

    with pytest.raises(SystemExit) as caught:
        _run_against(monkeypatch, tree)

    assert "publishes a count without a 'drc_comparability' literal" in str(caught.value)


def test_a_qualified_count_passes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    tree = _tree(tmp_path, {"new/run.json": {"drc": dict(_QUALIFIED)}})

    _run_against(monkeypatch, tree)


def test_a_count_nested_in_a_list_is_swept_too(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Per-board records live in a list, which is where the corpus runner publishes counts."""

    tree = _tree(tmp_path, {"new/run.json": {"boards": [{"drc": {"error_count": 941}}]}})

    with pytest.raises(SystemExit) as caught:
        _run_against(monkeypatch, tree)

    assert "/boards[0]/drc" in str(caught.value)


def test_a_literal_outside_the_closed_set_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    tree = _tree(
        tmp_path,
        {"new/run.json": {"drc": {"error_count": 941, "drc_comparability": "probably_fine"}}},
    )

    with pytest.raises(SystemExit) as caught:
        _run_against(monkeypatch, tree)

    assert "probably_fine" in str(caught.value)


# ---------------------------------------------------------------------------
# The exemption list cannot become a suppression mechanism
# ---------------------------------------------------------------------------


def test_an_exemption_covers_its_section(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    tree = _tree(tmp_path, {"old/run.json": {"drc": {"error_count": 941}}})

    _run_against(
        monkeypatch,
        tree,
        {("benchmarks/results/old/run.json", "/drc"): "B-111 qualifies this"},
    )


def test_an_exemption_that_matches_nothing_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    tree = _tree(tmp_path, {"old/run.json": {"drc": {"error_count": 941}}})

    with pytest.raises(SystemExit) as caught:
        _run_against(
            monkeypatch,
            tree,
            {
                ("benchmarks/results/old/run.json", "/drc"): "B-111 qualifies this",
                ("benchmarks/results/old/run.json", "/gone"): "B-111 qualifies nothing",
            },
        )

    assert "matched no DRC section; remove it" in str(caught.value)


def test_an_exemption_over_a_now_qualified_section_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A re-measured artifact carries its own literal, and the exemption has to go with it."""

    tree = _tree(tmp_path, {"old/run.json": {"drc": dict(_QUALIFIED)}})

    with pytest.raises(SystemExit) as caught:
        _run_against(
            monkeypatch,
            tree,
            {("benchmarks/results/old/run.json", "/drc"): "B-111 qualifies this"},
        )

    assert "remove its pre-policy exemption" in str(caught.value)


# ---------------------------------------------------------------------------
# The differential prohibition
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("literal", ["single_invocation", "repeated_disagreement"])
def test_a_delta_on_an_incomparable_count_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, literal: str
) -> None:
    tree = _tree(
        tmp_path,
        {
            "new/run.json": {
                "drc": {"error_count": 941, "error_count_delta": 5, "drc_comparability": literal}
            }
        },
    )

    with pytest.raises(SystemExit) as caught:
        _run_against(monkeypatch, tree)

    assert "publishes the differential 'error_count_delta'" in str(caught.value)


def test_a_delta_on_a_repeated_agreement_count_passes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    tree = _tree(
        tmp_path,
        {
            "new/run.json": {
                "drc": {
                    "error_count": 941,
                    "error_count_delta": 5,
                    "drc_comparability": "repeated_agreement",
                }
            }
        },
    )

    _run_against(monkeypatch, tree)


def test_the_prohibition_also_reaches_an_exempted_section(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An exemption excuses the missing literal, never a differential built on the count."""

    tree = _tree(tmp_path, {"old/run.json": {"drc": {"error_count": 941, "error_count_change": 5}}})

    with pytest.raises(SystemExit) as caught:
        _run_against(
            monkeypatch,
            tree,
            {("benchmarks/results/old/run.json", "/drc"): "B-111 qualifies this"},
        )

    assert "publishes the differential 'error_count_change'" in str(caught.value)


# ---------------------------------------------------------------------------
# The runner registry
# ---------------------------------------------------------------------------


def test_a_registered_runner_that_stops_importing_the_gate_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    tree = _tree(tmp_path, {}, runners=set())
    for runner in check_drc_comparability.DRC_RECORDING_RUNNERS:
        path = tree / runner
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("import json\n", encoding="utf-8")

    with pytest.raises(SystemExit) as caught:
        _run_against(monkeypatch, tree)

    assert "does not import copper_mcp.benchmarks.drc_comparability" in str(caught.value)


def test_a_registered_runner_that_no_longer_exists_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    tree = _tree(tmp_path, {}, runners=set())

    with pytest.raises(SystemExit) as caught:
        _run_against(monkeypatch, tree)

    assert "registered as a DRC-recording runner but does not exist" in str(caught.value)
