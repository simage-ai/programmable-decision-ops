"""Scenarios for the paper: each one runs the kernel on a decision, checks the claims it supports, and records
the data behind one figure.

    trade-study       the vehicle trade study (point in time): utilities, feasibility, and what flips it
    program-refresh   the vehicle program over refresh cycles: every episode selective and verified
    perturbations     single changes to the approved baseline: soundness and savings, and the ablations
    report-revisions  23 edits to the reports: section hashes send only changed values back to people
    uncertainty       the Fires sensor decision under uncertainty: the point ranking is not the decision
    onboarding        the vehicle decision built from its raw inputs: who supplied what

`pdo bench scenarios` writes results/scenarios/<name>/ (TSV tables, claims.tsv, and <name>.png/.pdf with the
figures extra) and results/scenarios/claims.tsv; it fails when a claim does not hold. tests/test_scenarios.py
runs the same scenarios as tests. Every number is the kernel's: a scenario only runs the kernel, reads its
results and compares them with what the claim states (from the research brief, the examples' hand-computed
expectations, or the benchmark's independent labels).
"""

from __future__ import annotations

import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pdo.bench.report import fmt, tsv, write_if_changed

ROOT = Path(__file__).resolve().parents[3]


@dataclass
class Claim:
    claim: str
    expected: str
    observed: str
    holds: bool


@dataclass
class ScenarioResult:
    name: str
    title: str
    shows: str
    tables: dict[str, tuple[tuple[str, ...], list[tuple]]] = field(default_factory=dict)
    claims: list[Claim] = field(default_factory=list)
    values: list[tuple[str, Any, str, str]] = field(default_factory=list)  # key, value, unit, description
    data: dict = field(default_factory=dict)  # what the figure draws
    seconds: float = 0.0

    def claim(self, claim: str, expected: Any, observed: Any, holds: bool | None = None) -> bool:
        ok = (expected == observed) if holds is None else bool(holds)
        self.claims.append(Claim(claim, _text(expected), _text(observed), ok))
        return ok

    @property
    def passed(self) -> bool:
        return all(c.holds for c in self.claims)


def _text(x: Any) -> str:
    if isinstance(x, float):
        return f"{x:.6g}"
    if isinstance(x, list | tuple):
        return "[" + ", ".join(_text(v) for v in x) + "]"
    return str(x)


def _close(a: float | None, b: float, tol: float = 1e-6) -> bool:
    return a is not None and abs(a - b) <= tol


def _vehicle() -> Path:
    from pdo.bench.onboarding import vehicle_dir

    return vehicle_dir()


def _example(name: str) -> Path:
    return _vehicle().parent / name


def _bench(name: str) -> Path:
    for root in (ROOT / "bench", Path.cwd() / "bench"):
        if (root / name).is_dir():
            return root / name
    raise FileNotFoundError(f"bench/{name} not found (scenarios that use the benchmark suites need a source checkout)")


# --- 1. the trade study -------------------------------------------------------------------------------

