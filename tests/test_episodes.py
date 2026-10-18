"""Decision programs: change-set operations, stamping, selective refresh and the `program` CLI.

Every test copies the small pump program in tests/fixtures/episodes/pump to a tmp dir and writes its
own change sets there. Baseline: runtime is computed (X 8 h, Y 10 h, Z 6 h); Z fails min runtime, so Y is
recommended, ready, and awaiting approval by role:approver.
"""

from __future__ import annotations

import json
import shutil
from datetime import date, timedelta
from pathlib import Path

import pytest
import yaml
from conftest import ROOT, evaluate_state
from typer.testing import CliRunner

from pdo import model as m
from pdo.actions import pending_actions
from pdo.cli import app
from pdo.episodes import ChangeError, Program, apply_change
from pdo.graph import state_digest
from pdo.load import LoadError, index

FIXTURE = ROOT / "tests" / "fixtures" / "episodes" / "pump"
runner = CliRunner()


@pytest.fixture
def program_dir(tmp_path) -> Path:
    path = tmp_path / "pump"
    shutil.copytree(FIXTURE, path)
    (path / "changes").mkdir()
    return path


@pytest.fixture
def baseline(program_dir) -> m.DecisionState:
    return Program.load(program_dir).materialize("ep:p00")


def write(program_dir: Path, name: str, **change) -> Path:
    file = program_dir / "changes" / f"{name}.yaml"
    file.write_text(yaml.safe_dump(change, sort_keys=False))
    return file


def change(**kw) -> m.ChangeSet:
    return m.ChangeSet.model_validate({"id": "ep:t", "parent": "ep:p00", **kw})


def rejected(state, **kw) -> str:
    with pytest.raises(ChangeError) as info:
        apply_change(state, change(**kw))
    return str(info.value)


def obs(oid, alt, measure, value, unit, evidence, **kw) -> dict:
    return {"id": oid, "alternative": alt, "measure": measure, "value": value, "unit": unit, "evidence": evidence,
            **kw}


# --- operations ----------------------------------------------------------------------------------

def test_assign_changes_a_copy_and_the_kernel_recomputes(baseline, program_dir):
    after = apply_change(baseline, change(assign=[{"target": "obs:Z.fuel", "slot": "value", "new_value": 24}]))
    assert index(after)["obs:Z.fuel"].value == 24 and index(baseline)["obs:Z.fuel"].value == 18
    write(program_dir, "p01", id="ep:p01", parent="ep:p00",
          assign=[{"target": "obs:Z.fuel", "slot": "value", "new_value": 24}])
    program = Program.load(program_dir)
    r = program.evaluate("ep:p01").run
    assert r.value("y/alt:Z/meas:runtime")["y"] == pytest.approx(8.0)
    assert r.decision["recommended"] == ["alt:Z"]


def test_as_of_moves_the_clock(baseline):
    assert apply_change(baseline, change(as_of="2027-09-30")).as_of == date(2027, 9, 30)
    assert apply_change(baseline, change()).as_of == baseline.as_of


def test_remove_then_add_replaces_an_object(baseline):
    replacement = obs("obs:Y.cost", "alt:Y", "meas:cost", 2.8, "kUSD", "ev:quote-Y")
    after = apply_change(baseline, change(remove=["obs:Y.cost"], add={"observations": [replacement]}))
    assert index(after)["obs:Y.cost"].value == 2.8
    assert len(after.observations) == len(baseline.observations)


def test_assign_applies_after_add(baseline):
    new = obs("obs:Y.cost-2", "alt:Y", "meas:cost", 2.8, "kUSD", "ev:quote-Y", applicability="rejected")
    after = apply_change(baseline, change(add={"observations": [new]},
                                          assign=[{"target": "obs:Y.cost-2", "slot": "applicability",
                                                   "new_value": "provisional"}]))
    assert index(after)["obs:Y.cost-2"].applicability == "provisional"


