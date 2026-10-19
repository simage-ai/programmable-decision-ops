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


def test_the_proposal_uses_three_of_the_scenario_figures(tmp_path):
    import importlib.util

    from conftest import ROOT

    from pdo.bench.scenario_figures import FIGURES

    spec = importlib.util.spec_from_file_location("proposal_figures", ROOT / "scripts" / "proposal_figures.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.PROPOSAL_FIGURES == ("trade-study", "program-refresh", "perturbations")
    written = module.copy_figures(ROOT / "results" / "scenarios", tmp_path)
    assert sorted(p.relative_to(tmp_path).as_posix() for p in written) == sorted(
        f"figures/{n}/{n}.{ext}" for n in module.PROPOSAL_FIGURES for ext in ("pdf", "png"))
    assert module.copy_figures(ROOT / "results" / "scenarios", tmp_path) == []
    assert module.undeclared(tmp_path) == list(module.PROPOSAL_FIGURES)
    PROPOSAL_FIGURES = module.PROPOSAL_FIGURES
    assert set(PROPOSAL_FIGURES) < set(FIGURES), "the others stay generated, outside the proposal's set"
