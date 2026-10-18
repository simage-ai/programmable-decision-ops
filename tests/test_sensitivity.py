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


def test_value_of_information_matches_closed_form_values(vehicle_state):
    state = analytic_state(vehicle_state)
    probe = Probe(state, VEHICLE)
    params = [find_parameter(state, "obs:B.cost.value"), find_parameter(state, "obs:B.reliability.value")]
    voi = value_of_information(probe, joint_sample(probe, params, 2048, seed=5, saltelli=False), seed=5)
    # Independent of the kernel: U_A = 7/15 fixed, C infeasible (value 0), U_B linear in c and q.
    rng = random.Random(1)
    u_a, ub = 7 / 15, []
    for _ in range(200_000):
        c, q = rng.uniform(1.0, 1.3), rng.uniform(0.95, 0.99)
        ub.append(0.24 + 0.4 * (1.4 - c) / 0.6 + 0.2 * (q - 0.94) / 0.05)
    evpi = math.fsum(max(u_a, u) for u in ub) / len(ub) - max(u_a, math.fsum(ub) / len(ub))
    assert voi["expected_value_choice"] == ["alt:B"]
    assert voi["evpi"] == pytest.approx(evpi, abs=0.002)
    # Closed form: E[U_B | c] = 0.36 + (2/3)(1.4 - c) beats U_A for c < 1.24, so EVPPI_c =
    # E[max(0, (2/3)(c - 1.24))] = 0.0040; E[U_B | q] = 4q - 3.35333 beats U_A for q > 0.955, so EVPPI_q =
    # E[max(0, 3.82 - 4q)] = 0.00125.
    by = {r["parameter"]: r for r in voi["parameters"]}
    assert by["obs:B.cost.value"]["evppi"] == pytest.approx(0.0040, abs=0.0015)
    assert by["obs:B.reliability.value"]["evppi"] == pytest.approx(0.00125, abs=0.0015)
    lo, hi = by["obs:B.cost.value"]["interval"]
    assert lo <= 0.0040 <= hi


def test_full_analysis_on_fires_finds_the_schedule():
    state, base = fires_state()
    result = analyze(state, base, samples=256, seed=7)
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


# --- regression tests for the verification audit ----------------------------------------------------

def test_an_unresolved_competitor_counts_at_its_upper_bound():
    """ep:e04: B is unresolved (U in [0.34, 0.54]); A's margin must not ignore it."""
    from pdo.episodes import Program

    program = Program.load(VEHICLE)
    probe = Probe(program.materialize("ep:e04"), VEHICLE)
    o = probe.point_outcome
    assert probe.baseline == ["alt:A"] and o.value["alt:B"] is None
    assert probe.margin(o) == pytest.approx(o.utility["alt:A"] - o.upper["alt:B"])
    assert probe.margin(o) < 0  # B could still win


def test_a_value_of_information_failure_keeps_the_rest():
    state, base = fires_state()
    state = assign(state, "obs:insert.range", "applicability", "provisional")
    result = analyze(state, base, samples=64, seed=1)
    assert result["tornado"]["rows"] and result["global"] is not None
    assert result["value_of_information"] is None
    assert any("value of information skipped" in n for n in result["notes"])


def test_a_tie_uses_the_tied_set_and_draws_figures(vehicle_state, tmp_path):
    from pdo.sensitivity_plots import write_figures

    state = assign(vehicle_state, "obs:B.cost", "value", 1.30)
    result = analyze(state, VEHICLE)
    assert result["baseline"] == ["alt:A", "alt:B"]
    assert result["tornado"]["margin"] == pytest.approx(7 / 15)  # the tied pair against infeasible C
    assert any("tie" in n for n in result["notes"])
    write_figures(result, tmp_path)