def test_single_objects_are_added_only_when_absent(baseline):
    rule = {"id": "rule:new", "rule_kind": "additive_mavt", "tie_tolerance": 1e-6}
    assert "decision_rule is already rule:main" in rejected(baseline, add={"decision_rule": rule})
    after = apply_change(baseline, change(remove=["rule:main"], add={"decision_rule": rule}))
    assert after.decision_rule.id == "rule:new"


def test_unknown_target_and_slot_are_rejected_with_suggestions(baseline):
    message = rejected(baseline, assign=[{"target": "obs:Z.fuels", "slot": "value", "new_value": 1}])
    assert "no object obs:Z.fuels" in message and "did you mean 'obs:Z.fuel'" in message
    message = rejected(baseline, assign=[{"target": "obs:Z.fuel", "slot": "valeu", "new_value": 1}])
    assert "has no slot 'valeu'" in message and "did you mean 'value'" in message
    assert "remove obs:nope: no such object" in rejected(baseline, remove=["obs:nope"])


def test_assignments_are_type_checked(baseline):
    message = rejected(baseline, assign=[{"target": "obs:Z.fuel", "slot": "value", "new_value": "lots"}])
    assert "assign obs:Z.fuel.value" in message and "valid number" in message
    message = rejected(baseline, assign=[{"target": "rev:framing", "slot": "review_status", "new_value": "maybe"}])
    assert "review_status" in message


def test_ids_and_stamps_cannot_be_assigned(baseline):
    assert "ids are stable" in rejected(baseline, assign=[{"target": "alt:X", "slot": "id", "new_value": "alt:Q"}])
    message = rejected(baseline, assign=[{"target": "rev:framing", "slot": "covered_digest", "new_value": "x"}])
    assert "authors never write digests" in message


def test_duplicate_adds_are_rejected(baseline):
    dup = obs("obs:Z.fuel", "alt:Z", "meas:fuel", 24, "L", "ev:sheet-Z")
    assert "add obs:Z.fuel: this id already exists" in rejected(baseline, add={"observations": [dup]})
    twice = obs("obs:Z.fuel-2", "alt:Z", "meas:fuel", 24, "L", "ev:sheet-Z")
    assert "add obs:Z.fuel-2" in rejected(baseline, add={"observations": [twice, twice]})
    risk = {"id": "alt:X", "affects": ["alt:Y"], "likelihood": 1, "consequence": 1}
    assert "add alt:X: this id already exists" in rejected(baseline, add={"risks": [risk]})


def test_add_cannot_carry_a_clock(baseline):
    assert "set the change set's own as_of" in rejected(baseline, add={"as_of": "2027-05-01"})


@pytest.mark.parametrize(("kw", "message"), [
    ({"add": {"observations": [obs("obs:Z.fuel-2", "alt:Z", "meas:fuel", 24, "gal", "ev:sheet-Z")]}},
     "added: obs:Z.fuel-2.unit: unit 'gal' does not match meas:fuel unit 'L'"),
    ({"remove": ["meas:flow"]}, "from ep:p00: crit:flow.measure: refers to meas:flow, which does not exist"),
    ({"assign": [{"target": "crit:flow", "slot": "weight", "new_value": 0.6}]}, "weights sum to 1.1, not 1"),
    ({"add": {"observations": [obs("obs:Z.fuel-2", "alt:Z", "meas:fuel", 24, "L", "ev:nope")]}},
     "added: obs:Z.fuel-2.evidence: refers to ev:nope, which does not exist"),
    ({"add": {"observations": [obs("obs:Z.runtime", "alt:Z", "meas:runtime", 7, "h", "ev:sheet-Z")]}},
     "produced by more than one source"),
])
def test_an_invalid_result_is_rejected(baseline, kw, message):
    assert message in rejected(baseline, **kw)


