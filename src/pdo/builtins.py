"""Built-in node functions. Each receives its node and the values of its inputs (by node id).

Values are JSON-ready dicts. Unknown is explicit: a missing value, a non-accepted applicability, a
failed evaluator or an unknown input yields None or status "unknown", never an invented pass or fail.
A function's VERSIONS entry is part of every signature it produces; bump it when semantics change.
"""

from __future__ import annotations

import math
from datetime import date
from typing import Any

VERSIONS = {
    "source": 1, "observe": 1, "pick": 1, "missing": 1, "python": 1, "linear_combination": 1, "threshold": 1,
    "value": 1, "utility": 1, "feasible": 1, "decide": 1, "aggregate": 1, "state_digest": 1, "actionable": 1,
    "recommendation_exists": 1, "unique_recommendation": 1, "coverage": 1, "evidence_current": 1,
    "evidence_traceable": 1, "assumptions_validated": 1, "risks_treated": 1, "review_status": 1,
    "omitted_options": 1, "distinct_measures": 1,
}
DEFAULT_TOLERANCE = 1.0e-9
DEFAULT_RISK_THRESHOLD = 12


def _of(inputs: dict[str, Any], prefix: str) -> dict[str, Any]:
    return {k: v for k, v in inputs.items() if k.startswith(prefix)}


def _action(kind: str, target: str, reason: str) -> dict:
    return {"kind": kind, "target": target, "reason": reason}


# --- measures -----------------------------------------------------------------------------------

def observe(node, inputs):
    """The value of one measure for one alternative, from its observations."""
    accepted, provisional = [], []
    missing = []
    for oid, obs in inputs.items():
        applicability = obs.get("applicability", "accepted")
        if applicability == "rejected":
            continue
        if obs.get("value") is None:
            missing.append(oid)
        elif applicability == "provisional":
            provisional.append(oid)
        else:
            accepted.append((oid, obs["value"]))
    if accepted:
        values = sorted({v for _, v in accepted})
        if len(values) == 1 or all(math.isclose(v, values[0], rel_tol=1e-12) for v in values):
            return {"y": values[0], "from": sorted(oid for oid, _ in accepted)}
        return {"y": None, "why": "conflicting accepted observations",
                "actions": [_action("review_applicability", oid, "conflicts with another accepted value")
                            for oid, _ in accepted]}
    if provisional:
        return {"y": None, "why": "applicability not yet accepted",
                "actions": [_action("review_applicability", oid, "accept or reject this observation")
                            for oid in sorted(provisional)]}
    if missing:
        return {"y": None, "why": "observation has no value",
                "actions": [_action("provide_value", oid, "the observation has no value") for oid in sorted(missing)]}
    return {"y": None, "why": "no applicable observation"}


def pick(node, inputs):
    (run,) = inputs.values()
    outputs = run.get("outputs")
    if outputs is None:
        return {"y": None, "why": run.get("why", "evaluator produced no result"),
                **({"actions": run["actions"]} if run.get("actions") else {})}
    return {"y": outputs.get(node.local["arg"])}


def missing(node, inputs):
    measure, alt = node.local["measure"], node.local["alternative"]
    return {"y": None, "why": "no observation or evaluator produces this measure",
            "actions": [_action("provide_value", alt, f"no value of {measure}")]}


def bound_inputs(node, inputs) -> tuple[dict[str, float], list[str]]:
    """Keyword arguments for a spec run, and the args whose inputs are unknown."""
    kwargs, unknown = {}, []
    for b in node.local["bindings"]:
        value = inputs[b["node"]]
        number = value.get("y") if b["kind"] == "measure" else value.get("value")
        if number is None:
            unknown.append(b["arg"])
        else:
            kwargs[b["arg"]] = number
    return kwargs, unknown


def spec_parameters(inputs: dict[str, Any], spec_id: str) -> dict[str, float]:
    return {p["arg"]: p["number"] for p in inputs[spec_id].get("parameters", [])}


# --- checks, values, utility, feasibility, decision ---------------------------------------------

def threshold(node, inputs):
    req_id, y_id = node.inputs
    req, y = inputs[req_id], inputs[y_id].get("y")
    mandatory = req.get("mandatory", True)
    if y is None:
        return {"status": "unknown", "g": None, "mandatory": mandatory}
    tol = req.get("check_tolerance", DEFAULT_TOLERANCE)
    t = req["threshold_value"]
    comparator = req["comparator"]
    g = t - y if comparator in ("ge", "gt") else y - t
    passed = g <= tol if comparator in ("ge", "le") else g < -tol
    return {"status": "pass" if passed else "fail", "g": g, "mandatory": mandatory}


