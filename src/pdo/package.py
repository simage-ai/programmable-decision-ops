"""Decision Packages: an evaluated episode as a validated record and a readable report.

episode_record turns a run into a DecisionEpisodeRecord (schema/record.yaml): the outcomes, readiness,
pending actions, one EvaluationRun per computed node (implementation, signature, run key, value digest),
and the assessments restated as Claims about Propositions, each with its basis: computed claims for
requirement checks and feasibility, human claims for reviews and for accepted (or rejected) evidence
applicability, and semantic claims for applicability an agent proposed that nobody has accepted yet.
Proposition and claim ids are content digests, so the same assessment keeps its id across runs.

render_markdown writes the human half from the record alone; write_package saves both and checks
record.json against the generated JSON Schema. replay re-evaluates the record's source in full and
compares value digests node by node. Every number in a package comes from a node evaluation.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from pdo import __version__
from pdo import model as m
from pdo.actions import pending_actions
from pdo.builtins import VERSIONS
from pdo.canonical import digest
from pdo.graph import DECISION_KINDS, covered_digest, resolve_selectors
from pdo.load import index, items
from pdo.provenance import cite, sources
from pdo.run import RunResult

_HERE = Path(__file__).resolve()
_SCHEMAS = (_HERE.parents[2] / "project" / "jsonschema" / "pdo_record.schema.json",  # a source checkout
            _HERE.parent / "jsonschema" / "pdo_record.schema.json")  # an installed package that ships it
RECORD_SCHEMA = next((p for p in _SCHEMAS if p.exists()), _SCHEMAS[0])
REVIEW_STATUS = {"pass": "pass", "fail": "fail", "pending": "unknown"}


# --- the record ---------------------------------------------------------------------------------

def implementation(result: RunResult, nid: str) -> str:
    """builtin@version, or a spec's implementation with its version and code digest."""
    node = result.graph.nodes[nid]
    if node.kind != "run" or node.fn != "python":  # builtins and linear_combination specs
        return f"{node.fn}@{VERSIONS.get(node.fn, 1)}"
    spec = index(result.graph.state)[node.subject]
    version = f"@{spec.implementation_version}" if spec.implementation_version else ""
    return f"{spec.implementation}{version}#{node.local.get('code_digest')}"


def _runs(result: RunResult) -> list[m.EvaluationRun]:
    out = []
    for nid in result.graph.order:
        node = result.graph.nodes[nid]
        if node.fn == "source":
            continue
        res = result.results[nid]
        out.append(m.EvaluationRun(node=nid, node_kind=node.kind, implementation=implementation(result, nid),
                                   signature=res.sig, run_key=res.key, value_digest=res.vdig,
                                   execution_status=res.exec_status, input_nodes=list(node.inputs),
                                   error=res.error))
    return out


def _outcomes(result: RunResult) -> list[m.AlternativeOutcome]:
    state = result.graph.state
    requirements = sorted(items(state.requirements), key=lambda r: r.id)
    criteria = sorted(state.criteria, key=lambda c: c.id)
    out = []
    for alt in sorted(a.id for a in state.alternatives):
        u, f = result.value(f"U/{alt}"), result.value(f"F/{alt}")
        checks = []
        for r in requirements:
            nid = f"g/{r.id}/{alt}"
            if nid in result.results:
                g = result.value(nid)
                checks.append(m.CheckOutcome(requirement=r.id, status=g["status"], g=g["g"],
                                             measured=result.value(f"y/{alt}/{r.measure}").get("y"),
                                             comparator=r.comparator, threshold_value=r.threshold_value, unit=r.unit))
        values = [m.CriterionValue(criterion=c.id, measured=result.value(f"v/{c.id}/{alt}")["y"],
                                   value_score=result.value(f"v/{c.id}/{alt}")["v"], weight=c.weight)
                  for c in criteria]
        out.append(m.AlternativeOutcome(alternative=alt, utility=u["U"], utility_lower=u["lower"],
                                        utility_upper=u["upper"], feasibility=f["status"], checks=checks,
                                        criterion_values=values))
    return out