def test_every_problem_is_listed_and_the_error_is_a_load_error(baseline):
    with pytest.raises(LoadError) as info:
        apply_change(baseline, change(remove=["obs:nope"], assign=[{"target": "alt:Q", "slot": "name",
                                                                     "new_value": "Q"}]))
    assert isinstance(info.value, ChangeError) and len(info.value.issues) == 2
    assert str(info.value).startswith("change set ep:t is rejected:")


# --- stamping ------------------------------------------------------------------------------------

APPROVE_Y = {"approvals": [{"id": "appr:p01-Y", "approved_alternative": "alt:Y", "approver_role": "role:approver"}]}


@pytest.fixture
def approved(program_dir) -> Path:
    """ep:p01 approves Y; ep:p02..p05 are children of it."""
    write(program_dir, "p01", id="ep:p01", parent="ep:p00", trigger="approval", add=APPROVE_Y)
    return program_dir


def test_an_approval_matches_in_its_own_episode(approved):
    program = Program.load(approved)
    ev = program.evaluate("ep:p01")
    approval = index(ev.state)["appr:p01-Y"]
    assert approval.recorded_in_episode == "ep:p01"
    assert approval.state_digest == state_digest(ev.state)
    assert ev.run.actionable["actionable"] is True and ev.run.actionable["approval"] == "appr:p01-Y"
    assert pending_actions(ev.run) == []


def test_an_approval_goes_stale_after_a_hashable_change(approved):
    write(approved, "p02", id="ep:p02", parent="ep:p01", trigger="cost",
          assign=[{"target": "obs:X.cost", "slot": "value", "new_value": 2.1}])
    r = Program.load(approved).evaluate("ep:p02").run
    assert r.decision["recommended"] == ["alt:Y"] and r.readiness["state"] == "ready"
    assert r.actionable["actionable"] is False and "covers an earlier state" in r.actionable["reason"]
    assert [(a.kind, a.target, a.role) for a in pending_actions(r)] == [("approve", "alt:Y", "role:approver")]


def test_an_approval_still_matches_after_prose_or_clock_changes(approved):
    write(approved, "p03", id="ep:p03", parent="ep:p01", trigger="no_change",
          assign=[{"target": "alt:Y", "slot": "description", "new_value": "Prose only."},
                  {"target": "role:approver", "slot": "assignees", "new_value": ["someone else"]}])
    write(approved, "p04", id="ep:p04", parent="ep:p01", trigger="time", as_of="2027-06-01")
    program = Program.load(approved)
    prose = program.evaluate("ep:p03")
    assert prose.run.actionable["actionable"] is True
    assert prose.impact == set() and prose.run.executed == [] and prose.verified is True
    clock = program.evaluate("ep:p04")
    assert clock.run.actionable["actionable"] is True and clock.impact and "clock" in clock.impact


def test_assigning_an_approval_restamps_it(approved):
    write(approved, "p02", id="ep:p02", parent="ep:p01",
          assign=[{"target": "obs:X.cost", "slot": "value", "new_value": 2.1}])
    write(approved, "p05", id="ep:p05", parent="ep:p02", trigger="approval",
          assign=[{"target": "appr:p01-Y", "slot": "valid_until", "new_value": "2027-12-01"}])
    ev = Program.load(approved).evaluate("ep:p05")
    assert index(ev.state)["appr:p01-Y"].recorded_in_episode == "ep:p05"
    assert ev.run.actionable["actionable"] is True


def test_human_actions_in_the_baseline_are_stamped_at_the_baseline(program_dir):
    (program_dir / "baseline" / "approvals.yaml").write_text(yaml.safe_dump(APPROVE_Y))
    ev = Program.load(program_dir).evaluate("ep:p00")
    assert index(ev.state)["appr:p01-Y"].recorded_in_episode == "ep:p00"
    assert index(ev.state)["rev:framing"].recorded_in_episode == "ep:p00"
    assert ev.run.actionable["actionable"] is True