def trade_study() -> ScenarioResult:
    from pdo import tools
    from pdo.episodes import Program
    from pdo.graph import compile_graph
    from pdo.load import index
    from pdo.run import run
    from pdo.sensitivity import analyze_path

    r = ScenarioResult("trade-study", "A trade study, and what flips it",
                       "The vehicle trade study at one point in time: each alternative's utility and feasibility, the "
                       "response of the decision to B's unit cost, and the swing weight at which it changes.")
    vehicle = _vehicle()
    program = Program.load(vehicle)
    state = program.materialize(program.baseline_episode)
    result = run(compile_graph(state, vehicle))
    alts = [a.id for a in state.alternatives]
    reqs = [q.id for q in state.requirements]
    rows = []
    for a in alts:
        failed = result.value(f"F/{a}").get("failed") or []
        margins = {q: result.value(f"g/{q}/{a}").get("g") for q in reqs}
        rows.append((a, result.value(f"U/{a}")["U"], result.value(f"F/{a}")["status"], ";".join(failed) or "-",
                     *[margins[q] for q in reqs]))
    r.tables["alternatives"] = (("alternative", "U", "feasible", "failed", *[f"g {q}" for q in reqs]), rows)
    utilities = [round(row[1], 6) for row in rows]
    r.claim("utilities of A, B and C (research brief)", [0.466667, 0.533333, 0.62], utilities)
    r.claim("recommended", ["alt:B"], result.decision["recommended"])
    r.claim("C fails minimum reliability by g = 0.01", 0.01,
            round(result.value("g/req:min-reliability/alt:C")["g"], 6))

    _, flips = tools._analyse(result, None, 0.5, 17)
    cost = next(f for f in flips if f["parameter"] == "obs:B.cost.value")
    weight = next(f for f in flips
                  if f["parameter"] == "crit:cost.weight" and f.get("counterpart") == "crit:reliability")
    r.claim("B's cost flips the decision to A at 1.30 MUSD, found exactly", "1.3 (affine_exact)",
            f"{cost['flip_number']:.6g} ({cost['method']})", _close(cost["flip_number"], 1.30, 1e-9)
            and cost["method"] == "affine_exact")
    r.claim("the cost weight, traded against reliability, flips it at 0.525", 0.525, round(weight["flip_number"], 6))

    sweep = analyze_path(vehicle, None, "obs:B.cost.value", low=0.8, high=1.6, points=161)["response"]
    curve = [(row["x"], *[row["utility"][a] for a in alts], *[row["feasibility"][a] for a in alts],
              ",".join(row["recommended"]) or "-") for row in sweep["rows"]]
    r.tables["cost_response"] = (("b_cost", *[f"U {a}" for a in alts], *[f"F {a}" for a in alts], "recommended"),
                                 curve)
    change = sweep["changes"][0]["at"] if sweep["changes"] else None
    r.claim("the response curve changes recommendation where the flip analysis says", 1.30,
            round(change, 6) if change else None, _close(change, 1.30, 1e-6))
    infeasible_from = min((row["x"] for row in sweep["rows"] if row["feasibility"]["alt:B"] == "fail"), default=None)
    r.claim("B becomes infeasible above its 1.40 MUSD cost cap", "> 1.4", infeasible_from,
            infeasible_from is not None and 1.40 < infeasible_from <= 1.41)

    weights = []
    for i in range(61):
        w = 0.6 * i / 60
        s = state.model_copy(deep=True)
        objects = index(s)
        objects["crit:cost"].weight, objects["crit:reliability"].weight = w, 0.6 - w
        out = run(compile_graph(s, vehicle))
        weights.append((w, *[out.value(f"U/{a}")["U"] for a in alts], ",".join(out.decision["recommended"]) or "-"))
    r.tables["weight_response"] = (("w_cost", *[f"U {a}" for a in alts], "recommended"), weights)
    switch = next((row[0] for row in weights if row[-1] == "alt:A"), None)
    r.claim("re-running the kernel over the cost weight: A takes over just past 0.525", "0.525 < w <= 0.535",
            switch, switch is not None and 0.525 < switch <= 0.535)
    r.data = {"alternatives": alts, "names": {a.id: a.name for a in state.alternatives}, "utilities": rows,
              "recommended": result.decision["recommended"],
              "cost": {"flip": cost["flip_number"], "cap": 1.40, "now": 1.20},
              "weight": {"flip": weight["flip_number"], "now": 0.40}}
    return r


# --- 2. the program over refresh cycles ------------------------------------------------------------------

def _mainline(rows: list) -> list:
    by_id = {r.episode: r for r in rows}
    kids: dict[str, list] = {}
    for x in rows:
        if x.parent:
            kids.setdefault(x.parent, []).append(x.episode)
    tips = [x for x in rows if not kids.get(x.episode)]
    tip = max(tips, key=lambda x: (x.as_of, x.episode))
    chain = [tip]
    while chain[-1].parent:
        chain.append(by_id[chain[-1].parent])
    return chain[::-1]