def value_function(crit: dict, y: float) -> float:
    points = crit.get("points")
    if points:
        xs = [p["x"] for p in points]
        vs = [p["v"] for p in points]
        if y <= xs[0]:
            v = vs[0]
        elif y >= xs[-1]:
            v = vs[-1]
        else:
            i = next(i for i in range(1, len(xs)) if y <= xs[i])
            v = vs[i - 1] + (vs[i] - vs[i - 1]) * (y - xs[i - 1]) / (xs[i] - xs[i - 1])
    else:
        v = (y - crit["anchor_worst"]) / (crit["anchor_best"] - crit["anchor_worst"])
    if crit.get("clip", True):
        v = min(1.0, max(0.0, v))
    return v


def value(node, inputs):
    crit_id, y_id = node.inputs
    y = inputs[y_id].get("y")
    if y is None:
        return {"v": None, "y": None}
    return {"v": value_function(inputs[crit_id], y), "y": y}


def utility(node, inputs):
    criteria = node.local["criteria"]
    known, unknown_weight = [], 0.0
    missing_criteria = []
    for cid, alt_v in zip(criteria, [inputs[n] for n in node.inputs[: len(criteria)]], strict=True):
        w = inputs[f"{cid}#w"]["weight"]
        if alt_v["v"] is None:
            unknown_weight += w
            missing_criteria.append(cid)
        else:
            known.append(w * alt_v["v"])
    lower = math.fsum(known)
    if missing_criteria:
        return {"U": None, "lower": lower, "upper": lower + unknown_weight, "missing": missing_criteria}
    return {"U": lower, "lower": lower, "upper": lower}


def feasible(node, inputs):
    failed = sorted(k.split("/")[1] for k, v in inputs.items() if v["status"] == "fail")
    unknown = sorted(k.split("/")[1] for k, v in inputs.items() if v["status"] == "unknown")
    status = "fail" if failed else ("unknown" if unknown else "pass")
    return {"status": status, "failed": failed, "unknown": unknown}


def decide(node, inputs):
    alts = node.local["alternatives"]
    rule = inputs[node.inputs[0]]
    tol = rule.get("tie_tolerance", DEFAULT_TOLERANCE)
    U = {a: inputs[f"U/{a}"]["U"] for a in alts}
    F = {a: inputs[f"F/{a}"]["status"] for a in alts}
    feasible_ = [a for a in alts if F[a] == "pass"]
    infeasible = [a for a in alts if F[a] == "fail"]
    unresolved = [a for a in alts if F[a] == "unknown" or (F[a] == "pass" and U[a] is None)]
    ranked = {a: U[a] for a in feasible_ if U[a] is not None}
    if ranked:
        best = max(ranked.values())
        recommended = sorted(a for a, u in ranked.items() if u >= best - tol)
    else:
        best, recommended = None, []
    return {"recommended": recommended, "feasible": feasible_, "infeasible": infeasible, "unresolved": unresolved,
            "best_utility": best, "utilities": U, "tolerance": tol}


# --- readiness predicates -----------------------------------------------------------------------

def _decision(inputs):
    return inputs["decision"]


def recommendation_exists(node, inputs):
    d = _decision(inputs)
    if d["recommended"]:
        return {"status": "pass"}
    if d["unresolved"]:
        return {"status": "unknown", "reason": "no feasible alternative is known yet; some are unresolved"}
    return {"status": "fail", "reason": "no alternative satisfies every mandatory requirement",
            "actions": [_action("revise_alternatives", node.subject, "the feasible set is empty")]}


def unique_recommendation(node, inputs):
    d = _decision(inputs)
    if len(d["recommended"]) == 1:
        return {"status": "pass"}
    if len(d["recommended"]) > 1:
        return {"status": "unknown", "reason": f"tie between {', '.join(d['recommended'])}",
                "actions": [_action("resolve_tie", node.subject, "choose among tied alternatives or add a tie rule")]}
    if d["unresolved"]:
        return {"status": "unknown", "reason": "no recommendation yet"}
    return {"status": "fail", "reason": "no recommendation"}


