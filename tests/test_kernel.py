"""Hashing, loading, graph structure and unknown propagation."""

from __future__ import annotations

import os
import random
import subprocess
import sys
import textwrap

import pytest
import yaml
from conftest import ROOT, VEHICLE, assign, evaluate_state

from pdo import model as m
from pdo.canonical import canonical, digest, hashable_dump, object_digest
from pdo.graph import CycleError, compile_graph, impact_set
from pdo.identity_slots import HASHABLE
from pdo.load import LoadError, check_state, index, load_state
from pdo.run import run

# --- canonical form and digests -----------------------------------------------------------------


def test_numbers_hash_alike_regardless_of_spelling():
    assert canonical({"a": 300, "b": [1, 2]}) == canonical({"b": [1.0, 2.0], "a": 300.0})
    assert digest(-0.0) == digest(0)


def test_non_finite_numbers_are_rejected():
    with pytest.raises(ValueError):
        canonical(float("nan"))


def test_digests_do_not_depend_on_hash_seed():
    code = "from pdo.canonical import digest; print(digest({'b': 1, 'a': [3, 2.0, 'x']}))"
    outs = {subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True,
                           env={**os.environ, "PYTHONHASHSEED": seed}).stdout for seed in ("0", "1", "12345")}
    assert len(outs) == 1


def test_unhashable_slots_do_not_change_an_object_digest(vehicle):
    alt = index(vehicle.state)["alt:A"]
    renamed = alt.model_copy(update={"name": "renamed", "description": "new prose"})
    assert object_digest(alt) == object_digest(renamed)
    assert object_digest(alt) != object_digest(alt.model_copy(update={"is_incumbent": False}))


def test_every_hashable_slot_changes_the_digest(vehicle):
    """Mutating any hashable slot of any example object changes its content digest."""
    changed = 0
    for obj in index(vehicle.state).values():
        for slot in HASHABLE[type(obj).__name__]:
            value = getattr(obj, slot)
            if isinstance(value, bool):
                new = not value
            elif isinstance(value, int | float):
                new = value + 1
            elif isinstance(value, str) and slot not in ("measure", "alternative", "evidence"):
                new = value + "x"
            else:
                continue
            before = object_digest(obj)
            mutated = obj.model_copy(update={slot: new})
            assert object_digest(mutated) != before, f"{obj.id}.{slot}"
            changed += 1
    assert changed > 50


def test_identity_table_covers_every_schema_class():
    for name in ("Alternative", "Criterion", "Requirement", "Observation", "EvaluationSpecification",
                 "HumanReview", "HumanApproval", "ChangeSet", "DecisionEpisodeRecord"):
        assert name in HASHABLE
    assert "name" not in HASHABLE["Alternative"]
    assert "arg" in HASHABLE["InputBinding"]


def test_hashable_dump_recurses_into_inlined_objects(vehicle):
    spec = index(vehicle.state)["spec:range-model"]
    dump = hashable_dump(spec)
    assert dump["inputs"][0] == {"arg": "tank_gal", "measure": "meas:tank-capacity", "unit": "gal"}
    assert "cost_units" not in dump and "name" not in dump


# --- loading and checks -------------------------------------------------------------------------


def write(tmp_path, name, data):
    (tmp_path / name).write_text(yaml.safe_dump(data, sort_keys=False))


def baseline_parts():
    return {p.name: yaml.safe_load(p.read_text()) for p in sorted((VEHICLE / "baseline").glob("*.yaml"))}


def copy_baseline(tmp_path, edit=None):
    parts = baseline_parts()
    if edit:
        edit(parts)
    for name, data in parts.items():
        write(tmp_path, name, data)
    (tmp_path / "evaluators.py").write_text((VEHICLE / "evaluators.py").read_text())
    return tmp_path


def test_unknown_fields_and_keys_are_rejected(tmp_path):
    def edit(parts):
        parts["alternatives.yaml"]["alternatives"][0]["colour"] = "green"
    with pytest.raises(LoadError, match="colour"):
        load_state(copy_baseline(tmp_path, edit))


def test_duplicate_ids_across_files_are_rejected(tmp_path):
    def edit(parts):
        parts["assumptions.yaml"]["alternatives"] = [{"id": "alt:A"}]
    with pytest.raises(LoadError, match="duplicate ids: alt:A"):
        load_state(copy_baseline(tmp_path, edit))