def test_a_review_goes_stale_when_a_covered_object_changes(program_dir):
    write(program_dir, "p01", id="ep:p01", parent="ep:p00", trigger="evidence",
          assign=[{"target": "obs:Y.flow", "slot": "value", "new_value": 520}])  # not covered
    write(program_dir, "p02", id="ep:p02", parent="ep:p00", trigger="preference",
          assign=[{"target": "crit:flow", "slot": "anchor_best", "new_value": 650}])  # covered
    write(program_dir, "p03", id="ep:p03", parent="ep:p02", trigger="review",
          assign=[{"target": "rev:framing", "slot": "review_status", "new_value": "pass"}])
    program = Program.load(program_dir)
    assert program.evaluate("ep:p01").run.value("cond/bc:framing")["status"] == "pass"
    stale = program.evaluate("ep:p02").run
    assert stale.value("cond/bc:framing")["reason"].startswith("stale")
    assert stale.readiness["state"] == "pending"
    assert ("complete_review", "rev:framing", "role:reviewer") in {(a.kind, a.target, a.role)
                                                                   for a in pending_actions(stale)}
    redone = program.evaluate("ep:p03")
    assert redone.run.readiness["state"] == "ready"
    assert index(redone.state)["rev:framing"].recorded_in_episode == "ep:p03"


def test_adding_an_alternative_makes_an_all_selector_review_stale(baseline):
    after = apply_change(baseline, change(add={"alternatives": [{"id": "alt:W", "name": "W"}]}))
    assert index(after)["rev:framing"].covered_digest == index(baseline)["rev:framing"].covered_digest
    r = evaluate_state(after, FIXTURE)
    assert r.value("cond/bc:framing")["reason"].startswith("stale")


# --- programs and refresh ------------------------------------------------------------------------

def build_program(path: Path) -> Path:
    """A program that exercises every kind of change, with branches."""
    (path / "evaluators_v2.py").write_text(
        "def runtime_model(fuel_l, burn_l_per_h, duty):\n    return 0.9 * fuel_l / burn_l_per_h / duty\n")
    w = {"id": "ev:sheet-W", "name": "Data sheet W", "source_ref": "Synthetic data sheet W"}
    w_obs = [obs("obs:W.flow", "alt:W", "meas:flow", 450, "L/min", "ev:sheet-W"),
             obs("obs:W.fuel", "alt:W", "meas:fuel", 30, "L", "ev:sheet-W"),
             obs("obs:W.burn", "alt:W", "meas:burn", 2.5, "L/h", "ev:sheet-W"),
             obs("obs:W.cost", "alt:W", "meas:cost", 2.2, "kUSD", "ev:sheet-W")]
    changes = [
        ("p01", "ep:p00", "evidence", {"as_of": "2027-04-01",
                                       "assign": [{"target": "obs:Z.fuel", "slot": "value", "new_value": 24}]}),
        ("p02", "ep:p01", "approval", {"add": {"approvals": [{"id": "appr:p02-Z", "approved_alternative": "alt:Z",
                                                              "approver_role": "role:approver"}]}}),
        ("p03", "ep:p02", "no_change", {"assign": [{"target": "alt:Z", "slot": "name", "new_value": "Electric"}]}),
        ("p04", "ep:p03", "time", {"as_of": "2028-02-01"}),
        ("p05", "ep:p00", "assumption", {"assign": [{"target": "asm:duty", "slot": "value", "new_value": 1.25}]}),
        ("p06", "ep:p00", "requirement", {"assign": [{"target": "req:min-runtime", "slot": "threshold_value",
                                                      "new_value": 5}]}),
        ("p07", "ep:p00", "preference", {"assign": [{"target": "crit:flow", "slot": "weight", "new_value": 0.4},
                                                    {"target": "crit:cost", "slot": "weight", "new_value": 0.4}]}),
        ("p08", "ep:p00", "alternative", {"add": {"alternatives": [{"id": "alt:W", "name": "W"}],
                                                  "evidence_items": [w], "observations": w_obs}}),
        ("p09", "ep:p08", "review", {"assign": [{"target": "rev:framing", "slot": "review_status",
                                                 "new_value": "pass"}]}),
        ("p10", "ep:p00", "evidence", {"remove": ["obs:Y.cost"], "add": {"observations": [
            obs("obs:Y.cost-2", "alt:Y", "meas:cost", 2.6, "kUSD", "ev:quote-Y", applicability="provisional")]}}),
        ("p11", "ep:p10", "evidence", {"assign": [{"target": "obs:Y.cost-2", "slot": "applicability",
                                                   "new_value": "accepted"}]}),
        ("p12", "ep:p00", "model_version", {"assign": [{"target": "spec:runtime", "slot": "implementation",
                                                        "new_value": "evaluators_v2.py:runtime_model"}]}),
    ]
    for name, parent, trigger, body in changes:
        write(path, name, id=f"ep:{name}", parent=parent, trigger=trigger, **body)
    return path