def _readiness(result: RunResult) -> m.ReadinessAssessment:
    conditions = []
    for nid in sorted(result.graph.nodes):
        if result.graph.nodes[nid].kind == "condition":
            v = result.value(nid)
            conditions.append(m.ConditionResult(condition=nid.split("/", 1)[1], status=v["status"],
                                                required=v.get("required", True), reason=v.get("reason")))
    return m.ReadinessAssessment(readiness_state=result.readiness["state"], conditions=conditions)


def _impact(result: RunResult, impact: Any) -> m.ImpactSummary | None:
    if isinstance(impact, m.ImpactSummary):
        return impact
    if isinstance(impact, dict):
        return m.ImpactSummary(**impact)
    affected = set(impact) if impact is not None else result.impact
    if affected is None:
        return None
    statuses = Counter(r.exec_status for nid, r in result.results.items() if result.graph.nodes[nid].fn != "source")
    return m.ImpactSummary(impact_size=len(affected), executed_count=len(result.executed),
                           reused_count=statuses["reused"] + statuses["cutoff_reused"],
                           total_nodes=len(result.graph.nodes))


def support(result: RunResult, nid: str) -> list[str]:
    """Observations (those actually used), their evidence, assumptions and specs a node rests on."""
    graph = result.graph
    out: set[str] = set()
    stack, seen = [nid], {nid}
    while stack:
        node = graph.nodes[stack.pop()]
        if node.kind == "measure" and node.fn == "observe":
            for oid in result.value(node.id).get("from", []):
                out.add(oid)
                out.add(result.value(oid)["evidence"])
            continue
        if node.kind == "source":
            if node.local.get("class") in ("Assumption", "EvaluationSpecification"):
                out.add(node.subject)
            continue
        for parent in node.inputs:
            if parent not in seen:
                seen.add(parent)
                stack.append(parent)
    return sorted(out)


class _Claims:
    """Propositions and claims, with content-derived ids."""

    def __init__(self):
        self.propositions: dict[str, m.Proposition] = {}
        self.claims: dict[str, m.Claim] = {}

    def add(self, statement: str, about: list[str], status: str, basis: str, produced_by: str,
            supported_by: list[str] | None = None, source_locator: str | None = None) -> str:
        about = sorted(set(about))
        pid = "prop:" + digest(statement, about)
        self.propositions.setdefault(pid, m.Proposition(id=pid, statement=statement, about=about))
        supported = sorted(set(supported_by or []))
        cid = "claim:" + digest(pid, status, basis, produced_by, supported)
        self.claims[cid] = m.Claim(id=cid, proposition=pid, status=status, basis=basis, produced_by=produced_by,
                                   supported_by=supported or None, source_locator=source_locator)
        return cid


def _claims(result: RunResult) -> tuple[list[m.Proposition], list[m.Claim]]:
    state, graph = result.graph.state, result.graph
    out = _Claims()
    by_check: dict[str, str] = {}
    for nid in graph.order:
        node = graph.nodes[nid]
        if node.kind == "check":
            by_check[nid] = out.add(f"{node.alternative} satisfies {node.subject}", [node.alternative, node.subject],
                                    result.value(nid)["status"], "computed", nid, support(result, nid))
    for alt in sorted(a.id for a in state.alternatives):
        nid = f"F/{alt}"
        out.add(f"{alt} is feasible: it satisfies every mandatory requirement", [alt], result.value(nid)["status"],
                "computed", nid, [by_check[c] for c in graph.nodes[nid].inputs])
    for rv in items(state.reviews):
        covered = resolve_selectors(state, items(rv.covers))
        stale = rv.covered_digest is not None and rv.covered_digest != covered_digest(state, rv)
        status = "unknown" if stale and rv.review_status != "fail" else REVIEW_STATUS[rv.review_status]
        out.add(f"{rv.id} passes review of {', '.join(items(rv.covers))}", [rv.id, *covered],
                status, "human", rv.id)
    located = {row["observation"]: cite(row) for row in sources(state, [o.id for o in items(state.observations)])
               if "quote" in row}
    for o in sorted(items(state.observations), key=lambda o: o.id):
        applicability = o.applicability or "accepted"
        status = {"accepted": "pass", "rejected": "fail", "provisional": "unknown"}[applicability]
        basis = "semantic" if applicability == "provisional" else "human"
        out.add(f"{o.evidence} applies to {o.alternative} for {o.measure}",
                [o.id, o.alternative, o.measure, o.evidence], status, basis, o.id, [o.evidence], located.get(o.id))
    return list(out.propositions.values()), list(out.claims.values())


