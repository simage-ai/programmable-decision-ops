"""Sensitivity analysis: how a decision responds to its inputs, beyond the point where it flips.

pdo.flip finds the value of one input at which the recommendation changes. This module describes the
response around and between those points, including through nonlinear evaluators:

  response curve        one input swept over a range, everything else fixed: each alternative's utility
                        and feasibility, the recommendation, the decision margin, its slope and curvature
  distribution          one input drawn from a distribution, everything else fixed: the spread of each
                        alternative's utility, P(win), P(feasible), expected regret, P(decision changes)
  tornado               every input across its range, one at a time: the swing of the decision margin,
                        ranked, and whether the recommendation changes anywhere in the range
  global                every uncertain input drawn jointly: variance-based (Sobol) first-order and total
                        indices of each alternative's value and of the decision margin, with bootstrap
                        intervals; rank correlations
  value of information  EVPI, and EVPPI per input with a bootstrap interval: how much the expected
                        decision would improve if that input's uncertainty were resolved, i.e. which
                        evidence is worth collecting next

Inputs (parameters) are written "<object id>.<slot>": an observation's or assumption's value
(obs:B.cost.value), a requirement threshold (req:min-range.threshold_value), a criterion anchor
(crit:cost.anchor_worst), or an evaluator parameter (spec:detection-range.range_exponent). A parameter's
range is its distribution's 5th to 95th percentile when it declares one, else its plausible range,
else the value plus or minus `span`. A criterion anchor's default range stops short of the other anchor.

The value of a choice. An alternative's value in a draw is its utility U when it is feasible, and
`infeasible_value` (default 0, the worst score on every criterion's scale; it may not be positive) when
a mandatory check fails. An unknown value is left out of the statistics and counted.

The decision margin compares the baseline recommendation (the tied set, when it is a tie) with every
other alternative: the recommendation's value minus the best competitor's optimistic value. A competitor
whose value is unknown counts at its utility upper bound (or its utility, when only its feasibility is
unknown), as the coverage readiness condition does, so an unresolved competitor can only shrink the
margin. A negative margin means another alternative could win.

Evaluation reuses the kernel. A probe is the point graph (the decision's part of it) with some source
nodes rebuilt from new content, exactly the nodes compile_graph builds for the assigned state, run in
cutoff mode against the point run, so only what the varied inputs reach is recomputed. Every result
reports how many node evaluations that took against a full run.
"""

from __future__ import annotations

import math
import random
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pdo import model as m
from pdo.graph import Graph, compile_graph, source_node
from pdo.load import items
from pdo.run import RunResult, run
from pdo.uncertainty import Uncertain, inverse_cdf, spec_params, stream

DEFAULT_SPAN = 0.10
DEFAULT_POINTS = 41
DEFAULT_SAMPLES = 1000
DEFAULT_GLOBAL_SAMPLES = 512
DEFAULT_SEED = 0
INFEASIBLE_VALUE = 0.0
BISECT_STEPS = 60
FLIP_SCAN = 9  # points (ends included) scanned per input for the tornado's "changes within range"
BOOTSTRAP = 200
NOISE_EPS = 1e-9


class SensitivityError(ValueError):
    """A parameter or option cannot be analyzed (unknown id, no range, invalid setting)."""


# --- parameters -----------------------------------------------------------------------------------

@dataclass(frozen=True)
class Param:
    id: str  # "<object id>.<slot>"
    target: str
    slot: str  # a slot of the object, or an evaluator parameter's arg (kind "spec_parameter")
    node: str  # the source node that carries it
    base: float
    low: float
    high: float
    range_source: str
    distribution: Uncertain | None = None
    evidence: str | None = None  # for an observation: the evidence item to refresh to resolve it
    kind: str = "slot"  # "slot" | "spec_parameter"
    avoid: float | None = None  # a value the input may not reach (a criterion's other anchor)

    def to_dict(self) -> dict:
        return {"parameter": self.id, "base": self.base, "low": self.low, "high": self.high,
                "range_source": self.range_source,
                "distribution": self.distribution.to_dict() if self.distribution else None,
                "evidence": self.evidence}


def _range(base: float, span: float, dist: Uncertain | None, plausible: tuple | None,
           avoid: float | None = None) -> tuple[float, float, str]:
    if dist is not None:
        return (inverse_cdf(dist.family, dist.params, 0.05), inverse_cdf(dist.family, dist.params, 0.95),
                f"{dist.family} 5th-95th percentile")
    if plausible and None not in plausible:
        return float(plausible[0]), float(plausible[1]), "plausible range"
    width = abs(base) * span
    low, high, source = base - width, base + width, f"value +/- {span:g}"
    if avoid is not None and low <= avoid <= high:  # a value function needs distinct anchors
        if avoid < base:
            low = (base + avoid) / 2
        else:
            high = (base + avoid) / 2
        source += ", stopping halfway to the other anchor"
    return low, high, source