@pytest.mark.parametrize(("mutate", "message"), [
    (lambda p: p["evidence.yaml"]["observations"][0].update(unit="liters"), "does not match"),
    (lambda p: p["decision.yaml"]["criteria"][0].update(weight=0.5), "weights sum to"),
    (lambda p: p["evidence.yaml"]["observations"][0].update(evidence="ev:nowhere"), "does not exist"),
    (lambda p: p["evidence.yaml"]["observations"].append(
        {"id": "obs:A.range", "alternative": "alt:A", "measure": "meas:range", "value": 330, "unit": "mi",
         "evidence": "ev:design-A"}), "more than one source"),
    (lambda p: p["governance.yaml"]["reviews"][0].update(covers=["all:nonsense"]), "unknown selector"),
])
def test_semantic_checks(tmp_path, mutate, message):
    loaded = load_state(copy_baseline(tmp_path, mutate))
    issues = [str(i) for i in check_state(loaded.state, loaded.origins)]
    assert any(message in i for i in issues), issues


def test_the_example_is_clean(vehicle):
    assert check_state(vehicle.state, vehicle.origins) == []


# --- graph structure ----------------------------------------------------------------------------


def test_compiled_order_is_deterministic(vehicle):
    a = compile_graph(vehicle.state, VEHICLE).order
    b = compile_graph(vehicle.state.model_copy(deep=True), VEHICLE).order
    assert a == b


def test_cycles_are_rejected_with_the_path(tmp_path):
    def edit(parts):
        spec = parts["evaluators.yaml"]["evaluation_specifications"][0]
        spec["inputs"][0] = {"arg": "tank_gal", "measure": "meas:range", "unit": "mi"}
    loaded = load_state(copy_baseline(tmp_path, edit))
    with pytest.raises(CycleError) as info:
        compile_graph(loaded.state, loaded.base_dir)
    assert "run/spec:range-model/" in str(info.value)


def test_removing_an_input_still_reaches_its_old_consumers(vehicle):
    """The impact set uses the old graph too: removing an observation reaches what read it."""
    state = vehicle.state.model_copy(deep=True)
    state.observations = [o for o in state.observations if o.id != "obs:B.reliability"]
    g0, g1 = compile_graph(vehicle.state, VEHICLE), compile_graph(state, VEHICLE)
    impact = impact_set(g0, g1)
    assert "y/alt:B/meas:reliability" in impact and "decision" in impact
    r = run(g1)
    assert r.value("y/alt:B/meas:reliability")["y"] is None
    assert r.decision["unresolved"] == ["alt:B"]
    assert r.decision["recommended"] == ["alt:A"]
    assert r.readiness["state"] == "pending"  # coverage: B could still win


def test_provisional_applicability_is_unknown_and_routed(vehicle):
    r = evaluate_state(assign(vehicle.state, "obs:B.reliability", "applicability", "provisional"))
    assert r.value("y/alt:B/meas:reliability")["y"] is None
    from pdo.actions import pending_actions
    assert ("review_applicability", "obs:B.reliability") in {(a.kind, a.target) for a in pending_actions(r)}


def test_known_fail_dominates_unknown(vehicle):
    state = assign(vehicle.state, "obs:C.cost", "value", None)
    r = evaluate_state(state)
    assert r.value("F/alt:C")["status"] == "fail"  # reliability still fails


def test_no_check_passes_on_unknown_input(vehicle):
    """Property: with random observations blanked, no check or feasibility passes on an unknown input."""
    rng = random.Random(7)
    obs_ids = [o.id for o in vehicle.state.observations]
    for _ in range(25):
        state = vehicle.state.model_copy(deep=True)
        blanked = set(rng.sample(obs_ids, rng.randint(1, 4)))
        for o in state.observations:
            if o.id in blanked:
                o.value = None
        r = evaluate_state(state)
        for nid, node in r.graph.nodes.items():
            if node.kind == "check":
                y = r.value(node.inputs[1])["y"]
                assert y is not None or r.value(nid)["status"] == "unknown", nid
            if node.kind == "feasible" and r.value(nid)["status"] == "pass":
                assert all(r.value(p)["status"] == "pass" for p in node.inputs), nid


def test_evaluator_failure_is_a_recorded_unknown(tmp_path):
    loaded = load_state(copy_baseline(tmp_path))
    (tmp_path / "evaluators.py").write_text(textwrap.dedent("""
        def range_model(tank_gal, mpg, terrain_factor):
            if tank_gal > 46:
                raise RuntimeError("out of calibrated range")
            return tank_gal * mpg * terrain_factor
    """))
    from pdo import registry
    registry._cache.clear()
    r = run(compile_graph(loaded.state, tmp_path))
    assert r.results["run/spec:range-model/alt:C"].exec_status == "failed"
    assert r.value("y/alt:C/meas:range")["y"] is None
    assert r.value("F/alt:C")["status"] == "fail"  # reliability still fails, so C stays infeasible
    assert r.decision["recommended"] == ["alt:B"]


def test_model_rejects_extra_fields():
    with pytest.raises(Exception):  # noqa: B017
        m.Alternative(id="alt:X", colour="green")


def test_repository_layout():
    assert (ROOT / "schema" / "pdo.yaml").exists()