def program_refresh() -> ScenarioResult:
    from pdo.bench.metrics import approval_invalidated
    from pdo.bench.suite import program_timeline
    from pdo.episodes import Program

    r = ScenarioResult("program-refresh", "A decision program over refresh cycles",
                       "The vehicle decision maintained over two years: each change set is re-evaluated selectively, "
                       "checked against a full recomputation, and its effect on the recommendation, readiness and "
                       "approval recorded.")
    vehicle = _vehicle()
    program = Program.load(vehicle)
    rows = program_timeline(program)
    runs = {ev.episode: ev.run for ev in program.evaluate_all(verify=False)}
    chain = _mainline(rows)
    alts = sorted({a for x in chain for a in x.utilities})
    table = []
    for x in chain:
        invalidated = bool(x.parent) and approval_invalidated(runs[x.parent], runs[x.episode])
        table.append((x.episode, x.parent or "-", x.as_of, x.trigger or "baseline", x.outcome or "-",
                      ",".join(x.recommended) or "-", x.readiness, x.actionable, invalidated,
                      *[x.utilities.get(a) for a in alts], x.total, x.computed, x.impact,
                      x.executed.get("cutoff"), x.verified.get("cutoff")))
    r.tables["main_line"] = (("episode", "parent", "as_of", "trigger", "outcome", "recommended", "readiness",
                              "actionable", "approval_invalidated", *[f"U {a}" for a in alts], "total_nodes",
                              "computed", "impact", "executed_cutoff", "verified"), table)
    children = [x for x in rows if x.parent]
    verified = sum(1 for x in children if x.verified.get("impact") and x.verified.get("cutoff"))
    r.claim("every episode, selectively re-evaluated, equals its full recomputation", f"{len(children)} of "
            f"{len(children)}", f"{verified} of {len(children)}")
    matches = sum(1 for x in rows if x.matches_expected)
    r.claim("every episode's outcome matches its hand-computed expectation (expected.yaml)",
            f"{len(rows)} of {len(rows)}", f"{matches} of {len(rows)}")
    flip = next((x for x in chain if x.trigger == "cost" and x.outcome == "changed_winner"), None)
    r.claim("a refresh cycle driven by a cost re-quote flips the recommendation B -> A", "ep:e10 alt:A",
            f"{flip.episode} {','.join(flip.recommended)}" if flip else None)
    approvals = [x.episode for x, row in zip(chain, table, strict=True) if row[8]]
    # Approvals are recorded at e01, e03, e08 and e11. The next changes to what they cover: B's cost (e02), B's
    # reliability becoming provisional (e04), B's re-quote (e10) and the range requirement (e12). B's quote
    # expiring (e09) changes no input: readiness becomes pending, and the approval still matches.
    r.claim("each approval stops matching at the next material change, and only then",
            ["ep:e02", "ep:e04", "ep:e10", "ep:e12"], approvals)
    main_children = [x for x in chain if x.parent]
    executed = sum(x.executed["cutoff"] for x in main_children)
    computed = sum(x.computed for x in main_children)
    r.claim("a refresh re-evaluates a fraction of the graph", "< 50% of computed nodes",
            f"{100 * executed / computed:.1f}%", executed < computed / 2)
    r.data = {"alternatives": alts, "chain": table, "bounds": {x.episode: dict(x.bounds) for x in chain if x.bounds}}
    return r


# --- 3. the perturbation benchmark ------------------------------------------------------------------------

ABLATIONS = ("impact", "cutoff", "delta_only", "one_hop", "no_readiness")


def perturbations() -> ScenarioResult:
    from pdo.bench import metrics as mx
    from pdo.bench.suite import run_suite

    r = ScenarioResult("perturbations", "Selective re-evaluation is sound, and cheaper",
                       "Single changes to the approved vehicle baseline, labeled by hand from the research brief's "
                       "arithmetic: how much each strategy re-evaluates, and whether its result equals a full "
                       "recomputation. The ablations remove one ingredient of the representation each.")
    result = run_suite(_bench("vehicle"), edge_p=(), timeline=False, flips=False)
    rows = []
    for c in sorted(result.cases, key=lambda c: (c.case.family, c.case.id)):
        outcome = (c.computed or {}).get("outcome", "-")
        for name in ABLATIONS:
            sm = c.strategies.get(name)
            if sm is None:
                continue
            rows.append((c.case.id, c.case.family, outcome, name, sm.computed, sm.executed, sm.equivalent, sm.sound,
                         mx.decision_matches(sm, c.full)))
    r.tables["cases"] = (("case", "family", "outcome", "strategy", "computed", "executed", "equivalent", "sound",
                          "decision_equal"), rows)
    summary = result.summary()
    agree = result.label_agreement
    r.claim("the kernel's labels agree with the independent hand labels", f"{agree.den:g} of {agree.den:g}",
            f"{agree.num:g} of {agree.den:g}")
    for name in ("impact", "cutoff"):
        eq = summary[name]["equivalent"]
        r.claim(f"{name}: every case equals full recomputation", f"{eq.den:g} of {eq.den:g}",
                f"{eq.num:g} of {eq.den:g}")
    for name in ("delta_only", "one_hop", "no_readiness"):
        eq = summary[name]["equivalent"]
        r.claim(f"ablation {name} gets some cases wrong", f"< {eq.den:g}", f"{eq.num:g} of {eq.den:g}", eq.num < eq.den)
    r.data = {"equivalent": {n: (summary[n]["equivalent"].num, summary[n]["equivalent"].den) for n in ABLATIONS},
              "decision": {n: (sum(1 for x in rows if x[3] == n and x[8]), sum(1 for x in rows if x[3] == n))
                           for n in ABLATIONS}}
    return r