def parameters(state: m.DecisionState, span: float = DEFAULT_SPAN, include_empty: bool = False) -> list[Param]:
    """Every numeric input of the state's objects, with its range (empty ranges only if asked)."""
    if not span > 0:
        raise SensitivityError("span must be positive")
    out: list[Param] = []
    for obj in [*items(state.observations), *items(state.assumptions)]:
        if obj.value is None:
            continue
        dist = None
        if obj.uncertainty is not None and obj.uncertainty.family != "point":
            dist = Uncertain(obj.id, obj.uncertainty.family, spec_params(obj.id, obj.uncertainty), obj.value)
        plausible = (getattr(obj, "plausible_low", None), getattr(obj, "plausible_high", None))
        low, high, source = _range(obj.value, span, dist, plausible)
        evidence = obj.evidence if isinstance(obj, m.Observation) else None
        out.append(Param(f"{obj.id}.value", obj.id, "value", obj.id, float(obj.value), low, high, source, dist,
                         evidence))
    for r in items(state.requirements):
        low, high, source = _range(r.threshold_value, span, None, None)
        out.append(Param(f"{r.id}.threshold_value", r.id, "threshold_value", r.id, float(r.threshold_value),
                         low, high, source))
    for c in items(state.criteria):
        for slot, other in (("anchor_worst", c.anchor_best), ("anchor_best", c.anchor_worst)):
            base = float(getattr(c, slot))
            low, high, source = _range(base, span, None, None, avoid=float(other))
            out.append(Param(f"{c.id}.{slot}", c.id, slot, f"{c.id}#vf", base, low, high, source,
                             avoid=float(other)))
    for s in items(state.evaluation_specifications):
        for p in items(s.parameters):
            low, high, source = _range(p.number, span, None, None)
            out.append(Param(f"{s.id}.{p.arg}", s.id, p.arg, s.id, float(p.number), low, high, source,
                             kind="spec_parameter"))
    return out if include_empty else [p for p in out if p.high > p.low]


def find_parameter(state: m.DecisionState, pid: str, span: float = DEFAULT_SPAN,
                   low: float | None = None, high: float | None = None) -> Param:
    known = {p.id: p for p in parameters(state, span, include_empty=True)}
    if pid not in known:
        close = sorted(k for k in known if pid.split(".")[0] in k)[:8]
        raise SensitivityError(f"no numeric parameter {pid!r}" + (f"; try one of {close}" if close else ""))
    p = known[pid]
    if low is not None or high is not None:
        p = Param(p.id, p.target, p.slot, p.node, p.base, p.low if low is None else low,
                  p.high if high is None else high, "given", p.distribution, p.evidence, p.kind, p.avoid)
    if not p.high > p.low:
        raise SensitivityError(f"{pid} has no range (its value is {p.base:g} and it declares no distribution or "
                               "plausible range); give one with low and high")
    if p.avoid is not None and p.low <= p.avoid <= p.high:
        raise SensitivityError(f"{pid}: the range [{p.low:g}, {p.high:g}] reaches the other anchor ({p.avoid:g}), "
                               "where the value function is undefined")
    return p


# --- probing the kernel ---------------------------------------------------------------------------

def _closure(start: set[str], edges) -> set[str]:
    seen, queue = set(start), deque(start)
    while queue:
        for nxt in edges(queue.popleft()):
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    return seen


def _with(local: dict, param: Param, x: float) -> dict:
    """A source node's content with one parameter set to x (what compile_graph would build)."""
    if param.kind == "spec_parameter":
        params = [dict(p) for p in local.get("parameters", [])]
        if not any(p["arg"] == param.slot for p in params):
            raise SensitivityError(f"{param.id}: {param.target} has no parameter {param.slot!r}")
        for p in params:
            if p["arg"] == param.slot:
                p["number"] = float(x)
        return {**local, "parameters": params}
    if param.slot not in local:
        raise SensitivityError(f"{param.id}: {param.target} has no slot {param.slot!r}")
    return {**local, param.slot: float(x)}


@dataclass
class Outcome:
    utility: dict[str, float | None]
    upper: dict[str, float]  # utility upper bound (U itself when known)
    feasibility: dict[str, str]
    value: dict[str, float | None]
    recommended: list[str]


