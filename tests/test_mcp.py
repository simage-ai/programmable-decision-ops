"""The agent tool surface: pdo.tools functions, the MCP server built on them, and the utility commands."""

from __future__ import annotations

import asyncio
import json
import math
import shutil
from datetime import date
from pathlib import Path

import pytest
import yaml
from conftest import ROOT, VEHICLE
from typer.testing import CliRunner

from pdo import tools
from pdo.cli import app

FIRES = ROOT / "examples" / "fires_sensor"
SYSML = VEHICLE / "sysml"
runner = CliRunner()


def ok(result: dict) -> dict:
    """Assert a successful, JSON-ready tool result."""
    assert result["ok"] is True, result
    json.dumps(result, allow_nan=False)
    return result


def failed(result: dict, *fragments: str) -> dict:
    """Assert a user-error result: ok false with an error (containing each fragment) and a hint."""
    assert result["ok"] is False, result
    assert result["error"] and result["hint"], result
    for fragment in fragments:
        assert fragment in result["error"], result["error"]
    json.dumps(result, allow_nan=False)
    return result


@pytest.fixture
def program(tmp_path) -> Path:
    """A private copy of the vehicle program, for the tools that write."""
    path = tmp_path / "vehicle"
    shutil.copytree(VEHICLE, path, ignore=tools.COPY_IGNORE)
    return path


def change_files(path: Path) -> list[str]:
    return sorted(p.name for p in (path / "changes").glob("*.yaml"))


def utilities(run_result: dict) -> dict[str, float | None]:
    return {a["alternative"]: a["utility"] for a in run_result["alternatives"]}


# --- schema ---------------------------------------------------------------------------------------

def test_describe_schema_lists_classes_and_enums():
    r = ok(tools.describe_schema())
    assert r["source"] == "linkml"
    names = {c["name"] for c in r["classes"]}
    assert {"Observation", "ChangeSet", "HumanApproval", "DecisionState"} <= names
    enums = {e["name"]: e["values"] for e in r["enums"]}
    assert "cost" in enums["TriggerKind"] and "provisional" in enums["Applicability"]
    assert {"name": "observations", "range": "Observation"} in r["state_slots"]


def test_describe_schema_class_slots_carry_identity_and_ranges():
    r = ok(tools.describe_schema("observation"))  # matched case-insensitively
    assert r["name"] == "Observation" and r["kind"] == "class" and r["is_a"] == "NamedThing"
    assert r["state_collection"] == "observations"
    slots = {s["name"]: s for s in r["slots"]}
    assert slots["value"]["identity"] == "hashable" and slots["value"]["range"] == "float"
    assert slots["applicability_rationale"]["identity"] == "unhashable"
    assert slots["id"]["identity"] == "identifier" and slots["id"]["required"]
    assert slots["alternative"]["required"] and slots["alternative"]["inlined"] is False
    assert slots["applicability"]["values"] == ["accepted", "provisional", "rejected"]
    assert ok(tools.describe_schema("ReviewStatus"))["kind"] == "enum"


def test_describe_schema_unknown_class_suggests_one():
    failed(tools.describe_schema("Observaton"), "did you mean Observation")


def test_describe_schema_falls_back_to_the_pydantic_models(monkeypatch):
    monkeypatch.setattr(tools, "_schema_view", lambda: None)
    r = ok(tools.describe_schema("Observation"))
    assert r["source"] == "pydantic" and r["state_collection"] == "observations"
    slots = {s["name"]: s for s in r["slots"]}
    assert slots["value"]["identity"] == "hashable" and slots["owner_role"]["identity"] == "unhashable"
    assert slots["alternative"]["required"] and not slots["value"]["required"]
    assert slots["applicability"]["values"] == ["accepted", "provisional", "rejected"]
    assert ok(tools.describe_schema())["source"] == "pydantic"


# --- validate and run -----------------------------------------------------------------------------

def test_validate_a_program_and_its_episodes():
    r = ok(tools.validate(VEHICLE))
    assert r["valid"] and r["errors"] == [] and r["episodes"] == 17 and r["objects"] > 0


