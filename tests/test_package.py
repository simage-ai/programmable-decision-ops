"""Decision Package: the episode record, its claims, the Markdown package, JSON Schema validation and replay."""

from __future__ import annotations

import json
import shutil

import jsonschema
import pytest
from conftest import ROOT, VEHICLE, assign, evaluate_state
from typer.testing import CliRunner

from pdo import model as m
from pdo.cli import app
from pdo.evaluate import evaluate
from pdo.flip import scan_parameters, scenario_sweep, weight_flips
from pdo.package import (
    episode_record,
    load_record,
    record_json,
    render_markdown,
    replay,
    validate_record_json,
    write_package,
)
from pdo.run import run

RECORD_SCHEMA = ROOT / "project" / "jsonschema" / "pdo_record.schema.json"


@pytest.fixture(scope="module")
def base():
    return evaluate(VEHICLE)


@pytest.fixture(scope="module")
def record(base):
    state = base.graph.state
    scans, flips = scan_parameters(state, base=base)
    flips += weight_flips(state, base=base)
    return episode_record(base, episode="ep:e00", source=VEHICLE, flips=flips, scans=scans,
                          scenarios=scenario_sweep(state, base=base))


def outcome(record, alt):
    return next(o for o in record.outcomes if o.alternative == alt)


# --- the record ---------------------------------------------------------------------------------

def test_record_carries_the_decision(record):
    assert isinstance(record, m.DecisionEpisodeRecord)
    assert (record.recommended, record.feasible, record.infeasible) == (["alt:B"], ["alt:A", "alt:B"], ["alt:C"])
    assert outcome(record, "alt:A").utility == pytest.approx(7 / 15, abs=1e-12)
    assert outcome(record, "alt:B").utility == pytest.approx(8 / 15, abs=1e-12)
    c = outcome(record, "alt:C")
    assert c.utility == pytest.approx(0.62, abs=1e-12) and c.feasibility == "fail"
    (rel,) = [k for k in c.checks if k.requirement == "req:min-reliability"]
    assert (rel.status, rel.measured, rel.comparator, rel.threshold_value) == ("fail", 0.94, "ge", 0.95)
    assert rel.g == pytest.approx(0.01, abs=1e-12)
    (cost,) = [v for v in outcome(record, "alt:B").criterion_values if v.criterion == "crit:cost"]
    assert (cost.measured, cost.weight) == (1.2, 0.4) and cost.value_score == pytest.approx(1 / 3, abs=1e-12)


def test_record_readiness_actions_and_replay_inputs(record):
    assert record.readiness.readiness_state == "ready" and len(record.readiness.conditions) == 10
    assert record.actionable is False and record.actionable_reason == "alt:B has no approval"
    (action,) = record.pending_actions
    assert (action.action_kind, action.target, action.role, action.produced_by) == \
        ("approve", "alt:B", "role:mda", "actionable")
    assert (record.episode, record.source_path, record.kernel_version) == ("ep:e00", str(VEHICLE), "0.1.0")
    assert record.question.startswith("Which vehicle configuration")


def test_one_run_per_computed_node(base, record):
    computed = [nid for nid in base.graph.order if base.graph.nodes[nid].fn != "source"]
    assert [r.node for r in record.runs] == computed
    runs = {r.node: r for r in record.runs}
    for nid in computed:
        res = base.results[nid]
        assert (runs[nid].signature, runs[nid].run_key, runs[nid].value_digest) == (res.sig, res.key, res.vdig)
    code = base.graph.nodes["run/spec:range-model/alt:B"].local["code_digest"]
    assert runs["run/spec:range-model/alt:B"].implementation == f"evaluators.py:range_model@1.0#{code}"
    assert runs["g/req:min-range/alt:B"].implementation == "threshold@1"
    assert runs["decision"].input_nodes[0] == "rule:main" and runs["decision"].execution_status == "executed"


def test_flips_scans_and_scenarios_are_in_the_record(record):
    (b_cost,) = [f for f in record.flips if f.parameter == "obs:B.cost.value"]
    assert b_cost.flip_number == pytest.approx(1.30, abs=1e-9) and b_cost.method == "affine_exact"
    (w,) = [f for f in record.flips if f.target == "crit:cost" and f.counterpart == "crit:reliability"]
    assert w.flip_number == pytest.approx(0.525, abs=1e-12) and w.method == "closed_form"
    assert len(record.parameter_scans) == 16
    assert record.robust is False
    opt = record.scenario_outcomes[-1]
    assert opt.scenario == "sc:optimistic-reliability" and opt.recommended == ["alt:C"] and opt.robust is False