def episode_record(result: RunResult, episode: str | None = None, parent: str | None = None,
                   source: str | Path | None = None, flips: list[dict] | None = None,
                   scenarios: list[dict] | None = None, impact: Any = None,
                   scans: list[dict] | None = None, sensitivity: dict | None = None) -> m.DecisionEpisodeRecord:
    """The validated record of one evaluated episode.

    `source` (the path the state was read from) and `episode` are what replay needs. Pass flips (from
    pdo.flip, parameter and weight flips together), scans and scenario outcomes to include them; None
    means "not analysed", an empty list means "analysed, nothing found". `impact` is an ImpactSummary
    (or its dict); by default it is derived from a selective run's impact set.
    """
    state = result.graph.state
    d, a = result.decision, result.actionable
    propositions, claims = _claims(result)
    actions = [m.PendingAction(id=p.id, action_kind=p.kind, target=p.target, role=p.role, reason=p.reason,
                               produced_by=p.source) for p in pending_actions(result)]
    return m.DecisionEpisodeRecord(
        episode=episode, parent=parent, source_path=str(source) if source is not None else None,
        question=state.question, as_of=state.as_of, state_digest=a["state_digest"], kernel_version=__version__,
        recommended=d["recommended"], feasible=d["feasible"], infeasible=d["infeasible"], unresolved=d["unresolved"],
        outcomes=_outcomes(result), readiness=_readiness(result), actionable=a["actionable"],
        actionable_reason=a.get("reason"), matching_approval=a.get("approval"),
        flips=[m.FlipPoint(**f) for f in flips] if flips is not None else None,
        parameter_scans=[m.ParameterScan(**s) for s in scans] if scans is not None else None,
        scenario_outcomes=[m.ScenarioOutcome(**s) for s in scenarios] if scenarios is not None else None,
        robust=all(s["robust"] for s in scenarios) if scenarios else None,
        **_sensitivity_fields(sensitivity),
        pending_actions=actions, runs=_runs(result), propositions=propositions, claims=claims,
        impact=_impact(result, impact),
    )


def _sensitivity_fields(result: dict | None) -> dict:
    """The record's sensitivity slots: one entry per tornado input, joined with its global index and EVPPI
    when those were computed, plus the settings that let `pdo replay` re-run the analysis."""
    if result is None:
        return {}
    glob = result.get("global") or {}
    main = glob.get("main_target")
    total = {r["parameter"]: r.get(main, {}) for r in glob.get("indices", [])} if main else {}
    voi_result = result.get("value_of_information") or {}
    voi = {r["parameter"]: r for r in voi_result.get("parameters", [])}
    entries = []
    for row in (result.get("tornado") or {}).get("rows", []):
        pid = row["parameter"]
        t, v = total.get(pid, {}), voi.get(pid, {})
        t_iv, v_iv = t.get("total_interval") or [None, None], v.get("interval") or [None, None]
        entries.append(m.SensitivityEntry(
            parameter=pid, low_number=row["low"], high_number=row["high"], range_source=row["range_source"],
            margin_low=row["margin_low"], margin_high=row["margin_high"], swing=row["swing"],
            flips_within=row["flips_within"], first_order=t.get("first_order"), total_index=t.get("total"),
            total_low=t_iv[0], total_high=t_iv[1], evppi=v.get("evppi"), evppi_low=v_iv[0], evppi_high=v_iv[1],
            worth_resolving=v.get("above_noise"), evidence=v.get("evidence")))
    settings = result.get("settings") or {}
    evpi_iv = voi_result.get("evpi_interval") or [None, None]
    return {"sensitivity": entries,
            "sensitivity_settings": m.SensitivitySettings(
                seed=settings.get("seed"), samples=settings.get("samples"), span=settings.get("span"),
                infeasible_value=settings.get("infeasible_value"), estimator=voi_result.get("estimator")),
            "evpi": voi_result.get("evpi"), "evpi_low": evpi_iv[0], "evpi_high": evpi_iv[1],
            "expected_value_choice": voi_result.get("expected_value_choice")}