class Probe:
    """The decision's part of a state's graph, run once on point values, then re-run per probe."""

    def __init__(self, state: m.DecisionState, base_dir: Path | str, infeasible_value: float = INFEASIBLE_VALUE):
        if infeasible_value > 0:
            raise SensitivityError("infeasible_value may not be positive: an infeasible choice cannot be worth more "
                                   "than the worst feasible score")
        full = compile_graph(state, base_dir)
        keep = _closure({"decision"}, lambda nid: full.nodes[nid].inputs)
        self.graph = Graph({n: full.nodes[n] for n in keep}, tuple(n for n in full.order if n in keep), state,
                           full.base_dir, {k: v for k, v in full.callables.items() if k in keep})
        self.point = run(self.graph)
        self.children = self.graph.children()
        self.alternatives = sorted(a.id for a in state.alternatives)
        self.infeasible_value = infeasible_value
        self.computed = sum(1 for n in self.graph.nodes.values() if n.fn != "source")
        self.evaluations = 0
        self.executed = 0
        self.baseline = self.point.decision["recommended"]
        self.rule_kind = (state.decision_rule.rule_kind if state.decision_rule else None) or "additive_mavt"
        self.point_outcome = self.outcome(self.point)

    def depends_on(self, param: Param) -> bool:
        return param.node in self.graph.nodes

    def run(self, values: list[tuple[Param, float]]) -> RunResult:
        """Re-run with each (parameter, value) assigned; parameters on one node compose."""
        nodes = dict(self.graph.nodes)
        changed = set()
        for param, x in values:
            if not self.depends_on(param):
                raise SensitivityError(f"{param.id}: the decision does not depend on {param.target}")
            nodes[param.node] = source_node(param.node, _with(nodes[param.node].local, param, x),
                                            subject=param.target)
            changed.add(param.node)
        g = self.graph
        result = run(Graph(nodes, g.order, g.state, g.base_dir, g.callables), "cutoff", prev=self.point,
                     impact=_closure(changed, lambda nid: self.children[nid]))
        self.evaluations += 1
        self.executed += len(result.executed)
        return result

    def outcome(self, result: RunResult) -> Outcome:
        utility, upper, feasibility, value = {}, {}, {}, {}
        for alt in self.alternatives:
            u = result.value(f"U/{alt}")
            f = result.value(f"F/{alt}")["status"]
            utility[alt], upper[alt], feasibility[alt] = u["U"], u["upper"], f
            value[alt] = u["U"] if f == "pass" else (self.infeasible_value if f == "fail" else None)
        return Outcome(utility, upper, feasibility, value, result.decision["recommended"])

    def evaluate(self, values: list[tuple[Param, float]]) -> Outcome:
        return self.outcome(self.run(values))

    def _optimistic(self, o: Outcome, alt: str) -> float:
        if o.feasibility[alt] == "fail":
            return self.infeasible_value
        return o.utility[alt] if o.utility[alt] is not None else o.upper[alt]

    def margin(self, o: Outcome) -> float | None:
        """The baseline recommendation's value minus the best competitor's optimistic value.

        None when there is no baseline recommendation, no recommended alternative's value is known, or
        there is no competitor.
        """
        if not self.baseline:
            return None
        own = [o.value[a] for a in self.baseline if o.value[a] is not None]
        others = [self._optimistic(o, a) for a in self.alternatives if a not in self.baseline]
        if not own or not others:
            return None
        return max(own) - max(others)

    def mark(self) -> tuple[int, int]:
        return self.evaluations, self.executed

    def cost(self, since: tuple[int, int] = (0, 0)) -> dict:
        """Probes run since `since` (a mark), and the share of computed nodes each re-ran on average."""
        evaluations, executed = self.evaluations - since[0], self.executed - since[1]
        mean = executed / evaluations if evaluations else 0.0
        return {"evaluations": evaluations, "computed_nodes": self.computed, "mean_executed": round(mean, 3),
                "executed_fraction": round(mean / self.computed, 4) if self.computed else None}


# --- statistics -------------------------------------------------------------------------------------

def _quantile(xs: list[float], q: float) -> float:
    s = sorted(xs)
    pos = q * (len(s) - 1)
    lo = math.floor(pos)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def _summary(xs: list[float]) -> dict:
    if not xs:
        return {"n": 0}
    mean = math.fsum(xs) / len(xs)
    var = math.fsum((x - mean) ** 2 for x in xs) / (len(xs) - 1) if len(xs) > 1 else 0.0
    return {"n": len(xs), "mean": mean, "sd": math.sqrt(var), "p5": _quantile(xs, 0.05),
            "p50": _quantile(xs, 0.5), "p95": _quantile(xs, 0.95)}


def _ranks(xs: list[float]) -> list[float]:
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    ranks = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return ranks


def _correlation(a: list[float], b: list[float]) -> float | None:
    n = len(a)
    if n < 3:
        return None
    ma, mb = math.fsum(a) / n, math.fsum(b) / n
    sab = math.fsum((x - ma) * (y - mb) for x, y in zip(a, b, strict=True))
    sa = math.sqrt(math.fsum((x - ma) ** 2 for x in a))
    sb = math.sqrt(math.fsum((y - mb) ** 2 for y in b))
    return sab / (sa * sb) if sa > 0 and sb > 0 else None


def spearman(a: list[float], b: list[float]) -> float | None:
    return _correlation(_ranks(a), _ranks(b))


def _draws(param: Param, samples: int, seed: int, dist: Uncertain | None, stream_key: str = "") -> list[float]:
    rng = stream(seed, param.target + stream_key)
    if dist is None:
        return [param.low + rng.random() * (param.high - param.low) for _ in range(samples)]
    return [inverse_cdf(dist.family, dist.params, rng.random()) for _ in range(samples)]


def _interval(xs: list[float]) -> list[float] | None:
    return [_quantile(xs, 0.05), _quantile(xs, 0.95)] if xs else None


# --- one parameter: response curve and distribution ---------------------------------------------------

def response_curve(probe: Probe, param: Param, points: int = DEFAULT_POINTS) -> dict:
    """Sweep one parameter over its range, everything else fixed."""
    if points < 2:
        raise SensitivityError("a response curve needs at least 2 points")
    step = (param.high - param.low) / (points - 1)
    xs = sorted({param.low + i * step for i in range(points)} | {param.base})
    rows = []
    for x in xs:
        o = probe.evaluate([(param, x)])
        rows.append({"x": x, "utility": o.utility, "feasibility": o.feasibility, "value": o.value,
                     "recommended": o.recommended, "margin": probe.margin(o)})
    changes = []
    for a, b in zip(rows, rows[1:], strict=False):
        if a["recommended"] != b["recommended"]:
            lo, hi = a["x"], b["x"]
            for _ in range(BISECT_STEPS):
                mid = (lo + hi) / 2
                if probe.evaluate([(param, mid)]).recommended == a["recommended"]:
                    lo = mid
                else:
                    hi = mid
            changes.append({"at": (lo + hi) / 2, "from": a["recommended"], "to": b["recommended"]})
    shape = {alt: _shape(probe, param, rows, alt) for alt in probe.alternatives}
    return {"parameter": param.to_dict(), "baseline": probe.baseline, "rows": rows, "changes": changes,
            "shape": shape}


