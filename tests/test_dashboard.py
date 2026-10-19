"""The read-only dashboard: its data export, the page it renders, and the CLI command."""

from __future__ import annotations

import json
import re
import shutil
import subprocess

import pytest
from conftest import ROOT, VEHICLE
from typer.testing import CliRunner

from pdo.cli import app
from pdo.dashboard import TEMPLATE, build, layout, render, write

FIRES = ROOT / "examples" / "fires_sensor"


@pytest.fixture(scope="module")
def program():
    return build(VEHICLE, flips=False, sensitivity=False)


@pytest.fixture(scope="module")
def state_dir(tmp_path_factory):
    """The vehicle baseline as a plain state directory, with the evaluators it names."""
    out = tmp_path_factory.mktemp("state")
    for f in (VEHICLE / "baseline").glob("*.yaml"):
        shutil.copy(f, out / f.name)
    shutil.copy(VEHICLE / "evaluators.py", out / "evaluators.py")
    return out


@pytest.fixture(scope="module")
def state(state_dir):
    return build(state_dir)


def test_program_export_covers_every_episode(program):
    assert len(program["episodes"]) == 21
    assert program["meta"]["baseline"] == "ep:e00"
    assert program["meta"]["main_line"][0] == "ep:e00" and program["meta"]["main_line"][-1] == "ep:e12"
    by_id = {e["id"]: e for e in program["episodes"]}
    assert by_id["ep:e00"]["recommended"] == ["alt:B"] and by_id["ep:e00"]["readiness"] == "ready"
    assert by_id["ep:b4"]["recommended"] == [] and by_id["ep:b4"]["readiness"] == "blocked"
    assert by_id["ep:e10"]["recommended"] == ["alt:A"]
    for e in program["episodes"]:
        if e["parent"]:  # every refresh is selective and verified against a full recomputation
            assert e["verified"] is True and e["impact"] is not None
            assert e["cost"]["executed"] <= e["cost"]["computed"] < e["cost"]["total"]


def test_node_states_carry_status_value_and_execution(program):
    e00 = program["episodes"][0]
    u = e00["nodes"]["U/alt:B"]
    assert {k: u[k] for k in ("s", "v", "e")} == {"s": "value", "v": "U = 0.5333", "e": "executed"}
    assert len(u["h"]) == 32  # its value digest
    assert e00["nodes"]["F/alt:C"]["s"] == "fail" and "min-reliability" in e00["nodes"]["F/alt:C"]["v"]
    assert e00["nodes"]["decision"]["v"] == "B"
    e01 = next(e for e in program["episodes"] if e["id"] == "ep:e01")
    assert e01["nodes"]["actionable"]["s"] == "pass"
    assert {n["e"] for n in e01["nodes"].values()} >= {"executed", "reused"}


def test_layout_places_every_node_of_every_episode_once(program):
    lay = program["layout"]
    for e in program["episodes"]:
        assert set(e["nodes"]) <= set(lay["nodes"])
    cells = [(n["col"], n["row"]) for n in lay["nodes"].values()]
    assert len(cells) == len(set(cells))
    assert all(a in lay["nodes"] and b in lay["nodes"] for a, b in lay["edges"])
    assert all(lay["nodes"][a]["col"] < lay["nodes"][b]["col"] for a, b in lay["edges"])  # every edge points right
    assert lay["columns"][lay["nodes"]["decision"]["col"]] == "Decision"
    model_inputs = {nid for nid, n in lay["nodes"].items() if lay["columns"][n["col"]] == "Model inputs"}
    assert model_inputs == {f"y/alt:{a}/meas:{m}" for a in "ABC" for m in ("tank-capacity", "fuel-economy")}


def test_layout_is_deterministic(program):
    from pdo.episodes import Program

    graphs = [ev.run.graph for ev in Program.load(VEHICLE).evaluate_all(mode="cutoff")]
    assert layout(graphs) == layout(list(reversed(graphs)))


