from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = ROOT / "benchmarks/results/board-ir/2026-08-17-step4-fourth-pass-root-dimension-v1.json"


def test_embedded_predeclaration_authenticates_its_exact_canonical_bytes() -> None:
    report = json.loads(ARTIFACT.read_text(encoding="utf-8"))
    predeclared = dict(report["predeclaration"])
    recorded = predeclared.pop("sha256")
    canonical = json.dumps(
        predeclared,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()

    assert recorded == hashlib.sha256(canonical).hexdigest()


def test_fourth_pass_artifact_applies_the_predeclared_stop_rule() -> None:
    report = json.loads(ARTIFACT.read_text(encoding="utf-8"))

    predeclared = report["predeclaration"]
    metrics = report["metrics"]
    probed = [row for row in metrics["boards"] if row["fourth_pass"]["probed"]]

    assert predeclared["predicted_subset_converted"] == 4
    assert predeclared["predicted_subset_boards"] == 4
    assert metrics["fourth_pass_candidates"] == 4
    assert metrics["fourth_pass_probed"] == 4
    assert metrics["fourth_pass_converted"] == 0
    assert metrics["aggregate_corpus_converted"] == 0
    assert metrics["prediction_met"] is False
    assert metrics["source_hashes_unchanged"] is True
    assert metrics["committed_board_bytes"] == 0
    assert len(probed) == 4
    assert {row["fourth_pass"]["outcome"]["source_locator"] for row in probed} == {
        "kicad_pcb.setup"
    }
    assert {row["fourth_pass"]["outcome"]["message"] for row in probed} == {
        "expression contains an unsupported semantic field"
    }
    assert "stop rule applies" in report["interpretation"]
