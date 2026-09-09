"""Generated local preferences are bounded disposable output, never source authority."""

import time

import pytest
from test_optimization_inputs import launch as launch
from test_optimization_native_import import imported_launch as imported_launch

from copper_mcp.optimization import native_import
from copper_mcp.optimization.native_import import NativeImportError, import_native_board


def _preference_output(settings, expression):
    cli = settings.kicad_cli
    cli.write_text(
        cli.read_text().replace("p.write_text(text)", "p.write_text(text)\n " + expression)
    )


def test_private_preferences_are_discarded_and_charged_before_strict_tree(
    imported_launch, monkeypatch
):
    value, settings = imported_launch
    _preference_output(settings, "p.with_suffix('.kicad_prl').write_bytes(b'not JSON; private')")
    original = (settings.workspace / value["board"]).read_bytes()
    real_preferences = settings.workspace / "board.kicad_prl"
    real_preferences.write_bytes(b"original workspace preferences")
    validate = native_import.kicad_cli._validate_snapshot_tree
    trees = []

    def strict(snapshot, files, limits):
        assert not snapshot.joinpath("board.kicad_prl").exists()
        trees.append(files)
        return validate(snapshot, files, limits)

    monkeypatch.setattr(native_import.kicad_cli, "_validate_snapshot_tree", strict)
    charges = []
    result = import_native_board(
        {value["board"]: original},
        value["board"],
        settings,
        deadline=time.monotonic() + 30,
        max_output_bytes=100_000,
        account_output=charges.append,
    )
    assert len(trees) == 2
    assert sum(charges) == result.output_bytes == 7 + 2 * (len(result.source) + 17)
    assert charges.count(17) == 2
    assert result.binding.discard_policy == "same-stem-private-kicad-prl/v1"
    assert (settings.workspace / value["board"]).read_bytes() == original
    assert real_preferences.read_bytes() == b"original workspace preferences"


@pytest.mark.parametrize(
    "expression",
    [
        "p.with_suffix('.kicad_prl').write_bytes(b'x'*100_000)",
        "p.with_suffix('.kicad_prl').symlink_to(p)",
        "p.with_suffix('.kicad_prl').hardlink_to(p)",
        "p.with_suffix('.kicad_prl').mkdir()",
        "p.with_suffix('.kicad_prl').write_bytes(b'{}'); "
        "p.with_suffix('.unknown').write_bytes(b'x')",
        "p.with_name('other.kicad_prl').write_bytes(b'{}')",
        "p.with_suffix('.KICAD_PRL').write_bytes(b'{}')",
    ],
)
def test_hostile_or_other_generated_output_still_refuses(imported_launch, expression):
    value, settings = imported_launch
    _preference_output(settings, expression)
    original = (settings.workspace / value["board"]).read_bytes()
    with pytest.raises(NativeImportError) as caught:
        import_native_board(
            {value["board"]: original},
            value["board"],
            settings,
            deadline=time.monotonic() + 30,
            max_output_bytes=100_000,
        )
    assert caught.value.__context__ is None
    assert (settings.workspace / value["board"]).read_bytes() == original


def test_captured_preferences_are_never_discarded(imported_launch):
    value, settings = imported_launch
    original = (settings.workspace / value["board"]).read_bytes()
    # The native fixture leaves this captured file alone: it is input, not generated output.
    context = {value["board"]: original, "board.kicad_prl": b"captured private preferences"}
    result = import_native_board(
        context,
        value["board"],
        settings,
        deadline=time.monotonic() + 30,
        max_output_bytes=100_000,
    )
    assert result.output_bytes == 7 + 2 * len(result.source)
    assert context["board.kicad_prl"] == b"captured private preferences"
