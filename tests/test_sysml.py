"""SysML v2 import: official excerpts, the vehicle round trip, and gaps instead of guesses."""

from __future__ import annotations

import json
import random
import re
import shutil

import pytest
import yaml
from conftest import ROOT, VEHICLE
from typer.testing import CliRunner

from pdo import model as m
from pdo.canonical import hashable_dump
from pdo.cli import app
from pdo.graph import compile_graph
from pdo.load import check_state, load_state
from pdo.report import run_summary
from pdo.run import run
from pdo.sysml import PROBLEM_KINDS, RULES, STATE_FILES, SysMLError, import_sysml, write_state

FIXTURES = ROOT / "tests" / "fixtures" / "sysml"
EXCERPTS = sorted(FIXTURES.glob("*.sysml"))
MODEL = VEHICLE / "sysml" / "vehicle.sysml"
MAPPING = VEHICLE / "sysml" / "mapping.yaml"
SKILL = ROOT / "skills" / "sysml-to-decision"
runner = CliRunner()


def model(tmp_path, text: str, name: str = "model.sysml"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def kinds(result) -> set[str]:
    return {g.kind for g in result.gaps}


def gaps_of(result, kind: str) -> list:
    return [g for g in result.gaps if g.kind == kind]


def by_id(objects, drop: tuple[str, ...] = ()) -> dict:
    return {o.id: {k: v for k, v in hashable_dump(o).items() if k not in drop} for o in objects or []}


def vehicle_mapping(**evaluation) -> dict:
    mapping = yaml.safe_load(MAPPING.read_text())
    if evaluation:
        mapping["evaluations"]["RangeModel"] = {"id": "spec:range-model", **evaluation}
    return mapping


# --- official excerpts ----------------------------------------------------------------------------------------

@pytest.mark.parametrize("path", EXCERPTS, ids=[p.stem for p in EXCERPTS])
def test_official_excerpts_parse_and_report_gaps(path):
    result = import_sysml(path, base=ROOT)
    m.DecisionState.model_validate(result.state)
    assert result.gaps, "every excerpt has constructs outside the subset"
    for gap in result.gaps:
        assert gap.element and gap.reason
        assert gap.kind in PROBLEM_KINDS or re.fullmatch(r"[a-z]+( [a-z]+)*", gap.kind), gap
    assert "parse" not in kinds(result)
    json.dumps(result.to_dict())


def test_excerpt_with_two_candidate_sets_imports_no_alternatives():
    files = [FIXTURES / "vehicle_definitions.sysml", FIXTURES / "vehicle_usages.sysml"]
    result = import_sysml(files, base=ROOT)
    assert "alternatives" not in result.state
    (gap,) = gaps_of(result, "alternatives")
    assert "VehicleDefinitions::Vehicle (3 usages)" in gap.reason and "Wheel (2 usages)" in gap.reason

    chosen = import_sysml(files, {"alternatives": {"from": "Vehicle"}}, base=ROOT)
    assert [a["id"] for a in chosen.state["alternatives"]] == ["alt:vehicle_C1", "alt:vehicle_C2", "alt:vehicle_C3"]
    assert all(a["mapping_rule"].startswith("alternative.same-definition") for a in chosen.state["alternatives"])
    values = {g.element.rsplit("::", 1)[-1] for g in gaps_of(chosen, "attribute-value")}
    assert values == {"tighteningTorque"}  # `= T1` is a reference, not a literal
    assert {"interface", "port def", "interface def"} <= kinds(chosen)


def test_excerpt_fuel_economy_requirements_follow_redefinitions():
    result = import_sysml(FIXTURES / "fuel_economy_requirements.sysml", base=ROOT)
    reqs = {r["id"]: r for r in result.state["requirements"]}
    city = reqs["req:vehicleFuelEconomyRequirement_city"]
    assert (city["measure"], city["comparator"], city["threshold_value"], city["unit"]) == (
        "meas:fuelEconomy_city", "ge", 25, "mpg")
    assert "measure via actualFuelEconomy" in city["mapping_rule"]
    assert "threshold from requiredFuelEconomy" in city["mapping_rule"]
    assert reqs["req:vehicleFuelEconomyRequirement_highway"]["threshold_value"] == 30
    cargo = [a for a in result.state["assumptions"] if a["name"] == "cargoMass"]
    assert len(cargo) == 2 and all((a["value"], a["unit"], a["validation_status"]) == (1000, "lb", "unvalidated")
                                   for a in cargo)
    assert {"analysis", "satisfy", "port"} <= kinds(result)
    # The template usages have no attribute to measure: a gap on each, not a guess.
    templates = {g.element.rsplit("::", 1)[-1] for g in gaps_of(result, "constraint")}
    assert templates == {"cityFuelEconomyRequirement", "highwayFuelEconomyRequirement"}


def test_excerpt_text_only_requirements_are_gaps():
    result = import_sysml(FIXTURES / "hsuv_requirements.sysml", base=ROOT)
    assert "requirements" not in result.state
    named = {g.element.rsplit("::", 1)[-1] for g in gaps_of(result, "requirement")}
    assert {"Passengers", "FuelCapacity", "Emissions", "Range", "Ergonomics"} <= named
    assert "HybridSUVSpec" not in named  # a group of required requirements, not a requirement itself


def test_excerpt_constraint_without_a_threshold_is_a_gap_on_each_usage():
    result = import_sysml(FIXTURES / "vehicle_requirement_derivation.sysml", base=ROOT)
    assert "requirements" not in result.state
    reasons = {g.element.rsplit("::", 1)[-1]: g.reason for g in gaps_of(result, "constraint")}
    assert set(reasons) == {"vehicleMassRequirement", "chassisMassRequirement", "engineMassRequirement"}
    assert all("`massLimit` has no value" in r for r in reasons.values())


def test_excerpt_cycles_and_states_do_not_crash():
    parts = import_sysml(FIXTURES / "part_test.sysml", base=ROOT)  # p1 :> p2 :> p3 :> p1 and p4 :> p4
    assert {"part", "port def"} <= kinds(parts)
    states = import_sysml(FIXTURES / "state_test.sysml", base=ROOT)
    assert {"state def", "state", "action"} <= kinds(states)


def test_excerpt_verification_case_is_not_a_measure():
    result = import_sysml(FIXTURES / "verification_test.sysml", base=ROOT)
    assert "evaluation_specifications" not in result.state
    assert any("verdict" in g.reason for g in gaps_of(result, "evaluation"))


# --- the vehicle model ------------------------------------------------------------------------------------------

def test_vehicle_round_trips_to_the_baseline():
    result = import_sysml(MODEL, MAPPING, base=ROOT)
    imported = m.DecisionState.model_validate(result.state)
    baseline = load_state(VEHICLE).state
    assert by_id(imported.alternatives) == by_id(baseline.alternatives)
    assert by_id(imported.requirements) == by_id(baseline.requirements)
    assert by_id(imported.observations, drop=("evidence",)) == by_id(baseline.observations, drop=("evidence",))
    assert {x.id: x.unit for x in imported.measures} == {x.id: x.unit for x in baseline.measures}
    (spec,), (base_spec,) = imported.evaluation_specifications, baseline.evaluation_specifications
    assert spec.id == base_spec.id and spec.implementation is None
    assert hashable_dump(spec.inputs) == hashable_dump(base_spec.inputs)
    assert hashable_dump(spec.outputs) == hashable_dump(base_spec.outputs)
    (asm,) = imported.assumptions
    assert (asm.id, asm.value, asm.unit, asm.validation_status) == ("asm:terrain-factor", 1.0, "1", "unvalidated")


def test_vehicle_provenance():
    result = import_sysml(MODEL, MAPPING, base=ROOT)
    (evidence,) = result.state["evidence_items"]
    digest = result.sources[0]["sha256"]
    assert evidence["source_ref"] == f"examples/vehicle/sysml/vehicle.sysml@sha256:{digest}"
    assert evidence["content_sha256"] == digest
    assert {o["evidence"] for o in result.state["observations"]} == {evidence["id"]}
    for obj in [*result.state["alternatives"], *result.state["requirements"]]:
        assert obj["source_model"] == "examples/vehicle/sysml/vehicle.sysml"
        assert obj["source_model_version"] == f"sha256:{digest}"
        assert obj["source_element"].startswith("VehicleTradeStudy::")
        assert re.split(r"[ :]", obj["mapping_rule"])[0] in RULES
    reliability = next(r for r in result.state["requirements"] if r["id"] == "req:min-reliability")
    assert reliability["source_element"] == "VehicleTradeStudy::vehicleRequirements::minReliability"
    assert "threshold from reliabilityRequired" in reliability["mapping_rule"]
    obs = next(o for o in result.state["observations"] if o["id"] == "obs:C.tank")
    assert "configC::tankCapacity = 47.5 [gal]" in obs["comments"][0]


def test_vehicle_gaps_are_the_out_of_scope_port_and_the_unwired_range_model():
    result = import_sysml(MODEL, MAPPING, base=ROOT)
    assert kinds(result) == {"port", "port def", "implementation"}
    (gap,) = gaps_of(result, "implementation")
    assert gap.element == "VehicleTradeStudy::RangeModel" and "spec:range-model" in gap.reason
    wired = import_sysml(MODEL, vehicle_mapping(implementation="evaluators.py:range_model",
                                                implementation_version="1.0"), base=ROOT)
    assert kinds(wired) == {"port", "port def"}


def test_vehicle_without_a_mapping_uses_sysml_names_and_flags_the_unitless_cost():
    result = import_sysml(MODEL, base=ROOT)
    assert [a["id"] for a in result.state["alternatives"]] == ["alt:configA", "alt:configB", "alt:configC"]
    assert not any(a.get("is_incumbent") for a in result.state["alternatives"])  # only a person says so
    units = {x["id"]: x["unit"] for x in result.state["measures"]}
    assert units == {"meas:tankCapacity": "gal", "meas:fuelEconomy": "mi/gal", "meas:range": "mi",
                     "meas:unitCost": "1", "meas:reliability": "1"}
    assert "unit" not in kinds(result)  # unitCost is typed Real, so "1" is the model's own statement
    assert {r["id"] for r in result.state["requirements"]} == {"req:minRange", "req:minReliability",
                                                                "req:maxUnitCost"}


def test_vehicle_decision_reproduces_the_baseline(tmp_path):
    """Import + the person's framing + the wired range model = the baseline's decision, number for number."""
    mapping = vehicle_mapping(implementation="evaluators.py:range_model", implementation_version="1.0")
    mapping["assumptions"]["RangeModel::terrain_factor"] = {
        "id": "asm:terrain-factor", "critical": True, "validation_status": "validated",
        "plausible_low": 0.80, "plausible_high": 1.05}
    result = import_sysml(MODEL, mapping, base=ROOT)
    write_state(result, tmp_path)
    framing = {}
    for name in ("decision.yaml", "governance.yaml"):
        data = yaml.safe_load((VEHICLE / "baseline" / name).read_text())
        framing.update({k: v for k, v in data.items() if k not in ("measures", "requirements")})
    (tmp_path / "framing.yaml").write_text(yaml.safe_dump(framing, sort_keys=False))
    shutil.copy(VEHICLE / "evaluators.py", tmp_path / "evaluators.py")

    loaded = load_state(tmp_path)
    assert [str(i) for i in check_state(loaded.state, loaded.origins)] == []
    baseline = load_state(VEHICLE).state
    assert by_id(loaded.state.evaluation_specifications) == by_id(baseline.evaluation_specifications)
    assert by_id(loaded.state.assumptions) == by_id(baseline.assumptions)

    got = run_summary(run(compile_graph(loaded.state, tmp_path)))
    want = run_summary(run(compile_graph(baseline, VEHICLE)))
    for key in ("recommended", "feasible", "infeasible", "unresolved", "best_utility", "readiness", "actionable"):
        assert got[key] == want[key], key
    assert got["alternatives"] == want["alternatives"]
    assert got["recommended"] == ["alt:B"]
    assert [round(a["utility"], 6) for a in got["alternatives"]] == [0.466667, 0.533333, 0.62]


# --- never guess ------------------------------------------------------------------------------------------------

UNSUPPORTED = """
package Demo {
    part def Car {
        attribute mass :> ISQ::mass;
        attribute range :> ISQ::length;
        port charge : ChargePort;
        state def Modes { state off; state on; }
        perform action drive;
    }
    port def ChargePort;
    part carA : Car { attribute :>> mass = 1500 [kg]; attribute :>> range = 2 * 150 [km]; }
    part carB : Car { attribute :>> mass = 1600 [kg]; }
    connection link connect carA to carB;
    calc def Doubled { in x : Real; return : Real = 2 * x; }
    requirement scaled { subject car : Car; require constraint { car.mass * 2 <= 4000 [kg] } }
    requirement either { subject car : Car; require constraint { car.mass <= 1800 [kg] or car.range >= 300 [km] } }
    requirement exact { subject car : Car; require constraint { car.mass == 1500 [kg] } }
    requirement both { subject car : Car; require constraint { 2000 [kg] >= car.mass and car.mass > 1000 [kg] } }
}
"""


def test_unsupported_constructs_become_gaps(tmp_path):
    result = import_sysml(model(tmp_path, UNSUPPORTED), base=tmp_path)
    assert {"port", "port def", "state def", "perform", "connection", "calc def"} <= kinds(result)
    constraint_gaps = {g.element.split("::")[1]: g.reason for g in gaps_of(result, "constraint")}
    assert set(constraint_gaps) == {"scaled", "either", "exact"}
    assert "`or` is not supported" in constraint_gaps["either"]
    assert "is not a requirement comparator" in constraint_gaps["exact"]
    (value_gap,) = gaps_of(result, "attribute-value")
    assert value_gap.element == "Demo::carA::range"
    # Only the readable requirement is imported: a conjunction, one id per term, threshold on the left reversed.
    reqs = {r["id"]: (r["comparator"], r["threshold_value"]) for r in result.state["requirements"]}
    assert reqs == {"req:both.1": ("le", 2000), "req:both.2": ("gt", 1000)}
    assert "comparator reversed" in result.state["requirements"][0]["mapping_rule"]
    assert [o["id"] for o in result.state["observations"]] == ["obs:carA.mass", "obs:carB.mass"]


def test_units_are_never_converted_or_guessed(tmp_path):
    text = """
    package U {
        part def Car { attribute mass; attribute width; attribute height; attribute seats; }
        part carA : Car { :>> mass = 1500 [kg]; :>> width = 1.8 [m]; :>> height = 1.5 [m]; :>> seats = 4; }
        part carB : Car { :>> mass = 1.6 [t]; :>> width = 1.9; :>> height = 1.6 [m]; :>> seats = 5; }
        requirement wide { subject car : Car; require constraint { car.width <= 2.5 [m] } }
        requirement low { subject car : Car; require constraint { car.height <= 1700 [mm] } }
    }"""
    path = model(tmp_path, text)
    result = import_sysml(path, base=tmp_path)
    measures = {x["id"]: x["unit"] for x in result.state["measures"]}
    assert measures == {"meas:width": "m", "meas:seats": "1"}  # mass (kg, t) and height (m, mm): no measure
    reasons = [g.reason for g in gaps_of(result, "unit")]
    assert any("several units (kg, t)" in r for r in reasons) and any("several units (m, mm)" in r for r in reasons)
    assert any("has no unit, but meas:width is in m" in r for r in reasons)  # carB's 1.9: not assumed to be m
    assert any("no unit for seats" in r for r in reasons)  # untyped and unitless: "1", flagged
    assert {o["id"] for o in result.state["observations"]} == {"obs:carA.width", "obs:carA.seats", "obs:carB.seats"}
    assert [r["id"] for r in result.state["requirements"]] == ["req:wide"]
    assert any("measure height was not created" in g.reason for g in gaps_of(result, "requirement"))

    mapped = import_sysml(path, {"measures": {"seats": {"unit": "seat"}}}, base=tmp_path)
    assert {x["id"]: x["unit"] for x in mapped.state["measures"]}["meas:seats"] == "seat"
    assert not any("seats" in g.reason for g in gaps_of(mapped, "unit"))


def test_two_variations_mean_no_alternatives_until_a_person_chooses(tmp_path):
    text = """
    package V {
        part def Engine { attribute power; }
        part def Gearbox { attribute ratio; }
        variation part engine : Engine { variant part e4 { attribute :>> power = 100 [kW]; }
                                         variant part e6 { attribute :>> power = 150 [kW]; } }
        variation part gearbox : Gearbox { variant part manual; variant part automatic; }
    }"""
    path = model(tmp_path, text)
    result = import_sysml(path, base=tmp_path)
    assert "alternatives" not in result.state and "observations" not in result.state
    (gap,) = gaps_of(result, "alternatives")
    assert "V::engine (2 variants)" in gap.reason and "V::gearbox (2 variants)" in gap.reason
    chosen = import_sysml(path, {"alternatives": {"from": "engine"}}, base=tmp_path)
    assert [o["value"] for o in chosen.state["observations"]] == [100, 150]
    assert "variation part" in kinds(chosen)  # the gearbox choice is reported, not merged in


def test_values_inherit_and_redefine(tmp_path):
    text = """
    package R {
        part def Truck {
            attribute payload default 2000 [kg];
            attribute axles : Integer = 2;
            part engine { attribute mass = 400 [kg]; }
        }
        part base : Truck;
        part heavy :> base {
            attribute redefines payload = 3500 [kg];
            part engine redefines engine { attribute :>> mass = 520 [kg]; }
        }
    }"""
    result = import_sysml(model(tmp_path, text), base=tmp_path)
    values = {o["id"]: (o["value"], o["unit"]) for o in result.state["observations"]}
    assert values == {"obs:base.payload": (2000, "kg"), "obs:base.axles": (2, "1"), "obs:base.engine.mass": (400, "kg"),
                      "obs:heavy.payload": (3500, "kg"), "obs:heavy.axles": (2, "1"),
                      "obs:heavy.engine.mass": (520, "kg")}
    inherited = next(o for o in result.state["observations"] if o["id"] == "obs:heavy.axles")
    assert "inherited from R::Truck" in inherited["comments"][0]


def test_evaluation_inputs_outputs_and_conflicts(tmp_path):
    text = """
    package E {
        part def Car { attribute range; attribute battery; }
        part carA : Car { attribute :>> battery = 60 [kWh]; attribute :>> range = 400 [km]; }
        part carB : Car { attribute :>> battery = 75 [kWh]; }
        analysis def RangeModel {
            subject car : Car;
            in kwh = car.battery;
            in efficiency : Real = 6.5;
            in weather;
            return km = car.range;
        }
        verification def RangeTest { subject car : Car; objective { verify requirement r; } }
    }"""
    result = import_sysml(model(tmp_path, text), base=tmp_path)
    (spec,) = result.state["evaluation_specifications"]
    assert [i["arg"] for i in spec["inputs"]] == ["kwh", "efficiency"]
    assert spec["inputs"][1]["ref"] == "asm:RangeModel.efficiency" and "implementation" not in spec
    assert spec["outputs"] == [{"arg": "km", "measure": "meas:range", "unit": "km"}]
    assert {"evaluation-input", "implementation", "producer-conflict", "evaluation"} <= kinds(result)
    (conflict,) = gaps_of(result, "producer-conflict")
    assert "alt:carA" in conflict.reason and "obs:carA.range" in conflict.reason


def test_assume_constraints(tmp_path):
    text = """
    package A {
        part def Car { attribute mass; attribute cargo; }
        part carA : Car { attribute :>> mass = 1500 [kg]; }
        part carB : Car { attribute :>> mass = 1600 [kg]; }
        requirement light {
            subject car : Car;
            require constraint { car.mass <= 1800 [kg] }
            assume constraint { car.cargo == 200 [kg] }
            assume constraint { car.cargo <= 500 [kg] }
        }
    }"""
    result = import_sysml(model(tmp_path, text), base=tmp_path)
    (asm,) = result.state["assumptions"]
    assert (asm["id"], asm["value"], asm["unit"], asm["validation_status"]) == ("asm:light.cargo", 200, "kg",
                                                                                "unvalidated")
    (gap,) = gaps_of(result, "assume-constraint")
    assert "car.cargo <= 500 [kg]" in gap.reason


def test_mapping_is_checked(tmp_path):
    with pytest.raises(SysMLError, match="unknown top-level keys"):
        import_sysml(MODEL, {"criteria": {}}, base=ROOT)
    with pytest.raises(SysMLError, match="unknown keys"):
        import_sysml(MODEL, {"measures": {"range": {"id": "meas:range", "weight": 0.4}}}, base=ROOT)
    with pytest.raises(SysMLError, match="does not validate"):
        import_sysml(MODEL, {"assumptions": {"terrain_factor": {"validation_status": "probably"}}}, base=ROOT)
    with pytest.raises(SysMLError, match="no such mapping file"):
        import_sysml(MODEL, tmp_path / "missing.yaml", base=ROOT)
    with pytest.raises(SysMLError, match="no such file"):
        import_sysml(tmp_path / "missing.sysml")
    result = import_sysml(MODEL, {"requirements": {"minRnage": "req:typo"}}, base=ROOT)
    (gap,) = gaps_of(result, "mapping")
    assert gap.element == "mapping:requirements.minRnage"


def test_import_is_deterministic_and_order_independent(tmp_path):
    a = model(tmp_path, "package P { part def Car { attribute mass; } part carA : Car { :>> mass = 1 [kg]; } }",
              "a.sysml")
    b = model(tmp_path, "package Q { part carB : P::Car { :>> mass = 2 [kg]; } }", "b.sysml")
    first = import_sysml([a, b], base=tmp_path).to_dict()
    assert first == import_sysml([b, a], base=tmp_path).to_dict() == import_sysml(tmp_path, base=tmp_path).to_dict()
    first = import_sysml([a, b], base=tmp_path)
    # One evidence item per model file that supplies values.
    assert [e["id"] for e in first.state["evidence_items"]] == ["ev:sysml-a", "ev:sysml-b"]
    assert {o["id"]: o["evidence"] for o in first.state["observations"]} == {"obs:carA.mass": "ev:sysml-a",
                                                                             "obs:carB.mass": "ev:sysml-b"}


def test_parser_survives_truncated_and_garbled_input(tmp_path):
    rng = random.Random(7)
    for path in [*EXCERPTS, MODEL]:
        text = path.read_text()
        for n in range(12):
            cut = rng.randrange(len(text))
            garbled = text[:cut] if n % 3 == 0 else text[:cut] + rng.choice("{}();'\"/*#@~") + text[cut + 1:]
            result = import_sysml(model(tmp_path, garbled), base=tmp_path)
            m.DecisionState.model_validate(result.state)
    deep = import_sysml(model(tmp_path, "package D " + "{ part p " * 150 + "}" * 150), base=tmp_path)
    assert any("nested more than" in g.reason for g in gaps_of(deep, "parse"))
    unclosed = import_sysml(model(tmp_path, "package D { part def X { attribute a;"), base=tmp_path)
    assert any("unclosed" in g.reason for g in gaps_of(unclosed, "parse"))


# --- writing, CLI, skill ---------------------------------------------------------------------------------------

def test_written_state_loads_and_only_lacks_framing(tmp_path):
    result = import_sysml(MODEL, MAPPING, base=ROOT)
    written = write_state(result, tmp_path)
    assert [p.name for p in written] == [name for name, _ in STATE_FILES] + ["sysml-import.json"]
    loaded = load_state(tmp_path)
    assert len(loaded.state.observations) == 12 and len(loaded.state.alternatives) == 3
    errors = [str(i) for i in check_state(loaded.state, loaded.origins) if i.severity == "error"]
    assert errors == ["error: sysml-evaluations.yaml: spec:range-model.implementation: "
                      "a python spec needs implementation: 'module.py:function'"]
    report = json.loads((tmp_path / "sysml-import.json").read_text())
    assert {g["kind"] for g in report["gaps"]} == {"port", "port def", "implementation"}
    assert report["missing_to_run"] == ["as_of", "criteria", "decision_rule", "readiness_policy"]
    # A re-import with nothing to say for a file empties it rather than leaving a stale part.
    write_state(import_sysml(FIXTURES / "hsuv_requirements.sysml", base=ROOT), tmp_path)
    assert yaml.safe_load((tmp_path / "sysml-alternatives.yaml").read_text()) == {}


def test_cli_import_and_gaps(tmp_path):
    out = runner.invoke(app, ["sysml", "import", str(MODEL), "--map", str(MAPPING), "--out", str(tmp_path), "--json"])
    assert out.exit_code == 0, out.output
    data = json.loads(out.output)
    assert data["ok"] and data["counts"]["observations"] == 12 and len(data["written"]) == 7
    assert data["state"]["requirements"][0]["id"] == "req:min-range"
    text = runner.invoke(app, ["sysml", "import", str(MODEL), "--map", str(MAPPING)])
    assert text.exit_code == 0 and "3 gaps" in text.output and "alt:A <- VehicleTradeStudy" in text.output
    gaps = runner.invoke(app, ["sysml", "gaps", str(FIXTURES / "hsuv_requirements.sysml"), "--json"])
    assert gaps.exit_code == 0 and json.loads(gaps.output)["count"] >= 10
    missing = runner.invoke(app, ["sysml", "import", str(tmp_path / "nope.sysml"), "--json"])
    assert missing.exit_code == 1 and json.loads(missing.output)["ok"] is False
    (tmp_path / "bad.yaml").write_text("weights: {}\n")
    bad = runner.invoke(app, ["sysml", "gaps", str(MODEL), "--map", str(tmp_path / "bad.yaml")])
    assert bad.exit_code == 1


def test_skill_is_well_formed_and_documents_every_rule_and_gap_kind():
    text = (SKILL / "SKILL.md").read_text()
    front = yaml.safe_load(text.split("---")[1])
    assert front["name"] == "sysml-to-decision" and "SysML" in front["description"]
    for link in re.findall(r"\]\(([^)#]+)", text):
        assert (SKILL / link).exists(), link
    for command in ("pdo sysml import", "pdo validate", "pdo run", "pdo explain", "applicability: provisional",
                    "pdo_import_sysml"):
        assert command in text, command
    mapping, gaps = (SKILL / "references" / "mapping.md").read_text(), (SKILL / "references" / "gaps.md").read_text()
    assert [r for r in RULES if f"`{r}`" not in mapping] == []
    assert [k for k in PROBLEM_KINDS if f"`{k}`" not in gaps] == []


def test_a_redefined_constraint_replaces_the_inherited_one(tmp_path):
    text = """
    package C {
        part def Car { attribute mass; }
        part carA : Car { :>> mass = 1500 [kg]; }
        part carB : Car { :>> mass = 1700 [kg]; }
        requirement def MassLimit { subject car : Car; require constraint limit { car.mass <= 2000 [kg] } }
        requirement strict : MassLimit { require constraint :>> limit { car.mass <= 1600 [kg] } }
        requirement loose : MassLimit;
    }"""
    result = import_sysml(model(tmp_path, text), base=tmp_path)
    assert {r["id"]: r["threshold_value"] for r in result.state["requirements"]} == {"req:strict": 1600,
                                                                                    "req:loose": 2000}
