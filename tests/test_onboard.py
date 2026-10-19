"""Onboarding: from raw inputs (reports and a SysML model) to a validated, run, studied and actionable decision.

The end-to-end test plays an agent following skills/decision-onboarding: at each step it asks `pdo onboard`
for the next step, checks it is the one expected, and does it with the CLI, supplying only what a person
would say (the question, the roles, the mapping, the quotes, the framing and each judgement).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml
from conftest import ROOT, VEHICLE
from typer.testing import CliRunner

from pdo.cli import app
from pdo.onboard import OnboardError, init_program, render, status

runner = CliRunner()
QUESTION = "Which vehicle configuration should the program carry into development?"
DOCS = VEHICLE / "docs"
SHEETS, REPORT = "inputs/docs/configuration-sheets.pdf", "inputs/docs/reliability-test-report.docx"

pytest.importorskip("docx")
pytest.importorskip("pypdf")


def cli(*args) -> dict:
    out = runner.invoke(app, [str(a) for a in args] + ["--json"])
    assert out.exit_code == 0, out.output
    return json.loads(out.output)


def next_step(d: Path) -> dict:
    report = cli("onboard", d)
    return report["next"] | {"stage": report["stage"]}


def write(path: Path, data: dict) -> Path:
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


def roles() -> list[dict]:
    return yaml.safe_load((VEHICLE / "baseline" / "governance.yaml").read_text())["roles"]


def mapping() -> dict:
    """The vehicle mapping, as the person completes it: values the reports state come from the reports."""
    data = yaml.safe_load((VEHICLE / "sysml" / "mapping.yaml").read_text())
    for key in ("tankCapacity", "fuelEconomy"):
        data["measures"][key]["observation"] = False
    data["measures"]["reliability"] = {"id": "meas:reliability", "observation": False}
    data["evaluations"]["RangeModel"] = {"id": "spec:range-model", "implementation": "evaluators.py:range_model",
                                         "implementation_version": "1.0"}
    data["assumptions"]["RangeModel::terrain_factor"] = {
        "id": "asm:terrain-factor", "critical": True, "validation_status": "validated",
        "plausible_low": 0.80, "plausible_high": 1.05}
    data["evidence"] = {"vehicle.sysml": {"id": "ev:sysml-vehicle", "owner_role": "role:systems-engineer"}}
    return data


def framing() -> dict:
    """What the decision owner states: criteria, weights, rule, scenarios, readiness, risks and a review to do."""
    out: dict = {}
    for name in ("decision.yaml", "governance.yaml"):
        data = yaml.safe_load((VEHICLE / "baseline" / name).read_text())
        out.update({k: v for k, v in data.items() if k not in ("measures", "requirements", "roles")})
    for review in out["reviews"]:
        review["review_status"] = "pending"  # the bias reviewer has not looked yet
    return out


def sheets_proposals() -> dict:
    evidence = [{"id": f"ev:design-{a}", "name": f"Configuration sheet {a}", "evidence_kind": "specification",
                 "source_ref": f"Synthetic configuration sheet, vehicle {a}, rev 1", "document_path": SHEETS,
                 "issued_on": "2026-11-02", "owner_role": "role:systems-engineer"} for a in "ABC"]
    tanks = {"A": "40", "B": "45", "C": "47.5"}
    observations = []
    for a in "ABC":
        observations += [
            {"id": f"obs:{a}.tank", "alternative": f"alt:{a}", "measure": "meas:tank-capacity",
             "value": float(tanks[a]), "evidence": f"ev:design-{a}",
             "quote": f"configuration {a} holds a usable fuel capacity of {tanks[a]} gal"},
            {"id": f"obs:{a}.mpg", "alternative": f"alt:{a}", "measure": "meas:fuel-economy", "value": 8.0,
             "evidence": f"ev:design-{a}", "quote": f"configuration {a} is rated at a fuel economy of 8.0 mi/gal"}]
    return {"accept": True, "evidence_items": evidence, "observations": observations}


def report_proposals() -> tuple[dict, dict]:
    evidence = [{"id": f"ev:reliability-{a}", "name": f"Reliability test report {a}", "evidence_kind": "test report",
                 "source_ref": f"Synthetic reliability growth test, vehicle {a}, phase 1", "document_path": REPORT,
                 "issued_on": "2026-12-10", "owner_role": "role:test-engineer"} for a in "ABC"]
    quote = {"A": ("0.97", "3.1"), "B": ("0.98", "3.2"), "C": ("0.94", "3.3")}

    def obs(a: str) -> dict:
        value, section = quote[a]
        return {"id": f"obs:{a}.reliability", "alternative": f"alt:{a}", "measure": "meas:reliability",
                "value": float(value), "evidence": f"ev:reliability-{a}", "section": section,
                "quote": f"Its demonstrated mission reliability is {value}"}

    accepted = {"accept": True, "evidence_items": evidence, "observations": [obs("A"), obs("C")]}
    proposed = {"observations": [obs("B")]}  # the agent's proposal; the test engineer reviews it later
    return accepted, proposed


def test_vehicle_from_raw_inputs_through_the_three_funnels(tmp_path):
    d = tmp_path / "vehicle"

    # Intake: the question, the owner, the reports and the model.
    made = cli("init", d, "--question", QUESTION, "--owner", "role:program-manager", "--name", "Vehicle",
               "--doc", DOCS / "configuration-sheets.pdf", "--doc", DOCS / "reliability-test-report.docx",
               "--model", VEHICLE / "sysml" / "vehicle.sysml")
    assert sorted(made["copied"]) == [SHEETS, REPORT, "inputs/models/vehicle.sysml"]
    assert (d / "program.yaml").exists() and (d / "baseline" / "roles.yaml").exists()
    first = next_step(d)
    assert first["step"] == "models" and first["stage"] == "construct" and "mapping.yaml" in first["do"]

    # Who holds which role, as the owner states it.
    write(d / "baseline" / "roles.yaml", {"roles": roles()})

    # Funnel 1, the SysML import: the mapping records what the model does not say.
    write(d / "inputs" / "models" / "mapping.yaml", mapping())
    shutil.copy(VEHICLE / "evaluators.py", d / "evaluators.py")
    step = next_step(d)
    assert step["step"] == "models" and "--map" in step["command"]
    imported = cli(*step["command"].split()[1:])
    assert imported["counts"]["alternatives"] == 3 and imported["counts"]["requirements"] == 3
    assert {v["measure"] for v in imported["values_elsewhere"]} == {"meas:tank-capacity", "meas:fuel-economy",
                                                                   "meas:reliability"}
    assert [o["id"] for o in imported["state"]["observations"]] == ["obs:A.cost", "obs:B.cost", "obs:C.cost"]

    # Funnel 1, the documents: each value with the passage that states it.
    step = next_step(d)
    assert step["step"] == "documents" and step["tool"] == "pdo_docs_extract" and "--baseline" in step["command"]
    rows = cli("docs", "extract", d, write(tmp_path / "sheets.yaml", sheets_proposals()), "--write", "--baseline",
               "--role", "role:systems-engineer")["rows"]
    assert len(rows) == 6 and {r["section"] for r in rows} == {"2", "3", "4"}
    accepted, proposed = report_proposals()
    cli("docs", "extract", d, write(tmp_path / "report.yaml", accepted), "--write", "--baseline",
        "--role", "role:test-engineer")
    cli("docs", "extract", d, write(tmp_path / "proposed.yaml", proposed), "--write", "--baseline")
    documents = yaml.safe_load((d / "baseline" / "documents.yaml").read_text())
    assert len(documents["evidence_items"]) == 6 and len(documents["observations"]) == 9
    assert all(o["locator"]["document_digest"] for o in documents["observations"])
    assert [o["id"] for o in documents["observations"] if o.get("applicability") == "provisional"] == \
        ["obs:B.reliability"]

    # Funnel 1, the framing: only the owner can supply it.
    step = next_step(d)
    assert step["step"] == "framing" and step["who"] == "role:program-manager"
    write(d / "baseline" / "framing.yaml", framing())

    # Funnel 2: the decision runs; B's reliability is the agent's proposal until the test engineer accepts it.
    step = next_step(d)
    assert step["stage"] == "run" and step["step"] == "evidence"
    assert "--kind applicability --target obs:B.reliability --role role:test-engineer" in step["command"]
    report = status(d)
    by_id = {s["id"]: s for s in report["steps"]}
    assert all(by_id[k]["state"] == "done" for k in ("question", "roles", "inputs", "models", "documents", "framing",
                                                       "evaluators", "valid"))
    assert "alt:B ?" in by_id["run"]["details"][0]  # unknown until accepted
    recorded = cli(*step["command"].split()[1:])
    assert recorded["episode"] == "ep:e01" and recorded["after"]["recommended"] == ["alt:B"]

    # The trade study, built from raw inputs, reproduces the reference decision number for number.
    ran = cli("run", d, "--episode", "ep:e01")
    assert ran["recommended"] == ["alt:B"]
    assert [round(a["utility"], 6) for a in ran["alternatives"]] == [0.466667, 0.533333, 0.62]
    assert ran["infeasible"] == ["alt:C"]

    # Funnel 3: study it, then the people who own the review and the approval act.
    step = next_step(d)
    assert step["stage"] == "study" and step["step"] == "study" and step["tool"] == "pdo_study"
    studied = cli(*step["command"].split()[1:])
    assert studied["episode"] == "ep:e01" and studied["recommended"] == ["alt:B"]
    assert "nearest flip" in studied["findings"]
    assert (d / "reports" / "package" / "record.json").exists() and (d / "reports" / "study.md").exists()
    step = next_step(d)
    assert step["step"] == "authorize" and "--kind review --target rev:framing" in step["command"]
    cli(*step["command"].split()[1:])
    step = next_step(d)  # a new episode: the study is for the previous state
    assert step["step"] == "study"
    cli(*step["command"].split()[1:])
    step = next_step(d)
    assert step["step"] == "authorize" and "--kind approval --target alt:B --role role:mda" in step["command"]
    cli(*step["command"].split()[1:])
    step = next_step(d)
    assert step["step"] == "study"  # re-study what will be acted on
    cli(*step["command"].split()[1:])

    final = cli("onboard", d)
    assert final["stage"] == "done" and final["next"]["tool"] == "pdo_refresh"
    assert all(f["complete"] for f in final["funnels"])
    assert cli("program", "refresh", d)["all_verified"] is True
    assert cli("docs", "verify", d)["errors"] == 0
    assert "Next" in render(status(d))


def test_onboard_names_the_first_step_of_an_empty_program(tmp_path):
    made = init_program(tmp_path / "d")
    assert made["next"]["step"] == "question" and made["stage"] == "construct"
    report = status(tmp_path / "d")
    assert [s["state"] for s in report["steps"]][:3] == ["todo", "todo", "todo"]
    assert report["next"]["who"] == "the decision owner"
    with pytest.raises(OnboardError, match="already holds"):
        init_program(tmp_path / "d")
    with pytest.raises(OnboardError, match="role id"):
        init_program(tmp_path / "e", owner="program-manager")
    with pytest.raises(OnboardError, match="not a report"):
        init_program(tmp_path / "f", docs=[ROOT / "README.md", ROOT / "Makefile"])


def test_onboard_reads_an_existing_program(tmp_path):
    d = tmp_path / "vehicle"
    shutil.copytree(VEHICLE, d, ignore=shutil.ignore_patterns("__pycache__"))
    report = status(d)
    steps = {s["id"]: s for s in report["steps"]}
    assert steps["models"]["state"] == "skip" and steps["valid"]["state"] == "done"
    assert steps["documents"]["state"] == "done" and "9 located values" in steps["documents"]["summary"]
    assert report["next"]["step"] == "study" and report["stage"] == "study"


def test_extract_into_the_baseline_only_until_an_episode_is_built_on_it(tmp_path):
    d = tmp_path / "vehicle"
    shutil.copytree(VEHICLE, d, ignore=shutil.ignore_patterns("__pycache__"))
    proposals = write(tmp_path / "p.yaml", {"observations": [{"id": "obs:A.reliability", "value": 0.97, "quote":
                                                              "Its demonstrated mission reliability is 0.97"}]})
    out = runner.invoke(app, ["docs", "extract", str(d), str(proposals), "--baseline", "--write", "--json"])
    assert out.exit_code == 1 and "episodes are built on the baseline" in out.output


def test_extract_into_a_state_directory_writes_only_its_documents_file(tmp_path):
    d = tmp_path / "state"
    shutil.copytree(VEHICLE / "baseline", d)
    shutil.copytree(VEHICLE / "docs", d / "docs")
    shutil.copy(VEHICLE / "evaluators.py", d)
    # obs:A.reliability lives in evidence.yaml, which pdo docs extract does not own.
    proposals = write(tmp_path / "p.yaml", {"accept": True, "observations": [
        {"id": "obs:A.reliability", "value": 0.97, "quote": "Its demonstrated mission reliability is 0.97"}]})
    out = runner.invoke(app, ["docs", "extract", str(d), str(proposals), "--write", "--role", "role:test-engineer",
                              "--json"])
    assert out.exit_code == 1 and "defined outside" in out.output and not (d / "documents.yaml").exists()
    # A new observation is written to documents.yaml, and the state still loads.
    evidence = yaml.safe_load((d / "evidence.yaml").read_text())
    evidence["observations"] = [o for o in evidence["observations"] if o["id"] != "obs:C.reliability"]
    write(d / "evidence.yaml", evidence)
    proposals = write(tmp_path / "q.yaml", {"evidence": "ev:reliability-C", "accept": True, "observations": [
        {"id": "obs:C.reliability", "alternative": "alt:C", "measure": "meas:reliability", "value": 0.94,
         "quote": "Its demonstrated mission reliability is 0.94"}]})
    r = cli("docs", "extract", d, proposals, "--write", "--role", "role:test-engineer")
    assert r["written"] and r["file"] == "documents.yaml"
    assert cli("validate", d)["valid"] is True
    assert cli("run", d)["recommended"] == ["alt:B"]


def test_every_skill_section_onboard_names_exists():
    import re

    from pdo import onboard

    skill = (ROOT / onboard.SKILL).read_text()
    assert skill.startswith("---\nname: decision-onboarding\n")
    anchors = {re.sub(r"[^a-z0-9 -]", "", h.lower()).replace(" ", "-") for h in re.findall(r"^## (.+)$", skill, re.M)}
    named = set(re.findall(r'section="([^"]+)"', (ROOT / "src" / "pdo" / "onboard.py").read_text()))
    assert named and named <= anchors, named - anchors
