"""A read-only dashboard of a decision program: data export and a self-contained HTML page.

The dashboard never computes anything itself. `build` evaluates the program the way `pdo program
refresh` does (every episode selectively against its parent, verified against a full recomputation)
and collects, per episode, each node's status and value and whether the refresh recomputed or reused
it, the decision table, readiness, pending actions by role, flip points, a tornado of the decision
margin, and the decision under uncertainty when inputs declare distributions. The graph layout is
computed here once, over the union of every episode's nodes, so a node keeps its place across episodes.
`render` embeds that data in the HTML template (dashboard.html); the page only draws it.
"""

from __future__ import annotations

import html
import json
import math
from pathlib import Path
from typing import Any

from pdo import __version__
from pdo import model as m
from pdo.actions import pending_actions
from pdo.graph import Graph
from pdo.load import index, items, iter_objects
from pdo.report import run_summary
from pdo.run import RunResult

TEMPLATE = Path(__file__).with_name("dashboard.html")

# Preferred column of each node kind, left to right. A node moves further left when a consumer sits at or
# before its preferred column (a measure that feeds an evaluator run lands left of the run), so every edge
# points rightward; source objects keep column 0 (hidden on the page by default).
COLUMNS = {"source": 0, "clock": 0, "run": 2, "measure": 3, "check": 4, "value": 4, "utility": 5, "feasible": 5,
           "decision": 6, "condition": 7, "readiness": 8, "state_digest": 8, "actionable": 9}
KIND_NAMES = {"source": "Inputs", "clock": "Inputs", "run": "Evaluator runs", "measure": "Measures",
              "check": "Checks and values", "value": "Checks and values", "utility": "Utility, feasibility",
              "feasible": "Utility, feasibility", "decision": "Decision", "condition": "Readiness conditions",
              "readiness": "Readiness", "state_digest": "Readiness", "actionable": "Authorization"}
KIND_ORDER = {"check": 0, "value": 1, "utility": 0, "feasible": 1, "readiness": 0, "state_digest": 1}
COL_W, ROW_H, NODE_W, NODE_H = 148, 40, 128, 32


def _short(oid: str | None) -> str:
    return (oid or "").split(":", 1)[-1]


def _label(nid: str, node) -> str:
    alt = _short(node.alternative)
    if node.kind == "measure":
        return f"{alt} · {_short(nid.split('/', 2)[2])}"
    if node.kind == "run":
        return f"{alt} · {_short(node.subject)}"
    if node.kind in ("check", "value"):
        return f"{alt} · {'g ' if node.kind == 'check' else 'v '}{_short(node.subject)}"
    if node.kind == "utility":
        return f"U({alt})"
    if node.kind == "feasible":
        return f"feasible({alt})"
    if node.kind == "condition":
        return _short(node.subject)
    if node.kind == "source":
        return nid.replace("#vf", " (value fn)").replace("#w", " (weight)").split(":", 1)[-1]
    return nid.replace("_", " ")


def _fmt(x: Any, unit: str | None = None) -> str:
    if x is None:
        return "unknown"
    if isinstance(x, bool):
        return "yes" if x else "no"
    if isinstance(x, int | float):
        text = f"{x:.4g}" if abs(x) < 1e5 else f"{x:.3e}"
        return f"{text} {unit}" if unit and unit != "1" else text
    return str(x)


