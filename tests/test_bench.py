"""The representation benchmark on a small suite: three vehicle perturbations written to a tmp dir.

Parent ep:e01 is the baseline plus the MDA's approval of B (ready and actionable). The cases cover a
changed winner (B's cost 1.32), a readiness-only change (the framing review reopened) and a prose-only
edit that changes nothing, with labels taken from docs/semantics.md.
"""

from __future__ import annotations

import importlib.util
import json

import pytest
import yaml
from conftest import ROOT, VEHICLE
from typer.testing import CliRunner

from pdo.bench import report
from pdo.bench import strategies as st
from pdo.bench.metrics import Rate, precision_recall
from pdo.bench.suite import SuiteError, disagreements, load_suite, run_suite
from pdo.cli import app
from pdo.graph import compile_graph, delta, impact_set
from pdo.load import index

runner = CliRunner()

CASES = [
    {"id": "b-cost-1p32", "family": "cost", "split": "dev",
     "change": {"assign": [{"target": "obs:B.cost", "slot": "value", "new_value": 1.32}]},
     "expect": {"outcome": "changed_winner", "recommended": ["alt:A"], "readiness": "ready", "winner_changed": True,
                "readiness_violation": False, "approval_invalidated": True}},
    {"id": "framing-review-pending", "family": "review", "split": "heldout",
     "change": {"assign": [{"target": "rev:framing", "slot": "review_status", "new_value": "pending"}]},
     "expect": {"outcome": "readiness_only", "recommended": ["alt:B"], "readiness": "pending",
                "winner_changed": False, "readiness_violation": True, "approval_invalidated": True}},
    {"id": "prose-only-edit", "family": "no_change", "split": "dev",
     "change": {"assign": [{"target": "alt:B", "slot": "description", "new_value": "Edited prose only."}]},
     "expect": {"outcome": "no_material_change", "recommended": ["alt:B"], "readiness": "ready",
                "winner_changed": False, "readiness_violation": False, "approval_invalidated": False}},
]


def write_suite(directory, cases=CASES, **extra):
    directory.mkdir(parents=True, exist_ok=True)
    data = {"suite": "mini", "program": str(VEHICLE), "parent": "ep:e01", "cases": cases, **extra}
    (directory / "perturbations.yaml").write_text(yaml.safe_dump(data, sort_keys=False))
    return directory


@pytest.fixture(scope="module")
def suite_dir(tmp_path_factory):
    return write_suite(tmp_path_factory.mktemp("bench") / "mini")


@pytest.fixture(scope="module")
def result(suite_dir):
    return run_suite(suite_dir, edge_p=(0.10,), edge_seeds=3, timeline=False, flips=False)


def case(result, cid):
    return next(c for c in result.cases if c.case.id == cid)


def test_small_suite_agrees_with_labels_and_selective_equals_full(result):
    assert [c.case.id for c in result.cases] == [c["id"] for c in CASES]
    assert disagreements(result) == []
    assert result.label_agreement == Rate(3, 3)
    summary = result.summary()
    for name in ("full", "impact", "cutoff"):
        assert summary[name]["equivalent"] == Rate(3, 3)
        assert summary[name]["sound"] == Rate(3, 3)
    assert result.passed


def test_change_set_id_and_parent_follow_the_suite(result):
    change = case(result, "b-cost-1p32").change
    assert (change.id, change.parent) == ("ep:bench-b-cost-1p32", "ep:e01")


def test_metrics_of_the_cost_case(result):
    c = case(result, "b-cost-1p32")
    impact, cutoff, full = c.strategies["impact"], c.strategies["cutoff"], c.strategies["full"]
    # y, g, v, U of B, the decision, the state digest and actionable change; nothing else does.
    assert impact.changed == len(c.changed) == 7
    assert impact.executed == impact.scope < impact.computed == full.executed
    assert cutoff.executed <= impact.executed
    assert impact.savings.value == pytest.approx(1 - impact.executed / impact.computed)
    assert impact.unneeded == impact.executed - impact.changed
    assert impact.detects_winner_change and not impact.detects_readiness_violation


def test_no_change_executes_nothing(result):
    c = case(result, "prose-only-edit")
    assert c.changed == frozenset()
    assert c.strategies["impact"].executed == 0
    assert c.strategies["impact"].overapprox.value is None  # 0 / 0 stays undefined