# --- 4. report revisions ----------------------------------------------------------------------------------

def report_revisions() -> ScenarioResult:
    from pdo.bench.provenance import run_provenance

    r = ScenarioResult("report-revisions", "Revised reports: only changed values go back to people",
                       "23 edits to the vehicle's Word reports (wording, tables, renumbering, moved and changed "
                       "sections): the values flagged for review by section digests, against reviewing every value "
                       "the document supports, and the values whose quoted passage really changed.")
    result = run_provenance(_bench("provenance"))
    rows = [(x["case"], x["family"], x["whole_document"], len(x["flagged"]), len(x["expected_changed"]),
             len(set(x["expected_changed"]) & set(x["flagged"])), x["approval_kept"], x["readiness"])
            for x in result.rows]
    r.tables["cases"] = (("case", "family", "whole_document", "flagged", "changed", "changed_flagged",
                          "approval_kept", "readiness"), rows)
    t = result.totals()
    r.claim("every flag matches the independent label", f"{t['cases']} of {t['cases']}", f"{t['agree'][0]} of "
            f"{t['agree'][1]}")
    r.claim("every value whose quoted passage changed is flagged (recall)", f"{t['changed_passages']} of "
            f"{t['changed_passages']}", f"{t['changed_flagged']} of {t['changed_passages']}")
    r.claim("a revision changes no value, so no approval is invalidated", f"{t['cases']} of {t['cases']}",
            f"{t['approvals_kept'][0]} of {t['approvals_kept'][1]}")
    reduction = 100 * (1 - t["flagged"] / t["whole_document"])
    r.claim("section digests cut the review load against whole-document review", "> 75%", f"{reduction:.1f}%",
            reduction > 75)
    families = sorted({x[1] for x in rows})
    r.data = {"families": {f: tuple(sum(x[i] for x in rows if x[1] == f) for i in (2, 3, 4)) + (
        sum(1 for x in rows if x[1] == f),) for f in families},
        "totals": (t["whole_document"], t["flagged"], t["changed_passages"])}
    return r


# --- 5. uncertainty ---------------------------------------------------------------------------------------

