"""Use case 3, a decision under uncertainty: the synthetic counterfire radar example and pdo.uncertainty."""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys

import pytest
from conftest import ROOT, VEHICLE, assign
from typer.testing import CliRunner

from pdo import model as m
from pdo.cli import app
from pdo.episodes import Program
from pdo.graph import compile_graph
from pdo.load import index
from pdo.run import run
from pdo.uncertainty import (
    DecisionModel,
    UncertaintyError,
    analyze,
    analyze_path,
    analyze_state,
    apply_scenario,
    inverse_cdf,
    point_warnings,
    rule_settings,
    sample,
    stream,
    summary,
    uncertain_objects,
    uniforms,
)

FIRES = ROOT / "examples" / "fires_sensor"
runner = CliRunner()


@pytest.fixture(scope="module")
def baseline():
    return Program.load(FIRES).materialize("ep:f00")


@pytest.fixture(scope="module")
def result(baseline):
    """The full analysis at the rule's own settings (1000 draws, its seed, every scenario)."""
    return analyze(baseline, FIRES)


def own_numbers(stats) -> dict:
    """An alternative's statistics that depend only on its own draws (p_wins_draw compares it with others)."""
    return {k: v for k, v in stats.to_dict().items() if k != "p_wins_draw"}


def with_slots(state, changes: list[tuple[str, str, object]]):
    for target, slot, value in changes:
        state = assign(state, target, slot, value)
    return state


# --- the deterministic program --------------------------------------------------------------------


def test_validate_passes():
    out = runner.invoke(app, ["validate", str(FIRES), "--json"])
    assert out.exit_code == 0, out.output
    assert json.loads(out.output)["valid"]


def test_point_run_recommends_the_new_array():
    out = runner.invoke(app, ["run", str(FIRES), "--json"])
    assert out.exit_code == 0, out.output
    data = json.loads(out.output)
    assert data["recommended"] == ["alt:new-array"]
    assert data["feasible"] == ["alt:insert", "alt:new-array", "alt:refresh"]
    # The declared rule is expected_mavt: the point ranking cannot authorize anything until
    # `pdo uncertainty` decides, so readiness waits on that analysis.
    assert data["readiness"] == "pending" and data["actionable"] is False
    assert "run_uncertainty_analysis" in {a["kind"] for a in data["pending_actions"]}
    by_alt = {a["alternative"]: a for a in data["alternatives"]}
    # Spec: detection range = reference range x rcs ** 0.25, at the point rcs 1.0.
    assert by_alt["alt:new-array"]["measures"]["meas:detection-range"] == pytest.approx(31.0)
    # U = 0.35 (31-18)/14 + 0.25 (0.96-0.85)/0.13 + 0.2 (12-9)/8 + 0.2 (40-30)/30
    expected = 0.35 * 13 / 14 + 0.25 * 0.11 / 0.13 + 0.2 * 3 / 8 + 0.2 * 10 / 30
    assert by_alt["alt:new-array"]["utility"] == pytest.approx(expected)
    assert by_alt["alt:insert"]["utility"] < by_alt["alt:new-array"]["utility"]


# --- distributions and streams --------------------------------------------------------------------


def test_inverse_cdfs_are_the_closed_forms():
    assert inverse_cdf("uniform", {"low": 18, "high": 26}, 0.5) == pytest.approx(22)
    tri = {"low": 0.0, "mode": 1.0, "high": 4.0}
    assert inverse_cdf("triangular", tri, 0.25) == pytest.approx(1.0)  # F(mode) = (mode - low) / (high - low)
    for u in (0.1, 0.6, 0.9):  # round trip through the triangular CDF
        x = inverse_cdf("triangular", tri, u)
        cdf = x ** 2 / 4 if x <= 1 else 1 - (4 - x) ** 2 / 12
        assert cdf == pytest.approx(u)
    assert inverse_cdf("normal", {"mean": 3.0, "sd": 2.0}, 0.5) == pytest.approx(3.0)
    assert inverse_cdf("normal", {"mean": 0.0, "sd": 1.0}, 0.975) == pytest.approx(1.959964, abs=1e-6)
    assert math.isfinite(inverse_cdf("normal", {"mean": 0.0, "sd": 1.0}, 0.0))  # random() can return 0.0
    rng = stream(1, "lognormal")
    xs = [inverse_cdf("lognormal", {"mean": 10.0, "sd": 3.0}, rng.random()) for _ in range(20000)]
    assert min(xs) > 0 and sum(xs) / len(xs) == pytest.approx(10.0, rel=0.02)


