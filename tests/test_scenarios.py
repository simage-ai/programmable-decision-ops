"""The paper's scenarios (pdo.bench.scenarios) as tests: every claim each one supports must hold."""

from __future__ import annotations

import pytest

from pdo.bench.scenarios import SCENARIOS, run_scenario, write_results

pytest.importorskip("docx")
pytest.importorskip("pypdf")


@pytest.fixture(scope="module")
def results():
    return {}


@pytest.mark.parametrize("name", list(SCENARIOS))
def test_scenario_claims_hold(name, results):
    result = run_scenario(name)
    results[name] = result
    failed = [f"{c.claim}: expected {c.expected}, observed {c.observed}" for c in result.claims if not c.holds]
    assert result.claims and not failed, failed
    assert result.tables, "a scenario records the data behind its figure"


def test_scenarios_write_tables_and_figures(tmp_path, results):
    pytest.importorskip("matplotlib")
    chosen = [results.get(n) or run_scenario(n) for n in ("trade-study", "program-refresh")]
    written = write_results(chosen, tmp_path)
    names = {p.name for p in written}
    assert {"trade-study.png", "trade-study.pdf", "program-refresh.png", "caption.md", "claims.tsv",
            "values.tsv"} <= names
    assert not write_results(chosen, tmp_path), "unchanged results are not rewritten"
