"""What the benchmark measures: outcome classes, label agreement, and the cost and correctness of a strategy.

Labels compare a full evaluation of the child with a full evaluation of the parent (docs/semantics.md,
"Benchmark outcome classes"). A decision number is one of the quantities that section lists: a measure
value y, a check's g and status, a value score v, a utility U and its bounds, a feasibility status. A
node of one of those kinds that exists in only one of the two graphs (a new check, a removed
alternative) counts as changed.

Node counts are over computed nodes: every node except sources and the clock, which hold the state
rather than compute from it, so savings are not inflated by inputs that are copied either way.

    changed       computed nodes whose value differs between full(parent) and full(child), or that are new
    scope         the computed nodes a strategy may execute (its impact set)
    executed      the computed nodes it did execute (cutoff executes fewer than its scope)
    equivalent    every node's value digest equals the full recomputation's
    sound         changed ⊆ scope: no node whose value changed was reused
    savings       1 - executed / computed, and the same weighted by node cost_units
    unneeded      |executed \\ changed| / |executed|, recomputation that reproduced the old value
    overapprox    |scope| / |changed|

A strategy detects a winner change when its recommended set differs from the parent's, and a readiness
violation when its readiness is not ready; precision and recall are against the hand labels.
"""

from __future__ import annotations

import statistics
from collections.abc import Iterable
from dataclasses import dataclass

from pdo.bench.strategies import StrategyRun
from pdo.graph import Graph
from pdo.run import RunResult, equivalent

LABEL_FIELDS = ("outcome", "recommended", "readiness", "winner_changed", "readiness_violation",
                "approval_invalidated")
OUTCOMES = ("empty_feasible_set", "changed_winner", "recompute_same_winner", "readiness_only", "no_material_change")
DECISION_NUMBERS = {"measure": ("y",), "check": ("g", "status"), "value": ("v",), "utility": ("U", "lower", "upper"),
                    "feasible": ("status",)}


@dataclass(frozen=True)
class Rate:
    """A fraction with its numerator and denominator, so an undefined rate (0/0) stays visible."""

    num: float
    den: float

    @property
    def value(self) -> float | None:
        return self.num / self.den if self.den else None

    @property
    def pct(self) -> float | None:
        return None if self.value is None else 100.0 * self.value


# --- labels -------------------------------------------------------------------------------------

def computed_nodes(graph: Graph) -> frozenset[str]:
    return frozenset(nid for nid, node in graph.nodes.items() if node.fn != "source")


def changed_nodes(parent: RunResult, child: RunResult) -> frozenset[str]:
    """Computed nodes of the child whose value differs from the parent's, or that the parent lacks."""
    out = set()
    for nid in computed_nodes(child.graph):
        old = parent.results.get(nid)
        if old is None or old.vdig != child.results[nid].vdig:
            out.add(nid)
    return frozenset(out)


def decision_numbers(result: RunResult) -> dict[str, tuple]:
    out = {}
    for nid, node in result.graph.nodes.items():
        keys = DECISION_NUMBERS.get(node.kind)
        if keys:
            value = result.value(nid)
            out[nid] = tuple(value.get(k) for k in keys)
    return out


def changed_decision_numbers(parent: RunResult, child: RunResult) -> list[str]:
    old, new = decision_numbers(parent), decision_numbers(child)
    return sorted(nid for nid in set(old) | set(new) if old.get(nid) != new.get(nid))


def outcome_class(parent: RunResult, child: RunResult) -> str:
    """The first benchmark outcome class that applies to a child against its parent (both full runs)."""
    if not child.decision["feasible"]:
        return "empty_feasible_set"
    if child.decision["recommended"] != parent.decision["recommended"]:
        return "changed_winner"
    if changed_decision_numbers(parent, child):
        return "recompute_same_winner"
    if parent.readiness["state"] != child.readiness["state"] \
            or parent.actionable["actionable"] != child.actionable["actionable"]:
        return "readiness_only"
    return "no_material_change"


def matching_approvals(result: RunResult) -> set[str]:
    """Approvals whose stamped state digest equals the run's state digest."""
    current = result.value("state_digest")["digest"]
    return {aid for aid in result.graph.nodes["actionable"].local["approvals"]
            if result.value(aid).get("state_digest") == current}


def approval_invalidated(parent: RunResult, child: RunResult) -> bool:
    """Some approval that matched the parent no longer matches the child (changed digest, or removed)."""
    return bool(matching_approvals(parent) - matching_approvals(child))


def labels(parent: RunResult, child: RunResult) -> dict:
    recommended = sorted(child.decision["recommended"])
    return {"outcome": outcome_class(parent, child), "recommended": recommended,
            "readiness": child.readiness["state"],
            "winner_changed": recommended != sorted(parent.decision["recommended"]),
            "readiness_violation": child.readiness["state"] != "ready",
            "approval_invalidated": approval_invalidated(parent, child)}


def agreement(expect: dict, computed: dict | None) -> dict[str, bool]:
    """Per label field: does the computed value equal the hand label?"""
    if computed is None:
        return {f: False for f in LABEL_FIELDS}
    out = {}
    for f in LABEL_FIELDS:
        want, got = expect.get(f), computed.get(f)
        out[f] = sorted(want or []) == got if f == "recommended" else want == got
    return out


# --- one strategy on one case -------------------------------------------------------------------