def test_streams_are_per_object_seeded_and_prefix_stable():
    assert stream(0, "obs:x").random() == 0.17173068325278118  # pinned: digest seeding + Mersenne Twister
    both = uniforms(7, ["obs:a", "obs:b"], 50)
    assert uniforms(7, ["obs:b"], 50)["obs:b"] == both["obs:b"]  # other objects do not reshuffle a stream
    assert uniforms(7, ["obs:a"], 20)["obs:a"] == both["obs:a"][:20]  # more draws keep a prefix
    assert uniforms(8, ["obs:a"], 50)["obs:a"] != both["obs:a"]
    assert both["obs:a"] != both["obs:b"]


def test_draws_do_not_depend_on_the_hash_seed():
    code = ("from pdo.uncertainty import analyze_path; "
            "print(analyze_path('examples/fires_sensor', samples=20, scenarios=False).draws_digest)")
    outs = {subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True, cwd=ROOT,
                           env={**os.environ, "PYTHONHASHSEED": seed}).stdout for seed in ("0", "1", "4242")}
    assert len(outs) == 1


def test_uncertain_inputs_and_their_point_values(baseline):
    uncertain = {u.id: u for u in uncertain_objects(baseline)}
    assert len(uncertain) == 13 and "asm:threat-rcs-factor" in uncertain
    assert {u.family for u in uncertain.values()} == {"normal", "triangular", "uniform"}
    assert point_warnings(list(uncertain.values())) == []  # every point value is the mode or the mean
    off = with_slots(baseline, [("obs:insert.cost", "value", 8.0)])
    assert any("obs:insert.cost" in w for w in point_warnings(uncertain_objects(off)))


def test_invalid_distributions_are_rejected(baseline):
    bad = with_slots(baseline, [("obs:insert.cost", "uncertainty", {"family": "triangular", "low": 7, "high": 9})])
    with pytest.raises(UncertaintyError, match="mode"):
        uncertain_objects(bad)
    bad = with_slots(baseline, [("obs:insert.range", "uncertainty", {"family": "normal", "mean": 26, "sd": 0})])
    with pytest.raises(UncertaintyError, match="sd > 0"):
        uncertain_objects(bad)


# --- each draw is a kernel evaluation -------------------------------------------------------------


def test_a_draw_equals_the_kernel_on_the_assigned_state(baseline):
    """The fast path (patched source nodes, cutoff run) gives exactly what a fresh compile and full run give."""
    uncertain = uncertain_objects(baseline)
    model = DecisionModel(baseline, FIRES, [u.id for u in uncertain])
    values = sample(uncertain, uniforms(3, [u.id for u in uncertain], 4))
    for k in range(4):
        draw = {oid: xs[k] for oid, xs in values.items()}
        fast = model.evaluate(draw)
        state = with_slots(baseline, [(oid, "value", x) for oid, x in draw.items()])
        full = run(compile_graph(state, FIRES))
        for nid in model.graph.nodes:
            assert fast.results[nid].vdig == full.results[nid].vdig, (k, nid)
            assert fast.results[nid].sig == full.results[nid].sig, (k, nid)
        statuses = {r.exec_status for r in fast.results.values()}
        assert "reused" in statuses and "executed" in statuses  # selective, not a full rerun


def test_alternatives_share_the_assumption_draw(baseline):
    uncertain = uncertain_objects(baseline)
    model = DecisionModel(baseline, FIRES, [u.id for u in uncertain])
    values = sample(uncertain, uniforms(5, [u.id for u in uncertain], 3))
    for k in range(3):
        result = model.evaluate({oid: xs[k] for oid, xs in values.items()})
        factor = values["asm:threat-rcs-factor"][k] ** 0.25
        for alt in ("refresh", "new-array", "insert"):
            detection = result.value(f"y/alt:{alt}/meas:detection-range")["y"]
            assert detection == pytest.approx(values[f"obs:{alt}.range"][k] * factor)


# --- the expected_mavt decision --------------------------------------------------------------------