def coverage(node, inputs):
    d = _decision(inputs)
    if not d["unresolved"]:
        return {"status": "pass"}
    best = d["best_utility"]
    could_win = []
    for alt in d["unresolved"]:
        upper = inputs[f"U/{alt}"]["upper"]
        if best is None or upper >= best - d["tolerance"]:
            could_win.append(alt)
    if not could_win:
        return {"status": "pass", "reason": "unresolved alternatives cannot beat the recommendation"}
    return {"status": "unknown", "reason": f"unresolved and could still win: {', '.join(could_win)}",
            "actions": [_action("provide_value", alt, "resolve its missing or unaccepted inputs") for alt in could_win]}


def _supporting_evidence(inputs) -> list[str]:
    recommended = set(_decision(inputs)["recommended"])
    evidence = set()
    for value in inputs.values():
        if value.get("class") == "Observation" and value.get("alternative") in recommended \
                and value.get("applicability", "accepted") == "accepted" and value.get("value") is not None:
            evidence.add(value["evidence"])
    return sorted(evidence)


def evidence_current(node, inputs):
    as_of = date.fromisoformat(inputs["clock"]["as_of"])
    stale = [ev for ev in _supporting_evidence(inputs)
             if inputs.get(ev, {}).get("valid_until") and date.fromisoformat(inputs[ev]["valid_until"]) < as_of]
    if stale:
        return {"status": "unknown", "reason": f"expired: {', '.join(stale)}",
                "actions": [_action("refresh_evidence", ev, "past valid_until") for ev in stale]}
    return {"status": "pass"}


def evidence_traceable(node, inputs):
    unstated = [ev for ev in _supporting_evidence(inputs) if not inputs.get(ev, {}).get("source_ref")]
    if unstated:
        return {"status": "fail", "reason": f"no stated source: {', '.join(unstated)}",
                "actions": [_action("state_source", ev, "name the source of this evidence") for ev in unstated]}
    return {"status": "pass"}


def assumptions_validated(node, inputs):
    critical = {k: v for k, v in inputs.items() if v.get("class") == "Assumption" and v.get("critical")}
    invalid = sorted(k for k, v in critical.items() if v.get("validation_status") == "invalid")
    unvalidated = sorted(k for k, v in critical.items() if v.get("validation_status") == "unvalidated")
    actions = [_action("validate_assumption", a, "critical assumption not validated") for a in invalid + unvalidated]
    if invalid:
        return {"status": "fail", "reason": f"invalid: {', '.join(invalid)}", "actions": actions}
    if unvalidated:
        return {"status": "unknown", "reason": f"unvalidated: {', '.join(unvalidated)}", "actions": actions}
    return {"status": "pass"}


def risks_treated(node, inputs):
    policy = next(v for v in inputs.values() if v.get("class") == "ReadinessPolicy")
    threshold_score = policy.get("risk_score_threshold", DEFAULT_RISK_THRESHOLD)
    allowed = set(policy.get("risk_acceptance_roles", []))
    failed, open_ = [], []
    for rid, risk in sorted(inputs.items()):
        if risk.get("class") != "Risk" or risk["likelihood"] * risk["consequence"] < threshold_score:
            continue
        treatment = risk.get("treatment_status", "untreated")
        if treatment == "unacceptable" or (treatment == "accepted" and risk.get("accepted_by_role") not in allowed):
            failed.append(rid)
        elif treatment == "untreated":
            open_.append(rid)
    actions = [_action("treat_risk", r, "mitigate, or accept by an allowed role") for r in failed + open_]
    if failed:
        return {"status": "fail", "reason": f"not acceptably treated: {', '.join(failed)}", "actions": actions}
    if open_:
        return {"status": "unknown", "reason": f"untreated: {', '.join(open_)}", "actions": actions}
    return {"status": "pass"}


def review_status(node, inputs):
    from pdo.graph import digest_of_sources

    review_id = node.local.get("review")
    if review_id is None:
        return {"status": "unknown", "reason": "the referenced review does not exist"}
    review = inputs[review_id]
    action = [_action("complete_review", review_id, "complete or redo this review")]
    stamped = review.get("covered_digest")
    if stamped is not None:
        current = digest_of_sources({nid: inputs[nid] for nid in node.local["covered"]})
        if current != stamped:
            return {"status": "unknown", "reason": "stale: covered objects changed since the review", "actions": action}
    status = review["review_status"]
    if status == "pass":
        return {"status": "pass"}
    if status == "fail":
        return {"status": "fail", "reason": "the review failed", "actions": action}
    return {"status": "unknown", "reason": "review pending", "actions": action}