def uncertainty() -> ScenarioResult:
    from pdo.sensitivity import analyze_path as sensitivity_path
    from pdo.uncertainty import analyze_path, summary

    r = ScenarioResult("uncertainty", "Deciding under uncertainty",
                       "The Fires counterfire radar decision with uncertain test, cost and schedule estimates: the "
                       "point-value ranking, expected utility under a chance constraint on feasibility, and the value "
                       "of resolving each input (which evidence to collect next).")
    fires = _example("fires_sensor")
    s = summary(analyze_path(fires, None, None, None))
    again = summary(analyze_path(fires, None, None, None))
    rule = s["rule"]
    rows = [(a["alternative"], a["point_utility"], a["expected_utility"], a["utility_se"], a["p_feasible"],
             a["chance"]) for a in s["alternatives"]]
    r.tables["alternatives"] = (("alternative", "point_U", "E_U", "se", "p_feasible", "chance"), rows)
    r.claim("the point-value ranking recommends the new array", ["alt:new-array"], s["deterministic"]["recommended"])
    r.claim("under expected_mavt the rule recommends the insert", ["alt:insert"], s["recommended"])
    na = next(a for a in s["alternatives"] if a["alternative"] == "alt:new-array")
    r.claim("the new array has the highest E[U] but fails the chance constraint",
            f"Pr(feasible) < {1 - rule['chance_alpha']:g}",
            f"{na['p_feasible']:.3f}", na["p_feasible"] < 1 - rule["chance_alpha"]
            and na["expected_utility"] == max(a["expected_utility"] for a in s["alternatives"]))
    r.claim("seeded: a second run draws the same samples", s["draws_digest"], again["draws_digest"])
    sens = sensitivity_path(fires, None)
    voi = sens["value_of_information"]
    params = sorted(voi["parameters"], key=lambda p: -(p["evppi"] or 0))
    r.tables["value_of_information"] = (("parameter", "evppi", "low", "high", "above_noise", "evidence"),
                                        [(p["parameter"], p["evppi"], *(p["interval"] or [None, None]),
                                          p["above_noise"], p.get("evidence") or "-") for p in params])
    top = voi["collect_next"][0] if voi["collect_next"] else None
    r.claim("value of information names the evidence to collect next", "obs:new-array.schedule.value",
            top["parameter"] if top else None)
    where = f"examples/fires_sensor, pdo uncertainty, {rule['samples']} samples, seed {rule['seed']}"
    r.values += [("PDO_UNC_FIRES_DECISION", ",".join(s["recommended"]), "alternative",
                  f"Recommendation under expected_mavt with the chance constraint ({where})"),
                 ("PDO_UNC_FIRES_POINT_DECISION", ",".join(s["deterministic"]["recommended"]), "alternative",
                  f"Recommendation from the point values alone ({where})"),
                 ("PDO_UNC_FIRES_P_FEASIBLE_NEW_ARRAY", f"{na['p_feasible']:.3f}", "1",
                  f"Probability the point-value winner meets every requirement ({where})"),
                 ("PDO_UNC_FIRES_CHANCE_LEVEL", f"{1 - rule['chance_alpha']:g}", "1",
                  f"Required probability of meeting every requirement (1 - alpha; {where})")]
    r.data = {"rows": rows, "alpha": rule["chance_alpha"], "samples": rule["samples"], "voi": params[:8],
              "evpi": voi["evpi"], "recommended": s["recommended"], "point": s["deterministic"]["recommended"]}
    return r


# --- 6. onboarding ----------------------------------------------------------------------------------------

ORIGINS = {"sysml": "SysML import (pdo)", "documents": "reports, quoted (agent proposes)",
           "person": "person (framing, roles)", "episodes": "person (later episodes)"}


def onboarding() -> ScenarioResult:
    from pdo.bench.onboarding import run_session
    from pdo.episodes import Program
    from pdo.load import iter_objects

    r = ScenarioResult("onboarding", "From raw inputs to an actionable decision",
                       "The vehicle decision built from its configuration sheets (PDF), reliability report (Word) and "
                       "SysML model by following `pdo onboard`: every step, who took it, and where each object of the "
                       "decision came from.")
    with tempfile.TemporaryDirectory(prefix="pdo-onboarding-") as tmp:
        session = run_session(tmp)
        program = Program.load(session.directory)
        newest = program.newest()
        result = program.evaluate(newest, verify=False).run
        origin_of = {}
        for oid, file in program.origins.items():
            name = Path(file).name
            origin_of[oid] = ("sysml" if name.startswith("sysml-") else "documents" if name == "documents.yaml"
                              else "person")
        counts: dict[tuple[str, str], int] = {}
        for _, obj in iter_objects(program.materialize(newest)):
            origin = origin_of.get(obj.id, "episodes")
            counts[(origin, type(obj).__name__)] = counts.get((origin, type(obj).__name__), 0) + 1
        nodes = len(result.graph.nodes)
        located = sum(1 for o in program.materialize(newest).observations if o.locator is not None)
        alternatives = [(a.id, (result.value(f"U/{a.id}") or {}).get("U")) for a in program.baseline.alternatives]
        refresh = session.steps[-1].result
    r.tables["steps"] = (("n", "funnel", "step", "actor", "action", "command"),
                         [(s.n, s.funnel, s.step or "-", s.actor, s.action, s.command) for s in session.steps])
    r.tables["objects"] = (("origin", "class", "count"), [(o, c, n) for (o, c), n in sorted(counts.items())])
    utilities = [round(u, 6) for _, u in alternatives]
    r.claim("built from raw inputs, the decision reproduces the reference trade study", [0.466667, 0.533333, 0.62],
            utilities)
    r.claim("the session ends actionable, every funnel complete", "done", session.final["stage"])
    r.claim("every value taken from a report carries a locator", 9, located)
    framing_by_tools = sum(n for (o, c), n in counts.items() if o in ("sysml", "documents") and c in
                           ("Criterion", "DecisionRule", "ReadinessPolicy", "ReadinessCondition", "BiasCheck", "Role"))
    r.claim("no framing object (criterion, rule, readiness, role) was written by a tool", 0, framing_by_tools)
    r.claim("every episode verified after the session", True, refresh.get("all_verified"))
    actors = session.actors()
    where = "the vehicle decision built from its PDF, Word report and SysML model by following pdo onboard"
    r.values += [("PDO_ONBOARD_STEPS", len(session.steps), "steps",
                  f"Steps from raw inputs to an actionable, studied decision ({where})"),
                 ("PDO_ONBOARD_PERSON_STEPS", actors["person"], "steps",
                  f"Of those, a person's statement or judgement ({where})"),
                 ("PDO_ONBOARD_AGENT_STEPS", actors["agent"], "steps",
                  f"Of those, an agent's proposal: values with quotes from the reports ({where})"),
                 ("PDO_ONBOARD_LOCATED_VALUES", located, "values",
                  f"Values taken from the reports, each with a computed locator ({where})"),
                 ("PDO_ONBOARD_OBJECTS", sum(counts.values()), "objects",
                  f"Objects in the resulting decision state ({where})")]
    r.data = {"steps": [s.to_dict() for s in session.steps], "counts": counts, "nodes": nodes, "actors": actors}
    return r