def test_ablations_miss_what_the_representation_catches(result):
    cost, review = case(result, "b-cost-1p32"), case(result, "framing-review-pending")
    assert not cost.strategies["delta_only"].equivalent and not cost.strategies["delta_only"].sound
    assert not cost.strategies["delta_only"].detects_winner_change
    no_readiness = review.strategies["no_readiness"]
    assert not no_readiness.equivalent and not no_readiness.detects_readiness_violation
    assert review.strategies["impact"].detects_readiness_violation


def test_edge_omission_is_seeded_and_bounded(result):
    c = case(result, "b-cost-1p32")
    assert len(c.edges) == 3  # one p, three seeds
    parent, child = result.parent.graph, c.full.graph
    d = delta(parent, child)
    first = st.scope_edge_omission(parent, child, d, 0.1, st.edge_rng("x", 0.1, 0))
    assert first == st.scope_edge_omission(parent, child, d, 0.1, st.edge_rng("x", 0.1, 0))
    assert st.scope_edge_omission(parent, child, d, 0.0, st.edge_rng("x", 0.0, 0))[0] == impact_set(parent, child, d)
    assert st.scope_edge_omission(parent, child, d, 1.0, st.edge_rng("x", 1.0, 0))[0] == d.roots & set(child.nodes)


def test_wrong_label_is_a_finding_and_fails_the_gate(tmp_path):
    wrong = [{**CASES[0], "expect": {**CASES[0]["expect"], "readiness": "pending"}}]
    result = run_suite(write_suite(tmp_path / "wrong", wrong), edge_p=(), timeline=False, flips=False)
    (found,) = disagreements(result)
    assert found["fields"] == ["readiness"] and found["computed"] == {"readiness": "ready"}
    assert not result.passed
    out = runner.invoke(app, ["bench", "check", str(tmp_path / "wrong")])
    assert out.exit_code == 1 and "FAIL label_agreement: 0/1" in out.output


def test_rejected_change_is_reported_not_raised(tmp_path):
    bad = [{**CASES[0], "change": {"assign": [{"target": "obs:nope", "slot": "value", "new_value": 1}]}}]
    result = run_suite(write_suite(tmp_path / "bad", bad), edge_p=(), timeline=False, flips=False)
    assert result.cases[0].error and "obs:nope" in result.cases[0].error
    assert not result.passed


def test_suite_loader_rejects_malformed_suites(tmp_path):
    with pytest.raises(SuiteError, match="duplicate case id"):
        load_suite(write_suite(tmp_path / "dup", [CASES[0], CASES[0]]))
    with pytest.raises(SuiteError, match="missing expect"):
        load_suite(write_suite(tmp_path / "noexpect", [{k: v for k, v in CASES[0].items() if k != "expect"}]))
    with pytest.raises(SuiteError, match="unknown expect fields"):
        load_suite(write_suite(tmp_path / "field", [{**CASES[0], "expect": {"winner": True}}]))


def test_suite_digest_ignores_formatting(tmp_path):
    a = load_suite(write_suite(tmp_path / "a"))
    text = (tmp_path / "a" / "perturbations.yaml").read_text()
    (tmp_path / "b").mkdir()
    (tmp_path / "b" / "perturbations.yaml").write_text("# a comment\n" + text)
    assert load_suite(tmp_path / "b").digest == a.digest


def test_table_writers_are_idempotent(result, tmp_path):
    first = report.write_tables(result, tmp_path)
    assert first and all(first.values())
    contents = {path: open(path, encoding="utf-8").read() for path in first}
    second = report.write_tables(result, tmp_path)
    assert set(second) == set(first) and not any(second.values())
    assert {path: open(path, encoding="utf-8").read() for path in second} == contents
    cases = (tmp_path / "bench" / "mini" / "cases.tsv").read_text().splitlines()
    assert len(cases) == 1 + 3 * len(st.STRATEGIES)
    assert cases[1].split("\t")[:4] == ["b-cost-1p32", "cost", "dev", "full"]


def test_tables_do_not_depend_on_the_run(result, suite_dir, tmp_path):
    again = run_suite(suite_dir, edge_p=(0.10,), edge_seeds=3, timeline=False, flips=False)
    for name, render in report.TABLES.items():
        if name not in ("timing.tsv", "timeline.tsv", "flips.tsv"):
            assert render(again) == render(result), name


