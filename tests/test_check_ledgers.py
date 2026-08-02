from __future__ import annotations

from pathlib import Path

import pytest

from scripts import check_ledgers


@pytest.mark.parametrize("number", ["1e999", "-1e999"])
def test_benchmark_artifacts_reject_overflowing_json_numbers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    number: str,
) -> None:
    results = tmp_path / "benchmarks" / "results"
    results.mkdir(parents=True)
    (results / "overflow.json").write_text(
        '{"run_id":"sha256:' + ("0" * 64) + '","runtime_seconds":' + number + "}",
        encoding="utf-8",
    )
    monkeypatch.setattr(check_ledgers, "ROOT", tmp_path)

    failures: list[str] = []
    check_ledgers._check_benchmark_artifacts(failures)

    assert len(failures) == 1
    assert "is not strict JSON: non-finite JSON number" in failures[0]