# --- the readable package -----------------------------------------------------------------------

def _num(x: float | None, signed: bool = False) -> str:
    if x is None:
        return "?"
    return f"{x:+.6g}" if signed else f"{x:.6g}"


def _alts(ids: list[str] | None) -> str:
    return ", ".join(ids) if ids else "none"


COMPARATORS = {"ge": "≥", "gt": ">", "le": "≤", "lt": "<"}


def _summary(r: m.DecisionEpisodeRecord, outcomes: dict[str, m.AlternativeOutcome]) -> list[str]:
    rec = r.recommended or []
    lines = ["## Summary", ""]
    if rec:
        us = ", ".join(f"{a} (U = {_num(outcomes[a].utility)})" for a in rec)
        lines.append(f"- **Recommended:** {us}" + (" (tie)" if len(rec) > 1 else ""))
    elif r.unresolved:
        lines.append("- **Recommended:** none yet (some alternatives are unresolved)")
    else:
        lines.append("- **Recommended:** none" + (" (the feasible set is empty)" if not r.feasible else ""))
    lines += [f"- Feasible: {_alts(r.feasible)}; infeasible: {_alts(r.infeasible)}; unresolved: {_alts(r.unresolved)}",
              f"- Readiness: **{(r.readiness.readiness_state if r.readiness else '?').upper()}**; "
              f"actionable: **{'yes' if r.actionable else 'no'}** ({r.actionable_reason or ''})"]
    if r.flips:
        nearest = _nearest_flip(r.flips)
        lines.append(f"- Smallest relative change that flips it: {_flip_phrase(nearest)}")
    if r.robust is not None:
        lines.append(f"- Robust over {len(r.scenario_outcomes)} declared scenarios: **{'yes' if r.robust else 'no'}**")
    if r.pending_actions:
        lines.append(f"- Pending actions: {len(r.pending_actions)}")
    lines += ["", f"Episode {r.episode or '(single state)'}" + (f", parent {r.parent}" if r.parent else "")
              + f"; as of {r.as_of}; state digest `{r.state_digest}`; pdo {r.kernel_version}"
              + (f"; source `{r.source_path}`" if r.source_path else ""), ""]
    return lines


def _flip_phrase(f: m.FlipPoint) -> str:
    where = "above" if f.direction == "above" else "below"
    against = f" (weight taken from {f.counterpart})" if f.counterpart else ""
    return (f"{f.parameter}{against} {where} {_num(f.flip_number)} (now {_num(f.baseline_number)}): "
            f"{_alts(f.previous_recommendation)} → {_alts(f.new_recommendation)}, {f.boundary_kind}")


def _nearest_flip(flips: list[m.FlipPoint]) -> m.FlipPoint:
    def relative(f: m.FlipPoint) -> float:
        scale = abs(f.baseline_number) or 1.0
        return abs(f.flip_number - f.baseline_number) / scale

    return min(flips, key=relative)


def _why(r: m.DecisionEpisodeRecord, outcomes: dict[str, m.AlternativeOutcome]) -> list[str]:
    lines = ["## The recommendation and why", ""]
    alts = sorted(outcomes)
    criteria = [cv.criterion for cv in outcomes[alts[0]].criterion_values or []] if alts else []
    weights = {cv.criterion: cv.weight for cv in outcomes[alts[0]].criterion_values or []} if alts else {}
    header = ["alternative", *[f"{c} (w = {_num(weights[c])})" for c in criteria], "U", "feasibility"]
    lines += ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    for alt in alts:
        o = outcomes[alt]
        cells = [f"**{alt}**" if alt in (r.recommended or []) else alt]
        cells += [f"{_num(cv.measured)} → v = {_num(cv.value_score)}" for cv in o.criterion_values or []]
        cells.append(_num(o.utility) if o.utility is not None
                     else f"[{_num(o.utility_lower)}, {_num(o.utility_upper)}]")
        cells.append(o.feasibility or "?")
        lines.append("| " + " | ".join(cells) + " |")
    lines.append("")
    best = r.recommended[0] if r.recommended else None
    if best is not None:
        others = [f"{a} (U = {_num(outcomes[a].utility)})" for a in (r.feasible or []) if a not in r.recommended]
        lines.append(f"{_alts(r.recommended)} has the highest utility among the feasible alternatives"
                     + (f"; the other feasible alternatives score lower: {', '.join(others)}." if others else "."))
    for alt in r.infeasible or []:
        o = outcomes[alt]
        failed = [c for c in o.checks or [] if c.status == "fail"]
        why = ", ".join(f"{c.requirement} (g = {_num(c.g, True)})" for c in failed)
        higher = best is not None and o.utility is not None and o.utility > outcomes[best].utility
        lines.append(f"- {alt} is infeasible: it fails {why}" + (f"; its utility {_num(o.utility)} would otherwise "
                     "rank higher, but a score cannot compensate for a mandatory requirement" if higher else "") + ".")
    for alt in r.unresolved or []:
        o = outcomes[alt]
        lines.append(f"- {alt} is unresolved: utility in [{_num(o.utility_lower)}, {_num(o.utility_upper)}], "
                     f"feasibility {o.feasibility}.")
    return lines + [""]


