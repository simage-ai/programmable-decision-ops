"""Re-evaluation strategies the benchmark compares, each run on a child graph against the parent's full run.

A strategy is a scope, the set of nodes it may execute; every node outside it is reused from the parent
run (pdo.run in impact mode with that set), and cutoff additionally reuses a node inside it whose input
values are unchanged. The kernel's own strategies:

    full           every node: the reference
    impact         I = Δ ∪ Descendants(Δ; G_old ∪ G_new)            (pdo.graph.impact_set)
    cutoff         I, with early cutoff on unchanged input values

Ablations, each removing one ingredient of the representation:

    delta_only     Δ only: the changed nodes run, nothing downstream of them does
    one_hop        Δ and its direct children, no transitive closure
    no_readiness   I, but readiness conditions, readiness and actionable are reused from the parent
                   (the decision without explicit readiness predicates)
    edge_omission  I computed after dropping each dependency edge independently with probability p,
                   a dependency the author forgot to declare. Edges are dropped from the impact analysis
                   only: execution still reads every input, so a wrong result can only come from a node
                   that should have run but was reused.
"""

from __future__ import annotations

import random
import time
from collections import defaultdict, deque
from collections.abc import Iterable
from dataclasses import dataclass

from pdo.graph import Delta, Graph, impact_set
from pdo.run import RunResult, run

STRATEGIES = ("full", "impact", "cutoff", "delta_only", "one_hop", "no_readiness")
KERNEL_STRATEGIES = ("impact", "cutoff")  # the ones `pdo bench check` holds to 100%
EDGE_P = (0.02, 0.05, 0.10, 0.20)
EDGE_SEEDS = 20
READINESS_KINDS = frozenset({"condition", "readiness", "actionable"})


@dataclass
class StrategyRun:
    name: str
    scope: frozenset[str]  # the nodes this strategy may execute
    result: RunResult | None
    duration_ms: float
    error: str | None = None
    dropped_edges: int = 0


Edge = tuple[str, str]  # (input, node)


def union_edges(old: Graph, new: Graph) -> list[Edge]:
    """Every dependency edge of either graph, sorted (so seeded edge drops are reproducible)."""
    edges = {(parent, node.id) for graph in (old, new) for node in graph.nodes.values() for parent in node.inputs}
    return sorted(edges)


def _children(edges: Iterable[Edge]) -> dict[str, set[str]]:
    out: dict[str, set[str]] = defaultdict(set)
    for parent, child in edges:
        out[parent].add(child)
    return out


def descendants(roots: Iterable[str], edges: Iterable[Edge], depth: int | None = None) -> set[str]:
    """The roots and everything reachable from them along `edges` (at most `depth` hops when given)."""
    children = _children(edges)
    seen = set(roots)
    queue = deque((nid, 0) for nid in seen)
    while queue:
        nid, hops = queue.popleft()
        if depth is not None and hops >= depth:
            continue
        for child in children[nid]:
            if child not in seen:
                seen.add(child)
                queue.append((child, hops + 1))
    return seen


def scope_full(old: Graph, new: Graph, d: Delta) -> frozenset[str]:
    return frozenset(new.nodes)


def scope_impact(old: Graph, new: Graph, d: Delta) -> frozenset[str]:
    return frozenset(impact_set(old, new, d))


def scope_delta_only(old: Graph, new: Graph, d: Delta) -> frozenset[str]:
    return frozenset(d.roots & set(new.nodes))


def scope_one_hop(old: Graph, new: Graph, d: Delta) -> frozenset[str]:
    return frozenset(descendants(d.roots, union_edges(old, new), depth=1) & set(new.nodes))


def scope_no_readiness(old: Graph, new: Graph, d: Delta) -> frozenset[str]:
    # A readiness node that is new (an added condition) has no parent result to reuse, so it still runs.
    return frozenset(nid for nid in impact_set(old, new, d)
                     if not (new.nodes[nid].kind in READINESS_KINDS and nid in old.nodes))


def scope_edge_omission(old: Graph, new: Graph, d: Delta, p: float, rng: random.Random) -> tuple[frozenset[str], int]:
    """The impact set over the edges that survive independent drops with probability p, and how many dropped."""
    edges = union_edges(old, new)
    kept = [e for e in edges if rng.random() >= p]
    return frozenset(descendants(d.roots, kept) & set(new.nodes)), len(edges) - len(kept)


SCOPES = {"full": scope_full, "impact": scope_impact, "cutoff": scope_impact, "delta_only": scope_delta_only,
          "one_hop": scope_one_hop, "no_readiness": scope_no_readiness}


def edge_rng(case_id: str, p: float, seed: int) -> random.Random:
    """One reproducible stream per (case, p, seed); a string seed is hashed with SHA-512 by random."""
    return random.Random(f"{case_id}|{p:.4f}|{seed}")


def execute(name: str, graph: Graph, parent: RunResult, scope: frozenset[str], dropped: int = 0) -> StrategyRun:
    """Run the child graph within `scope`, reusing the parent's results outside it."""
    mode = "full" if name == "full" else ("cutoff" if name == "cutoff" else "impact")
    start = time.perf_counter()
    try:
        result = run(graph, mode, prev=parent, impact=set(scope)) if mode != "full" else run(graph)
        error = None
    except Exception as exc:  # noqa: BLE001 - stale reused inputs can break a builtin; that is a failed strategy
        result, error = None, f"{type(exc).__name__}: {exc}"
    return StrategyRun(name, scope, result, (time.perf_counter() - start) * 1000, error, dropped)


def run_strategy(name: str, old: Graph, new: Graph, d: Delta, parent: RunResult) -> StrategyRun:
    return execute(name, new, parent, SCOPES[name](old, new, d))


def run_edge_omission(case_id: str, old: Graph, new: Graph, d: Delta, parent: RunResult, p: float,
                      seed: int) -> StrategyRun:
    scope, dropped = scope_edge_omission(old, new, d, p, edge_rng(case_id, p, seed))
    return execute(f"edge_omission@{p:.2f}", new, parent, scope, dropped)