def test_a_plain_state_is_a_one_episode_program(state):
    assert [e["id"] for e in state["episodes"]] == ["state"] and state["meta"]["main_line"] == ["state"]
    e = state["episodes"][0]
    assert e["parent"] is None and e["impact"] is None
    assert any(f["parameter"] == "obs:B.cost.value" and f["flip_number"] == pytest.approx(1.30, abs=1e-9)
               for f in e["flips"])
    assert e["sensitivity"]["tornado"]["margin"] == pytest.approx(0.533333 - 0.466667, abs=1e-5)
    assert e["sensitivity"]["global"] is None  # no distributions declared
    assert [r["alternative"] for r in e["table"]] == ["alt:A", "alt:B", "alt:C"]


def test_uncertainty_under_expected_mavt():
    data = build(FIRES, flips=False, sensitivity=False)
    assert data["meta"]["rule_kind"] == "expected_mavt"
    for e in data["episodes"]:
        u = e["uncertainty"]
        assert u["recommended"] and {a["alternative"] for a in u["alternatives"]} == set(data["alternatives"])


def test_render_embeds_the_data_safely(program):
    hostile = json.loads(json.dumps(program))
    hostile["meta"]["title"] = "</script><script>alert(1)</script>"
    hostile["episodes"][0]["table"][0]["utility"] = float("nan")
    page = render(hostile)
    assert page.startswith("<!doctype html>") and page.rstrip().endswith("</html>")
    assert "/*__PDO_DATA__*/" not in page
    assert "</script><script>alert" not in page  # "</" is escaped inside the embedded JSON
    payload = re.search(r"const DATA = (.*?);\n", page, re.S).group(1)
    data = json.loads(payload)
    assert data["meta"]["title"] == hostile["meta"]["title"]
    assert data["episodes"][0]["table"][0]["utility"] is None  # non-finite numbers become null
    fragment = render(program, standalone=False)
    assert not re.search(r"<(!doctype|html|head|body)\b", fragment, re.I)
    assert fragment.lstrip().startswith("<title>Light tactical vehicle configuration</title>")
    assert "<title>&lt;/script&gt;" in page  # the program name is escaped in the page title too