def _node_state(graph: Graph, result: RunResult, nid: str, units: dict[str, str]) -> dict:
    """What the page shows for one node: status (pass/fail/unknown/value), a short value, how it was run."""
    node, res = graph.nodes[nid], result.results[nid]
    v = res.value if isinstance(res.value, dict) else {}
    status, text, detail = "value", "", None
    if node.kind == "measure":
        status = "value" if v.get("y") is not None else "unknown"
        text = _fmt(v.get("y"), units.get(node.subject))
        detail = v.get("why")
    elif node.kind == "run":
        outs = v.get("outputs")
        status = "unknown" if outs is None else "value"
        text = ", ".join(f"{k} {_fmt(x)}" for k, x in (outs or {}).items()) or v.get("why", "unknown")
        detail = v.get("error") or v.get("why")
    elif node.kind == "check":
        status, text = v["status"], f"g = {_fmt(v.get('g'))}"
    elif node.kind == "value":
        status = "value" if v.get("v") is not None else "unknown"
        text = f"v = {_fmt(v.get('v'))}"
    elif node.kind == "utility":
        status = "value" if v.get("U") is not None else "unknown"
        text = f"U = {_fmt(v['U'])}" if v.get("U") is not None else f"U in [{_fmt(v['lower'])}, {_fmt(v['upper'])}]"
    elif node.kind == "feasible":
        status = v["status"]
        text = "feasible" if status == "pass" else (f"fails {', '.join(_short(x) for x in v['failed'])}"
                                                    if v["failed"] else "unresolved")
    elif node.kind == "decision":
        text = ", ".join(_short(a) for a in v["recommended"]) or "no recommendation"
        status = "pass" if v["recommended"] else ("unknown" if v["unresolved"] else "fail")
    elif node.kind == "condition":
        status, text, detail = v["status"], v["status"], v.get("reason")
    elif node.kind == "readiness":
        status = {"ready": "pass", "pending": "unknown", "blocked": "fail"}[v["state"]]
        text = v["state"]
    elif node.kind == "actionable":
        status = "pass" if v["actionable"] else ("unknown" if v.get("ready") else "fail")
        text, detail = ("actionable" if v["actionable"] else "not actionable"), v.get("reason")
    elif node.kind == "state_digest":
        text = v["digest"][:12]
    elif node.kind in ("source", "clock"):
        status, text = "input", _source_text(res.value)
    out = {"s": status, "v": text, "e": res.exec_status}
    if detail:
        out["d"] = detail
    if node.kind in ("measure", "source"):
        from pdo.provenance import sources

        located = [{k: row.get(k) for k in ("observation", "document", "section", "section_path", "page", "quote",
                                            "current")}
                   for row in sources(graph.state, node.inputs if node.kind == "measure" else [nid]) if "quote" in row]
        if located:
            out["src"] = located  # the report passages the value was taken from
    return out


def _source_text(value: dict) -> str:
    for key in ("value", "threshold_value", "weight", "as_of", "review_status", "applicability"):
        if key in value:
            return f"{key.replace('_', ' ')} {_fmt(value[key], value.get('unit'))}"
    if "anchor_worst" in value:
        return f"{_fmt(value['anchor_worst'])} to {_fmt(value['anchor_best'])}"
    return value.get("class", "")


def _columns(nodes: dict, children: dict[str, set[str]]) -> dict[str, int]:
    """As-late-as-possible layering capped by each kind's preferred column, renumbered so the leftmost
    computed column is 1."""
    col: dict[str, int] = {}

    def place(nid: str, trail: frozenset = frozenset()) -> int:
        if nid not in col:
            pref = COLUMNS.get(nodes[nid].kind, 0)
            kids = [place(k, trail | {nid}) for k in children.get(nid, ()) if k not in trail]
            col[nid] = min([pref] + [k - 1 for k in kids])
        return col[nid]

    for nid in sorted(nodes):
        place(nid)
    computed = [c for nid, c in col.items() if nodes[nid].kind not in ("source", "clock")]
    shift = 1 - min(computed, default=1)
    return {nid: 0 if nodes[nid].kind in ("source", "clock") else c + shift for nid, c in col.items()}


def _column_name(kinds: set[str], col: int, run_cols: list[int]) -> str:
    if kinds == {"measure"} and run_cols and col < max(run_cols):
        return "Model inputs"
    names = sorted({KIND_NAMES.get(k, k) for k in kinds}, key=list(dict.fromkeys(KIND_NAMES.values())).index)
    return " · ".join(names)


def layout(graphs: list[Graph]) -> dict:
    """Positions for the union of every episode's nodes, in layered columns where every edge points right."""
    nodes: dict[str, Any] = {}
    edges: set[tuple[str, str]] = set()
    for g in graphs:
        for nid, node in g.nodes.items():
            nodes.setdefault(nid, node)
            for parent in node.inputs:
                edges.add((parent, nid))
    children: dict[str, set[str]] = {}
    for parent, child in edges:
        if parent in nodes and child in nodes:
            children.setdefault(parent, set()).add(child)
    col_of = _columns(nodes, children)
    columns: dict[int, list[str]] = {}
    for nid, c in col_of.items():
        columns.setdefault(c, []).append(nid)
    out = {}
    for col, nids in columns.items():
        def key(nid: str, col: int = col) -> tuple:
            node = nodes[nid]
            if col == 0:
                return (str(node.local.get("class", "")) if isinstance(node.local, dict) else "", nid)
            return (node.alternative or "~", KIND_ORDER.get(node.kind, 0), nid)
        for row, nid in enumerate(sorted(nids, key=key)):
            node = nodes[nid]
            out[nid] = {"col": col, "row": row, "kind": node.kind, "label": _label(nid, node),
                        "alternative": node.alternative, "subject": node.subject}
    run_cols = [c for nid, c in col_of.items() if nodes[nid].kind == "run"]
    names = [_column_name({nodes[n].kind for n in columns.get(c, [])}, c, run_cols) if c in columns else ""
             for c in range(max(columns, default=0) + 1)]
    return {"columns": names, "nodes": out, "edges": sorted(edges),
            "geometry": {"col_w": COL_W, "row_h": ROW_H, "node_w": NODE_W, "node_h": NODE_H}}


