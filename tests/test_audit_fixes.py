"""Regression tests for the defects the stage 2 verification audit found, one per defect."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
import yaml
from conftest import ROOT, VEHICLE
from typer.testing import CliRunner

from pdo.cli import app
from pdo.episodes import ChangeError, Program
from pdo.evaluate import evaluate
from pdo.load import LoadError, load_state

runner = CliRunner()


@pytest.fixture
def program_dir(tmp_path):
    """A copy of the vehicle program that tests can add change sets to."""
    dest = tmp_path / "vehicle"
    shutil.copytree(VEHICLE, dest, ignore=shutil.ignore_patterns("sysml", "__pycache__"))
    return dest


def add_change(program_dir: Path, name: str, change: dict) -> None:
    (program_dir / "changes" / f"{name}.yaml").write_text(yaml.safe_dump(change, sort_keys=False))


def test_cutoff_equals_full_after_an_id_only_rename(program_dir):
    """An object re-recorded under a new id with identical content must not reuse stale values."""
    add_change(program_dir, "x01-rename-requirement", {
        "id": "ep:x01", "parent": "ep:e01", "trigger": "requirement",
        "remove": ["req:min-reliability"],
        "add": {"requirements": [{"id": "req:min-rel-2", "measure": "meas:reliability", "comparator": "ge",
                                  "threshold_value": 0.95, "unit": "1"}]}})
    program = Program.load(program_dir)
    evaluation = program.evaluate("ep:x01", mode="cutoff", verify=True)
    assert evaluation.verified, evaluation.mismatched
    assert evaluation.run.value("F/alt:C")["failed"] == ["req:min-rel-2"]
    assert evaluation.run.actionable["actionable"] is False  # the state changed, so the approval is stale


def test_a_prose_edit_does_not_revive_a_stale_approval(program_dir):
    add_change(program_dir, "x02-typo", {
        "id": "ep:x02", "parent": "ep:e02", "trigger": "no_change",
        "assign": [{"target": "appr:e01-b", "slot": "rationale", "new_value": "typo fixed"}]})
    program = Program.load(program_dir)
    assert program.evaluate("ep:e02").run.actionable["actionable"] is False
    assert program.evaluate("ep:x02").run.actionable["actionable"] is False


def test_a_prose_edit_does_not_refresh_a_stale_review_but_a_new_status_does(program_dir):
    add_change(program_dir, "x03-rename-review", {
        "id": "ep:x03", "parent": "ep:e12", "trigger": "no_change",
        "assign": [{"target": "rev:framing", "slot": "rationale", "new_value": "reworded"}]})
    add_change(program_dir, "x04-redo-review", {
        "id": "ep:x04", "parent": "ep:e12", "trigger": "review",
        "assign": [{"target": "rev:framing", "slot": "review_status", "new_value": "pass"}]})
    program = Program.load(program_dir)
    assert program.evaluate("ep:e12").run.readiness["unknown"] == ["bc:framing-review"]
    assert program.evaluate("ep:x03").run.readiness["unknown"] == ["bc:framing-review"]
    assert program.evaluate("ep:x04").run.readiness["state"] == "ready"


def test_authored_digests_are_rejected(tmp_path, program_dir):
    governance = program_dir / "baseline" / "governance.yaml"
    data = yaml.safe_load(governance.read_text())
    data["reviews"][0]["covered_digest"] = "forged"
    governance.write_text(yaml.safe_dump(data, sort_keys=False))
    with pytest.raises(LoadError, match="authors never write digests"):
        Program.load(program_dir)
    with pytest.raises(LoadError, match="authors never write digests"):
        load_state(program_dir / "baseline")


def test_a_change_set_cannot_add_a_stamped_approval(program_dir):
    add_change(program_dir, "x05-forged", {
        "id": "ep:x05", "parent": "ep:e00", "trigger": "approval",
        "add": {"approvals": [{"id": "appr:forged", "approved_alternative": "alt:B", "approver_role": "role:mda",
                               "state_digest": "copied-from-pdo-run"}]}})
    with pytest.raises(LoadError, match="authors never write digests"):
        Program.load(program_dir)


def test_run_refuses_a_state_that_validate_rejects(tmp_path):
    shutil.copytree(VEHICLE / "baseline", tmp_path / "state")
    shutil.copy(VEHICLE / "evaluators.py", tmp_path / "state")
    decision = tmp_path / "state" / "decision.yaml"
    data = yaml.safe_load(decision.read_text())
    for criterion in data["criteria"]:
        criterion["weight"] = 0.5
    decision.write_text(yaml.safe_dump(data, sort_keys=False))
    with pytest.raises(LoadError, match="weights sum to"):
        evaluate(tmp_path / "state")
    out = runner.invoke(app, ["run", str(tmp_path / "state"), "--json"])
    assert out.exit_code == 1 and "weights sum to" in json.loads(out.output)["error"]


def test_a_stale_failed_review_still_fails(program_dir):
    add_change(program_dir, "x06-review-fails", {
        "id": "ep:x06", "parent": "ep:e01", "trigger": "review",
        "assign": [{"target": "rev:framing", "slot": "review_status", "new_value": "fail"}]})
    add_change(program_dir, "x07-range-310", {
        "id": "ep:x07", "parent": "ep:x06", "trigger": "requirement",
        "assign": [{"target": "req:min-range", "slot": "threshold_value", "new_value": 310}]})
    program = Program.load(program_dir)
    assert program.evaluate("ep:x06").run.readiness["state"] == "blocked"
    assert program.evaluate("ep:x07").run.readiness["state"] == "blocked"


def test_approvals_expire_by_policy_however_they_are_recorded(program_dir):
    """e01's approval has no valid_until; the policy's 365 days apply from its episode's as_of."""
    add_change(program_dir, "x08-much-later", {"id": "ep:x08", "parent": "ep:e01", "trigger": "time",
                                               "as_of": "2028-03-01"})
    program = Program.load(program_dir)
    approval = next(a for a in program.materialize("ep:e01").approvals if a.id == "appr:e01-b")
    assert str(approval.valid_until) == "2028-02-01"
    result = program.evaluate("ep:x08").run
    assert result.actionable["actionable"] is False and "expired" in result.actionable["reason"]


