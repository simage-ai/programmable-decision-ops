from __future__ import annotations

import json

from conftest import VEHICLE
from typer.testing import CliRunner

from pdo.cli import app

runner = CliRunner()


def test_run_json():
    out = runner.invoke(app, ["run", str(VEHICLE), "--json"])
    assert out.exit_code == 0, out.output
    data = json.loads(out.output)
    assert data["ok"] and data["recommended"] == ["alt:B"] and data["readiness"] == "ready"
    assert data["actionable"] is False
    assert data["pending_actions"][0]["kind"] == "approve"


def test_run_text_mentions_the_recommendation():
    out = runner.invoke(app, ["run", str(VEHICLE)])
    assert out.exit_code == 0 and "alt:B" in out.output and "READY" in out.output


def test_validate_ok_and_failure(tmp_path):
    assert runner.invoke(app, ["validate", str(VEHICLE)]).exit_code == 0
    (tmp_path / "bad.yaml").write_text("alternatives: [{id: alt:A, colour: green}]\n")
    out = runner.invoke(app, ["validate", str(tmp_path / "bad.yaml"), "--json"])
    assert out.exit_code == 1 and json.loads(out.output)["ok"] is False


def test_explain_and_graph():
    out = runner.invoke(app, ["explain", str(VEHICLE), "decision", "--json"])
    assert json.loads(out.output)["value"]["recommended"] == ["alt:B"]
    out = runner.invoke(app, ["graph", str(VEHICLE)])
    assert out.output.startswith("flowchart LR")
    out = runner.invoke(app, ["explain", str(VEHICLE), "nope", "--json"])
    assert out.exit_code == 1