SCENARIOS: dict[str, Callable[[], ScenarioResult]] = {
    "trade-study": trade_study, "program-refresh": program_refresh, "perturbations": perturbations,
    "report-revisions": report_revisions, "uncertainty": uncertainty, "onboarding": onboarding,
}


def run_scenario(name: str) -> ScenarioResult:
    if name not in SCENARIOS:
        raise KeyError(f"no scenario {name!r} (scenarios: {', '.join(SCENARIOS)})")
    start = time.perf_counter()
    result = SCENARIOS[name]()
    result.seconds = time.perf_counter() - start
    return result


def write_results(results: list[ScenarioResult], out_dir: str | Path, figures: bool = True) -> list[Path]:
    """Write each scenario's tables, claims and figure under out_dir/<name>/, and out_dir/claims.tsv."""
    out = Path(out_dir)
    written: list[Path] = []
    for res in results:
        d = out / res.name
        d.mkdir(parents=True, exist_ok=True)
        for table, (header, rows) in res.tables.items():
            if write_if_changed(d / f"{table}.tsv", tsv(header, rows)):
                written.append(d / f"{table}.tsv")
        claims = tsv(("claim", "expected", "observed", "holds"),
                     [(c.claim, c.expected, c.observed, c.holds) for c in res.claims])
        if write_if_changed(d / "claims.tsv", claims):
            written.append(d / "claims.tsv")
        if figures:
            from pdo.bench.scenario_figures import write_figure

            written += write_figure(res, d)
    rows = [(res.name, c.claim, c.expected, c.observed, c.holds) for res in results for c in res.claims]
    if write_if_changed(out / "claims.tsv", tsv(("scenario", "claim", "expected", "observed", "holds"), rows)):
        written.append(out / "claims.tsv")
    values = [(k, v, unit, desc, res.name) for res in results for k, v, unit, desc in res.values]
    if write_if_changed(out / "values.tsv", tsv(("key", "value", "unit", "description", "scenario"),
                                               sorted(values, key=lambda x: x[0]))):
        written.append(out / "values.tsv")
    return written


def summary_text(results: list[ScenarioResult]) -> str:
    lines = []
    for res in results:
        lines.append(f"{res.name}: {res.title} ({res.seconds:.1f}s)")
        for c in res.claims:
            lines.append(f"  {'ok  ' if c.holds else 'FAIL'} {c.claim}: {c.observed}"
                         + ("" if c.holds else f" (expected {c.expected})"))
    return "\n".join(lines)


__all__ = ["SCENARIOS", "Claim", "ScenarioResult", "fmt", "run_scenario", "summary_text", "write_results"]