@pytest.mark.parametrize("mode", ["impact", "cutoff"])
def test_selective_equals_full_for_every_episode(program_dir, mode):
    program = Program.load(build_program(program_dir))
    evaluations = program.evaluate_all(mode=mode)
    assert [ev.episode for ev in evaluations][:5] == ["ep:p00", "ep:p01", "ep:p02", "ep:p03", "ep:p04"]
    assert len(evaluations) == 13
    for ev in evaluations[1:]:
        assert ev.verified is True, (ev.episode, ev.mismatched)
        assert ev.run.mode == mode and ev.impact is not None
    assert evaluations[0].verified is None and evaluations[0].impact is None
    rows = {ev.episode: ev.summary() for ev in evaluations}
    assert all(r["executed"] < r["computed_nodes"] for ep, r in rows.items() if ep != "ep:p00")
    assert rows["ep:p00"]["executed"] == rows["ep:p00"]["computed_nodes"]


def test_program_outcomes_across_episodes(program_dir):
    program = Program.load(build_program(program_dir))
    rows = {ev.episode: ev.summary() for ev in program.evaluate_all()}
    assert rows["ep:p01"]["recommended"] == ["alt:Z"] and rows["ep:p02"]["actionable"] is True
    assert rows["ep:p03"]["actionable"] is True and rows["ep:p03"]["executed"] == 0
    assert rows["ep:p04"]["readiness"] == "pending" and rows["ep:p04"]["actionable"] is False  # quotes expired
    assert rows["ep:p05"]["feasible"] == ["alt:Y"]
    assert rows["ep:p06"]["recommended"] == ["alt:Z"]
    assert rows["ep:p08"]["readiness"] == "pending" and rows["ep:p09"]["readiness"] == "ready"
    assert "alt:Y" in rows["ep:p10"]["unresolved"] and "alt:Y" not in rows["ep:p11"]["unresolved"]
    assert rows["ep:p12"]["trigger"] == "model_version" and rows["ep:p12"]["impact_size"] > 0
    assert rows["ep:p01"]["as_of"] == "2027-04-01" and rows["ep:p02"]["as_of"] == "2027-04-01"


def test_tree_lineage_and_newest(program_dir):
    program = Program.load(build_program(program_dir))
    assert program.lineage("ep:p04") == ["ep:p00", "ep:p01", "ep:p02", "ep:p03", "ep:p04"]
    assert program.children()["ep:p00"] == ["ep:p01", "ep:p05", "ep:p06", "ep:p07", "ep:p08", "ep:p10", "ep:p12"]
    assert program.newest() == "ep:p04"  # the latest clock among leaves
    assert program.next_episode_id("ep:p04") == "ep:p13"