def _shape(probe: Probe, param: Param, rows: list[dict], alt: str) -> dict:
    """Slope at the base value, and how far the utility curve departs from a straight line."""
    known = [(r["x"], r["utility"][alt]) for r in rows if r["utility"][alt] is not None]
    out: dict[str, Any] = {"slope": None, "elasticity": None, "chord_deviation": None, "relative_deviation": None}
    h = (param.high - param.low) * 1e-4
    up, down = probe.evaluate([(param, param.base + h)]), probe.evaluate([(param, param.base - h)])
    if up.utility[alt] is not None and down.utility[alt] is not None:
        slope = (up.utility[alt] - down.utility[alt]) / (2 * h)
        out["slope"] = slope
        u0 = probe.point_outcome.utility[alt]
        if u0 not in (None, 0) and param.base != 0:
            out["elasticity"] = slope * param.base / u0
    if len(known) >= 3:
        (x0, y0), (x1, y1) = known[0], known[-1]
        deviation = max(abs(y - (y0 + (y1 - y0) * (x - x0) / (x1 - x0))) for x, y in known)
        out["chord_deviation"] = deviation
        rise = max(y for _, y in known) - min(y for _, y in known)
        out["relative_deviation"] = deviation / rise if rise > 0 else 0.0
    return out


def distribution_response(probe: Probe, param: Param, samples: int = DEFAULT_SAMPLES, seed: int = DEFAULT_SEED,
                          distribution: Uncertain | None = None) -> dict:
    """Draw one parameter from a distribution, everything else fixed."""
    if samples < 1:
        raise SensitivityError("samples must be at least 1")
    dist = distribution or param.distribution
    xs = _draws(param, samples, seed, dist)
    utilities: dict[str, list[float]] = {a: [] for a in probe.alternatives}
    feasible = dict.fromkeys(probe.alternatives, 0)
    wins = dict.fromkeys(probe.alternatives, 0.0)
    regret: dict[str, list[float]] = {a: [] for a in probe.alternatives}
    unknown = dict.fromkeys(probe.alternatives, 0)
    changed = none = 0
    for x in xs:
        o = probe.evaluate([(param, x)])
        for alt in probe.alternatives:
            if o.utility[alt] is not None:
                utilities[alt].append(o.utility[alt])
            feasible[alt] += o.feasibility[alt] == "pass"
        for alt in o.recommended:
            wins[alt] += 1 / len(o.recommended)
        none += not o.recommended
        known = [v for v in o.value.values() if v is not None]
        best = max(known) if known else None
        for alt in probe.alternatives:
            if o.value[alt] is None or best is None:
                unknown[alt] += 1
            else:
                regret[alt].append(best - o.value[alt])
        changed += o.recommended != probe.baseline
    alternatives = {alt: {"utility": _summary(utilities[alt]), "p_feasible": feasible[alt] / samples,
                          "p_win": wins[alt] / samples,
                          "expected_regret": math.fsum(regret[alt]) / len(regret[alt]) if regret[alt] else None,
                          "unknown_draws": unknown[alt]} for alt in probe.alternatives}
    if dist:
        source = f"{dist.family}(" + ", ".join(f"{k}={v:g}" for k, v in dist.params.items()) + ")"
    else:
        source = f"uniform over [{param.low:g}, {param.high:g}]"
    return {"parameter": param.to_dict(), "distribution": source, "samples": samples, "seed": seed,
            "x": _summary(xs), "baseline": probe.baseline, "p_decision_changes": changed / samples,
            "p_no_recommendation": none / samples, "alternatives": alternatives,
            "infeasible_value": probe.infeasible_value}


# --- all parameters: tornado, global indices, value of information ----------------------------------

def tornado(probe: Probe, params: list[Param], scan: int = FLIP_SCAN) -> dict:
    """Each parameter across its range, one at a time: the margin at both ends, and any change of the
    recommendation at `scan` evenly spaced points (ends included)."""
    m0 = probe.margin(probe.point_outcome)
    rows, skipped = [], []
    for p in params:
        if not probe.depends_on(p):
            skipped.append({"parameter": p.id, "reason": "the decision does not depend on it"})
            continue
        if not p.high > p.low:
            skipped.append({"parameter": p.id, "reason": "no range (value 0, no distribution or plausible range)"})
            continue
        xs = [p.low + i * (p.high - p.low) / (scan - 1) for i in range(scan)]
        outcomes = [probe.evaluate([(p, x)]) for x in xs]
        lo, hi = outcomes[0], outcomes[-1]
        m_lo, m_hi = probe.margin(lo), probe.margin(hi)
        swing = None if None in (m_lo, m_hi, m0) else max(abs(m_lo - m0), abs(m_hi - m0))
        rows.append({"parameter": p.id, "low": p.low, "high": p.high, "range_source": p.range_source,
                     "margin_low": m_lo, "margin_high": m_hi, "swing": swing,
                     "recommended_low": lo.recommended, "recommended_high": hi.recommended,
                     "flips_within": any(o.recommended != probe.baseline for o in outcomes)})
    rows.sort(key=lambda r: (r["swing"] is None, -(r["swing"] or 0.0), r["parameter"]))
    return {"baseline": probe.baseline, "margin": m0, "rows": rows, "skipped": skipped,
            "infeasible_value": probe.infeasible_value}


