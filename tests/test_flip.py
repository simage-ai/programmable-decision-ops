"""Flip points, weight flips and scenario robustness on the vehicle baseline (values worked by hand).

Baseline (docs/semantics.md): v_range = (r - 300)/100, v_cost = (1.40 - c)/0.60, v_rel = (q - 0.94)/0.05,
U = 0.4 v_range + 0.4 v_cost + 0.2 v_rel; U_A = 7/15, U_B = 8/15, U_C = 0.62 (C fails min reliability).
"""

from __future__ import annotations

import json
import math
import shutil

import pytest
from conftest import VEHICLE, assign, evaluate_state
from typer.testing import CliRunner

from pdo.cli import app
from pdo.flip import (
    flip_parameters,
    is_robust,
    numeric_parameters,
    scan_parameters,
    scenario_sweep,
    weight_flips,
)
from pdo.load import load_state

U_A, U_B = 7 / 15, 8 / 15


@pytest.fixture(scope="module")
def base():
    return evaluate_state(load_state(VEHICLE).state)


@pytest.fixture(scope="module")
def scanned(base):
    return scan_parameters(base.graph.state, VEHICLE, base=base)


@pytest.fixture(scope="module")
def flips(scanned):
    return scanned[1]


def flips_of(flips, parameter):
    return [f for f in flips if f["parameter"] == parameter]


def only(flips, parameter):
    (flip,) = flips_of(flips, parameter)
    return flip


# --- parameter flips ----------------------------------------------------------------------------

def test_b_cost_flips_to_a_at_exactly_1_30(flips):
    # U_B(c) = 0.40 + (0.40 / 0.60)(1.40 - c) = U_A  =>  c = 1.30 (the brief's worked threshold)
    f = only(flips, "obs:B.cost.value")
    assert f["flip_number"] == pytest.approx(1.30, abs=1e-9)
    assert (f["method"], f["boundary_kind"], f["direction"]) == ("affine_exact", "preference", "above")
    assert (f["previous_recommendation"], f["new_recommendation"]) == (["alt:B"], ["alt:A"])
    assert f["crossing"] == "U/alt:B - U/alt:A" and f["verified"] is True


def test_a_cost_flips_to_a_below_0_90(flips):
    # U_A(c) = 0.08 + 0.12 + (2/3)(1.40 - c) = 8/15  =>  c = 0.90
    f = only(flips, "obs:A.cost.value")
    assert f["flip_number"] == pytest.approx(0.90, abs=1e-9)
    assert (f["direction"], f["boundary_kind"], f["new_recommendation"]) == ("below", "preference", ["alt:A"])


def test_b_reliability_flips_by_preference_before_its_requirement_binds(flips):
    # The grid bracket also contains the feasibility boundary q = 0.95, but A already wins above it:
    # U_B(q) = 0.24 + 0.4/3 + 4 (q - 0.94) = 7/15  =>  q = 0.94 + 7/300 = 0.963333
    f = only(flips, "obs:B.reliability.value")
    assert f["flip_number"] == pytest.approx(0.94 + 7 / 300, abs=1e-9)
    assert (f["boundary_kind"], f["method"], f["new_recommendation"]) == ("preference", "affine_exact", ["alt:A"])


def test_c_reliability_at_0_95_makes_c_feasible_and_best(flips):
    # At q_C = 0.95, U_C = 0.32 + 0.30 + 0.04 = 0.66 > U_B
    f = only(flips, "obs:C.reliability.value")
    assert f["flip_number"] == pytest.approx(0.95, abs=1e-9)
    assert (f["boundary_kind"], f["direction"], f["new_recommendation"]) == ("feasibility", "above", ["alt:C"])
    assert f["crossing"] == "g/req:min-reliability/alt:C"


def test_max_cost_threshold_below_b_cost_then_below_a_cost(flips):
    first, second = flips_of(flips, "req:max-cost.threshold_value")
    assert first["flip_number"] == pytest.approx(1.20, abs=1e-9)
    assert (first["boundary_kind"], first["direction"], first["new_recommendation"]) == \
        ("feasibility", "below", ["alt:A"])
    assert second["flip_number"] == pytest.approx(1.00, abs=1e-9)
    assert (second["previous_recommendation"], second["new_recommendation"], second["boundary_kind"]) == \
        (["alt:A"], [], "empty_set")


def test_min_range_threshold_above_360_empties_the_feasible_set(flips):
    # Above 320 A fails but B still wins; above 360 B fails too and C already fails reliability.
    f = only(flips, "req:min-range.threshold_value")
    assert f["flip_number"] == pytest.approx(360, abs=1e-9)
    assert (f["direction"], f["boundary_kind"], f["new_recommendation"]) == ("above", "empty_set", [])