def test_claims_keep_computed_human_and_semantic_bases_apart(record):
    props = {p.id: p for p in record.propositions}
    claims = record.claims
    assert len([c for c in claims if c.basis == "computed"]) == 9 + 3  # every check, every feasibility
    assert len([c for c in claims if c.basis == "human"]) == 1 + 12  # the review, every accepted observation
    (c_rel,) = [c for c in claims if c.produced_by == "g/req:min-reliability/alt:C"]
    assert c_rel.status == "fail" and props[c_rel.proposition].statement == "alt:C satisfies req:min-reliability"
    assert c_rel.supported_by == ["ev:reliability-C", "obs:C.reliability"]
    (b_range,) = [c for c in claims if c.produced_by == "g/req:min-range/alt:B"]
    assert b_range.supported_by == ["asm:terrain-factor", "ev:design-B", "obs:B.mpg", "obs:B.tank",
                                    "spec:range-model"]
    (f_c,) = [c for c in claims if c.produced_by == "F/alt:C"]
    assert f_c.status == "fail" and c_rel.id in f_c.supported_by
    (review,) = [c for c in claims if c.produced_by == "rev:framing"]
    assert review.basis == "human" and review.status == "pass"
    assert all(p.id.startswith("prop:") for p in record.propositions)
    assert all(c.id.startswith("claim:") and c.proposition in props for c in claims)


def test_claim_ids_are_content_derived(base, record):
    again = episode_record(base, episode="ep:e00", source=VEHICLE)
    assert [c.id for c in again.claims] == [c.id for c in record.claims]
    assert [p.id for p in again.propositions] == [p.id for p in record.propositions]


def test_provisional_applicability_is_a_semantic_claim_on_the_same_proposition(base, record):
    state = assign(base.graph.state, "obs:B.reliability", "applicability", "provisional")
    proposed = episode_record(evaluate_state(state))
    (claim,) = [c for c in proposed.claims if c.produced_by == "obs:B.reliability"]
    (accepted,) = [c for c in record.claims if c.produced_by == "obs:B.reliability"]
    assert (claim.basis, claim.status) == ("semantic", "unknown")
    assert (accepted.basis, accepted.status) == ("human", "pass")
    assert claim.proposition == accepted.proposition and claim.id != accepted.id


def test_impact_summary_of_a_selective_run(base):
    state = assign(base.graph.state, "obs:B.cost", "value", 1.32)
    graph = evaluate_state(state).graph
    selective = run(graph, "cutoff", prev=base)
    record = episode_record(selective, episode="ep:cost", parent="ep:e00")
    assert record.recommended == ["alt:A"] and record.parent == "ep:e00"
    assert record.impact.total_nodes == len(graph.nodes)
    assert record.impact.impact_size == len(selective.impact)
    assert record.impact.executed_count == len(selective.executed) < record.impact.impact_size
    assert episode_record(base).impact is None  # a full run has no impact set


# --- files --------------------------------------------------------------------------------------

def test_write_package_validates_and_round_trips(record, tmp_path):
    paths = write_package(record, tmp_path / "pkg")
    data = json.loads(paths["record"].read_text())
    jsonschema.validate(data, json.loads(RECORD_SCHEMA.read_text()))
    assert load_record(paths["record"]) == record
    assert paths["package"].read_text() == render_markdown(record)


def test_invalid_records_are_refused(record):
    data = record_json(record)
    data["flips"][0]["method"] = "guesswork"
    with pytest.raises(jsonschema.ValidationError):
        validate_record_json(data)
    with pytest.raises(ValueError):
        validate_record_json(data, schema_path=ROOT / "no-such-schema.json")  # the pydantic fallback


def test_markdown_package_sections(record):
    text = render_markdown(record)
    for heading in ("# Decision Package: alt:B", "## Summary", "## The recommendation and why", "## Feasibility",
                    "## What flips the decision", "## Readiness and pending actions", "### role:mda",
                    "## Scenario robustness", "## Decision graph", "## Provenance"):
        assert heading in text
    assert "FAIL: 0.94, g = +0.01" in text  # C's reliability check, with its margin
    assert "| obs:B.cost.value | 1.2 | ↑ 1.3 | alt:B → alt:A | preference |" in text
    assert "crit:cost.weight (from crit:reliability) | 0.4 | ↑ 0.525" in text
    assert "not feasible and uniquely best in sc:optimistic-reliability (recommends alt:C)" in text
    assert "```mermaid\nflowchart LR" in text and '["decision"]' in text
    assert record.runs[0].signature in text
    assert "obs:C.tank.value over [23.75, 71.25]" in text