def _decision_table(result: RunResult) -> list[dict]:
    """Per alternative: utility, feasibility, measured values, and for the reviewer view each criterion's value
    score (v) and each requirement's margin (g) and status."""
    criteria = [c.id for c in items(result.graph.state.criteria)]
    rows = []
    for alt in run_summary(result)["alternatives"]:
        a = alt["alternative"]
        values = {c: result.value(f"v/{c}/{a}").get("v") for c in criteria if f"v/{c}/{a}" in result.results}
        rows.append({k: alt[k] for k in ("alternative", "utility", "utility_bounds", "feasibility", "failed",
                                         "unknown", "measures")}
                    | {"g": {req: c.get("g") for req, c in alt["checks"].items()},
                       "checks": {req: c.get("status") for req, c in alt["checks"].items()}, "values": values})
    return rows


def _framing(state) -> dict:
    """What this episode's numbers are judged against (weights and thresholds can change between episodes)."""
    return {"weights": {c.id: c.weight for c in items(state.criteria)},
            "requirements": {r.id: {"comparator": r.comparator, "threshold": r.threshold_value, "unit": r.unit,
                                    "measure": r.measure, "mandatory": r.mandatory is not False}
                             for r in items(state.requirements)}}


PROVENANCE_SLOTS = {"locator", "document_digest", "document_path"}


def _change_ops(change, parent_state) -> list[dict]:
    """The change set in reviewer terms: each assignment with the value it replaces, each object added or
    removed. Provenance slots (locators, document versions) are marked, so the page can summarize them."""
    if change is None:
        return []
    before = index(parent_state) if parent_state is not None else {}
    ops = []
    for a in items(change.assign):
        obj = before.get(a.target)
        old = getattr(obj, a.slot, None) if obj is not None else None
        ops.append({"op": "set", "target": a.target, "slot": a.slot,
                    "before": old.model_dump(exclude_none=True) if hasattr(old, "model_dump") else old,
                    "after": a.new_value, "provenance": a.slot in PROVENANCE_SLOTS})
    if change.add is not None:
        for _, obj in iter_objects(change.add):
            ops.append({"op": "add", "target": obj.id, "kind": type(obj).__name__,
                        "value": getattr(obj, "value", None), "unit": getattr(obj, "unit", None)})
    for oid in items(change.remove):
        ops.append({"op": "remove", "target": oid, "kind": type(before.get(oid)).__name__ if oid in before else None})
    return ops


def _evidence_rows(state) -> list[dict]:
    """Every observation with its value and where it came from (the document passage when located)."""
    from pdo.provenance import cite, sources

    obs = {o.id: o for o in items(state.observations)}
    out = []
    for row in sources(state, sorted(obs)):
        o = obs[row["observation"]]
        out.append({"observation": o.id, "alternative": o.alternative, "measure": o.measure, "value": o.value,
                    "unit": o.unit, "applicability": o.applicability or "accepted", "evidence": o.evidence,
                    "located": "quote" in row, "current": row.get("current", True), "source": cite(row)})
    return out