def _feasibility(outcomes: dict[str, m.AlternativeOutcome]) -> list[str]:
    alts = sorted(outcomes)
    rows: dict[str, dict[str, m.CheckOutcome]] = defaultdict(dict)
    labels: dict[str, str] = {}
    for alt in alts:
        for c in outcomes[alt].checks or []:
            rows[c.requirement][alt] = c
            unit = f" {c.unit}" if c.unit and c.unit != "1" else ""
            labels[c.requirement] = f"{c.requirement} ({COMPARATORS.get(c.comparator, c.comparator)} " \
                                    f"{_num(c.threshold_value)}{unit})"
    lines = ["## Feasibility", "", "Each cell is the measured value, then g in the g ≤ 0 convention (a negative g "
             "is slack, a positive g the size of the violation).", "",
             "| requirement | " + " | ".join(alts) + " |", "|" + "---|" * (len(alts) + 1)]
    for req in sorted(rows):
        cells = []
        for alt in alts:
            c = rows[req].get(alt)
            status = "" if c is None else "pass" if c.status == "pass" else c.status.upper()
            cells.append("n/a" if c is None else f"{status}: {_num(c.measured)}, g = {_num(c.g, True)}")
        lines.append(f"| {labels[req]} | " + " | ".join(cells) + " |")
    return lines + ["", "| alternative | feasibility |", "|---|---|"] + \
        [f"| {alt} | {outcomes[alt].feasibility} |" for alt in alts] + [""]


def _flips(r: m.DecisionEpisodeRecord) -> list[str]:
    lines = ["## What flips the decision", ""]
    if r.flips is None:
        return lines + ["Not analysed for this package.", ""]
    lines.append("Each row changes one input with every other input held fixed. The flip value is where the "
                 "recommendation changes; *affine_exact* is a linear solve on the crossing quantity, checked by "
                 "re-running either side, and *closed_form* is the exact weight-shift solution.")
    lines.append("")
    if not r.flips:
        lines.append("No flip within the scanned ranges.")
    else:
        lines += ["| input | now | flips at | from → to | boundary | crossing | method | verified |",
                  "|---|---|---|---|---|---|---|---|"]
        for f in r.flips:
            name = f.parameter + (f" (from {f.counterpart})" if f.counterpart else "")
            arrow = "↑ " if f.direction == "above" else "↓ "
            verified = {True: "yes", False: "no", None: "?"}[f.verified]
            lines.append(f"| {name} | {_num(f.baseline_number)} | {arrow}{_num(f.flip_number)} | "
                         f"{_alts(f.previous_recommendation)} → {_alts(f.new_recommendation)} | {f.boundary_kind} | "
                         f"`{f.crossing or ''}` | {f.method} | {verified} |")
    stable = [s for s in r.parameter_scans or [] if not s.flip_count]
    if stable:
        lines += ["", "Scanned without a flip (all other inputs fixed): "
                  + "; ".join(f"{s.parameter} over [{_num(s.scan_low)}, {_num(s.scan_high)}]" for s in stable) + "."]
    if r.parameter_scans:
        grid = r.parameter_scans[0].grid_points
        lines += ["", f"{len(r.parameter_scans)} numeric inputs were scanned, each on a {grid}-point grid; a flip "
                  "that reverses itself between two grid points would not be seen."]
    return lines + [""]