@dataclass
class JointSample:
    """A Saltelli design over the uncertain parameters: matrices A and B, and A with column i from B."""

    params: list[Param]
    a: list[list[float]]  # samples x params
    b: list[list[float]]
    outcomes_a: list[Outcome] = field(default_factory=list)
    outcomes_b: list[Outcome] = field(default_factory=list)
    outcomes_ab: list[list[Outcome]] = field(default_factory=list)  # params x samples


def joint_sample(probe: Probe, params: list[Param], samples: int, seed: int, saltelli: bool = True) -> JointSample:
    if samples < 8:
        raise SensitivityError("a joint sample needs at least 8 samples")
    cols_a = [_draws(p, samples, seed, p.distribution) for p in params]
    cols_b = [_draws(p, samples, seed, p.distribution, stream_key="#saltelli-B") for p in params]
    a = [list(row) for row in zip(*cols_a, strict=True)]
    b = [list(row) for row in zip(*cols_b, strict=True)]
    js = JointSample(params, a, b)
    js.outcomes_a = [probe.evaluate(list(zip(params, row, strict=True))) for row in a]
    js.outcomes_b = [probe.evaluate(list(zip(params, row, strict=True))) for row in b]
    if saltelli:
        for i in range(len(params)):
            mixed = []
            for ra, rb in zip(a, b, strict=True):
                row = list(ra)
                row[i] = rb[i]
                mixed.append(probe.evaluate(list(zip(params, row, strict=True))))
            js.outcomes_ab.append(mixed)
    return js


def _sobol_trip(trip: list[tuple[float, float, float]]) -> tuple[float | None, float | None]:
    """First-order and total indices from (f(A), f(B), f(A_B^i)) triples.

    First-order: the Saltelli (2010) estimator on centered outputs, S_i = E[(f(B) - mu)(f(A_B^i) - f(A))] / V.
    Centering removes its sensitivity to a large mean (utilities sit far from 0), and an input with no effect
    gets exactly 0 because f(A_B^i) = f(A). Total: Jansen (1999), ST_i = E[(f(A) - f(A_B^i))^2] / 2V.
    """
    if len(trip) < 4:
        return None, None
    pooled = [t[0] for t in trip] + [t[1] for t in trip]
    mean = math.fsum(pooled) / len(pooled)
    var = math.fsum((v - mean) ** 2 for v in pooled) / (len(pooled) - 1)
    if var <= 0:
        return 0.0, 0.0
    n = len(trip)
    first = math.fsum((y - mean) * (z - x) for x, y, z in trip) / n / var
    total = math.fsum((x - z) ** 2 for x, _, z in trip) / (2 * n) / var
    return first, total


def global_sensitivity(probe: Probe, js: JointSample, bootstrap: int = BOOTSTRAP, seed: int = DEFAULT_SEED) -> dict:
    """Sobol indices of each alternative's value and of the decision margin, with bootstrap intervals for
    the margin's indices, and rank correlations."""
    targets: dict[str, tuple] = {}
    for alt in probe.alternatives:
        targets[alt] = ([o.value[alt] for o in js.outcomes_a], [o.value[alt] for o in js.outcomes_b],
                        [[o.value[alt] for o in col] for col in js.outcomes_ab])
    main = None
    if probe.baseline:
        main = "margin"
        targets["margin"] = ([probe.margin(o) for o in js.outcomes_a], [probe.margin(o) for o in js.outcomes_b],
                             [[probe.margin(o) for o in col] for col in js.outcomes_ab])
    rng = random.Random(seed)
    dropped: dict[str, int] = {}
    out = []
    for i, p in enumerate(js.params):
        xs = [row[i] for row in js.a]
        row: dict[str, Any] = {"parameter": p.id}
        for name, (ya, yb, yab) in targets.items():
            trip = [(x, y, z) for x, y, z in zip(ya, yb, yab[i], strict=True) if None not in (x, y, z)]
            dropped[name] = max(dropped.get(name, 0), len(ya) - len(trip))
            first, total = _sobol_trip(trip)
            pairs = [(x, y) for x, y in zip(xs, ya, strict=True) if y is not None]
            rho = spearman([x for x, _ in pairs], [y for _, y in pairs]) if pairs else None
            entry = {"first_order": first, "total": total, "spearman": rho}
            if name == main and bootstrap and len(trip) >= 8:
                firsts, totals = [], []
                for _ in range(bootstrap):
                    f, t = _sobol_trip([trip[rng.randrange(len(trip))] for _ in trip])
                    firsts.append(f)
                    totals.append(t)
                entry["first_order_interval"] = _interval(firsts)
                entry["total_interval"] = _interval(totals)
            row[name] = entry
        out.append(row)
    key = main or probe.alternatives[0]
    out.sort(key=lambda r: -(r[key]["total"] or 0.0))
    return {"samples": len(js.a), "evaluations": len(js.a) * (len(js.params) + 2), "targets": list(targets),
            "main_target": key, "dropped_draws": dropped, "bootstrap": bootstrap, "indices": out,
            "infeasible_value": probe.infeasible_value}