def _param_label(state, parameter: str) -> dict:
    """A flip parameter ("obs:B.cost.value", "crit:cost.weight") in words, with its unit."""
    oid, _, slot = parameter.rpartition(".")
    obj = index(state).get(oid)
    if obj is None:
        return {"label": parameter, "unit": None}
    objects = index(state)
    if isinstance(obj, m.Observation):
        measure = objects.get(obj.measure)
        return {"label": f"{_short(obj.alternative)}'s {(measure.name if measure else obj.measure).lower()}",
                "unit": obj.unit}
    if isinstance(obj, m.Requirement):
        return {"label": f"the {obj.name.lower() if obj.name else oid} threshold", "unit": obj.unit}
    if isinstance(obj, m.Criterion) and slot == "weight":
        return {"label": f"the weight on {(obj.name or oid).lower()}", "unit": "%"}
    if isinstance(obj, m.Criterion):
        return {"label": f"the {slot.replace('_', ' ')} of {(obj.name or oid).lower()}", "unit": None}
    return {"label": (getattr(obj, "name", None) or oid).lower() if slot == "value" else
            f"{(getattr(obj, 'name', None) or oid).lower()} ({slot.replace('_', ' ')})",
            "unit": getattr(obj, "unit", None)}


def _episode(ev, units: dict[str, str], analyses: dict, parent_state=None) -> dict:
    result = ev.run
    graph = result.graph
    summary = run_summary(result)
    change = ev.change
    episode = {
        "id": ev.episode, "parent": ev.parent, "as_of": str(ev.state.as_of),
        "trigger": change.trigger if change else "baseline",
        "description": (change.change_description if change else None) or ("Baseline" if ev.parent is None else ""),
        "recommended": summary["recommended"], "feasible": summary["feasible"], "infeasible": summary["infeasible"],
        "unresolved": summary["unresolved"], "readiness": summary["readiness"], "actionable": summary["actionable"],
        "actionable_reason": summary["actionable_reason"], "approval": summary["approval"],
        "state_digest": summary["state_digest"], "conditions": summary["conditions"],
        "pending": [a.to_dict() for a in pending_actions(result)], "table": _decision_table(result),
        "nodes": {nid: _node_state(graph, result, nid, units) for nid in graph.nodes},
        "impact": sorted(ev.impact) if ev.impact is not None else None, "verified": ev.verified,
        "cost": {"executed": len(result.executed),
                 "computed": sum(1 for n in graph.nodes.values() if n.fn != "source"), "total": len(graph.nodes)},
        "author_role": change.author_role if change else None, "changes": _change_ops(change, parent_state),
        "evidence": _evidence_rows(ev.state), **_framing(ev.state),
    }
    for f in analyses.get("flips") or []:
        f.update(_param_label(ev.state, f["parameter"]))
        if f.get("counterpart"):
            f["counterpart_label"] = _param_label(ev.state, f"{f['counterpart']}.weight")["label"]
    episode.update(analyses)
    return episode


def _analyses(result: RunResult, flips: bool, sensitivity: bool, uncertainty: bool, samples: int | None,
              full_sensitivity: bool) -> dict:
    out: dict[str, Any] = {}
    state, base_dir = result.graph.state, result.graph.base_dir
    if flips:
        from pdo.flip import scan_parameters, weight_flips

        _, found = scan_parameters(state, base_dir, None, 0.5, 17, base=result)
        found += weight_flips(state, base_dir, base=result)
        out["flips"] = [{k: f.get(k) for k in ("parameter", "counterpart", "direction", "flip_number",
                                               "baseline_number", "boundary_kind", "method",
                                               "previous_recommendation", "new_recommendation", "verified")}
                        for f in found]
    if sensitivity:
        from pdo.sensitivity import analyze

        sens = analyze(state, base_dir, samples=samples) if full_sensitivity else None
        if sens is None:  # tornado only: cheap, one input at a time
            from pdo.sensitivity import Probe, parameters, tornado

            probe = Probe(state, base_dir)
            sens = {"tornado": tornado(probe, parameters(state, include_empty=True))}
        out["sensitivity"] = {k: sens.get(k) for k in ("tornado", "global", "value_of_information", "notes")}
    rule = state.decision_rule
    if uncertainty and rule is not None and rule.rule_kind == "expected_mavt":
        from pdo.uncertainty import analyze as uncertainty_analysis
        from pdo.uncertainty import summary

        out["uncertainty"] = summary(uncertainty_analysis(state, base_dir))
    return out


