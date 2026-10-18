"""What flips the decision: flip points of numeric inputs, weight flips, and scenario robustness.

A flip point is a value of one input at which the recommendation changes, every other input held fixed
(the research brief's "what would change the preferred alternative"). The kernel finds them by re-running
the decision, so every number reported comes from node evaluations:

1. Scan. Each numeric source upstream of the decision (an accepted observation's value, an assumption's
   value, a requirement's threshold) is scanned on a grid between its bounds: an assumption's plausible
   range when set, else +/- `span` of the value. Each grid point is a cutoff run of an in-memory copy
   against the base run, so only the changed value's descendants execute.
2. Bracket. Adjacent points with different outcomes (the recommended set, or the feasible set becoming
   empty) bracket a boundary; the side nearer the baseline is "near". After each boundary the search
   continues toward the far point, so one cell can yield several (B -> C -> A).
3. Refine. The quantity that crosses zero at the boundary is the utility margin U_old - U_new when every
   alternative's feasibility is the same on both sides, else the one requirement check g whose status
   changed. If it is affine on the bracket (checked at two interior points, on a bracket not much narrower
   than its grid cell, since anything smooth looks affine on a tiny one), a two-point linear solve gives
   the boundary exactly (affine_exact). Otherwise, or when re-running either side of that root does not
   confirm it, the bracket is halved and the refinement retried, ending in plain bisection.
4. Verify. Re-running at p* -/+ eps must give the near and far recommendations (`verified`).

Weight flips have a closed form. Shifting delta of weight from criterion l to k (others fixed) moves each
utility linearly, U_a(delta) = U_a + delta (v_k(a) - v_l(a)), so the winner changes where the upper
envelope of those lines changes hands, for w_k in [0, w_k + w_l].

A scenario sweep evaluates each declared Scenario. The recommendation is robust over the set when it is
feasible and uniquely best in every scenario; that supports a claim about the tested scenarios only.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pdo import model as m
from pdo.graph import Graph, compile_graph
from pdo.load import index, items
from pdo.run import RunResult, run

AFFINE_RTOL = 1e-10  # |q(x) - linear interpolation of q at x| <= AFFINE_RTOL * max |q|, at two interior points
# Any smooth quantity looks affine on a small enough bracket, so the affine test is only trusted on a
# bracket at least this fraction of the grid cell it started from.
AFFINE_MIN_WIDTH = 2.0 ** -10
MAX_REFINE = 80
MAX_TRANSITIONS = 8  # boundaries followed inside one grid cell
VERIFY_STEP = 1e-6  # eps, relative to the scanned range (absolute for weights)


@dataclass(frozen=True)
class Parameter:
    """One numeric input and the range it is scanned over."""

    target: str
    slot: str
    baseline: float
    low: float
    high: float

    @property
    def id(self) -> str:
        return f"{self.target}.{self.slot}"


@dataclass(frozen=True)
class Point:
    """The decision at one value of a scanned input."""

    x: float
    recommended: tuple[str, ...]
    feasible: tuple[str, ...]
    utilities: dict[str, float | None]
    feasibility: dict[str, str]
    checks: dict[str, tuple[str, float | None]]  # mandatory check node -> (status, g)

    @property
    def outcome(self) -> tuple[tuple[str, ...], bool]:
        return self.recommended, bool(self.feasible)


def point(x: float, result: RunResult) -> Point:
    d = result.decision
    alts = sorted(d["utilities"])
    checks = {}
    for alt in alts:
        for nid in result.graph.nodes[f"F/{alt}"].inputs:
            value = result.value(nid)
            checks[nid] = (value["status"], value["g"])
    return Point(x, tuple(d["recommended"]), tuple(d["feasible"]), dict(d["utilities"]),
                 {a: result.value(f"F/{a}")["status"] for a in alts}, checks)


def _base(state: m.DecisionState, base_dir: Path | str, base: RunResult | None) -> RunResult:
    return base if base is not None else run(compile_graph(state, base_dir))


def _base_dir(base_dir: Path | str | None, base: RunResult | None) -> Path:
    if base_dir is not None:
        return Path(base_dir)
    return base.graph.base_dir if base is not None else Path(".")


# --- numeric inputs -----------------------------------------------------------------------------

def ancestors(graph: Graph, nid: str) -> set[str]:
    seen: set[str] = set()
    stack = [nid]
    while stack:
        for parent in graph.nodes[stack.pop()].inputs:
            if parent not in seen:
                seen.add(parent)
                stack.append(parent)
    return seen


def _bounded(target: str, slot: str, value: float, low: float | None, high: float | None,
             span: float) -> Parameter:
    half = span * abs(value) if value else span
    low = value - half if low is None else low
    high = value + half if high is None else high
    return Parameter(target, slot, float(value), float(min(low, value)), float(max(high, value)))


def numeric_parameters(state: m.DecisionState, graph: Graph, span: float = 0.5) -> list[Parameter]:
    """Every numeric source upstream of the decision, with the range to scan."""
    upstream = ancestors(graph, "decision")
    out = []
    for o in items(state.observations):
        # A provisional or rejected observation's value cannot move y, so it cannot flip anything.
        if o.id in upstream and o.value is not None and (o.applicability or "accepted") == "accepted":
            out.append(_bounded(o.id, "value", o.value, None, None, span))
    for a in items(state.assumptions):
        if a.id in upstream:
            out.append(_bounded(a.id, "value", a.value, a.plausible_low, a.plausible_high, span))
    for r in items(state.requirements):
        if r.id in upstream:
            out.append(_bounded(r.id, "threshold_value", r.threshold_value, None, None, span))
    return out


def _select(params: list[Parameter], wanted: Iterable[str] | None) -> list[Parameter]:
    if wanted is None:
        return params
    wanted = list(wanted)
    known = {p.id for p in params} | {p.target for p in params}
    unknown = [w for w in wanted if w not in known]
    if unknown:
        raise KeyError(f"not a numeric input upstream of the decision: {', '.join(unknown)}; "
                       f"choose from {', '.join(p.id for p in params)}")
    return [p for p in params if p.id in wanted or p.target in wanted]


# --- scanning one input -------------------------------------------------------------------------

class _Probe:
    """The decision with one input set to x: an in-memory copy, run in cutoff mode against the base."""

    def __init__(self, state: m.DecisionState, base_dir: Path, base: RunResult, target: str, slot: str):
        self.work = state.model_copy(deep=True)
        self.obj = index(self.work)[target]
        self.slot = slot
        self.base_dir = base_dir
        self.base = base
        self.cache: dict[float, Point] = {}

    def __call__(self, x: float) -> Point:
        if x not in self.cache:
            setattr(self.obj, self.slot, x)
            self.cache[x] = point(x, run(compile_graph(self.work, self.base_dir), "cutoff", prev=self.base))
        return self.cache[x]


def _crossing(near: Point, far: Point) -> tuple[str, Callable[[Point], float | None]] | None:
    """The quantity whose zero separates near from far, if the bracket isolates one mechanism."""
    if near.feasibility == far.feasibility:
        old = [a for a in near.recommended if a not in far.recommended] or list(near.recommended)
        new = [a for a in far.recommended if a not in near.recommended] or list(far.recommended)
        if not old or not new or old[0] == new[0]:
            return None
        a, b = old[0], new[0]

        def margin(p: Point) -> float | None:
            ua, ub = p.utilities.get(a), p.utilities.get(b)
            return None if ua is None or ub is None else ua - ub

        return f"U/{a} - U/{b}", margin
    changed = [nid for nid, (status, _) in near.checks.items()
               if nid in far.checks and far.checks[nid][0] != status]
    if len(changed) != 1:
        return None
    (check,) = changed
    return check, lambda p: p.checks.get(check, (None, None))[1]


def _affine_root(q: Callable[[Point], float | None], near: Point, far: Point, interior: list[Point]) -> float | None:
    """The zero of q by a two-point linear solve, if q is affine at every interior point."""
    qn, qf = q(near), q(far)
    if qn is None or qf is None or qn == qf:
        return None
    scale = max(abs(qn), abs(qf))
    for p in interior:
        qp = q(p)
        if qp is None:
            return None
        linear = qn + (qf - qn) * (p.x - near.x) / (far.x - near.x)
        if abs(qp - linear) > AFFINE_RTOL * max(scale, abs(qp)) + 1e-15:
            return None
    root = near.x - qn * (far.x - near.x) / (qf - qn)
    return root if min(near.x, far.x) <= root <= max(near.x, far.x) else None


def _boundary_kind(near: Point, far: Point) -> str:
    if not far.feasible:
        return "empty_set"
    if not far.recommended:
        return "unresolved"
    return "feasibility" if near.feasibility != far.feasibility else "preference"


def _sides(near: Point, far: Point) -> dict[str, Any]:
    return {"previous_recommendation": list(near.recommended), "new_recommendation": list(far.recommended),
            "boundary_kind": _boundary_kind(near, far)}


def _refine(probe: _Probe, near: Point, far: Point, eps: float) -> dict[str, Any]:
    """The first boundary from near toward far. Near keeps its outcome; far may become a third outcome."""
    sign = 1.0 if far.x > near.x else -1.0
    cell = abs(far.x - near.x)
    for _ in range(MAX_REFINE):
        mid = probe((near.x + far.x) / 2)
        crossing = _crossing(near, far)
        if crossing is not None and abs(far.x - near.x) >= AFFINE_MIN_WIDTH * cell:
            name, q = crossing
            root = _affine_root(q, near, far, [mid, probe((3 * near.x + far.x) / 4)])
            if root is not None:
                before, after = probe(root - sign * eps), probe(root + sign * eps)
                if before.outcome == near.outcome and after.outcome == far.outcome:
                    return {"flip_number": root, "method": "affine_exact", "crossing": name, "verified": True,
                            **_sides(before, after)}
        if abs(far.x - near.x) <= eps / 100:
            break
        if mid.outcome == near.outcome:
            near = mid
        else:
            far = mid
    root = (near.x + far.x) / 2
    before, after = probe(root - sign * eps), probe(root + sign * eps)
    crossing = _crossing(near, far)
    return {"flip_number": root, "method": "bisection", "crossing": crossing[0] if crossing else None,
            "verified": before.outcome == near.outcome and after.outcome == far.outcome, **_sides(near, far)}


def _grid(p: Parameter, grid: int, eps: float) -> list[float]:
    xs = [p.low + (p.high - p.low) * i / (grid - 1) for i in range(grid)] if grid > 1 else []
    return sorted({x for x in xs if abs(x - p.baseline) > eps / 10} | {p.baseline})


def _merge(flips: list[dict], eps: float) -> list[dict]:
    """Join a flip into and out of a tie that a grid point happened to land on."""
    out: list[dict] = []
    for flip in flips:
        last = out[-1] if out else None
        if last and last["direction"] == flip["direction"] \
                and abs(last["flip_number"] - flip["flip_number"]) <= 10 * eps:
            last["new_recommendation"] = flip["new_recommendation"]
            last["boundary_kind"] = flip["boundary_kind"]
            last["verified"] = last["verified"] and flip["verified"]
            continue
        out.append(flip)
    return out


def scan_parameter(state: m.DecisionState, base_dir: Path, base: RunResult, p: Parameter,
                   grid: int = 17) -> list[dict]:
    """FlipPoint dicts for one input, nearest to the baseline first in each direction."""
    eps = VERIFY_STEP * (p.high - p.low) or 1e-9 * max(1.0, abs(p.baseline))
    probe = _Probe(state, base_dir, base, p.target, p.slot)
    probe.cache[p.baseline] = point(p.baseline, base)
    points = [probe(x) for x in _grid(p, grid, eps)]
    found = []
    for lo, hi in zip(points, points[1:], strict=False):
        near, far = (lo, hi) if lo.x >= p.baseline else (hi, lo)
        sign = 1.0 if far.x > near.x else -1.0
        for _ in range(MAX_TRANSITIONS):  # a cell can hold more than one boundary (B -> C -> A)
            if near.outcome == far.outcome:
                break
            refined = _refine(probe, near, far, eps)
            flip = {"parameter": p.id, "target": p.target, "slot": p.slot, "baseline_number": p.baseline,
                    **refined, "direction": "above" if sign > 0 else "below"}
            found.append({k: v for k, v in flip.items() if v is not None})
            near = probe(refined["flip_number"] + sign * eps)
            if not refined["verified"] or (far.x - near.x) * sign <= 0:
                break
    above = sorted((f for f in found if f["direction"] == "above"), key=lambda f: f["flip_number"])
    below = sorted((f for f in found if f["direction"] == "below"), key=lambda f: -f["flip_number"])
    return _merge(above, eps) + _merge(below, eps)


def scan_parameters(state: m.DecisionState, base_dir: Path | str | None = None, params: Iterable[str] | None = None,
                    span: float = 0.5, grid: int = 17, base: RunResult | None = None) -> tuple[list[dict], list[dict]]:
    """(ParameterScan dicts, FlipPoint dicts) for every selected numeric input."""
    base_dir = _base_dir(base_dir, base)
    base = _base(state, base_dir, base)
    scans, flips = [], []
    for p in _select(numeric_parameters(state, base.graph, span), params):
        found = scan_parameter(state, base_dir, base, p, grid)
        scans.append({"parameter": p.id, "target": p.target, "slot": p.slot, "baseline_number": p.baseline,
                      "scan_low": p.low, "scan_high": p.high, "grid_points": grid, "flip_count": len(found)})
        flips.extend(found)
    return scans, flips


def flip_parameters(state: m.DecisionState, base_dir: Path | str | None = None, params: Iterable[str] | None = None,
                    span: float = 0.5, grid: int = 17, base: RunResult | None = None) -> list[dict]:
    """FlipPoint dicts for every numeric source upstream of the decision (or the ones in `params`)."""
    return scan_parameters(state, base_dir, params, span, grid, base)[1]


# --- weights ------------------------------------------------------------------------------------

def _envelope(utilities: dict[str, float], slope: dict[str, float], start: list[str],
              limit: float) -> list[tuple[float, str, str]]:
    """(s, before, after) where the best line U_a + s * slope_a changes hands, for 0 <= s <= limit."""
    if not start:
        return []
    current = max(start, key=lambda a: (slope[a], a))
    s0, out = 0.0, []
    while True:
        best = None
        for b in utilities:
            gain = slope[b] - slope[current]
            if b == current or gain <= 0:
                continue
            s = (utilities[current] - utilities[b]) / gain
            if s0 - 1e-12 <= s <= limit + 1e-12 and (best is None or (s, -gain) < (best[0], -best[2])):
                best = (s, b, gain)
        if best is None:
            return out
        out.append((best[0], current, best[1]))
        s0, current = best[0], best[1]


def _with_weights(state: m.DecisionState, weights: dict[str, float]) -> m.DecisionState:
    work = state.model_copy(deep=True)
    objects = index(work)
    for cid, w in weights.items():
        objects[cid].weight = w
    return work


def weight_flips(state: m.DecisionState, base_dir: Path | str | None = None, base: RunResult | None = None,
                 criteria: Iterable[str] | None = None, verify: bool = True) -> list[dict]:
    """FlipPoint dicts (closed_form) for shifting weight between each pair of criteria.

    The lines come from the base run's value scores and utilities; each flip is then checked by
    re-running with the shifted weights just either side of it. Pass the base run (or base_dir) so
    evaluators resolve.
    """
    base_dir = _base_dir(base_dir, base)
    base = _base(state, base_dir, base)
    d = base.decision
    ranked = [a for a in d["feasible"] if d["utilities"][a] is not None]
    utilities = {a: d["utilities"][a] for a in ranked}
    crits = sorted(state.criteria, key=lambda c: c.id)
    wanted = set(criteria) if criteria is not None else None
    v = {(c.id, a): base.value(f"v/{c.id}/{a}")["v"] for c in crits for a in ranked}
    flips = []
    for i, k in enumerate(crits):
        for other in crits[i + 1:]:
            if wanted is not None and k.id not in wanted and other.id not in wanted:
                continue
            total = k.weight + other.weight
            for sign, limit in ((1.0, other.weight), (-1.0, k.weight)):
                slope = {a: sign * (v[(k.id, a)] - v[(other.id, a)]) for a in ranked}
                for s, before, after in _envelope(utilities, slope, d["recommended"], limit):
                    w = k.weight + sign * s
                    flip = {"parameter": f"{k.id}.weight", "target": k.id, "slot": "weight", "counterpart": other.id,
                            "baseline_number": k.weight, "flip_number": w, "method": "closed_form",
                            "boundary_kind": "preference", "previous_recommendation": [before],
                            "new_recommendation": [after], "direction": "above" if sign > 0 else "below",
                            "crossing": f"U/{before} - U/{after}"}
                    if verify:
                        flip["verified"] = _verify_weight(state, base, k.id, other.id, w, total, sign,
                                                          [before], [after])
                    flips.append(flip)
    return flips


def _verify_weight(state, base, k: str, other: str, w: float, total: float, sign: float,
                   before: list[str], after: list[str]) -> bool:
    outcomes = []
    for side in (-1.0, 1.0):
        wk = min(total, max(0.0, w + side * sign * VERIFY_STEP))
        work = _with_weights(state, {k: wk, other: total - wk})
        outcomes.append(run(compile_graph(work, base.graph.base_dir), "cutoff", prev=base).decision["recommended"])
    return outcomes == [before, after]


# --- scenarios ----------------------------------------------------------------------------------

def apply_assignments(state: m.DecisionState, assignments: Iterable[m.SlotAssignment],
                      where: str = "scenario") -> m.DecisionState:
    """A copy of `state` with each assignment applied (validated on assignment)."""
    work = state.model_copy(deep=True)
    objects = index(work)
    for a in assignments:
        if a.target not in objects:
            raise ValueError(f"{where}: no object {a.target}")
        try:
            setattr(objects[a.target], a.slot, a.new_value)
        except ValueError as exc:
            raise ValueError(f"{where}: cannot set {a.target}.{a.slot} to {a.new_value!r}: {exc}") from exc
    return work


def scenario_sweep(state: m.DecisionState, base_dir: Path | str | None = None,
                   base: RunResult | None = None) -> list[dict]:
    """ScenarioOutcome dicts, one per declared scenario, in declaration order."""
    base_dir = _base_dir(base_dir, base)
    base = _base(state, base_dir, base)
    baseline = base.decision["recommended"]
    out = []
    for sc in items(state.scenarios):
        work = apply_assignments(state, items(sc.assignments), sc.id)
        result = run(compile_graph(work, base_dir), "cutoff", prev=base)
        d = result.decision
        outcomes = []
        for alt in sorted(d["utilities"]):
            u = result.value(f"U/{alt}")
            outcomes.append({"alternative": alt, "utility": u["U"], "utility_lower": u["lower"],
                             "utility_upper": u["upper"], "feasibility": result.value(f"F/{alt}")["status"]})
        out.append({"scenario": sc.id, "recommended": d["recommended"], "feasible": d["feasible"],
                    "infeasible": d["infeasible"], "unresolved": d["unresolved"],
                    "robust": len(baseline) == 1 and d["recommended"] == baseline, "outcomes": outcomes})
    return out


def is_robust(outcomes: list[dict]) -> bool | None:
    """Robust over the declared set: the baseline winner is feasible and uniquely best in every scenario."""
    return all(o["robust"] for o in outcomes) if outcomes else None