def test_program_load_errors(program_dir):
    write(program_dir, "p01", id="ep:p01", parent="ep:p02")
    write(program_dir, "p02", id="ep:p02", parent="ep:p01")
    with pytest.raises(LoadError, match="parent cycle"):
        Program.load(program_dir).episodes()
    write(program_dir, "p02", id="ep:p02", parent="ep:p09")
    with pytest.raises(LoadError, match="parent ep:p09 does not exist"):
        Program.load(program_dir).episodes()
    with pytest.raises(LoadError, match="no episode ep:p77"):
        Program.load(program_dir).evaluate("ep:p77")
    write(program_dir, "p02", id="ep:p02", parent="ep:p00", colour="green")
    with pytest.raises(LoadError, match="changes/p02.yaml: colour"):
        Program.load(program_dir)
    write(program_dir, "p02", id="ep:p01", parent="ep:p00")
    with pytest.raises(LoadError, match="defined twice"):
        Program.load(program_dir)


def test_a_rejected_change_names_its_file(program_dir):
    write(program_dir, "p01", id="ep:p01", parent="ep:p00", remove=["obs:nope"])
    with pytest.raises(ChangeError, match=r"ep:p01 \(changes/p01.yaml\) is rejected"):
        Program.load(program_dir).materialize("ep:p01")


def test_preview_does_not_add_the_episode(program_dir):
    program = Program.load(program_dir)
    ev = program.preview(change(id="ep:draft", assign=[{"target": "obs:Y.cost", "slot": "value",
                                                         "new_value": 3.6}]))  # over max cost
    assert ev.verified is True and ev.run.decision["recommended"] == ["alt:X"]
    assert "ep:draft" not in program.changes and list((program_dir / "changes").iterdir()) == []


# --- CLI -----------------------------------------------------------------------------------------

def cli(*args):
    return runner.invoke(app, [str(a) for a in args])


def test_cli_status(program_dir):
    build_program(program_dir)
    out = cli("program", "status", program_dir, "--json")
    assert out.exit_code == 0, out.output
    data = json.loads(out.output)
    rows = {r["episode"]: r for r in data["episodes"]}
    assert data["newest"] == "ep:p04" and rows["ep:p04"]["depth"] == 4
    assert rows["ep:p00"]["recommended"] == ["alt:Y"] and rows["ep:p02"]["actionable"] is True
    text = cli("program", "status", program_dir).output
    assert "└─ ep:p04" in text and "Newest episode: ep:p04" in text and "refresh_evidence" in text


def test_cli_refresh(program_dir):
    build_program(program_dir)
    out = cli("program", "refresh", program_dir, "--json")
    assert out.exit_code == 0, out.output
    data = json.loads(out.output)
    assert data["ok"] and data["all_verified"] and data["executed"] < data["computed_nodes"]
    assert "Every selective result equals its full recomputation" in cli("program", "refresh", program_dir).output


def test_cli_refresh_fails_when_a_selective_run_is_wrong(program_dir, monkeypatch):
    import pdo.episodes

    write(program_dir, "p01", id="ep:p01", parent="ep:p00",
          assign=[{"target": "obs:Z.fuel", "slot": "value", "new_value": 24}])
    monkeypatch.setattr(pdo.episodes, "impact_set", lambda old, new: set())  # an unsound impact analysis
    out = cli("program", "refresh", program_dir, "--json")
    assert out.exit_code == 1
    data = json.loads(out.output)
    assert data["ok"] is False and data["unverified"] == ["ep:p01"]
    assert "decision" in data["episodes"][1]["mismatched"]


def test_cli_record_approval_and_review(program_dir):
    out = cli("program", "record", program_dir, "--kind", "approval", "--target", "alt:Y", "--role", "role:approver",
              "--json")
    assert out.exit_code == 0, out.output
    data = json.loads(out.output)
    assert data["episode"] == "ep:p01" and data["parent"] == "ep:p00" and data["file"] == "changes/p01-approval.yaml"
    assert data["summary"]["actionable"] is True
    program = Program.load(program_dir)
    approval = index(program.materialize("ep:p01"))["appr:p01-Y"]
    assert approval.valid_until == date(2027, 3, 1) + timedelta(days=180)
    assert approval.recorded_in_episode == "ep:p01"
    out = cli("program", "record", program_dir, "--kind", "review", "--target", "rev:framing",
              "--role", "role:reviewer", "--status", "fail")
    assert out.exit_code == 0, out.output
    program = Program.load(program_dir)
    ev = program.evaluate("ep:p02")
    assert ev.parent == "ep:p01" and ev.run.readiness["state"] == "blocked"
    assert program.changes["ep:p02"].trigger == "review"


