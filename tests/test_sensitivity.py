"""Sensitivity analysis: probes equal compiled states, curves match the brief, indices match analytic values."""

from __future__ import annotations

import json
import math
import random
from pathlib import Path

import pytest
from conftest import ROOT, VEHICLE, assign, evaluate_state
from typer.testing import CliRunner

from pdo import model as m
from pdo.cli import app
from pdo.graph import compile_graph
from pdo.load import index, load_state
from pdo.run import run
from pdo.sensitivity import (
    Probe,
    SensitivityError,
    analyze,
    distribution_response,
    find_parameter,
    global_sensitivity,
    joint_sample,
    response_curve,
    tornado,
    value_of_information,
)

FIRES = ROOT / "examples" / "fires_sensor"
runner = CliRunner()


@pytest.fixture
def vehicle_state(vehicle):
    return vehicle.state


def fires_state():
    from pdo.episodes import Program

    program = Program.load(FIRES)
    return program.materialize(program.baseline_episode), program.base_dir


def full_values(state, base_dir):
    r = run(compile_graph(state, base_dir))
    return {n: r.results[n].vdig for n in r.graph.nodes if n.startswith(("U/", "F/", "decision"))}


@pytest.mark.parametrize(("pid", "x"), [("obs:B.cost.value", 1.27), ("req:min-range.threshold_value", 340.0),
                                         ("crit:cost.anchor_worst", 1.5), ("asm:terrain-factor.value", 0.9)])
def test_a_probe_equals_compiling_the_assigned_state(vehicle_state, pid, x):
    probe = Probe(vehicle_state, VEHICLE)
    param = find_parameter(vehicle_state, pid)
    result = probe.run([(param, x)])
    expected = full_values(assign(vehicle_state, param.target, param.slot, x), VEHICLE)
    assert {n: result.results[n].vdig for n in expected} == expected


def test_a_probe_of_an_evaluator_parameter_equals_the_compiled_state():
    state, base = fires_state()
    state = assign(state, "asm:threat-rcs-factor", "value", 0.8)  # at 1.0 the exponent has no effect
    probe = Probe(state, base)
    param = find_parameter(state, "spec:detection-range.range_exponent")
    result = probe.run([(param, 0.3)])
    changed = state.model_copy(deep=True)
    spec = index(changed)["spec:detection-range"]
    spec.parameters = [m.Parameter(arg=p.arg, number=0.3 if p.arg == "range_exponent" else p.number, unit=p.unit)
                       for p in spec.parameters]
    expected = full_values(changed, base)
    assert {n: result.results[n].vdig for n in expected} == expected


def test_the_cost_response_reproduces_the_brief(vehicle_state):
    probe = Probe(vehicle_state, VEHICLE)
    curve = response_curve(probe, find_parameter(vehicle_state, "obs:B.cost.value"))
    (change,) = curve["changes"]
    # The recommendation leaves {B} where U_B - U_A falls below the tie tolerance (1e-9 of utility), i.e.
    # 1e-9 / (0.4/0.6) MUSD before the exact crossing at 1.30.
    assert change["at"] == pytest.approx(1.30, abs=2e-9)
    assert change["from"] == ["alt:B"] and change["to"] == ["alt:A"]
    shape = curve["shape"]["alt:B"]
    assert shape["slope"] == pytest.approx(-0.4 / 0.6)
    assert shape["elasticity"] == pytest.approx(-0.4 / 0.6 * 1.2 / (8 / 15))
    assert shape["relative_deviation"] < 1e-9  # linear between the anchors
    assert curve["shape"]["alt:A"]["slope"] == 0


def test_a_nonlinear_evaluator_gives_a_curved_response():
    """Detection range follows the radar equation (range ~ RCS ** 0.25), so utility is not linear in RCS."""
    state, base = fires_state()
    probe = Probe(state, base)
    curve = response_curve(probe, find_parameter(state, "asm:threat-rcs-factor.value"))
    shape = curve["shape"]["alt:new-array"]
    assert shape["slope"] > 0
    assert 0.2 < shape["elasticity"] < 0.35
    assert shape["relative_deviation"] > 0.05


def test_the_exponent_matters_only_away_from_unit_rcs():
    state, base = fires_state()
    at_one = response_curve(Probe(state, base), find_parameter(state, "spec:detection-range.range_exponent"))
    assert all(s["slope"] == 0 for s in at_one["shape"].values())
    smaller = assign(state, "asm:threat-rcs-factor", "value", 0.8)
    away = response_curve(Probe(smaller, base), find_parameter(smaller, "spec:detection-range.range_exponent"))
    assert away["shape"]["alt:new-array"]["slope"] < 0  # 0.8 ** e falls as e grows


def test_distribution_response_is_reproducible_and_consistent(vehicle_state):
    probe = Probe(vehicle_state, VEHICLE)
    param = find_parameter(vehicle_state, "obs:B.cost.value")
    a = distribution_response(probe, param, samples=2000, seed=11)
    b = distribution_response(Probe(vehicle_state, VEHICLE), param, samples=2000, seed=11)
    assert a == b
    assert sum(x["p_win"] for x in a["alternatives"].values()) == pytest.approx(1.0)
    # uniform on [1.08, 1.32]: B loses above 1.30, with probability 0.02 / 0.24
    assert a["p_decision_changes"] == pytest.approx(0.02 / 0.24, abs=0.025)
    assert a["alternatives"]["alt:C"]["p_feasible"] == 0