def build(path: str | Path, *, flips: bool = True, sensitivity: bool = True, uncertainty: bool = True,
          samples: int | None = None) -> dict:
    """Everything the dashboard shows, as JSON-ready data. A plain state is shown as a one-episode program."""
    from pdo.evaluate import evaluate, is_program

    path = Path(path)
    if is_program(path):
        from pdo.episodes import Program

        program = Program.load(path)
        evaluations = program.evaluate_all(mode="cutoff", verify=True)
        title, question = program.meta.name or program.meta.id, program.meta.question
        baseline = program.baseline_episode
        main_line = program.lineage(program.newest())
    else:
        from pdo.episodes import EpisodeEvaluation

        result = evaluate(path)
        evaluations = [EpisodeEvaluation("state", None, result.graph.state, result)]
        title, question, baseline = path.name, result.graph.state.question, "state"
        main_line = ["state"]
    state0 = evaluations[0].state
    units = {m_.id: m_.unit for m_ in items(state0.measures)}
    episodes = []
    states = {ev.episode: ev.state for ev in evaluations}
    for ev in evaluations:
        full = ev.episode == baseline  # global indices and value of information for the baseline only
        analyses = _analyses(ev.run, flips, sensitivity, uncertainty, samples, full_sensitivity=full)
        episodes.append(_episode(ev, units, analyses, states.get(ev.parent)))
    names = {a.id: a.name or a.id for a in items(state0.alternatives)}
    objects = index(state0)
    roles = {r.id: r.name or r.id for r in items(state0.roles)}
    return {
        "meta": {"title": title, "question": (question or state0.question or "").strip(), "path": str(path),
                 "kernel_version": __version__, "baseline": baseline, "main_line": main_line,
                 "rule_kind": state0.decision_rule.rule_kind if state0.decision_rule else None},
        "alternatives": sorted(names), "alternative_names": names, "roles": roles,
        "measures": [{"id": m_.id, "name": m_.name or m_.id, "unit": m_.unit} for m_ in items(state0.measures)],
        "requirements": [{"id": r.id, "name": r.name or r.id, "measure": r.measure, "comparator": r.comparator,
                          "threshold": r.threshold_value, "unit": r.unit} for r in items(state0.requirements)],
        "criteria": [{"id": c.id, "name": c.name or c.id, "measure": c.measure, "weight": c.weight}
                     for c in items(state0.criteria)],
        "objects": {oid: getattr(o, "name", None) or oid for ev in evaluations
                    for oid, o in index(ev.state).items()} | {oid: getattr(o, "name", None) or oid
                                                             for oid, o in objects.items()},
        "layout": layout([ev.run.graph for ev in evaluations]),
        "episodes": episodes,
    }


def _clean(value: Any) -> Any:
    """JSON-safe: non-finite floats become null."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_clean(v) for v in value]
    return value


def render(data: dict, standalone: bool = True) -> str:
    """The dashboard page with the data embedded. `standalone` adds the document wrapper for opening the file
    directly; without it the page is the body content an artifact host wraps itself."""
    payload = json.dumps(_clean(data), separators=(",", ":"), default=str).replace("</", "<\\/")
    title = html.escape(data["meta"]["title"] or "Decision program", quote=False)
    page = (TEMPLATE.read_text(encoding="utf-8").replace("/*__PDO_DATA__*/null", payload)
            .replace("<title>Decision Program Console</title>", f"<title>{title}</title>", 1))
    if not standalone:
        return page
    return ('<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
            "</head>\n<body>\n" + page + "\n</body>\n</html>\n")


FILES = {"page": "index.html", "data": "dashboard.json", "fragment": "fragment.html"}


def _previous_dashboard(out: Path) -> bool:
    try:
        data = json.loads((out / FILES["data"]).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(data, dict) and {"meta", "layout", "episodes"} <= set(data)


def write(data: dict, out_dir: str | Path, fragment: bool = False) -> dict[str, Path]:
    """index.html (open it directly) and dashboard.json; with `fragment`, also fragment.html for hosts that
    supply the document wrapper themselves. Other files in the directory are left alone, and a page that a
    previous dashboard did not write is never overwritten."""
    out = Path(out_dir)
    clashes = [name for name in FILES.values() if (out / name).exists()]
    if clashes and not _previous_dashboard(out):
        raise FileExistsError(f"{out} already holds {', '.join(clashes)} not written by pdo dashboard")
    out.mkdir(parents=True, exist_ok=True)
    paths = {"page": out / FILES["page"], "data": out / FILES["data"]}
    paths["page"].write_text(render(data), encoding="utf-8")
    if fragment:
        paths["fragment"] = out / FILES["fragment"]
        paths["fragment"].write_text(render(data, standalone=False), encoding="utf-8")
    paths["data"].write_text(json.dumps(_clean(data), indent=1, default=str) + "\n", encoding="utf-8")
    return paths