def value_of_information(probe: Probe, js: JointSample, bins: int | None = None, seed: int = DEFAULT_SEED,
                         bootstrap: int = BOOTSTRAP) -> dict:
    """EVPI, and EVPPI per parameter by cross-fitted quantile binning, from the joint sample (A and B draws).

    Plain binning (the best alternative's mean in each bin of the input) is biased upward: the maximum of
    noisy bin means exceeds the maximum of the true conditional means, so an input that does not matter
    still looks valuable. Cross-fitting removes that selection bias: the draws are split in two folds (by
    draw index, so independently of the input), each bin's choice is made on one fold and scored on the
    other, and the two directions are averaged. The estimate is then unbiased up to the binning's own
    smoothing, can be slightly negative for an input that does not matter, and carries a Poisson-bootstrap
    90% interval. An input is worth resolving when the interval's lower end is above zero.
    """
    alts = probe.alternatives
    pairs = list(zip(js.a, js.outcomes_a, strict=True)) + list(zip(js.b, js.outcomes_b, strict=True))
    rows = [(r, o) for r, o in pairs if all(o.value[a] is not None for a in alts)]
    n = len(rows)
    if n < 32:
        raise SensitivityError(f"value of information needs at least 32 draws with every alternative's value "
                               f"known; {n} of {len(pairs)} have one")
    values = [[o.value[a] for a in alts] for _, o in rows]
    fold = [k % 2 for k in range(n)]
    bins = bins or max(4, int(math.sqrt(n / 2)))
    na = len(alts)

    def argmax(sums: list[float]) -> int:
        return max(range(na), key=lambda j: sums[j])

    def evppi(order: list[int], w: list[float]) -> float | None:
        size = n / bins
        bin_of = [0] * n
        for b in range(bins):
            for k in order[round(b * size): round((b + 1) * size)]:
                bin_of[k] = b
        estimates = []
        for fa, fb in ((0, 1), (1, 0)):
            # choices on fold fa: overall and per bin (weighted sums; the argmax ignores the denominator)
            overall = [0.0] * na
            per_bin = [[0.0] * na for _ in range(bins)]
            seen = [0.0] * bins
            for k in range(n):
                if fold[k] == fa and w[k]:
                    for j in range(na):
                        overall[j] += w[k] * values[k][j]
                        per_bin[bin_of[k]][j] += w[k] * values[k][j]
                    seen[bin_of[k]] += w[k]
            choice_all = argmax(overall)
            choice = [argmax(per_bin[b]) if seen[b] else choice_all for b in range(bins)]
            # scored on fold fb
            wb = gain = base = 0.0
            for k in range(n):
                if fold[k] == fb and w[k]:
                    wb += w[k]
                    gain += w[k] * values[k][choice[bin_of[k]]]
                    base += w[k] * values[k][choice_all]
            if wb > 0:
                estimates.append((gain - base) / wb)
        return math.fsum(estimates) / len(estimates) if estimates else None

    ones = [1.0] * n
    rng = random.Random(seed)
    means = [math.fsum(v[j] for v in values) / n for j in range(na)]
    ev_now = max(means)
    choice = [alts[j] for j, v in enumerate(means) if v == ev_now]
    evpi = math.fsum(max(v) for v in values) / n - ev_now
    weight_sets = [[_poisson1(rng) for _ in range(n)] for _ in range(bootstrap)]

    def evpi_w(w: list[float]) -> float:
        total = math.fsum(w)
        mean_w = [math.fsum(wk * v[j] for wk, v in zip(w, values, strict=True)) / total for j in range(na)]
        return math.fsum(wk * max(v) for wk, v in zip(w, values, strict=True)) / total - max(mean_w)

    evpis = [evpi_w(w) for w in weight_sets if math.fsum(w) > 0]
    out = []
    for i, p in enumerate(js.params):
        order = sorted(range(n), key=lambda k: rows[k][0][i])
        e = evppi(order, ones)
        boots = [b for b in (evppi(order, w) for w in weight_sets) if b is not None]
        interval = _interval(boots)
        worth = bool(interval) and interval[0] > NOISE_EPS
        out.append({"parameter": p.id, "evppi": e, "interval": interval, "above_noise": worth,
                    "evidence": p.evidence})
    out.sort(key=lambda r: (-(r["evppi"] or 0.0), r["parameter"]))
    collect = [{"parameter": r["parameter"], "evidence": r["evidence"], "evppi": r["evppi"],
                "interval": r["interval"]} for r in out if r["above_noise"]]
    return {"samples": n, "bins": bins, "bootstrap": bootstrap, "estimator": "cross-fitted quantile binning",
            "expected_value_now": ev_now, "expected_value_choice": choice, "evpi": evpi,
            "evpi_interval": _interval(evpis), "parameters": out, "collect_next": collect,
            "infeasible_value": probe.infeasible_value,
            "note": "expected-value choice (infeasible alternatives valued at infeasible_value; no chance "
                    "constraint); EVPPI by cross-fitted quantile binning; an input is worth resolving when its "
                    "90% bootstrap interval excludes zero"}