def test_validate_reports_an_invalid_state_as_a_verdict(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("alternatives: [{id: alt:A, colour: green}]\n")
    r = ok(tools.validate(bad))
    assert r["valid"] is False and r["errors"] and r["hint"]
    failed(tools.validate(tmp_path / "missing.yaml"), "no such file")


def test_validate_names_the_rejected_episode_and_skips_its_children(program):
    (program / "changes" / "e13-bad.yaml").write_text(yaml.safe_dump(
        {"id": "ep:e13", "parent": "ep:e12", "assign": [{"target": "obs:B.cst", "slot": "value", "new_value": 1}]}))
    (program / "changes" / "e14-child.yaml").write_text(yaml.safe_dump({"id": "ep:e14", "parent": "ep:e13"}))
    r = ok(tools.validate(program))
    assert r["valid"] is False
    assert len(r["errors"]) == 1 and "ep:e13" in r["errors"][0] and "obs:B.cst" in r["errors"][0]
    assert r["unchecked_episodes"] == ["ep:e14"]


def test_run_reports_the_kernel_result():
    r = ok(tools.run(VEHICLE))
    assert r["episode"] == "ep:e00" and r["recommended"] == ["alt:B"] and r["readiness"] == "ready"
    u = utilities(r)
    assert u["alt:A"] == pytest.approx(0.466667, abs=1e-6) and u["alt:B"] == pytest.approx(0.533333, abs=1e-6)
    assert u["alt:C"] == pytest.approx(0.62)
    c = next(a for a in r["alternatives"] if a["alternative"] == "alt:C")
    assert c["feasibility"] == "fail" and c["checks"]["req:min-reliability"]["g"] == pytest.approx(0.01)


def test_run_an_episode_is_verified_against_full_recomputation():
    r = ok(tools.run(VEHICLE, "ep:e05"))
    assert r["parent"] == "ep:e04" and r["verified"] is True and r["recommended"] == ["alt:B"]
    failed(tools.run(VEHICLE, "ep:e5"), "no episode ep:e5", "did you mean")
    failed(tools.run(VEHICLE / "baseline", "ep:e05"), "not a program directory")


def test_explain_traces_a_node_to_its_inputs():
    r = ok(tools.explain(VEHICLE, "U/alt:B", depth=2))
    assert r["value"]["U"] == pytest.approx(0.533333, abs=1e-6)
    inputs = {i["node"]: i for i in r["inputs"]}
    assert "v/crit:cost/alt:B" in inputs and "inputs" in inputs["v/crit:cost/alt:B"]
    assert failed(tools.explain(VEHICLE, "U/alt:Z"), "no node")["hint"] == tools.NODE_HINT
    failed(tools.explain(VEHICLE, "decision", depth=0), "depth")


# --- analysis -------------------------------------------------------------------------------------

def test_flip_finds_the_cost_boundary():
    r = ok(tools.flip(VEHICLE, param="obs:B.cost.value"))
    (f,) = r["flips"]
    assert f["flip_number"] == pytest.approx(1.30) and f["new_recommendation"] == ["alt:A"] and f["verified"]
    weights = ok(tools.flip(VEHICLE, param=["crit:cost.weight"]))
    assert weights["flips"] and all(f["method"] == "closed_form" for f in weights["flips"])
    failed(tools.flip(VEHICLE, param="obs:B.nope.value"), "not a numeric input")
    failed(tools.flip(VEHICLE, grid=1), "grid")


def test_scenarios_report_robustness():
    r = ok(tools.scenarios(VEHICLE))
    assert [s["scenario"] for s in r["scenarios"]] == ["sc:nominal", "sc:hot-desert", "sc:cost-growth",
                                                     "sc:optimistic-reliability"]
    assert r["robust"] is all(s["robust"] for s in r["scenarios"])


def test_readiness_names_the_condition_that_is_open():
    r = ok(tools.readiness(VEHICLE, "ep:e12"))
    assert r["readiness"] == "pending" and r["unknown"] == ["bc:framing-review"]
    cond = next(c for c in r["conditions"] if c["condition"] == "bc:framing-review")
    assert cond["predicate"] == "review_status" and cond["owner_role"] == "role:bias-reviewer"
    base = ok(tools.readiness(VEHICLE))
    assert base["readiness"] == "ready" and base["actionable"] is False and "no approval" in base["actionable_reason"]


def test_pending_actions_are_routed_by_role_with_the_resolving_call():
    r = ok(tools.pending_actions(VEHICLE))
    (action,) = r["pending_actions"]
    assert action["kind"] == "approve" and action["target"] == "alt:B" and action["role"] == "role:mda"
    assert action["resolve_with"] == {"tool": "pdo_record_human_action", "kind": "approval", "target": "alt:B",
                                      "role": "role:mda"}
    assert r["by_role"]["role:mda"]["assignees"] == ["you"]
    assert ok(tools.pending_actions(VEHICLE, role="role:cost-analyst"))["pending_actions"] == []
    failed(tools.pending_actions(VEHICLE, role="role:md"), "did you mean role:mda")


def test_uncertainty_is_reproducible_by_seed():
    a = ok(tools.uncertainty(FIRES, samples=40, seed=1))
    b = ok(tools.uncertainty(FIRES, samples=40, seed=1))
    assert a["uncertain"] and a["rule"]["samples"] == 40 and a["draws_digest"] == b["draws_digest"]
    assert a["recommended"] == b["recommended"]
    failed(tools.uncertainty(FIRES, samples=0), "samples")
    failed(tools.uncertainty(VEHICLE, episode="ep:nope"), "no episode")


# --- programs: status, refresh, impact ------------------------------------------------------------

def test_program_status_gives_the_tree_and_the_newest_episode():
    r = ok(tools.program_status(VEHICLE))
    depth = {e["episode"]: e["depth"] for e in r["episodes"]}
    assert len(depth) == 17 and depth["ep:e00"] == 0 and depth["ep:b1"] == 1 and depth["ep:e12"] == 12
    assert r["newest"] == "ep:e12" and r["episodes"][0]["file"] is None
    assert r["pending_actions"][0]["resolve_with"]["kind"] == "review"
    failed(tools.program_status(VEHICLE / "baseline"), "not a program directory")


def test_refresh_verifies_every_episode():
    r = ok(tools.refresh(VEHICLE))
    assert r["all_verified"] and r["unverified"] == []
    assert 0 < r["executed"] < r["computed_nodes"]


def test_impact_is_a_dry_run(program):
    before = change_files(program)
    r = ok(tools.impact(program, {"assign": [{"target": "obs:B.cost", "slot": "value", "new_value": 1.32}]}))
    assert r["parent"] == "ep:e00" and r["outcome"] == "changed_winner" and r["verified"] is True
    assert r["changed"]["recommended"] == {"before": ["alt:B"], "after": ["alt:A"]}
    assert "obs:B.cost" in r["delta"]["changed"] and r["executed"] < r["computed_nodes"]
    assert change_files(program) == before


def test_impact_takes_yaml_text_or_a_change_set_file():
    text = ok(tools.impact(VEHICLE, "assign: [{target: obs:B.cost, slot: value, new_value: 1.32}]"))
    assert text["after"]["recommended"] == ["alt:A"]
    r = ok(tools.impact(VEHICLE, "changes/e12-min-range-310.yaml"))
    assert r["parent"] == "ep:e11" and r["outcome"] == "recompute_same_winner" and r["approval_invalidated"]
    assert r["changed"]["readiness"] == {"before": "ready", "after": "pending"}


def test_impact_errors_explain_what_to_fix():
    r = failed(tools.impact(VEHICLE, {"assign": [{"target": "obs:B.cst", "slot": "value", "new_value": 1}]}),
               "obs:B.cst", "did you mean 'obs:B.cost'")
    assert r["hint"] == tools.CHANGE_HINT
    failed(tools.impact(VEHICLE, "just words"), "mapping")
    failed(tools.impact(VEHICLE, "missing.yaml"), "no such change-set file")
    failed(tools.impact(VEHICLE, {"assign": []}, episode="ep:e99"), "no episode")


# --- programs: writing change sets ----------------------------------------------------------------

def test_apply_change_previews_then_writes_a_new_episode(program):
    change = {"assign": [{"target": "obs:A.cost", "slot": "value", "new_value": 1.5}], "trigger": "cost",
              "change_description": "Supplier A requotes at 1.5 MUSD."}
    preview = ok(tools.apply_change(program, change))
    assert preview["written"] is False and preview["file"] is None
    assert preview["episode"] == "ep:e13" and preview["parent"] == "ep:e12"
    assert preview["after"]["recommended"] == ["alt:B"] and "alt:A" in preview["after"]["infeasible"]
    assert "e13-cost.yaml" not in change_files(program)

    written = ok(tools.apply_change(program, change, write=True))
    assert written["written"] and written["file"] == "changes/e13-cost.yaml"
    assert ok(tools.program_status(program))["newest"] == "ep:e13"
    assert ok(tools.run(program, "ep:e13"))["recommended"] == preview["after"]["recommended"]


def test_apply_change_never_writes_a_rejected_change_or_reuses_an_id(program):
    before = change_files(program)
    bad = {"assign": [{"target": "obs:A.cost", "slot": "colour", "new_value": "red"}]}
    failed(tools.apply_change(program, bad, write=True), "no slot 'colour'")
    failed(tools.apply_change(program, {"id": "ep:e05", "assign": []}, write=True), "already exists")
    assert change_files(program) == before


def test_record_review_then_approval_makes_the_decision_actionable(program):
    review = ok(tools.record_human_action(program, "review", "rev:framing", "role:bias-reviewer",
                                          rationale="Re-reviewed after the range change.", write=True))
    assert (review["episode"], review["parent"], review["file"]) == ("ep:e13", "ep:e12", "changes/e13-review.yaml")
    assert review["changed"]["readiness"] == {"before": "pending", "after": "ready"}
    assert review["pending_actions_after"][0]["resolve_with"]["kind"] == "approval"

    approval = ok(tools.record_human_action(program, "approval", "alt:A", "role:mda", write=True))
    assert approval["parent"] == "ep:e13" and approval["after"]["actionable"] is True and approval["warnings"] == []
    r = ok(tools.run(program, "ep:e14"))
    assert r["actionable"] is True and r["approval"] == "appr:e14-A" and r["verified"] is True


def test_record_applicability_reproduces_the_accepted_report(program):
    r = ok(tools.record_human_action(program, "applicability", "obs:B.reliability-2", "role:test-engineer",
                                     rationale="The phase 2 article matches configuration B.", parent="ep:e04"))
    assert r["written"] is False and r["parent"] == "ep:e04"
    expected = ok(tools.run(VEHICLE, "ep:e05"))  # the program's own record of the same acceptance
    assert r["after"]["recommended"] == expected["recommended"] == ["alt:B"]
    assert r["after"]["utilities"]["alt:B"] == pytest.approx(utilities(expected)["alt:B"])
    slots = {a["slot"]: a["new_value"] for a in r["change"]["assign"]}
    assert slots == {"applicability": "accepted",
                     "applicability_rationale": "The phase 2 article matches configuration B."}


def test_record_risk_treatment_and_assumption_validation(program):
    failed(tools.record_human_action(program, "risk_treatment", "risk:b-supplier-capacity", "role:program-manager"),
           "needs status")
    r = ok(tools.record_human_action(program, "risk_treatment", "risk:b-supplier-capacity", "role:program-manager",
                                     status="accepted"))
    assert r["warnings"] and "risk_acceptance_roles" in r["warnings"][0]
    assert {"target": "risk:b-supplier-capacity", "slot": "accepted_by_role", "new_value": "role:program-manager"} \
        in r["change"]["assign"]
    assert ok(tools.record_human_action(program, "risk_treatment", "risk:b-supplier-capacity", "role:mda",
                                        status="accepted"))["warnings"] == []
    a = ok(tools.record_human_action(program, "assumption_validation", "asm:terrain-factor", "role:systems-engineer",
                                     status="unvalidated", parent="ep:e00"))
    assert a["after"]["readiness"] == "pending" and a["after"]["readiness_unknown"] == ["rc:assumptions-validated"]
    assert change_files(program) == change_files(VEHICLE)


def test_record_human_action_rejects_bad_requests(program):
    failed(tools.record_human_action(program, "sign", "alt:A", "role:mda"), "unknown kind")
    failed(tools.record_human_action(program, "approval", "rev:framing", "role:mda"), "not an Alternative")
    failed(tools.record_human_action(program, "approval", "alt:A", "role:md"), "did you mean role:mda")
    failed(tools.record_human_action(program, "approval", "alt:A", "role:mda", status="pass"), "not approvals")
    failed(tools.record_human_action(program, "review", "rev:framing", "role:mda", status="done"), "not a review")
    failed(tools.record_human_action(VEHICLE / "baseline", "approval", "alt:A", "role:mda"), "not a program")
    assert change_files(program) == change_files(VEHICLE)


# --- packages, SysML, examples --------------------------------------------------------------------

def test_package_returns_markdown_or_writes_a_replayable_record(tmp_path):
    from pdo.package import load_record, replay

    inline = ok(tools.package(VEHICLE))
    assert inline["written"] is False and "alt:B" in inline["markdown"] and inline["runs"] > 0
    out = tmp_path / "pkg"
    r = ok(tools.package(VEHICLE, out=out))
    assert r["written"] and Path(r["package"]).exists() and Path(r["record"]).exists()
    assert (r["recommended"], r["flips"], r["claims"]) == (inline["recommended"], inline["flips"], inline["claims"])
    assert replay(load_record(r["record"]), r["record"])["reproduced"]


def test_import_sysml_with_a_mapping_path_or_text(tmp_path):
    r = ok(tools.import_sysml(str(SYSML / "vehicle.sysml"), str(SYSML / "mapping.yaml")))
    assert r["counts"]["alternatives"] == 3 and r["counts"]["observations"] == 12 and len(r["gaps"]) == 3
    assert "criteria" in r["missing_to_run"] and r["written"] == []
    text = ok(tools.import_sysml([SYSML / "vehicle.sysml"], (SYSML / "mapping.yaml").read_text()))
    assert text["state"] == r["state"]
    out = tmp_path / "decision"
    written = ok(tools.import_sysml(SYSML / "vehicle.sysml", SYSML / "mapping.yaml", out=out))
    assert (out / "sysml-alternatives.yaml").exists() and str(out / "sysml-import.json") in written["written"]


def test_import_sysml_errors():
    failed(tools.import_sysml("nope.sysml"), "no such file")
    failed(tools.import_sysml([]), "no SysML files")
    failed(tools.import_sysml(SYSML / "vehicle.sysml", "colours: [red]"), "unknown top-level keys")


def test_examples_list_and_copy(tmp_path):
    names = {e["name"]: e for e in ok(tools.list_examples())["examples"]}
    assert {"vehicle", "fires_sensor"} <= set(names) and names["vehicle"]["kind"] == "program"
    r = ok(tools.copy_example("vehicle", tmp_path / "mine"))
    assert "program.yaml" in r["files"] and not any("__pycache__" in f for f in r["files"])
    assert ok(tools.run(tmp_path / "mine"))["recommended"] == ["alt:B"]
    failed(tools.copy_example("vehicle", tmp_path / "mine"), "already exists")
    failed(tools.copy_example("vehicel", tmp_path / "other"), "did you mean vehicle")


def test_plain_makes_values_json_ready():
    from pdo import model as m

    value = {"d": date(2027, 1, 15), "s": {"b", "a"}, "p": Path("x/y"), "e": m.Applicability.accepted,
             "inf": math.inf, "t": (1, 2), 3: None}
    assert tools.plain(value) == {"d": "2027-01-15", "s": ["a", "b"], "p": "x/y", "e": "accepted", "inf": "inf",
                                  "t": [1, 2], "3": None}


# --- the MCP server -------------------------------------------------------------------------------

EXPECTED_TOOLS = {f"pdo_{name}" for name in (
    "describe_schema", "validate", "run", "explain", "flip", "scenarios", "readiness", "pending_actions",
    "program_status", "refresh", "impact", "uncertainty", "list_examples", "apply_change", "record_human_action",
    "package", "import_sysml", "copy_example")}


@pytest.fixture(scope="module")
def server():
    pytest.importorskip("mcp")
    from pdo.mcp_server import build_server

    return build_server()


def test_server_lists_every_tool_with_typed_described_parameters(server):
    listed = {t.name: t for t in asyncio.run(server.list_tools())}
    assert set(listed) == EXPECTED_TOOLS
    for t in listed.values():
        assert t.description and t.input_schema["type"] == "object"
        for name, prop in t.input_schema.get("properties", {}).items():
            assert prop.get("description"), f"{t.name}.{name} has no description"
    assert listed["pdo_run"].annotations.read_only_hint is True
    assert listed["pdo_record_human_action"].annotations.read_only_hint is False
    assert listed["pdo_record_human_action"].input_schema["properties"]["kind"]["enum"] == list(tools.HUMAN_ACTIONS)
    assert listed["pdo_run"].input_schema["required"] == ["path"]


def test_server_calls_return_the_tool_results(server):
    from mcp import Client

    async def session():
        async with Client(server) as client:
            return (await client.call_tool("pdo_run", {"path": str(VEHICLE), "episode": "ep:e05"}),
                    await client.call_tool("pdo_run", {"path": str(VEHICLE / "missing")}),
                    await client.call_tool("pdo_explain", {"path": str(VEHICLE), "node": "decision", "depth": 0}))

    good, missing, invalid = asyncio.run(session())
    assert not good.is_error and good.structured_content == tools.run(VEHICLE, "ep:e05")
    assert json.loads(good.content[0].text)["recommended"] == ["alt:B"]
    assert not missing.is_error and missing.structured_content["ok"] is False and missing.structured_content["hint"]
    assert invalid.is_error  # rejected by the input schema before the tool runs


def test_server_returns_a_defect_as_data(monkeypatch):
    pytest.importorskip("mcp")
    from pdo import mcp_server

    def broken(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(tools, "run", broken)
    r = mcp_server.pdo_run(str(VEHICLE))
    assert r["ok"] is False and "internal error" in r["error"] and "boom" in r["error"]
    assert r["hint"] == mcp_server.INTERNAL_HINT


@pytest.mark.slow
def test_stdio_server_initializes_lists_tools_and_runs(tmp_path):
    pytest.importorskip("mcp")
    from mcp import Client, StdioServerParameters
    from mcp.client.stdio import stdio_client

    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("uv is not on PATH")
    # An evaluator that prints: its output must go to the server's stderr, never onto the JSON-RPC stream.
    noisy = tmp_path / "noisy"
    shutil.copytree(VEHICLE, noisy, ignore=tools.COPY_IGNORE)
    (noisy / "evaluators.py").write_text("def range_model(tank_gal, mpg, terrain_factor):\n"
                                         "    print('evaluator noise')\n"
                                         "    return tank_gal * mpg * terrain_factor\n")
    # --no-sync: run the environment as it is, never modify it from a test.
    params = StdioServerParameters(command=uv, args=["run", "--no-sync", "pdo", "mcp"], cwd=str(ROOT))
    faults: list[Exception] = []

    async def on_message(message) -> None:
        if isinstance(message, Exception):
            faults.append(message)

    async def session(errlog):
        transport = stdio_client(params, errlog=errlog)
        async with Client(transport, mode="legacy", read_timeout_seconds=120, message_handler=on_message) as client:
            listed = await client.list_tools()  # Client entered with mode="legacy": the initialize handshake
            result = await client.call_tool("pdo_run", {"path": str(VEHICLE)})
            loud = await client.call_tool("pdo_run", {"path": str(noisy)})
            return client.server_info, {t.name for t in listed.tools}, result, loud

    log = tmp_path / "server.log"
    with log.open("w") as errlog:
        info, names, result, loud = asyncio.run(asyncio.wait_for(session(errlog), timeout=180))
    assert info.name == "pdo" and names == EXPECTED_TOOLS
    assert not result.is_error and result.structured_content["ok"] is True
    assert result.structured_content["recommended"] == ["alt:B"]
    assert result.structured_content == tools.run(VEHICLE)
    assert loud.structured_content["recommended"] == ["alt:B"] and faults == []
    assert "evaluator noise" in log.read_text()


# --- utility commands -----------------------------------------------------------------------------

def test_cli_schema_describe():
    out = runner.invoke(app, ["schema", "describe"])
    assert out.exit_code == 0 and "Observation" in out.output and "TriggerKind" in out.output
    out = runner.invoke(app, ["schema", "describe", "ChangeSet", "--json"])
    data = json.loads(out.output)
    assert out.exit_code == 0 and data["ok"] and {s["name"] for s in data["slots"]} >= {"assign", "add", "remove"}
    out = runner.invoke(app, ["schema", "describe", "Observation"])
    assert "In a decision state: observations" in out.output and "hashable" in out.output
    out = runner.invoke(app, ["schema", "describe", "Nope", "--json"])
    assert out.exit_code == 1 and json.loads(out.output)["ok"] is False


def test_cli_example_list_and_copy(tmp_path):
    out = runner.invoke(app, ["example", "list", "--json"])
    assert out.exit_code == 0 and "vehicle" in {e["name"] for e in json.loads(out.output)["examples"]}
    out = runner.invoke(app, ["example", "copy", "vehicle", str(tmp_path / "v")])
    assert out.exit_code == 0 and (tmp_path / "v" / "program.yaml").exists()
    out = runner.invoke(app, ["example", "copy", "vehicle", str(tmp_path / "v")])
    assert out.exit_code == 1


def test_cli_mcp_command_is_registered():
    out = runner.invoke(app, ["mcp", "--help"])
    assert out.exit_code == 0 and "stdio" in out.output