@pytest.mark.parametrize("args", [
    ["--kind", "approval", "--target", "alt:Q", "--role", "role:approver"],
    ["--kind", "approval", "--target", "alt:Y", "--role", "role:approver", "--status", "pass"],
    ["--kind", "review", "--target", "alt:Y", "--role", "role:reviewer"],
    ["--kind", "review", "--target", "rev:framing", "--role", "role:nobody"],
    ["--kind", "review", "--target", "rev:framing", "--role", "role:reviewer", "--status", "maybe"],
    ["--kind", "audit", "--target", "rev:framing", "--role", "role:reviewer"],
    ["--kind", "approval", "--target", "alt:Y", "--role", "role:approver", "--id", "ep:p00"],
])
def test_cli_record_rejects_bad_actions_without_writing(program_dir, args):
    out = cli("program", "record", program_dir, *args, "--json")
    assert out.exit_code == 1 and json.loads(out.output)["ok"] is False
    assert list((program_dir / "changes").iterdir()) == []


def test_cli_impact_is_a_dry_run(program_dir, tmp_path):
    draft = tmp_path / "draft.yaml"
    draft.write_text("assign:\n  - {target: obs:Y.cost, slot: value, new_value: 3.6}\n")  # over max cost
    out = cli("impact", program_dir, draft, "--json")
    assert out.exit_code == 0, out.output
    data = json.loads(out.output)
    assert data["parent"] == "ep:p00" and data["delta"] == {"changed": ["obs:Y.cost"], "added": [], "deleted": []}
    assert data["before"]["recommended"] == ["alt:Y"] and data["after"]["recommended"] == ["alt:X"]
    assert data["verified"] is True and 0 < data["executed"] < data["computed_nodes"]
    assert data["impact_size"] < data["total_nodes"] and "U/alt:Y" in data["value_changes"]
    assert list((program_dir / "changes").iterdir()) == []
    text = cli("impact", program_dir, draft).output
    assert "Delta: 1 changed" in text
    assert next(line for line in text.splitlines() if line.startswith("recommended")).endswith("changed")


def test_cli_impact_on_a_later_episode_and_a_rejected_change(program_dir, tmp_path):
    write(program_dir, "p01", id="ep:p01", parent="ep:p00", trigger="approval", add=APPROVE_Y)
    draft = tmp_path / "draft.yaml"
    draft.write_text("assign:\n  - {target: alt:Y, slot: description, new_value: prose}\n")
    data = json.loads(cli("impact", program_dir, draft, "--episode", "ep:p01", "--json").output)
    assert data["impact_size"] == 0 and data["executed"] == 0 and data["after"]["actionable"] is True
    draft.write_text("assign:\n  - {target: alt:Q, slot: name, new_value: Q}\n")
    out = cli("impact", program_dir, draft, "--json")
    assert out.exit_code == 1 and "no object alt:Q" in json.loads(out.output)["error"]


def test_run_and_validate_use_program_episodes(program_dir):
    write(program_dir, "p01", id="ep:p01", parent="ep:p00",
          assign=[{"target": "obs:Z.fuel", "slot": "value", "new_value": 24}])
    out = cli("run", program_dir, "--episode", "ep:p01", "--json")
    assert out.exit_code == 0 and json.loads(out.output)["recommended"] == ["alt:Z"]
    assert cli("validate", program_dir).exit_code == 0
    write(program_dir, "p02", id="ep:p02", parent="ep:p01", remove=["meas:flow"])
    out = cli("validate", program_dir, "--json")
    assert out.exit_code == 1 and "ep:p02" in " ".join(json.loads(out.output)["errors"])