def test_template_draws_with_text_only():
    """The page builds its DOM with textContent, so names and descriptions from YAML cannot inject markup."""
    text = TEMPLATE.read_text(encoding="utf-8")
    assert text.count("/*__PDO_DATA__*/null") == 1
    assert "innerHTML" not in text and "insertAdjacentHTML" not in text and "document.write" not in text


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_template_script_parses(tmp_path):
    script = re.search(r"<script>\n(.*)</script>", TEMPLATE.read_text(encoding="utf-8"), re.S).group(1)
    (tmp_path / "page.js").write_text(script)
    result = subprocess.run(["node", "--check", str(tmp_path / "page.js")], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_write_and_cli(tmp_path, state, state_dir):
    paths = write(state, tmp_path / "w", fragment=True)
    assert {p.name for p in paths.values()} == {"index.html", "dashboard.json", "fragment.html"}
    assert json.loads(paths["data"].read_text())["meta"]["baseline"] == "state"
    out = CliRunner().invoke(app, ["dashboard", str(state_dir), "-o", str(tmp_path / "c"),
                                   "--no-flips", "--no-sensitivity", "--json"])
    assert out.exit_code == 0, out.output
    result = json.loads(out.output)
    assert result["ok"] and result["episodes"] == 1 and result["newest"]["recommended"] == ["alt:B"]
    assert (tmp_path / "c" / "index.html").exists() and "fragment" not in result["files"]
    bad = CliRunner().invoke(app, ["dashboard", str(tmp_path / "missing"), "-o", str(tmp_path / "x"), "--json"])
    assert bad.exit_code != 0


def test_never_overwrites_a_page_it_did_not_write(tmp_path, state):
    (tmp_path / "index.html").write_text("<p>someone else's page</p>")
    with pytest.raises(FileExistsError):
        write(state, tmp_path)
    assert (tmp_path / "index.html").read_text() == "<p>someone else's page</p>"
    out = tmp_path / "again"
    write(state, out)
    write(state, out)  # a previous dashboard's own files are replaced


def test_mcp_tool_writes_the_dashboard(tmp_path, state_dir):
    from pdo import tools

    r = tools.dashboard(state_dir, tmp_path / "d", flips=False, sensitivity=False)
    assert r["ok"] and r["episodes"] == 1 and r["newest"]["recommended"] == ["alt:B"]
    assert (tmp_path / "d" / "index.html").exists()
    (tmp_path / "taken").mkdir()
    (tmp_path / "taken" / "index.html").write_text("mine")
    refused = tools.dashboard(state_dir, tmp_path / "taken", flips=False, sensitivity=False)
    assert refused["ok"] is False and "not written by pdo dashboard" in refused["error"] and refused["hint"]


def test_reviewer_data_explains_each_episode(program):
    by_id = {e["id"]: e for e in program["episodes"]}
    e10 = by_id["ep:e10"]
    assert {"op": "set", "target": "obs:B.cost", "slot": "value", "before": 1.22, "after": 1.32,
            "provenance": False} in e10["changes"]
    assert any(c["op"] == "add" and c["kind"] == "Evidence" for c in e10["changes"])
    assert e10["author_role"] == "role:cost-analyst" and e10["weights"]["crit:cost"] == 0.4
    assert by_id["ep:e12"]["requirements"]["req:min-range"]["threshold"] == 310  # framing as of each episode
    b = next(r for r in by_id["ep:e00"]["table"] if r["alternative"] == "alt:B")
    assert b["values"]["crit:range"] == pytest.approx(0.6) and b["checks"]["req:min-reliability"] == "pass"
    r1 = by_id["ep:r1"]
    assert all(c["provenance"] for c in r1["changes"])  # a revision touches provenance only
    c_rel = next(r for r in r1["evidence"] if r["observation"] == "obs:C.reliability")
    assert c_rel["located"] and c_rel["current"] is False and "§ 3.3" in c_rel["source"]
    assert by_id["ep:e00"]["changes"] == [] and "Re-approval" in program["objects"]["appr:e08-b"]


def test_flips_carry_words(state):
    flips = {f["parameter"]: f for f in state["episodes"][0]["flips"]}
    assert flips["obs:B.cost.value"]["label"] == "B's unit acquisition cost"
    assert flips["obs:B.cost.value"]["unit"] == "MUSD"
    weight = next(f for f in state["episodes"][0]["flips"] if f["parameter"] == "crit:cost.weight")
    assert weight["label"] == "the weight on cost" and weight["unit"] == "%" and weight["counterpart_label"]


def test_template_has_both_views():
    text = TEMPLATE.read_text(encoding="utf-8")
    for anchor in ('id="view-decision"', 'id="view-engineering"', 'id="timeline"', 'id="verdict"', 'id="changed"',
                   'id="why"', 'id="matrix"', 'id="act"', 'id="robust"', 'id="sources"', 'id="graph"'):
        assert anchor in text, anchor
    assert 'role="tablist"' in text and ".engineering" in text  # the hash keeps the view


def test_raw_values_and_objects_are_content_addressed(program):
    e00 = program["episodes"][0]
    node = e00["nodes"]["U/alt:B"]
    assert program["values"][node["h"]]["U"] == pytest.approx(0.533333, abs=1e-6)
    obs = program["object_pool"][e00["objects"]["obs:B.cost"]]
    assert obs == {"class": "Observation", "id": "obs:B.cost", "alternative": "alt:B", "measure": "meas:unit-cost",
                   "value": 1.2, "unit": "MUSD", "evidence": "ev:cost-B"}
    # an object that never changes is stored once, however many episodes hold it
    keys = {e["objects"]["req:max-cost"] for e in program["episodes"]}
    assert len(keys) == 1
    obs_schema = program["schema"]["Observation"]
    assert "value" in obs_schema["hashable"] and "locator" not in obs_schema["hashable"]
    assert obs_schema["slots"]["locator"].startswith("The passage")
    e10 = next(e for e in program["episodes"] if e["id"] == "ep:e10")
    assert e10["change_set"]["parent"] == "ep:e09" and e10["change_set"]["assign"][2]["new_value"] == 1.32
    assert program["layout"]["nodes"]["U/alt:B"]["fn"] == "utility"


def test_template_has_the_calculation_panel():
    text = TEMPLATE.read_text(encoding="utf-8")
    for anchor in ('id="calc"', 'id="calc-flow"', 'id="calc-inspect"', "function calcText", "function openCalc",
                   "function objChip", "function statusPill", "const epName"):
        assert anchor in text, anchor