def test_figure_writer_is_idempotent(result, tmp_path):
    pytest.importorskip("matplotlib")
    first = report.write_figures(result, tmp_path)
    assert sorted(p.rsplit("/", 1)[-1] for p in first) == ["edge_omission.png", "impact_per_case.png",
                                                          "savings_by_family.png"]
    assert not any(report.write_figures(result, tmp_path).values())


def test_precision_recall_reports_undefined_denominators():
    p, r = precision_recall([(True, False), (False, False)])
    assert (p.num, p.den, p.value) == (0, 0, None)
    assert (r.num, r.den, r.value) == (0, 1, 0.0)


def test_cli_run_json(suite_dir, tmp_path):
    out = runner.invoke(app, ["bench", "run", str(suite_dir), "--results", str(tmp_path), "--no-figures",
                              "--seeds", "1", "--json"])
    assert out.exit_code == 0, out.output
    data = json.loads(out.output)
    assert data["ok"] and data["passed"] and data["cases"] == 3
    assert data["label_agreement"] == {"value": 1.0, "numerator": 3, "denominator": 3}
    assert data["timeline"]["matches_expected"]["value"] == 1.0
    assert (tmp_path / "bench" / "mini" / "timeline.tsv").exists()
    rerun = runner.invoke(app, ["bench", "run", str(suite_dir), "--results", str(tmp_path), "--no-figures",
                                "--seeds", "1", "--json"])
    assert sorted(json.loads(rerun.output)["written"]) == [str(tmp_path / "bench" / "mini" / "timing.tsv")]


def test_cli_check_passes_on_correct_labels(suite_dir):
    out = runner.invoke(app, ["bench", "check", str(suite_dir)])
    assert out.exit_code == 0, out.output
    assert "ok   impact_equivalent: 3/3" in out.output


def test_timeline_and_flips_of_the_vehicle_program(suite_dir):
    result = run_suite(suite_dir, edge_p=(), timeline=True, flips=True)
    rows = {r.episode: r for r in result.timeline}
    assert rows["ep:e04"].outcome == "changed_winner" and rows["ep:e04"].bounds["alt:B"] == pytest.approx((0.34, 0.54))
    assert all(r.matches_expected for r in result.timeline)
    assert all(v for r in result.timeline if r.parent for v in r.verified.values())
    values = proposal_values().values(result, ROOT)
    keys = {k: v for k, v, *_ in values}
    assert [k for k, *_ in values] == sorted(keys)
    assert keys["PDO_EX_FLIP_COST_B"] == "1.30" and keys["PDO_EX_FLIP_WEIGHT"] == "0.525"
    assert (keys["PDO_EX_U_A"], keys["PDO_EX_U_B"], keys["PDO_EX_U_C"]) == ("0.466667", "0.533333", "0.620000")
    assert keys["PDO_BENCH_CASES"] == "3" and keys["PDO_EQUIV_PCT"] == "100.0"
    assert keys["PDO_PROGRAM_EPISODES"] == str(len(result.timeline))
    required = {"PDO_BENCH_FAMILIES", "PDO_CUTOFF_EQUIV_PCT", "PDO_SOUND_PCT", "PDO_GRAPH_NODES",
                "PDO_IMPACT_MEDIAN_FRAC", "PDO_SAVINGS_PCT", "PDO_SAVINGS_COST_PCT", "PDO_CUTOFF_SAVINGS_PCT",
                "PDO_UNNEEDED_RECOMP_PCT", "PDO_WINNER_P", "PDO_WINNER_R", "PDO_READY_VIOL_P", "PDO_READY_VIOL_R",
                "PDO_ABL_NO_READINESS_RECALL", "PDO_LABEL_AGREE_PCT", "PDO_PROGRAM_FLIPS", "PDO_SCHEMA_CLASSES",
                "PDO_EXAMPLE_YAML_LINES"}
    assert required <= set(keys)
    assert "PDO_EDGE10_EQUIV_PCT" not in keys  # only with the edge-omission sweep


def test_unhashable_edit_leaves_graph_signatures_alone(result):
    # The benchmark's no_change family relies on this: prose edits compile to an identical graph.
    c = case(result, "prose-only-edit")
    parent_state = result.parent.graph.state
    edited = parent_state.model_copy(deep=True)
    index(edited)["alt:B"].description = "something else"
    assert delta(result.parent.graph, compile_graph(edited, VEHICLE)).roots == frozenset()
    assert c.delta.roots == frozenset()


def proposal_values():
    spec = importlib.util.spec_from_file_location("proposal_values", ROOT / "scripts" / "proposal_values.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