@dataclass(frozen=True)
class StrategyMetrics:
    strategy: str
    error: str | None
    equivalent: bool
    mismatched: tuple[str, ...]
    sound: bool
    missed: tuple[str, ...]  # changed nodes outside the scope
    computed: int
    total: int
    changed: int
    scope: int
    executed: int
    unneeded: int
    cost_computed: float
    cost_executed: float
    recommended: tuple[str, ...] | None
    readiness: str | None
    actionable: bool | None
    detects_winner_change: bool
    detects_readiness_violation: bool
    duration_ms: float
    dropped_edges: int = 0

    @property
    def savings(self) -> Rate:
        return Rate(self.computed - self.executed, self.computed)

    @property
    def savings_cost(self) -> Rate:
        return Rate(self.cost_computed - self.cost_executed, self.cost_computed)

    @property
    def unneeded_rate(self) -> Rate:
        return Rate(self.unneeded, self.executed)

    @property
    def overapprox(self) -> Rate:
        return Rate(self.scope, self.changed)

    @property
    def scope_frac(self) -> Rate:
        return Rate(self.scope, self.computed)


def strategy_metrics(srun: StrategyRun, parent: RunResult, full: RunResult, changed: frozenset[str]) -> StrategyMetrics:
    graph = full.graph
    computed = computed_nodes(graph)
    scope = srun.scope & computed
    missed = tuple(sorted(changed - srun.scope))
    cost_computed = sum(graph.nodes[n].cost_units for n in computed)
    parent_rec = sorted(parent.decision["recommended"])
    result = srun.result
    if result is None:
        return StrategyMetrics(srun.name, srun.error, False, (), not missed, missed, len(computed), len(graph.nodes),
                               len(changed), len(scope), 0, 0, cost_computed, 0.0, None, None, None, False, False,
                               srun.duration_ms, srun.dropped_edges)
    check = equivalent(result, full)
    executed = set(result.executed)
    recommended = sorted(result.decision["recommended"])
    readiness = result.readiness["state"]
    return StrategyMetrics(
        srun.name, None, bool(check), tuple(check.mismatched + check.missing), not missed, missed, len(computed),
        len(graph.nodes), len(changed), len(scope), len(executed), len(executed - changed), cost_computed,
        sum(graph.nodes[n].cost_units for n in executed), tuple(recommended), readiness,
        bool(result.actionable["actionable"]), recommended != parent_rec, readiness != "ready", srun.duration_ms,
        srun.dropped_edges)


def decision_matches(metrics: StrategyMetrics, full: RunResult) -> bool:
    """The strategy reports the reference's recommendation, readiness and actionable (whatever else differs)."""
    return metrics.error is None and list(metrics.recommended or ()) == sorted(full.decision["recommended"]) \
        and metrics.readiness == full.readiness["state"] and metrics.actionable == bool(full.actionable["actionable"])


# --- aggregates ---------------------------------------------------------------------------------

def precision_recall(pairs: Iterable[tuple[bool, bool]]) -> tuple[Rate, Rate]:
    """(precision, recall) over (truth, predicted) pairs."""
    pairs = list(pairs)
    tp = sum(1 for t, p in pairs if t and p)
    fp = sum(1 for t, p in pairs if not t and p)
    fn = sum(1 for t, p in pairs if t and not p)
    return Rate(tp, tp + fp), Rate(tp, tp + fn)


def fraction(flags: Iterable[bool]) -> Rate:
    flags = list(flags)
    return Rate(sum(flags), len(flags))


def pooled(num: Iterable[float], den: Iterable[float]) -> Rate:
    return Rate(sum(num), sum(den))


def median(values: Iterable[float | None]) -> float | None:
    known = [v for v in values if v is not None]
    return statistics.median(known) if known else None


def mean(values: Iterable[float | None]) -> float | None:
    known = [v for v in values if v is not None]
    return statistics.fmean(known) if known else None


def summarize_strategy(rows: list[tuple[dict, StrategyMetrics]]) -> dict[str, Rate | float | None]:
    """Suite-level metrics for one strategy; rows are (expect labels, metrics) per case."""
    ms = [m for _, m in rows]
    winner_p, winner_r = precision_recall((bool(e.get("winner_changed")), m.detects_winner_change) for e, m in rows)
    ready_p, ready_r = precision_recall((bool(e.get("readiness_violation")), m.detects_readiness_violation)
                                        for e, m in rows)
    return {
        "cases": len(ms),
        "equivalent": fraction(m.equivalent for m in ms),
        "sound": fraction(m.sound for m in ms),
        "errors": sum(m.error is not None for m in ms),
        "executed": sum(m.executed for m in ms),
        "computed": sum(m.computed for m in ms),
        "savings": pooled((m.computed - m.executed for m in ms), (m.computed for m in ms)),
        "savings_cost": pooled((m.cost_computed - m.cost_executed for m in ms), (m.cost_computed for m in ms)),
        "savings_mean": mean(m.savings.value for m in ms),
        "scope_frac_median": median(m.scope_frac.value for m in ms),
        "unneeded": pooled((m.unneeded for m in ms), (m.executed for m in ms)),
        "overapprox_median": median(m.overapprox.value for m in ms),
        "overapprox_pooled": pooled((m.scope for m in ms if m.changed), (m.changed for m in ms if m.changed)),
        "winner_precision": winner_p, "winner_recall": winner_r,
        "readiness_violation_precision": ready_p, "readiness_violation_recall": ready_r,
    }