def _readiness_section(r: m.DecisionEpisodeRecord) -> list[str]:
    state = r.readiness.readiness_state if r.readiness else "?"
    lines = ["## Readiness and pending actions", "", f"Readiness: **{state.upper()}**. "
             f"Actionable: **{'yes' if r.actionable else 'no'}** ({r.actionable_reason or ''}).", ""]
    for c in r.readiness.conditions if r.readiness else []:
        mark = {"pass": "pass", "fail": "FAIL", "unknown": "unknown"}[c.status]
        opt = "" if c.required is not False else " (not required)"
        lines.append(f"- {mark}: {c.condition}{opt}" + (f": {c.reason}" if c.reason else ""))
    lines.append("")
    if not r.pending_actions:
        return lines + ["No pending actions.", ""]
    by_role: dict[str, list[m.PendingAction]] = defaultdict(list)
    for p in r.pending_actions:
        by_role[p.role or "unassigned"].append(p)
    for role in sorted(by_role):
        lines += [f"### {role}", ""]
        lines += [f"- {p.action_kind} {p.target}: {p.reason}" for p in by_role[role]]
        lines.append("")
    return lines


def _sensitivity_section(r: m.DecisionEpisodeRecord) -> list[str]:
    if r.sensitivity is None:
        return []
    lines = ["## Sensitivity and value of information", "",
             "Each input across its range with everything else fixed: the decision margin (the recommendation's "
             "value minus the best other alternative's; infeasible choices count as 0) at the low and high end. "
             "Where inputs declare distributions, the Sobol total index is the input's share of the margin's "
             "variance, and EVPPI is the expected gain in utility from resolving it.", "",
             "| input | range | margin at low | margin at high | flips | total index | EVPPI | evidence |",
             "|---|---|---|---|---|---|---|---|"]
    for e in r.sensitivity[:15]:
        distributed = "percentile" in (e.range_source or "")  # only inputs with a distribution get indices
        dash = "–" if not distributed else "?"
        evppi = dash if e.evppi is None else _num(e.evppi) + (" *" if e.worth_resolving else "")
        lines.append(f"| {e.parameter} | [{_num(e.low_number)}, {_num(e.high_number)}] | {_num(e.margin_low)} | "
                     f"{_num(e.margin_high)} | {'yes' if e.flips_within else ''} | "
                     f"{dash if e.total_index is None else _num(e.total_index)} | {evppi} | {e.evidence or ''} |")
    if len(r.sensitivity) > 15:
        lines.append(f"| ... {len(r.sensitivity) - 15} more in record.json | | | | | | | |")
    if r.evpi is not None:
        best: dict[str, float] = {}  # an evidence item is worth its most valuable input's EVPPI
        for e in r.sensitivity:
            if e.worth_resolving and e.evppi:
                key = e.evidence or e.parameter
                best[key] = max(best.get(key, 0.0), e.evppi)
        top = sorted(best.items(), key=lambda kv: -kv[1])[:3]
        choice = f" for the expected-value choice {_alts(r.expected_value_choice)}" if r.expected_value_choice else ""
        interval = f" (90% interval {_num(r.evpi_low)} to {_num(r.evpi_high)})" if r.evpi_low is not None else ""
        lines += ["", f"Resolving every uncertain input would be worth {_num(r.evpi)} utility points on expectation "
                  f"(EVPI{choice}){interval}. " + ("Evidence worth collecting next (EVPPI interval excludes zero, "
                  "marked *): " + "; ".join(f"{k} ({_num(v)})" for k, v in top) + "." if top else
                  "No single input's EVPPI interval excludes zero.")]
    return lines + [""]