def test_terrain_flip_uses_the_plausible_range_and_solves_through_the_evaluator(scanned):
    scans, flips = scanned
    (scan,) = [s for s in scans if s["parameter"] == "asm:terrain-factor.value"]
    assert (scan["scan_low"], scan["scan_high"]) == (0.80, 1.05)
    f = only(flips, "asm:terrain-factor.value")
    # B's range 360 t falls below 300 at t = 5/6; A (320 t) already failed at t = 0.9375
    assert f["flip_number"] == pytest.approx(300 / 360, abs=1e-9)
    assert (f["boundary_kind"], f["method"], f["crossing"]) == ("empty_set", "affine_exact", "g/req:min-range/alt:B")


def test_min_reliability_threshold_both_ways(flips):
    above, below = flips_of(flips, "req:min-reliability.threshold_value")
    assert below["flip_number"] == pytest.approx(0.94, abs=1e-9)
    assert (below["direction"], below["boundary_kind"], below["new_recommendation"]) == \
        ("below", "feasibility", ["alt:C"])
    assert above["flip_number"] == pytest.approx(0.98, abs=1e-9)
    assert (above["direction"], above["boundary_kind"]) == ("above", "empty_set")


def test_range_inputs_flip_through_the_python_evaluator(flips):
    # A wins once B's range 8 * tank falls to 343.33 mi (v = 13/30), or A's rises to 336.67 mi (v = 11/30)
    assert only(flips, "obs:B.tank.value")["flip_number"] * 8 == pytest.approx(300 + 130 / 3, abs=1e-8)
    assert only(flips, "obs:A.tank.value")["flip_number"] * 8 == pytest.approx(300 + 110 / 3, abs=1e-8)


def test_every_flip_is_verified_and_c_inputs_are_stable(scanned):
    scans, flips = scanned
    assert len(flips) == 15
    assert all(f["verified"] for f in flips)
    stable = sorted(s["parameter"] for s in scans if s["flip_count"] == 0)
    assert stable == ["obs:C.cost.value", "obs:C.mpg.value", "obs:C.tank.value"]
    assert all(s["grid_points"] == 17 for s in scans) and len(scans) == 16


def test_rerunning_either_side_of_a_flip_confirms_it(base, flips):
    """Independent of the scan's own verification: evaluate the state at the flip -/+ a small step."""
    for parameter, slot in (("obs:B.cost", "value"), ("req:max-cost", "threshold_value")):
        f = flips_of(flips, f"{parameter}.{slot}")[0]
        step = 1e-4 if f["direction"] == "above" else -1e-4
        before = evaluate_state(assign(base.graph.state, parameter, slot, f["flip_number"] - step))
        after = evaluate_state(assign(base.graph.state, parameter, slot, f["flip_number"] + step))
        assert before.decision["recommended"] == f["previous_recommendation"]
        assert after.decision["recommended"] == f["new_recommendation"]


def test_two_boundaries_in_one_grid_cell_are_both_found(base, flips):
    # With 3 grid points the cell [0.70, 1.40] holds both B -> A (1.20) and A -> empty (1.00).
    coarse = flip_parameters(base.graph.state, VEHICLE, params=["req:max-cost.threshold_value"], grid=3, base=base)
    fine = flips_of(flips, "req:max-cost.threshold_value")
    assert [(f["flip_number"], f["previous_recommendation"], f["new_recommendation"]) for f in coarse] == \
        [(f["flip_number"], f["previous_recommendation"], f["new_recommendation"]) for f in fine]


def test_selected_parameters_and_unknown_names(base):
    only_b = flip_parameters(base.graph.state, VEHICLE, params=["obs:B.cost.value"], base=base)
    assert [f["parameter"] for f in only_b] == ["obs:B.cost.value"]
    by_id = flip_parameters(base.graph.state, VEHICLE, params=["req:max-cost"], base=base)
    assert len(by_id) == 2
    with pytest.raises(KeyError, match="obs:Z.cost"):
        flip_parameters(base.graph.state, VEHICLE, params=["obs:Z.cost.value"], base=base)


def test_only_counted_upstream_inputs_are_scanned(base):
    state = assign(base.graph.state, "obs:B.reliability", "applicability", "provisional")
    ids = {p.id for p in numeric_parameters(state, evaluate_state(state).graph)}
    assert "obs:B.reliability.value" not in ids and "obs:A.reliability.value" in ids
    assert "asm:terrain-factor.value" in ids and "req:min-range.threshold_value" in ids


def test_a_nonlinear_evaluator_falls_back_to_bisection(tmp_path):
    program = tmp_path / "vehicle"
    shutil.copytree(VEHICLE, program)
    (program / "evaluators.py").write_text(
        "def range_model(tank_gal, mpg, terrain_factor):\n    return tank_gal * mpg * terrain_factor ** 2\n")
    state = load_state(program).state
    (f,) = flip_parameters(state, program, params=["asm:terrain-factor.value"])
    # B's range 360 t^2 reaches 300 at t = sqrt(5/6); g is not affine in t, so the midpoint test fails
    assert f["method"] == "bisection" and f["boundary_kind"] == "empty_set" and f["verified"]
    assert f["flip_number"] == pytest.approx(math.sqrt(300 / 360), abs=1e-6)


# --- weight flips -------------------------------------------------------------------------------