def test_markdown_says_what_was_not_analysed(base):
    text = render_markdown(episode_record(base))
    assert text.count("Not analysed for this package.") == 2


def test_empty_feasible_set_package(base):
    state = assign(base.graph.state, "req:min-range", "threshold_value", 370)
    text = render_markdown(episode_record(evaluate_state(state)))
    assert "# Decision Package: no recommendation" in text and "(the feasible set is empty)" in text
    assert "alt:A is infeasible: it fails req:min-range (g = +50)" in text


# --- replay -------------------------------------------------------------------------------------

def test_replay_reproduces_every_node(record, tmp_path):
    paths = write_package(record, tmp_path / "pkg")
    report = replay(load_record(paths["record"]), paths["record"])
    assert report["reproduced"] and report["state_digest_matches"]
    assert report["matched"] == report["nodes"] == len(record.runs)
    assert report["recommended"] == ["alt:B"]


def test_replay_detects_a_changed_source(tmp_path):
    program = tmp_path / "vehicle"
    shutil.copytree(VEHICLE, program)
    result = evaluate(program)
    paths = write_package(episode_record(result, episode="ep:e00", source=program), tmp_path / "pkg")
    evidence = program / "baseline" / "evidence.yaml"
    text = evidence.read_text()
    assert "value: 1.20, unit: MUSD" in text
    evidence.write_text(text.replace("value: 1.20, unit: MUSD", "value: 1.32, unit: MUSD"))
    report = replay(load_record(paths["record"]), paths["record"])
    assert not report["reproduced"] and not report["state_digest_matches"]
    assert "y/alt:B/meas:unit-cost" in report["mismatched"] and "decision" in report["mismatched"]
    assert "y/alt:A/meas:unit-cost" not in report["mismatched"]
    assert report["recommended"] == ["alt:A"] and report["recorded_recommended"] == ["alt:B"]


def test_replay_needs_a_source(base):
    with pytest.raises(ValueError, match="source_path"):
        replay(episode_record(base))


# --- CLI ----------------------------------------------------------------------------------------

runner = CliRunner()


def test_cli_package_then_replay(tmp_path):
    out = runner.invoke(app, ["package", str(VEHICLE), "--out", str(tmp_path / "pkg"), "--json"])
    assert out.exit_code == 0, out.output
    data = json.loads(out.output)
    assert data["ok"] and data["episode"] == "ep:e00" and data["recommended"] == ["alt:B"]
    assert data["robust"] is False and data["flips"] == 18
    assert (tmp_path / "pkg" / "package.md").exists()
    record = load_record(tmp_path / "pkg" / "record.json")
    assert record.parent is None and len(record.runs) == data["runs"]

    out = runner.invoke(app, ["replay", str(tmp_path / "pkg" / "record.json"), "--json"])
    assert out.exit_code == 0, out.output
    report = json.loads(out.output)
    assert report["ok"] and report["reproduced"] and report["matched"] == data["runs"]
    out = runner.invoke(app, ["replay", str(tmp_path / "pkg" / "record.json")])
    assert out.exit_code == 0 and "reproduced" in out.output


def test_cli_replay_of_a_missing_file_fails(tmp_path):
    out = runner.invoke(app, ["replay", str(tmp_path / "nope.json"), "--json"])
    assert out.exit_code == 1 and json.loads(out.output)["ok"] is False


def test_cli_package_of_a_state_directory(tmp_path):
    out = runner.invoke(app, ["package", str(VEHICLE / "baseline"), "--out", str(tmp_path / "pkg"), "--json"])
    # The baseline directory alone has no evaluators.py beside it: the range runs fail and stay unknown.
    assert out.exit_code == 0, out.output
    record = load_record(tmp_path / "pkg" / "record.json")
    assert record.episode is None and record.source_path == str(VEHICLE / "baseline")
    assert record.unresolved == ["alt:A", "alt:B"] and record.readiness.readiness_state == "pending"
    assert "fix_evaluator" in {a.action_kind for a in record.pending_actions}
    text = (tmp_path / "pkg" / "package.md").read_text()
    assert "none yet (some alternatives are unresolved)" in text and "no unique baseline recommendation" in text
    assert replay(record)["reproduced"]