def _poisson1(rng: random.Random) -> float:
    """A Poisson(1) draw (Knuth), for bootstrap weights that keep each draw's position."""
    limit, k, p = math.exp(-1.0), 0, 1.0
    while True:
        p *= rng.random()
        if p <= limit:
            return float(k)
        k += 1


# --- entry points -------------------------------------------------------------------------------------

def load(path: str | Path, episode: str | None = None) -> tuple[m.DecisionState, Path]:
    """The state and base directory for a path (and episode), checked like `pdo run`."""
    from pdo.evaluate import is_program

    if is_program(path):
        from pdo.episodes import Program

        program = Program.load(path)
        return program.materialize(episode or program.baseline_episode), program.base_dir
    if episode is not None:
        raise SensitivityError(f"{path} is not a program directory; --episode needs one")
    from pdo.evaluate import evaluate

    result = evaluate(path)  # validates the state and stamps it like `pdo run`
    return result.graph.state, result.graph.base_dir


def analyze(state: m.DecisionState, base_dir: Path | str, param: str | None = None, *, span: float = DEFAULT_SPAN,
            points: int = DEFAULT_POINTS, samples: int | None = None, seed: int | None = None,
            low: float | None = None, high: float | None = None,
            infeasible_value: float = INFEASIBLE_VALUE) -> dict:
    """One parameter (response curve and distribution), or all of them (tornado, global, value of information)."""
    if param is None and (low is not None or high is not None):
        raise SensitivityError("low and high set one input's range; give the input as param")
    if samples is not None and samples < 1:
        raise SensitivityError("samples must be at least 1")
    rule = state.decision_rule
    seed = seed if seed is not None else (rule.seed if rule and rule.seed is not None else DEFAULT_SEED)
    probe = Probe(state, base_dir, infeasible_value)
    settings = {"seed": seed, "samples": samples, "span": span, "points": points, "infeasible_value": infeasible_value}
    out: dict[str, Any] = {"baseline": probe.baseline, "seed": seed, "infeasible_value": infeasible_value,
                           "settings": settings, "notes": []}
    if probe.rule_kind == "expected_mavt":
        out["notes"].append("the declared rule is expected_mavt, so `pdo uncertainty` decides; this analysis is "
                            f"framed on the point-value recommendation ({', '.join(probe.baseline) or 'none'})")
    if len(probe.baseline) > 1:
        out["notes"].append(f"the baseline is a tie ({', '.join(probe.baseline)}): the margin compares the tied set "
                            "with the other alternatives")
    if param is not None:
        p = find_parameter(state, param, span, low, high)
        if not probe.depends_on(p):
            raise SensitivityError(f"{p.id}: the decision does not depend on {p.target}")
        mark = probe.mark()
        out["response"] = response_curve(probe, p, points)
        out["response"]["cost"] = probe.cost(mark)
        mark = probe.mark()
        out["distribution"] = distribution_response(probe, p, samples or DEFAULT_SAMPLES, seed)
        out["distribution"]["cost"] = probe.cost(mark)
    else:
        params = parameters(state, span, include_empty=True)
        mark = probe.mark()
        out["tornado"] = tornado(probe, params)
        out["tornado"]["cost"] = probe.cost(mark)
        uncertain = [p for p in params if p.distribution is not None and probe.depends_on(p) and p.high > p.low]
        out["global"] = out["value_of_information"] = None
        if uncertain:
            mark = probe.mark()
            try:
                js = joint_sample(probe, uncertain, samples or DEFAULT_GLOBAL_SAMPLES, seed)
                out["global"] = global_sensitivity(probe, js, seed=seed)
                out["global"]["cost"] = probe.cost(mark)
                try:
                    out["value_of_information"] = value_of_information(probe, js, seed=seed)
                except SensitivityError as exc:
                    out["notes"].append(f"value of information skipped: {exc}")
            except SensitivityError as exc:
                out["notes"].append(f"global sensitivity skipped: {exc}")
        else:
            out["notes"].append("no input declares a distribution, so global indices and value of information "
                                "are skipped")
    out["cost"] = probe.cost()
    return out


def analyze_path(path: str | Path, episode: str | None = None, param: str | None = None, **kwargs) -> dict:
    state, base_dir = load(path, episode)
    return {"path": str(path), "episode": episode, **analyze(state, base_dir, param, **kwargs)}


# --- rendering ----------------------------------------------------------------------------------------

def _n(x: Any, digits: int = 4) -> str:
    if x is None:
        return "?"
    if isinstance(x, float):
        return f"{x:.{digits}g}"
    return str(x)


def _iv(interval: list[float] | None, digits: int = 3) -> str:
    return f"[{_n(interval[0], digits)}, {_n(interval[1], digits)}]" if interval else ""


