"""The research brief's worked trade study, reproduced exactly, and its four episode branches."""

from __future__ import annotations

import pytest
from conftest import assign, evaluate_state

from pdo.actions import pending_actions
from pdo.graph import compile_graph, impact_set
from pdo.run import equivalent, run


def test_utilities_values_and_feasibility(vehicle):
    r = evaluate_state(vehicle.state)
    assert r.value("U/alt:A")["U"] == pytest.approx(0.466667, abs=1e-6)
    assert r.value("U/alt:B")["U"] == pytest.approx(0.533333, abs=1e-6)
    assert r.value("U/alt:C")["U"] == pytest.approx(0.620000, abs=1e-6)
    for crit, expected in {"crit:range": 0.20, "crit:cost": 2 / 3, "crit:reliability": 0.60}.items():
        assert r.value(f"v/{crit}/alt:A")["v"] == pytest.approx(expected)
    for crit, expected in {"crit:range": 0.60, "crit:cost": 1 / 3, "crit:reliability": 0.80}.items():
        assert r.value(f"v/{crit}/alt:B")["v"] == pytest.approx(expected)
    assert r.value("y/alt:A/meas:range")["y"] == 320
    assert r.value("y/alt:C/meas:range")["y"] == 380
    d = r.decision
    assert d["feasible"] == ["alt:A", "alt:B"]
    assert d["infeasible"] == ["alt:C"]
    assert d["recommended"] == ["alt:B"]
    check = r.value("g/req:min-reliability/alt:C")
    assert check["status"] == "fail" and check["g"] == pytest.approx(0.01)


def test_ready_but_not_actionable_until_approved(vehicle):
    r = evaluate_state(vehicle.state)
    assert r.readiness["state"] == "ready"
    assert r.actionable["actionable"] is False
    actions = pending_actions(r)
    assert [(a.kind, a.target, a.role) for a in actions] == [("approve", "alt:B", "role:mda")]


BRANCHES = [
    # (target, slot, value, recommended, feasible, readiness)
    ("obs:B.cost", "value", 1.32, ["alt:A"], ["alt:A", "alt:B"], "ready"),
    ("req:min-range", "threshold_value", 350, ["alt:B"], ["alt:B"], "ready"),
    ("rev:framing", "review_status", "pending", ["alt:B"], ["alt:A", "alt:B"], "pending"),
    ("req:min-range", "threshold_value", 370, [], [], "blocked"),
]


@pytest.mark.parametrize(("target", "slot", "value", "recommended", "feasible", "readiness"), BRANCHES)
def test_brief_branches(vehicle, target, slot, value, recommended, feasible, readiness):
    r = evaluate_state(assign(vehicle.state, target, slot, value))
    assert r.decision["recommended"] == recommended
    assert r.decision["feasible"] == feasible
    assert r.readiness["state"] == readiness


def test_branch_numbers(vehicle):
    r = evaluate_state(assign(vehicle.state, "obs:B.cost", "value", 1.32))
    assert r.value("U/alt:B")["U"] == pytest.approx(0.453333, abs=1e-6)
    r = evaluate_state(assign(vehicle.state, "req:min-range", "threshold_value", 350))
    assert r.value("g/req:min-range/alt:A")["g"] == pytest.approx(30)
    r = evaluate_state(assign(vehicle.state, "req:min-range", "threshold_value", 370))
    assert "rc:recommendation-exists" in r.readiness["failed"]
    assert any(a.kind == "revise_alternatives" for a in pending_actions(r))


def test_bias_review_pending_routes_to_reviewer(vehicle):
    r = evaluate_state(assign(vehicle.state, "rev:framing", "review_status", "pending"))
    assert r.readiness["unknown"] == ["bc:framing-review"]
    assert ("complete_review", "rev:framing", "role:bias-reviewer") in {(a.kind, a.target, a.role)
                                                                         for a in pending_actions(r)}


def test_tie_at_the_cost_flip(vehicle):
    r = evaluate_state(assign(vehicle.state, "obs:B.cost", "value", 1.30))
    assert r.decision["recommended"] == ["alt:A", "alt:B"]
    assert r.readiness["state"] == "pending"


@pytest.mark.parametrize(("target", "slot", "value"), [(t, s, v) for t, s, v, *_ in BRANCHES]
                         + [("crit:cost", "weight", 0.55), ("asm:terrain-factor", "value", 0.9)])
def test_selective_runs_equal_full_recompute(vehicle, target, slot, value):
    if slot == "weight":
        state = assign(assign(vehicle.state, "crit:cost", "weight", 0.55), "crit:reliability", "weight", 0.05)
    else:
        state = assign(vehicle.state, target, slot, value)
    g0 = compile_graph(vehicle.state, vehicle.base_dir)
    r0 = run(g0)
    g1 = compile_graph(state, vehicle.base_dir)
    full = run(g1)
    impact = impact_set(g0, g1)
    changed = {n for n in g1.nodes if n not in r0.results or r0.results[n].vdig != full.results[n].vdig}
    assert changed <= impact
    assert equivalent(run(g1, "impact", r0), full)
    assert equivalent(run(g1, "cutoff", r0), full)


def test_requirement_change_does_not_rerun_the_range_model(vehicle):
    g0 = compile_graph(vehicle.state, vehicle.base_dir)
    r0 = run(g0)
    g1 = compile_graph(assign(vehicle.state, "req:min-range", "threshold_value", 350), vehicle.base_dir)
    selective = run(g1, "cutoff", r0)
    assert not [n for n in selective.executed if n.startswith("run/")]


def test_prose_edits_touch_nothing(vehicle):
    g0 = compile_graph(vehicle.state, vehicle.base_dir)
    g1 = compile_graph(assign(vehicle.state, "crit:cost", "description", "edited prose"), vehicle.base_dir)
    assert impact_set(g0, g1) == set()