def test_cost_weight_taken_from_reliability_flips_at_0_525(base):
    # Range weight fixed at 0.4: U_B - U_A = 7/25 - (8/15) w = 0  =>  w = 21/40 (the brief)
    flips = weight_flips(base.graph.state, VEHICLE, base=base)
    (f,) = [x for x in flips if x["target"] == "crit:cost" and x["counterpart"] == "crit:reliability"]
    assert f["flip_number"] == pytest.approx(21 / 40, abs=1e-12)
    assert (f["method"], f["boundary_kind"], f["direction"]) == ("closed_form", "preference", "above")
    assert (f["previous_recommendation"], f["new_recommendation"], f["verified"]) == (["alt:B"], ["alt:A"], True)


def test_other_weight_pairs(base):
    flips = {(f["target"], f["counterpart"]): f for f in weight_flips(base.graph.state, VEHICLE, base=base)}
    assert len(flips) == 3
    # cost from range: slopes v_cost - v_range are 7/15 (A) and -4/15 (B); gap 1/15  =>  w = 0.4 + 1/11
    assert flips[("crit:cost", "crit:range")]["flip_number"] == pytest.approx(0.4 + 1 / 11, abs=1e-12)
    # range given to reliability: A gains 0.2 per unit on B; gap 1/15  =>  w_range = 0.4 - 1/3
    f = flips[("crit:range", "crit:reliability")]
    assert f["flip_number"] == pytest.approx(1 / 15, abs=1e-12) and f["direction"] == "below"


def test_weight_flip_agrees_with_rerunning(base):
    for w, expected in ((0.525 - 1e-4, ["alt:B"]), (0.525 + 1e-4, ["alt:A"])):
        state = assign(base.graph.state, "crit:cost", "weight", w)
        state = assign(state, "crit:reliability", "weight", 0.6 - w)
        assert evaluate_state(state).decision["recommended"] == expected


# --- scenarios ----------------------------------------------------------------------------------

def test_scenario_sweep_shows_b_is_not_robust(base):
    outcomes = {o["scenario"]: o for o in scenario_sweep(base.graph.state, VEHICLE, base=base)}
    assert list(outcomes) == ["sc:nominal", "sc:hot-desert", "sc:cost-growth", "sc:optimistic-reliability"]
    for sc in ("sc:nominal", "sc:hot-desert", "sc:cost-growth"):
        assert outcomes[sc]["recommended"] == ["alt:B"] and outcomes[sc]["robust"]
    assert outcomes["sc:hot-desert"]["feasible"] == ["alt:B"]  # A's range 294.4 < 300
    opt = outcomes["sc:optimistic-reliability"]
    assert opt["recommended"] == ["alt:C"] and not opt["robust"]
    u = {o["alternative"]: o["utility"] for o in opt["outcomes"]}
    assert u["alt:C"] == pytest.approx(0.32 + 0.30 + 0.04, abs=1e-12)
    assert is_robust(list(outcomes.values())) is False


def test_scenario_with_an_unknown_target_is_rejected(base):
    state = base.graph.state.model_copy(deep=True)
    state.scenarios[1].assignments[0].target = "asm:nope"
    with pytest.raises(ValueError, match="asm:nope"):
        scenario_sweep(state, VEHICLE, base=base)


def test_no_scenarios_means_no_robustness_claim():
    assert is_robust([]) is None


# --- CLI ----------------------------------------------------------------------------------------

runner = CliRunner()


def test_cli_flip_json_filters_parameters():
    out = runner.invoke(app, ["flip", str(VEHICLE), "--param", "obs:B.cost.value", "--param", "crit:cost.weight",
                              "--json"])
    assert out.exit_code == 0, out.output
    data = json.loads(out.output)
    assert data["ok"] and data["recommended"] == ["alt:B"]
    params = [(f["parameter"], f.get("counterpart")) for f in data["flips"]]
    assert params == [("obs:B.cost.value", None), ("crit:cost.weight", "crit:range"),
                      ("crit:cost.weight", "crit:reliability")]
    assert data["flips"][0]["flip_number"] == pytest.approx(1.30, abs=1e-9)


def test_cli_flip_text_and_bad_parameter():
    out = runner.invoke(app, ["flip", str(VEHICLE)])
    assert out.exit_code == 0 and "obs:B.cost.value: above 1.3" in out.output and "no flip:" in out.output
    out = runner.invoke(app, ["flip", str(VEHICLE), "--param", "obs:nope.value", "--json"])
    assert out.exit_code == 1 and json.loads(out.output)["ok"] is False


def test_cli_scenarios():
    out = runner.invoke(app, ["scenarios", str(VEHICLE), "--json"])
    assert out.exit_code == 0, out.output
    data = json.loads(out.output)
    assert data["robust"] is False and data["scenarios"][-1]["recommended"] == ["alt:C"]
    out = runner.invoke(app, ["scenarios", str(VEHICLE)])
    assert "sc:optimistic-reliability: alt:C" in out.output and "robust: no" in out.output