def render(result: dict) -> str:
    lines = [f"# Sensitivity of {', '.join(result['baseline']) or 'no recommendation'}", ""]
    lines.append(f"Infeasible choices are valued at {result['infeasible_value']:g}. Each probe re-runs only what "
                 "the varied inputs reach:")
    for name in ("response", "distribution", "tornado", "global"):
        part = result.get(name)
        if part and part.get("cost"):
            c = part["cost"]
            lines.append(f"- {name}: {c['evaluations']} probes, {c['mean_executed']} of {c['computed_nodes']} "
                         f"computed nodes each ({_n((c['executed_fraction'] or 0) * 100, 3)}%)")
    for note in result.get("notes", []):
        lines.append(f"- note: {note}")
    if "response" in result:
        resp, dist = result["response"], result["distribution"]
        p = resp["parameter"]
        lines += ["", f"## Response to {p['parameter']} over [{_n(p['low'])}, {_n(p['high'])}] ({p['range_source']})",
                  ""]
        for c in resp["changes"]:
            lines.append(f"- recommendation changes at {_n(c['at'], 8)}: {', '.join(c['from']) or 'none'} -> "
                         f"{', '.join(c['to']) or 'none'}")
        if not resp["changes"]:
            lines.append("- the recommendation holds over the whole range")
        lines += ["", "| alternative | slope at base | elasticity | deviation from linear |", "|---|---|---|---|"]
        for alt, s in resp["shape"].items():
            rel = s["relative_deviation"]
            lines.append(f"| {alt} | {_n(s['slope'])} | {_n(s['elasticity'])} | "
                         f"{_n(s['chord_deviation'])} ({_n(rel * 100 if rel is not None else None, 3)}% of rise) |")
        lines += ["", f"## Distribution of {p['parameter']}: {dist['distribution']}, {dist['samples']} draws, "
                  f"seed {dist['seed']}", "",
                  f"P(decision changes) = {_n(dist['p_decision_changes'])}; "
                  f"P(no recommendation) = {_n(dist['p_no_recommendation'])}", "",
                  "| alternative | E[U] | sd | p5 | p95 | P(feasible) | P(win) | E[regret] | unknown draws |",
                  "|---|---|---|---|---|---|---|---|---|"]
        for alt, a in dist["alternatives"].items():
            u = a["utility"]
            lines.append(f"| {alt} | {_n(u.get('mean'))} | {_n(u.get('sd'))} | {_n(u.get('p5'))} | {_n(u.get('p95'))} "
                         f"| {_n(a['p_feasible'])} | {_n(a['p_win'])} | {_n(a['expected_regret'])} | "
                         f"{a['unknown_draws']} |")
    if "tornado" in result:
        t = result["tornado"]
        lines += ["", f"## Tornado: decision margin {_n(t['margin'])} (one input at a time)", "",
                  "| input | range | margin at low | margin at high | swing | changes within range |",
                  "|---|---|---|---|---|---|"]
        for r in t["rows"][:15]:
            lines.append(f"| {r['parameter']} | [{_n(r['low'])}, {_n(r['high'])}] | {_n(r['margin_low'])} | "
                         f"{_n(r['margin_high'])} | {_n(r['swing'])} | {'yes' if r['flips_within'] else ''} |")
        if len(t["rows"]) > 15:
            lines.append(f"| ... {len(t['rows']) - 15} more | | | | | |")
        if t.get("skipped"):
            lines.append(f"\nSkipped {len(t['skipped'])} inputs: " + "; ".join(
                f"{s['parameter']} ({s['reason']})" for s in t["skipped"][:6]) + ("; ..." if len(t["skipped"]) > 6
                                                                                  else ""))
    g = result.get("global")
    if g:
        target = g["main_target"]
        lines += ["", f"## Global sensitivity of the {target} ({g['samples']} samples, {g['evaluations']} probes; "
                  f"90% bootstrap intervals)", "",
                  "| input | first-order | total | Spearman |", "|---|---|---|---|"]
        for r in g["indices"]:
            s = r[target]
            lines.append(f"| {r['parameter']} | {_n(s['first_order'], 3)} {_iv(s.get('first_order_interval'))} | "
                         f"{_n(s['total'], 3)} {_iv(s.get('total_interval'))} | {_n(s['spearman'], 3)} |")
        if g["dropped_draws"].get(target):
            lines.append(f"\n{g['dropped_draws'][target]} draws had an unknown {target} and were left out.")
    voi = result.get("value_of_information")
    if voi:
        lines += ["", f"## Value of information (utility points; {voi['samples']} draws, {voi['bins']} bins)", "",
                  f"Expected-value choice {', '.join(voi['expected_value_choice'])} at "
                  f"{_n(voi['expected_value_now'])}; EVPI {_n(voi['evpi'])} {_iv(voi['evpi_interval'])}; "
                  f"EVPPI by {voi['estimator']}", "",
                  "| input | EVPPI | 90% interval | worth resolving | evidence |", "|---|---|---|---|---|"]
        for r in voi["parameters"]:
            lines.append(f"| {r['parameter']} | {_n(r['evppi'])} | {_iv(r['interval'])} | "
                         f"{'yes' if r['above_noise'] else ''} | {r['evidence'] or ''} |")
    return "\n".join(lines) + "\n"


__all__ = ["Param", "Probe", "SensitivityError", "analyze", "analyze_path", "distribution_response",
           "find_parameter", "global_sensitivity", "joint_sample", "parameters", "render", "response_curve",
           "spearman", "tornado", "value_of_information"]