def test_chance_constraint_changes_the_recommendation(result):
    s = summary(result)
    assert s["rule"]["samples"] == 1000 and s["rule"]["chance_alpha"] == pytest.approx(0.10)
    assert s["deterministic"]["recommended"] == ["alt:new-array"]
    assert s["recommended"] == ["alt:insert"] and not s["agrees_with_deterministic"]
    assert s["unconstrained"] == ["alt:new-array"]  # expected value alone still prefers it
    stats = {a["alternative"]: a for a in s["alternatives"]}
    new = stats["alt:new-array"]
    assert new["chance"] == "fail" and new["p_feasible"] < 0.65
    assert new["p_pass"]["req:max-schedule"] < 0.65  # the binding requirement
    assert new["p_feasible"] <= min(new["p_pass"].values())  # joint, not marginal
    assert stats["alt:insert"]["chance"] == "pass" and stats["alt:insert"]["p_feasible"] > 0.99
    assert stats["alt:refresh"]["chance"] == "pass" and stats["alt:refresh"]["p_feasible"] > 0.93
    assert s["chance_feasible"] == ["alt:insert", "alt:refresh"] and s["chance_infeasible"] == ["alt:new-array"]
    runner_up = s["runner_up"]
    assert runner_up["alternative"] == "alt:refresh" and runner_up["margin"] > 5 * runner_up["margin_se"]
    for a in stats.values():
        assert a["point_feasibility"] == "pass" and a["utility_se"] > 0
        assert a["expected_utility"] < a["point_utility"]  # right-skewed costs and schedules, clipped values
        assert a["utility_bounds"][0] == pytest.approx(a["expected_utility"])
    assert sum(a["p_wins_draw"] for a in stats.values()) == pytest.approx(1.0)


def test_robust_over_threat_environments(result):
    scenarios = {s.scenario: s for s in result.scenarios}
    assert result.robust is True
    assert all(s.analysis.recommended == ["alt:insert"] for s in result.scenarios)
    assert all(s.analysis.point["recommended"] == ["alt:new-array"] for s in result.scenarios)
    # A small threat shortens every range; the refresh then misses the detection-range requirement.
    small = scenarios["sc:small-rocket"].analysis
    assert small.chance_feasible == ["alt:insert"] and small.chance_infeasible == ["alt:new-array", "alt:refresh"]
    assert small.alternatives["alt:refresh"].p_pass["req:min-detection-range"] < 0.5
    assert scenarios["sc:nominal"].analysis is result.baseline  # no assignments: not sampled twice


def test_scenarios_condition_on_what_they_set(baseline):
    sc = {s.id: s for s in baseline.scenarios}
    state, fixed = apply_scenario(baseline, sc["sc:reference-threat"])
    assert fixed == {"asm:threat-rcs-factor"}
    assert "asm:threat-rcs-factor" not in {u.id for u in uncertain_objects(state, fixed)}
    state, fixed = apply_scenario(baseline, sc["sc:small-rocket"])
    assert fixed == set()  # its uncertainty is respecified, not fixed
    assert index(state)["asm:threat-rcs-factor"].uncertainty.mode == pytest.approx(0.6)
    assert index(baseline)["asm:threat-rcs-factor"].value == 1.0  # the point state is not modified
    bad = m.Scenario(id="sc:bad", assignments=[{"target": "asm:nope", "slot": "value", "new_value": 1.0}])
    with pytest.raises(UncertaintyError, match="asm:nope"):
        apply_scenario(baseline, bad)
    bad = m.Scenario(id="sc:bad", assignments=[{"target": "asm:threat-rcs-factor", "slot": "value",
                                                 "new_value": "high"}])
    with pytest.raises(UncertaintyError, match="sc:bad"):
        apply_scenario(baseline, bad)


def test_same_seed_gives_identical_results(baseline):
    a = summary(analyze(baseline, FIRES, samples=150, seed=99))
    b = summary(analyze(baseline, FIRES, samples=150, seed=99))
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    c = summary(analyze(baseline, FIRES, samples=150, seed=100, scenarios=False))
    assert c["draws_digest"] != a["draws_digest"]


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5, 2027])
def test_the_decision_holds_across_seeds(baseline, seed):
    s = summary(analyze(baseline, FIRES, samples=400, seed=seed, scenarios=False))
    assert s["recommended"] == ["alt:insert"]
    assert s["chance_infeasible"] == ["alt:new-array"]
    assert s["unconstrained"] == ["alt:new-array"]