def test_an_undeclared_evaluator_parameter_is_refused(tmp_path):
    shutil.copytree(VEHICLE / "baseline", tmp_path / "state")
    (tmp_path / "state" / "evaluators.py").write_text(textwrap.dedent("""
        def range_model(tank_gal, mpg, terrain_factor, reserve_fraction=0.1):
            return tank_gal * mpg * terrain_factor * (1 - reserve_fraction)
    """))
    result = evaluate(tmp_path / "state")
    run_result = result.results["run/spec:range-model/alt:A"]
    assert run_result.exec_status == "failed" and "reserve_fraction" in run_result.error
    assert result.value("y/alt:A/meas:range")["y"] is None


def test_a_ref_binding_must_name_an_assumption(tmp_path):
    shutil.copytree(VEHICLE / "baseline", tmp_path / "state")
    shutil.copy(VEHICLE / "evaluators.py", tmp_path / "state")
    path = tmp_path / "state" / "evaluators.yaml"
    data = yaml.safe_load(path.read_text())
    data["evaluation_specifications"][0]["inputs"][2] = {"arg": "terrain_factor", "ref": "obs:B.reliability",
                                                         "unit": "1"}
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    with pytest.raises(LoadError, match="not a Assumption"):
        evaluate(tmp_path / "state")


def test_the_declared_expected_rule_keeps_the_point_ranking_provisional():
    result = evaluate(ROOT / "examples" / "fires_sensor")
    assert result.readiness["state"] == "pending"
    assert any("recommendation" in c for c in result.readiness["unknown"])


def test_sysml_import_does_not_depend_on_the_working_directory():
    model = VEHICLE / "sysml" / "vehicle.sysml"
    mapping = VEHICLE / "sysml" / "mapping.yaml"
    code = ("import sys, json; from pdo.sysml import import_sysml; "
            "r = import_sysml(sys.argv[1], sys.argv[2]); "
            "print(json.dumps(r.state['evidence_items'], sort_keys=True))")
    outs = {subprocess.run([sys.executable, "-c", code, str(model), str(mapping)], cwd=cwd, capture_output=True,
                           text=True, check=True).stdout for cwd in (ROOT, model.parent, ROOT / "tests")}
    assert len(outs) == 1


def test_a_package_replays_from_any_directory(tmp_path):
    out = runner.invoke(app, ["package", str(VEHICLE), "--out", str(tmp_path / "pkg"), "--json"])
    assert out.exit_code == 0, out.output
    record = json.loads((tmp_path / "pkg" / "record.json").read_text())
    assert all("duration_ms" not in r for r in record["runs"])  # byte-reproducible
    replay = subprocess.run([sys.executable, "-m", "pdo.cli", "replay", str(tmp_path / "pkg" / "record.json"),
                             "--json"], cwd=tmp_path, capture_output=True, text=True,
                            env={**os.environ, "PYTHONPATH": str(ROOT / "src")})
    assert replay.returncode == 0, replay.stdout + replay.stderr


def test_a_review_claim_reports_staleness(tmp_path):
    out = runner.invoke(app, ["package", str(VEHICLE), "--episode", "ep:e12", "--out", str(tmp_path), "--json"])
    assert out.exit_code == 0, out.output
    record = json.loads((tmp_path / "record.json").read_text())
    claim = next(c for c in record["claims"] if c["produced_by"] == "rev:framing")
    assert claim["status"] == "unknown"


def test_change_errors_are_still_reported(program_dir):
    add_change(program_dir, "x09-bad", {"id": "ep:x09", "parent": "ep:e00", "assign": [
        {"target": "obs:nowhere", "slot": "value", "new_value": 1}]})
    with pytest.raises(ChangeError):
        Program.load(program_dir).evaluate("ep:x09")