def test_an_input_at_zero_needs_an_explicit_range(vehicle_state):
    state = assign(vehicle_state, "obs:C.tank", "value", 0.0)
    with pytest.raises(SensitivityError, match="has no range"):
        find_parameter(state, "obs:C.tank.value")
    param = find_parameter(state, "obs:C.tank.value", low=0.0, high=50.0)
    assert param.low == 0.0 and param.high == 50.0
    skipped = analyze(state, VEHICLE)["tornado"]["skipped"]
    assert any(s["parameter"] == "obs:C.tank.value" for s in skipped)


def test_anchor_ranges_stop_short_of_the_other_anchor(vehicle_state):
    param = find_parameter(vehicle_state, "crit:range.anchor_best", span=0.5)
    assert param.low > 300  # anchor_worst is 300
    with pytest.raises(SensitivityError, match="other anchor"):
        find_parameter(vehicle_state, "crit:range.anchor_best", low=250, high=450)


def test_invalid_options_are_refused(vehicle_state):
    with pytest.raises(SensitivityError, match="at least 2 points"):
        response_curve(Probe(vehicle_state, VEHICLE), find_parameter(vehicle_state, "obs:B.cost.value"), points=1)
    with pytest.raises(SensitivityError, match="may not be positive"):
        Probe(vehicle_state, VEHICLE, infeasible_value=0.7)
    with pytest.raises(SensitivityError, match="give the input as param"):
        analyze(vehicle_state, VEHICLE, low=1.0)
    with pytest.raises(SensitivityError, match="at least 1"):
        analyze(vehicle_state, VEHICLE, samples=0)
    out = runner.invoke(app, ["sensitivity", str(VEHICLE), "-p", "crit:range.anchor_best", "--low", "250",
                              "--high", "450", "--json"])
    assert out.exit_code == 1 and json.loads(out.output)["ok"] is False


def test_a_spec_parameter_never_overwrites_a_slot_of_the_same_name():
    from pdo.sensitivity import Param, _with

    local = {"spec_kind": "python", "parameters": [{"arg": "spec_kind", "number": 1.0}]}
    param = Param("spec:x.spec_kind", "spec:x", "spec_kind", "spec:x", 1.0, 0.5, 1.5, "given", kind="spec_parameter")
    out = _with(local, param, 1.2)
    assert out["spec_kind"] == "python" and out["parameters"][0]["number"] == 1.2


def test_the_tornado_sees_changes_inside_the_range(vehicle_state):
    probe = Probe(vehicle_state, VEHICLE)
    param = find_parameter(vehicle_state, "obs:B.cost.value", low=1.0, high=1.6)  # flips at 1.30, B infeasible >1.40
    curve = response_curve(probe, param)
    row = tornado(probe, [param])["rows"][0]
    assert curve["changes"] and row["flips_within"]


def test_no_recommendation_is_reported_in_a_distribution(vehicle_state):
    probe = Probe(vehicle_state, VEHICLE)
    dist = distribution_response(probe, find_parameter(vehicle_state, "asm:terrain-factor.value"), samples=500,
                                 seed=2)
    total = sum(a["p_win"] for a in dist["alternatives"].values()) + dist["p_no_recommendation"]
    assert dist["p_no_recommendation"] > 0 and total == pytest.approx(1.0)


def test_the_expected_rule_is_named_in_the_notes():
    state, base = fires_state()
    result = analyze(state, base, "obs:new-array.cost.value", samples=50)
    assert any("expected_mavt" in n for n in result["notes"])


def test_a_package_with_sensitivity_replays(tmp_path):
    out = runner.invoke(app, ["package", str(VEHICLE), "--sensitivity", "-o", str(tmp_path), "--json"])
    assert out.exit_code == 0, out.output
    record = json.loads((tmp_path / "record.json").read_text())
    assert record["sensitivity_settings"]["seed"] == 0 and record["sensitivity"]
    out = runner.invoke(app, ["replay", str(tmp_path / "record.json"), "--json"])
    data = json.loads(out.output)
    assert data["reproduced"] and data["sensitivity_reproduced"] is True