def _scenarios(r: m.DecisionEpisodeRecord) -> list[str]:
    lines = ["## Scenario robustness", ""]
    if r.scenario_outcomes is None:
        return lines + ["Not analysed for this package.", ""]
    if not r.scenario_outcomes:
        return lines + ["No scenarios are declared.", ""]
    alts = sorted(o.alternative for o in r.scenario_outcomes[0].outcomes or [])
    lines += ["| scenario | recommended | feasible | " + " | ".join(f"U {a}" for a in alts) + " | baseline holds |",
              "|" + "---|" * (len(alts) + 4)]
    for s in r.scenario_outcomes:
        us = {o.alternative: o for o in s.outcomes or []}
        cells = [f"{_num(us[a].utility)}" + ("" if us[a].feasibility == "pass" else f" ({us[a].feasibility})")
                 for a in alts]
        lines.append(f"| {s.scenario} | {_alts(s.recommended)} | {_alts(s.feasible)} | " + " | ".join(cells)
                     + f" | {'yes' if s.robust else 'no'} |")
    flipped = [f"{s.scenario} (recommends {_alts(s.recommended)})" for s in r.scenario_outcomes if not s.robust]
    if len(r.recommended or []) != 1:
        return lines + ["", "There is no unique baseline recommendation, so there is nothing to hold across scenarios.",
                        ""]
    if r.robust:
        verdict = "is robust over the declared scenarios: it is feasible and uniquely best in every one"
    else:
        verdict = ("is not robust over the declared scenarios: it is not feasible and uniquely best in "
                   + ", ".join(flipped))
    lines += ["", f"The recommendation {_alts(r.recommended)} {verdict}. This is a claim about the tested scenarios "
              "only, not about every possible condition.", ""]
    return lines


def _value_sources(r: m.DecisionEpisodeRecord) -> list[str]:
    """Where each observed value comes from: the document passage when located, else the evidence."""
    rows = [c for c in r.claims or [] if c.basis in ("human", "semantic") and c.supported_by
            and c.produced_by.startswith("obs:")]
    if not any(c.source_locator for c in rows):
        return []
    status = {"pass": "accepted", "fail": "rejected", "unknown": "provisional"}
    lines = ["## Where the values come from", "", "Each value located in an indexed report cites its section and "
             "the exact passage; `pdo docs verify` checks every passage against its document.", "",
             "| observation | applicability | source |", "|---|---|---|"]
    for c in sorted(rows, key=lambda c: c.produced_by):
        source = (c.source_locator or f"{', '.join(c.supported_by)} (not located in a document)").replace("|", "\\|")
        lines.append(f"| `{c.produced_by}` | {status.get(c.status, c.status)} | {source} |")
    return lines + [""]


def _provenance(r: m.DecisionEpisodeRecord) -> list[str]:
    lines = ["## Provenance", "", "Every computed node, in evaluation order. The signature identifies what was "
             "computed from what; equal value digests on replay show the result is reproduced.", "",
             "| node | kind | implementation | signature | value digest | status |", "|---|---|---|---|---|---|"]
    for run in r.runs or []:
        lines.append(f"| `{run.node}` | {run.node_kind} | `{run.implementation}` | `{run.signature}` | "
                     f"`{run.value_digest}` | {run.execution_status}{' (' + run.error + ')' if run.error else ''} |")
    if r.impact is not None:
        i = r.impact
        lines += ["", f"Impact: {i.impact_size} of {i.total_nodes} nodes in the impact set; "
                  f"{i.executed_count} executed, {i.reused_count} reused."]
    return lines + [""]


def mermaid(r: m.DecisionEpisodeRecord) -> str:
    """Flowchart of the decision nodes (measures, runs, checks, values, utilities, feasibility, decision)."""
    keep = sorted(run.node for run in r.runs or [] if run.node_kind in DECISION_KINDS)
    ids = {n: f"n{i}" for i, n in enumerate(keep)}
    inputs = {run.node: run.input_nodes or [] for run in r.runs or []}
    lines = ["flowchart LR"] + [f'  {ids[n]}["{n}"]' for n in keep]
    lines += [f"  {ids[p]} --> {ids[n]}" for n in keep for p in inputs[n] if p in ids]
    return "\n".join(lines) + "\n"


def render_markdown(record: m.DecisionEpisodeRecord) -> str:
    """The Decision Package as Markdown, from the record alone."""
    outcomes = {o.alternative: o for o in record.outcomes or []}
    title = ", ".join(record.recommended or []) or "no recommendation"
    lines = [f"# Decision Package: {title}", ""]
    if record.question:
        lines += [f"> {record.question.strip()}", ""]
    lines += _summary(record, outcomes) + _why(record, outcomes) + _feasibility(outcomes) + _flips(record)
    lines += _readiness_section(record) + _value_sources(record) + _scenarios(record) + _sensitivity_section(record)
    lines += ["## Decision graph", "", "```mermaid", mermaid(record).rstrip(), "```", ""]
    lines += _provenance(record)
    claims = Counter(c.basis for c in record.claims or [])
    if claims:
        lines += [f"Claims: {sum(claims.values())} ({', '.join(f'{n} {b}' for b, n in sorted(claims.items()))}) "
                  f"about {len(record.propositions or [])} propositions; see record.json.", ""]
    return "\n".join(lines)


