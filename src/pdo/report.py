"""Plain-text (Markdown-like) rendering of runs and nodes for the CLI."""

from __future__ import annotations

from typing import Any

from pdo.actions import pending_actions
from pdo.load import items
from pdo.run import RunResult


def _num(x: Any, digits: int = 6) -> str:
    if x is None:
        return "?"
    if isinstance(x, float):
        return f"{x:.{digits}g}" if abs(x) >= 1e4 or (x and abs(x) < 1e-3) else f"{round(x, digits):g}"
    return str(x)


def run_summary(result: RunResult) -> dict:
    """A JSON-ready summary of a run (the shape `pdo run --json` and the MCP tool return)."""
    state = result.graph.state
    d, r, a = result.decision, result.readiness, result.actionable
    alternatives = []
    for alt in sorted(x.id for x in state.alternatives):
        u = result.value(f"U/{alt}")
        f = result.value(f"F/{alt}")
        checks = {req.id: result.value(f"g/{req.id}/{alt}") for req in items(state.requirements)
                  if f"g/{req.id}/{alt}" in result.results}
        measures = {}
        for nid in result.graph.nodes:
            if nid.startswith(f"y/{alt}/"):
                measures[nid.split("/", 2)[2]] = result.value(nid).get("y")
        alternatives.append({"alternative": alt, "utility": u["U"], "utility_bounds": [u["lower"], u["upper"]],
                             "feasibility": f["status"], "failed": f["failed"], "unknown": f["unknown"],
                             "measures": measures, "checks": checks})
    conditions = []
    for nid in result.graph.order:
        if nid.startswith("cond/"):
            v = result.value(nid)
            conditions.append({"condition": nid[5:], "status": v["status"], "required": v.get("required", True),
                               "reason": v.get("reason")})
    return {
        "as_of": str(state.as_of), "question": state.question,
        "recommended": d["recommended"], "feasible": d["feasible"], "infeasible": d["infeasible"],
        "unresolved": d["unresolved"], "best_utility": d["best_utility"],
        "readiness": r["state"], "actionable": a["actionable"], "actionable_reason": a.get("reason"),
        "approval": a.get("approval"), "state_digest": a["state_digest"],
        "alternatives": alternatives, "conditions": conditions,
        "pending_actions": [p.to_dict() for p in pending_actions(result)],
    }


def render_run(result: RunResult, title: str | None = None) -> str:
    s = run_summary(result)
    state = result.graph.state
    rec = ", ".join(s["recommended"]) or "no recommendation"
    lines = [f"# {title or rec}", ""]
    if title:
        lines += [f"Recommended: {rec}"]
    lines += [f"Readiness: {s['readiness'].upper()}   Actionable: {'yes' if s['actionable'] else 'no'}"
              f" ({s['actionable_reason']})",
              f"as_of {s['as_of']}   state digest {s['state_digest']}", ""]
    measures = [c.measure for c in sorted(state.criteria, key=lambda c: c.id)]
    for r in items(state.requirements):
        if r.measure not in measures:
            measures.append(r.measure)
    units = {mm.id: mm.unit for mm in state.measures}
    header = ["alternative", *[f"{mid.split(':', 1)[-1]} [{units.get(mid, '')}]" for mid in measures], "U", "feasible"]
    lines += ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    for alt in s["alternatives"]:
        name = alt["alternative"] + (" *" if alt["alternative"] in s["recommended"] else "")
        cells = [name, *[_num(alt["measures"].get(mid)) for mid in measures]]
        u = alt["utility"]
        low, high = alt["utility_bounds"]
        cells.append(_num(u) if u is not None else f"[{_num(low)}, {_num(high)}]")
        feas = alt["feasibility"]
        if alt["failed"]:
            gs = ", ".join(f"{req} (g={_num(alt['checks'][req]['g'], 4)})" for req in alt["failed"])
            feas = f"fail: {gs}"
        elif alt["unknown"]:
            feas = f"unknown: {', '.join(alt['unknown'])}"
        cells.append(feas)
        lines.append("| " + " | ".join(cells) + " |")
    lines += ["", "## Readiness conditions", ""]
    for c in s["conditions"]:
        mark = {"pass": "pass", "fail": "FAIL", "unknown": "unknown"}[c["status"]]
        opt = "" if c["required"] else " (not required)"
        lines.append(f"- {mark:8} {c['condition']}{opt}" + (f": {c['reason']}" if c["reason"] else ""))
    if s["pending_actions"]:
        lines += ["", "## Pending actions", ""]
        for p in s["pending_actions"]:
            lines.append(f"- [{p['role'] or 'unassigned'}] {p['kind']} {p['target']}: {p['reason']}")
    return "\n".join(lines) + "\n"


def explain_node(result: RunResult, nid: str, depth: int = 1) -> dict:
    graph = result.graph
    if nid not in graph.nodes:
        close = sorted(n for n in graph.nodes if nid in n)[:10]
        raise KeyError(f"no node {nid!r}" + (f"; did you mean one of {close}?" if close else ""))
    node = graph.nodes[nid]
    res = result.results[nid]
    out = {"node": nid, "kind": node.kind, "fn": node.fn, "subject": node.subject, "alternative": node.alternative,
           "value": res.value, "signature": res.sig, "value_digest": res.vdig, "execution": res.exec_status,
           "error": res.error, "inputs": []}
    observed = [nid] if node.kind == "source" else list(node.inputs) if node.kind == "measure" else []
    if observed:
        from pdo.provenance import sources

        found = sources(graph.state, observed)
        if found:
            out["sources"] = found  # the evidence, and the document passage when the value is located
    for parent in node.inputs:
        if depth > 1:
            out["inputs"].append(explain_node(result, parent, depth - 1))
        else:
            out["inputs"].append({"node": parent, "value": result.value(parent)})
    return out


def graph_mermaid(result: RunResult, decision_only: bool = True) -> str:
    """Mermaid flowchart of the computed graph (sources omitted unless decision_only is False)."""
    graph = result.graph
    keep = {n for n, node in graph.nodes.items() if not decision_only or node.kind not in ("source", "clock")}
    ids = {n: f"n{i}" for i, n in enumerate(sorted(keep))}
    lines = ["flowchart LR"]
    for n in sorted(keep):
        lines.append(f'  {ids[n]}["{n}"]')
    for n in sorted(keep):
        for p in graph.nodes[n].inputs:
            if p in keep:
                lines.append(f"  {ids[p]} --> {ids[n]}")
    return "\n".join(lines) + "\n"