def omitted_options(node, inputs):
    params = {p["arg"]: p["number"] for p in inputs[node.subject].get("parameters", [])}
    minimum = int(params.get("min_alternatives", 2))
    alts = {k: v for k, v in inputs.items() if v.get("class") == "Alternative"}
    challengers = [k for k, v in alts.items() if not v.get("is_incumbent")]
    if len(alts) < minimum or not challengers:
        return {"status": "fail", "reason": "only the incumbent (or too few alternatives) is under consideration",
                "actions": [_action("revise_alternatives", node.subject, "add alternatives to the incumbent")]}
    return {"status": "pass"}


def distinct_measures(node, inputs):
    seen: dict[str, str] = {}
    doubled = []
    for nid, crit in sorted(inputs.items()):
        if crit.get("class") != "Criterion":
            continue
        if crit["measure"] in seen:
            first, second = seen[crit["measure"]].split("#")[0], nid.split("#")[0]
            doubled.append(f"{first} and {second} both score {crit['measure']}")
        seen[crit["measure"]] = nid
    if doubled:
        return {"status": "fail", "reason": "; ".join(doubled),
                "actions": [_action("revise_alternatives", node.subject, "merge or drop the duplicate criterion")]}
    return {"status": "pass"}


# --- readiness, digest, authorization -----------------------------------------------------------

def aggregate(node, inputs):
    failed, unknown, passed = [], [], []
    for nid, value in inputs.items():
        if not value.get("required", True):
            continue
        cid = nid.split("/", 1)[1]
        {"fail": failed, "unknown": unknown, "pass": passed}[value["status"]].append(cid)
    state = "blocked" if failed else ("pending" if unknown else "ready")
    return {"state": state, "failed": sorted(failed), "unknown": sorted(unknown), "passed": len(passed)}


def state_digest_fn(node, inputs):
    from pdo.graph import digest_of_sources

    return {"digest": digest_of_sources(inputs)}


def actionable(node, inputs):
    readiness, decision = inputs["readiness"], inputs["decision"]
    current = inputs["state_digest"]["digest"]
    as_of = date.fromisoformat(inputs["clock"]["as_of"])
    policy = next(v for k, v in inputs.items() if v.get("class") == "ReadinessPolicy")
    roles = set(policy.get("approval_roles", []))
    out = {"actionable": False, "approval": None, "ready": readiness["state"] == "ready", "state_digest": current}
    if len(decision["recommended"]) != 1:
        out["reason"] = "no unique recommendation"
        return out
    (choice,) = decision["recommended"]
    problems = []
    for aid in node.local["approvals"]:
        ap = inputs[aid]
        if ap["approved_alternative"] != choice:
            problems.append(f"{aid} approves {ap['approved_alternative']}, not {choice}")
        elif ap["approver_role"] not in roles:
            problems.append(f"{aid} is from {ap['approver_role']}, which may not approve")
        elif ap.get("state_digest") != current:
            problems.append(f"{aid} covers an earlier state")
        elif ap.get("valid_until") and date.fromisoformat(ap["valid_until"]) < as_of:
            problems.append(f"{aid} expired")
        else:
            out["approval"] = aid
    if out["approval"] and out["ready"]:
        out["actionable"] = True
        out["reason"] = f"{choice} is approved ({out['approval']}) and ready"
    elif out["approval"]:
        out["reason"] = f"approved, but readiness is {readiness['state']}"
    else:
        out["reason"] = "; ".join(problems) if problems else f"{choice} has no approval"
        if out["ready"]:
            role = sorted(roles)[0] if roles else None
            out["actions"] = [_action("approve", choice, f"approve {choice}" + (f" ({role})" if role else ""))]
    return out


BUILTINS = {
    "observe": observe, "pick": pick, "missing": missing, "threshold": threshold, "value": value,
    "utility": utility, "feasible": feasible, "decide": decide, "aggregate": aggregate,
    "state_digest": state_digest_fn, "actionable": actionable,
    "recommendation_exists": recommendation_exists, "unique_recommendation": unique_recommendation,
    "coverage": coverage, "evidence_current": evidence_current, "evidence_traceable": evidence_traceable,
    "assumptions_validated": assumptions_validated, "risks_treated": risks_treated, "review_status": review_status,
    "omitted_options": omitted_options, "distinct_measures": distinct_measures,
}