# --- files and replay ---------------------------------------------------------------------------

def record_json(record: m.DecisionEpisodeRecord) -> dict:
    return record.model_dump(mode="json", exclude_none=True)


def validate_record_json(data: dict, schema_path: Path = RECORD_SCHEMA) -> None:
    """Check against the generated JSON Schema (jsonschema when available, else the pydantic model)."""
    try:
        import jsonschema
    except ImportError:  # pragma: no cover - linkml brings jsonschema in development
        jsonschema = None
    if jsonschema is not None and schema_path.exists():
        jsonschema.validate(data, json.loads(schema_path.read_text(encoding="utf-8")))
    else:
        m.DecisionEpisodeRecord.model_validate(data)


def write_package(record: m.DecisionEpisodeRecord, out_dir: str | Path) -> dict[str, Path]:
    """Write package.md and record.json (validated) into out_dir."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    data = record_json(record)
    validate_record_json(data)
    paths = {"package": out_dir / "package.md", "record": out_dir / "record.json"}
    paths["record"].write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    paths["package"].write_text(render_markdown(record), encoding="utf-8")
    return paths


def load_record(path: str | Path) -> m.DecisionEpisodeRecord:
    return m.DecisionEpisodeRecord.model_validate(json.loads(Path(path).read_text(encoding="utf-8")))


def _source(record: m.DecisionEpisodeRecord, record_path: Path | None) -> Path:
    if not record.source_path:
        raise ValueError("the record has no source_path, so it cannot be replayed")
    source = Path(record.source_path)
    if not source.exists() and not source.is_absolute() and record_path is not None:
        source = record_path.parent / source
    if not source.exists():
        raise ValueError(f"source {record.source_path} does not exist")
    return source


def replay(record: m.DecisionEpisodeRecord, record_path: str | Path | None = None) -> dict:
    """Re-evaluate the record's source in full and compare value digests node by node."""
    from pdo.evaluate import evaluate, is_program
    from pdo.run import run

    source = _source(record, Path(record_path) if record_path is not None else None)
    episode = record.episode if is_program(source) else None
    full = run(evaluate(source, episode).graph, "full")
    replayed = {nid: res.vdig for nid, res in full.results.items() if full.graph.nodes[nid].fn != "source"}
    recorded = {r.node: r.value_digest for r in record.runs or []}
    mismatched = sorted(n for n in recorded if n in replayed and replayed[n] != recorded[n])
    missing = sorted(set(recorded) - set(replayed))
    extra = sorted(set(replayed) - set(recorded))
    state_matches = full.actionable["state_digest"] == record.state_digest
    sensitivity = None
    if record.sensitivity is not None and record.sensitivity_settings is not None:
        from pdo.sensitivity import analyze

        s = record.sensitivity_settings
        again = analyze(full.graph.state, full.graph.base_dir, seed=s.seed, samples=s.samples,
                        span=s.span if s.span is not None else 0.10,
                        infeasible_value=s.infeasible_value if s.infeasible_value is not None else 0.0)
        fields = _sensitivity_fields(again)
        sensitivity = ([e.model_dump() for e in fields["sensitivity"]] == [e.model_dump() for e in record.sensitivity]
                       and fields["evpi"] == record.evpi)
    reproduced = not mismatched and not missing and not extra and state_matches and sensitivity is not False
    return {"source": str(source), "episode": record.episode, "nodes": len(recorded),
            "matched": len(recorded) - len(mismatched) - len(missing), "mismatched": mismatched,
            "missing": missing, "extra": extra, "state_digest_matches": state_matches,
            "sensitivity_reproduced": sensitivity, "reproduced": reproduced,
            "recommended": full.decision["recommended"], "recorded_recommended": record.recommended or []}