def test_changing_one_object_leaves_the_others_draws(baseline):
    """Common random numbers: alternatives whose inputs did not change get identical statistics."""
    rule = rule_settings(baseline, samples=200)
    edited = with_slots(baseline, [("obs:refresh.cost", "uncertainty",
                                    {"family": "triangular", "low": 4.0, "mode": 5.0, "high": 9.0})])
    oids = sorted(u.id for u in uncertain_objects(baseline))
    draws = uniforms(rule["seed"], oids, rule["samples"])
    before = analyze_state(baseline, FIRES, rule, draws).alternatives
    after = analyze_state(edited, FIRES, rule, draws).alternatives
    assert before["alt:refresh"].expected_utility != after["alt:refresh"].expected_utility
    for alt in ("alt:insert", "alt:new-array"):
        assert own_numbers(before[alt]) == own_numbers(after[alt])


def test_episode_that_narrows_the_schedule_flips_the_expected_decision():
    base = analyze_path(FIRES, samples=400, scenarios=False)
    later = analyze_path(FIRES, "ep:f01", samples=400, scenarios=False)
    assert later.recommended == ["alt:new-array"] == later.deterministic.decision["recommended"]
    assert later.baseline.alternatives["alt:new-array"].chance == "pass"
    for alt in ("alt:insert", "alt:refresh"):  # same draws, so unchanged options give identical numbers
        assert own_numbers(later.baseline.alternatives[alt]) == own_numbers(base.baseline.alternatives[alt])


# --- unknowns and defaults -------------------------------------------------------------------------


def test_unknown_draws_leave_an_alternative_unresolved(baseline):
    state = with_slots(baseline, [("obs:insert.cost", "applicability", "provisional")])
    s = summary(analyze(state, FIRES, samples=50, scenarios=False))
    insert = next(a for a in s["alternatives"] if a["alternative"] == "alt:insert")
    assert insert["chance"] == "unknown" and insert["p_unknown"] == 1.0
    assert insert["expected_utility"] is None and insert["utility_bounds"][0] < insert["utility_bounds"][1]
    assert "alt:insert" in s["unresolved"] and s["recommended"] == ["alt:refresh"]


def test_without_alpha_the_constraint_is_strict(baseline):
    state = with_slots(baseline, [("rule:main", "chance_alpha", None)])
    s = summary(analyze(state, FIRES, samples=300, scenarios=False))
    assert s["rule"]["chance_alpha"] == 0.0
    assert s["chance_feasible"] == ["alt:insert"]  # the refresh misses range in a few draws


def test_without_uncertainty_the_expected_rule_is_the_point_rule():
    r = analyze_path(VEHICLE, samples=5)
    assert r.baseline.uncertain == []
    assert r.recommended == r.deterministic.decision["recommended"] == ["alt:B"]
    for s in r.baseline.alternatives.values():
        assert s.p_feasible in (0.0, 1.0) and s.utility_se == pytest.approx(0.0, abs=1e-12)
        assert s.expected_utility == pytest.approx(s.point_utility)
    assert any("additive_mavt" in w for w in r.warnings)


# --- command line ----------------------------------------------------------------------------------


def test_cli_json():
    out = runner.invoke(app, ["uncertainty", str(FIRES), "--samples", "200", "--seed", "11", "--json"])
    assert out.exit_code == 0, out.output
    data = json.loads(out.output)
    assert data["ok"] and data["rule"]["samples"] == 200 and data["rule"]["seed"] == 11
    assert data["recommended"] == ["alt:insert"] and data["deterministic"]["recommended"] == ["alt:new-array"]
    assert data["robust"] is True and len(data["scenarios"]) == 4


def test_cli_text_and_episode():
    out = runner.invoke(app, ["uncertainty", str(FIRES), "-n", "100"])
    assert out.exit_code == 0, out.output
    assert out.output.startswith("# alt:insert") and "differs from the expected-value decision" in out.output
    out = runner.invoke(app, ["uncertainty", str(FIRES), "--episode", "ep:f01", "-n", "200", "--json"])
    assert out.exit_code == 0, out.output
    assert json.loads(out.output)["recommended"] == ["alt:new-array"]


def test_cli_errors():
    out = runner.invoke(app, ["uncertainty", str(FIRES), "--episode", "ep:nope", "--json"])
    assert out.exit_code == 1 and json.loads(out.output)["ok"] is False
    out = runner.invoke(app, ["uncertainty", str(FIRES / "baseline"), "--episode", "ep:f00", "--json"])
    assert out.exit_code == 1 and "program directory" in json.loads(out.output)["error"]
    out = runner.invoke(app, ["uncertainty", str(FIRES), "--samples", "0", "--json"])
    assert out.exit_code == 1 and "samples" in json.loads(out.output)["error"]