def test_tornado_ranks_the_flipping_input_first(vehicle_state):
    probe = Probe(vehicle_state, VEHICLE)
    rows = tornado(probe, [find_parameter(vehicle_state, p) for p in
                           ("obs:B.cost.value", "obs:A.cost.value", "obs:C.tank.value")])["rows"]
    assert rows[0]["parameter"] == "obs:B.cost.value" and rows[0]["flips_within"]
    tank = next(r for r in rows if r["parameter"] == "obs:C.tank.value")
    assert tank["swing"] == 0 and not tank["flips_within"]  # C is infeasible on reliability anyway


def analytic_state(vehicle_state):
    """B's cost ~ U[1.0, 1.3] and reliability ~ U[0.95, 0.99]: B is always feasible and U_B is linear in both."""
    state = vehicle_state.model_copy(deep=True)
    objects = index(state)
    objects["obs:B.cost"].uncertainty = m.UncertaintySpec(family="uniform", low=1.0, high=1.3)
    objects["obs:B.cost"].value = 1.15
    objects["obs:B.reliability"].uncertainty = m.UncertaintySpec(family="uniform", low=0.95, high=0.99)
    objects["obs:B.reliability"].value = 0.97
    return state


def test_sobol_indices_match_the_analytic_values(vehicle_state):
    state = analytic_state(vehicle_state)
    probe = Probe(state, VEHICLE)
    params = [find_parameter(state, "obs:B.cost.value"), find_parameter(state, "obs:B.reliability.value")]
    g = global_sensitivity(probe, joint_sample(probe, params, 1024, seed=3))
    # U_B = const - (0.4/0.6) c + (0.2/0.05) q: variance shares a^2 Var(c) : b^2 Var(q)
    vc, vq = (0.4 / 0.6) ** 2 * 0.3 ** 2 / 12, (0.2 / 0.05) ** 2 * 0.04 ** 2 / 12
    expected = {"obs:B.cost.value": vc / (vc + vq), "obs:B.reliability.value": vq / (vc + vq)}
    for row in g["indices"]:
        s = row["alt:B"]
        assert s["first_order"] == pytest.approx(expected[row["parameter"]], abs=0.06)
        assert s["total"] == pytest.approx(expected[row["parameter"]], abs=0.06)
        assert row["alt:A"]["total"] == 0  # A does not depend on B's inputs


def test_value_of_information_matches_an_independent_monte_carlo(vehicle_state):
    state = analytic_state(vehicle_state)
    probe = Probe(state, VEHICLE)
    params = [find_parameter(state, "obs:B.cost.value"), find_parameter(state, "obs:B.reliability.value")]
    voi = value_of_information(probe, joint_sample(probe, params, 2048, seed=5, saltelli=False), seed=5)
    # Independent of the kernel: U_A = 7/15 fixed, C infeasible (value 0), U_B from the formula.
    rng = random.Random(1)
    u_a, ub = 7 / 15, []
    for _ in range(200_000):
        c, q = rng.uniform(1.0, 1.3), rng.uniform(0.95, 0.99)
        ub.append(0.24 + 0.4 * (1.4 - c) / 0.6 + 0.2 * (q - 0.94) / 0.05)
    ev_now = max(u_a, math.fsum(ub) / len(ub))
    evpi = math.fsum(max(u_a, u) for u in ub) / len(ub) - ev_now
    assert voi["expected_value_choice"] == ["alt:B"]
    assert voi["evpi"] == pytest.approx(evpi, abs=0.002)
    for row in voi["parameters"]:
        assert 0 <= row["evppi"] <= voi["evpi"] + 0.002
    assert voi["parameters"][0]["parameter"] == "obs:B.cost.value"  # cost carries more of the variance


def test_full_analysis_on_fires_finds_the_schedule():
    state, base = fires_state()
    result = analyze(state, base, samples=128, seed=7)
    assert result["global"]["indices"][0]["parameter"] == "obs:new-array.schedule.value"
    voi = result["value_of_information"]
    assert voi["parameters"][0]["parameter"] == "obs:new-array.schedule.value"
    assert voi["collect_next"][0]["evidence"] == "ev:estimate-new-array"
    assert result["tornado"]["cost"]["executed_fraction"] < 0.5  # one input at a time re-runs little


def test_unknown_parameters_are_refused(vehicle_state):
    with pytest.raises(SensitivityError, match="no numeric parameter"):
        find_parameter(vehicle_state, "obs:B.colour.value")


def test_cli_json_and_figures(tmp_path):
    out = runner.invoke(app, ["sensitivity", str(VEHICLE), "-p", "obs:B.cost.value", "--samples", "200",
                              "--figures", str(tmp_path), "--json"])
    assert out.exit_code == 0, out.output
    data = json.loads(out.output)
    assert data["response"]["changes"][0]["to"] == ["alt:A"]
    assert {Path(p).name for p in data["figures"]} == {"sensitivity_response.png", "sensitivity_distribution.png"}
    out = runner.invoke(app, ["sensitivity", str(VEHICLE), "--json"])
    data = json.loads(out.output)
    assert data["global"] is None and data["tornado"]["rows"]  # no distributions in the vehicle example


def test_the_mcp_server_lists_the_tool():
    pytest.importorskip("mcp")
    from pdo.mcp_server import READ_ONLY

    assert "pdo_sensitivity" in {fn.__name__ for fn in READ_ONLY}


def test_the_example_still_evaluates(vehicle_state):
    assert evaluate_state(vehicle_state).decision["recommended"] == ["alt:B"]
    assert load_state(VEHICLE).state is not None
