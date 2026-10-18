"""Compile a decision state into its evaluation graph, and compare graphs across episodes.

Authors never write nodes or edges: they follow from the typed objects by fixed rules, so a node can
only read what its edges provide and impact analysis is sound by construction.

    source objects ──► y/<alt>/<measure>      observe (observations) or a spec run (run/<spec>/<alt>)
    requirement, y ──► g/<req>/<alt>          threshold check, g <= 0 passes
    criterion, y   ──► v/<crit>/<alt>         value function
    weights, v     ──► U/<alt>                additive utility
    g (mandatory)  ──► F/<alt>                feasibility: a known fail dominates unknown
    rule, U, F     ──► decision               recommended set, feasible / infeasible / unresolved
    selectors      ──► cond/<condition>       readiness predicates (pass / fail / unknown)
    cond/*         ──► readiness              ready / pending / blocked
    everything but approvals ──► state_digest
    readiness, decision, state_digest, clock, policy, approvals ──► actionable

Every source object is a node whose value is its hashable content; a criterion is two nodes, its value
function (crit#vf) and its weight (crit#w), so a weight change does not touch the value functions.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pdo import model as m
from pdo.builtins import VERSIONS
from pdo.canonical import digest, hashable_dump, normalize
from pdo.load import LIST_SLOTS, executable_gaps, items, iter_objects
from pdo.registry import EvaluatorError, resolve, signature_problem

VF_SLOTS = ("measure", "anchor_worst", "anchor_best", "points", "clip")

# Readiness predicates read these inputs (static supersets; a predicate may use fewer).
PREDICATE_INPUTS = {
    "recommendation_exists": ("decision",),
    "unique_recommendation": ("decision",),
    "coverage": ("decision", "utilities"),
    "evidence_current": ("decision", "clock", "observations", "evidence_items"),
    "evidence_traceable": ("decision", "observations", "evidence_items"),
    "locators_current": ("observations", "evidence_items"),
    "assumptions_validated": ("assumptions",),
    "risks_treated": ("risks", "policy"),
    "review_status": ("review",),
    "omitted_options": ("alternatives",),
    "distinct_measures": ("criteria_vf",),
}
# Nodes whose values carry the decision's numbers (used by the benchmark's outcome classes).
DECISION_KINDS = {"measure", "run", "check", "value", "utility", "feasible", "decision"}


class CompileError(ValueError):
    """The state cannot be compiled (incomplete, or its dependencies form a cycle)."""


class CycleError(CompileError):
    def __init__(self, cycle: list[str]):
        super().__init__("dependency cycle: " + " -> ".join(cycle))
        self.cycle = cycle


@dataclass(frozen=True)
class Node:
    id: str
    kind: str  # source | clock | measure | run | check | value | utility | feasible | decision | condition | ...
    fn: str
    inputs: tuple[str, ...]
    local: Any
    local_sig: str
    subject: str | None = None  # the object this node is derived from
    alternative: str | None = None
    cost_units: float = 1.0
    deterministic: bool = True


@dataclass
class Graph:
    nodes: dict[str, Node]
    order: tuple[str, ...]
    state: m.DecisionState
    base_dir: Path
    callables: dict[str, Any] = field(default_factory=dict)  # run node id -> python callable

    def children(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = defaultdict(list)
        for node in self.nodes.values():
            for parent in node.inputs:
                out[parent].append(node.id)
        return out


def _node(nid: str, kind: str, fn: str, inputs, local=None, **kw) -> Node:
    local = normalize(local if local is not None else {})
    sig = digest(kind, nid, fn, VERSIONS.get(fn, 1), local)
    return Node(nid, kind, fn, tuple(inputs), local, sig, **kw)


def source_node(nid: str, value: dict, subject: str | None = None) -> Node:
    """A source node holding `value` (an object's hashable content), e.g. to substitute a sampled value."""
    return _node(nid, "source", "source", (), value, subject=subject)


def source_entries(state: m.DecisionState) -> list[tuple[str, str, str, dict]]:
    """(node id, object id, collection, value) for every source object, criteria split in two."""
    out = []
    for collection, obj in iter_objects(state):
        content = {"class": type(obj).__name__, **hashable_dump(obj)}
        if isinstance(obj, m.Criterion):
            vf = {k: v for k, v in content.items() if k in ("class", *VF_SLOTS)}
            out.append((f"{obj.id}#vf", obj.id, collection, vf))
            out.append((f"{obj.id}#w", obj.id, collection, {"class": "CriterionWeight", "weight": content["weight"]}))
        else:
            out.append((obj.id, obj.id, collection, content))
    return out


def source_node_ids(state: m.DecisionState, object_id: str) -> list[str]:
    """Node ids standing for one object (criteria have two)."""
    for obj in items(state.criteria):
        if obj.id == object_id:
            return [f"{object_id}#vf", f"{object_id}#w"]
    return [object_id]


def resolve_selectors(state: m.DecisionState, targets: list[str]) -> list[str]:
    """Expand "all:<collection>" selectors to object ids."""
    out: list[str] = []
    for target in targets:
        if target.startswith("all:"):
            collection = target[4:]
            if collection in LIST_SLOTS:
                out.extend(obj.id for obj in items(getattr(state, collection)))
        else:
            out.append(target)
    return sorted(set(out))


def digest_of_sources(values: dict[str, Any]) -> str:
    """Digest over (node id, value digest) pairs; shared by state digests and review stamps."""
    return digest(sorted((nid, digest(value)) for nid, value in values.items()))


def state_digest(state: m.DecisionState) -> str:
    """The digest an approval binds to: every source object except approvals (and not the clock)."""
    return digest_of_sources({nid: value for nid, _, coll, value in source_entries(state) if coll != "approvals"})


def covered_digest(state: m.DecisionState, review: m.HumanReview) -> str:
    """The digest a review binds to: the objects it covers."""
    entries = {nid: value for nid, _, _, value in source_entries(state)}
    covered = [nid for oid in resolve_selectors(state, items(review.covers)) for nid in source_node_ids(state, oid)]
    return digest_of_sources({nid: entries[nid] for nid in covered if nid in entries})


def compile_graph(state: m.DecisionState, base_dir: Path | str = ".") -> Graph:
    gaps = executable_gaps(state)
    if gaps:
        raise CompileError("state is not executable: " + "; ".join(f"{g.where} {g.message}" for g in gaps))
    base_dir = Path(base_dir)
    nodes: dict[str, Node] = {}
    callables: dict[str, Any] = {}

    def add(node: Node) -> None:
        nodes[node.id] = node

    entries = source_entries(state)
    for nid, oid, _, value in entries:
        add(_node(nid, "source", "source", (), value, subject=oid))
    add(_node("clock", "clock", "source", (), {"as_of": state.as_of}))

    alts = sorted(a.id for a in state.alternatives)
    obs_by_pair: dict[tuple[str, str], list[str]] = defaultdict(list)
    for o in items(state.observations):
        obs_by_pair[(o.alternative, o.measure)].append(o.id)

    spec_by_pair: dict[tuple[str, str], tuple[m.EvaluationSpecification, str]] = {}
    objects = {obj.id: obj for _, obj in iter_objects(state)}
    for s in items(state.evaluation_specifications):
        for b in items(s.inputs):
            if b.ref is not None and not isinstance(objects.get(b.ref), m.Assumption):
                raise CompileError(f"{s.id}: input {b.arg} refers to {b.ref}, which is not an Assumption")
        for alt in sorted(items(s.applies_to)) or alts:
            for out in items(s.outputs):
                pair = (alt, out.measure)
                if pair in spec_by_pair or pair in obs_by_pair:
                    other = spec_by_pair[pair][0].id if pair in spec_by_pair else "observations"
                    raise CompileError(f"{alt}/{out.measure} is produced by both {s.id} and {other}")
                spec_by_pair[pair] = (s, out.arg)

    needed: set[tuple[str, str]] = set(obs_by_pair) | set(spec_by_pair)
    for r in items(state.requirements):
        for alt in sorted(items(r.applies_to)) or alts:
            needed.add((alt, r.measure))
    for c in state.criteria:
        for alt in alts:
            needed.add((alt, c.measure))
    for s in items(state.evaluation_specifications):
        for alt in sorted(items(s.applies_to)) or alts:
            for b in items(s.inputs):
                if b.measure is not None:
                    needed.add((alt, b.measure))

    def y_id(alt: str, measure: str) -> str:
        return f"y/{alt}/{measure}"

    for alt, measure in sorted(needed):
        nid = y_id(alt, measure)
        if (alt, measure) in obs_by_pair:
            add(_node(nid, "measure", "observe", sorted(obs_by_pair[(alt, measure)]), {"measure": measure},
                      alternative=alt, subject=measure))
        elif (alt, measure) in spec_by_pair:
            spec, arg = spec_by_pair[(alt, measure)]
            add(_node(nid, "measure", "pick", [f"run/{spec.id}/{alt}"], {"arg": arg}, alternative=alt, subject=measure))
        else:
            add(_node(nid, "measure", "missing", (), {"measure": measure, "alternative": alt},
                      alternative=alt, subject=measure))

    for s in items(state.evaluation_specifications):
        code_digest, error = None, None
        func = None
        if s.spec_kind == "python":
            try:
                func, code_digest = resolve(s.implementation, base_dir)
                declared = {b.arg for b in items(s.inputs)} | {p.arg for p in items(s.parameters)}
                error = signature_problem(func, declared)
                if error:
                    func = None
            except EvaluatorError as exc:
                error = str(exc)
        for alt in sorted(items(s.applies_to)) or alts:
            inputs = [s.id]
            bindings = []
            for b in items(s.inputs):
                source = y_id(alt, b.measure) if b.measure is not None else b.ref
                inputs.append(source)
                bindings.append({"arg": b.arg, "node": source, "kind": "measure" if b.measure else "ref"})
            nid = f"run/{s.id}/{alt}"
            local = {"spec": s.id, "bindings": bindings, "code_digest": code_digest, "resolve_error": error}
            add(_node(nid, "run", s.spec_kind, inputs, local, subject=s.id, alternative=alt,
                      cost_units=s.cost_units if s.cost_units is not None else 1.0,
                      deterministic=(s.deterministic is not False) and (s.cacheable is not False)))
            if func is not None:
                callables[nid] = func

    for r in items(state.requirements):
        for alt in sorted(items(r.applies_to)) or alts:
            add(_node(f"g/{r.id}/{alt}", "check", "threshold", [r.id, y_id(alt, r.measure)], subject=r.id,
                      alternative=alt))

    criteria = sorted(state.criteria, key=lambda c: c.id)
    for c in criteria:
        for alt in alts:
            add(_node(f"v/{c.id}/{alt}", "value", "value", [f"{c.id}#vf", y_id(alt, c.measure)], subject=c.id,
                      alternative=alt))
    for alt in alts:
        inputs = [f"v/{c.id}/{alt}" for c in criteria] + [f"{c.id}#w" for c in criteria]
        add(_node(f"U/{alt}", "utility", "utility", inputs, {"criteria": [c.id for c in criteria]}, alternative=alt))
        checks = [f"g/{r.id}/{alt}" for r in sorted(items(state.requirements), key=lambda r: r.id)
                  if r.mandatory is not False and (not r.applies_to or alt in r.applies_to)]
        add(_node(f"F/{alt}", "feasible", "feasible", checks, alternative=alt))

    rule = state.decision_rule
    add(_node("decision", "decision", "decide", [rule.id, *[f"U/{a}" for a in alts], *[f"F/{a}" for a in alts]],
              {"alternatives": alts}))

    selectors = {
        "decision": ["decision"],
        "clock": ["clock"],
        "utilities": [f"U/{a}" for a in alts],
        "observations": sorted(o.id for o in items(state.observations)),
        "evidence_items": sorted(e.id for e in items(state.evidence_items)),
        "assumptions": sorted(a.id for a in items(state.assumptions)),
        "risks": sorted(r.id for r in items(state.risks)),
        "policy": [state.readiness_policy.id],
        "alternatives": alts,
        "criteria_vf": [f"{c.id}#vf" for c in criteria],
    }
    reviews = {rv.id: rv for rv in items(state.reviews)}
    conditions = sorted([*items(state.readiness_conditions), *items(state.bias_checks)], key=lambda c: c.id)
    for c in conditions:
        inputs = [c.id]
        local: dict[str, Any] = {"condition": c.id}
        for selector in PREDICATE_INPUTS[c.predicate]:
            if selector == "review":
                review = reviews.get(c.review)
                if review is not None:
                    covered = [nid for oid in resolve_selectors(state, items(review.covers))
                               for nid in source_node_ids(state, oid) if nid in nodes]
                    inputs += [review.id, *covered]
                    local["review"] = review.id
                    local["covered"] = covered
            else:
                inputs += selectors[selector]
        if c.predicate == "locators_current":
            # Locators and document digests are unhashable (they locate values without being part of them),
            # so they reach this condition through its local data: re-anchoring changes this node alone.
            local["documents"] = {e.id: e.document_digest for e in items(state.evidence_items) if e.document_digest}
            local["locators"] = {o.id: {"evidence": o.evidence, "document_digest": o.locator.document_digest,
                                        "section": o.locator.section, "section_path": o.locator.section_path}
                                 for o in items(state.observations) if o.locator is not None}
            local["unlocated"] = sorted(o.id for o in items(state.observations)
                                        if o.locator is None and o.evidence in local["documents"])
        add(_node(f"cond/{c.id}", "condition", c.predicate, inputs, local, subject=c.id))

    add(_node("readiness", "readiness", "aggregate", [f"cond/{c.id}" for c in conditions]))
    digest_inputs = sorted(nid for nid, _, coll, _ in entries if coll != "approvals")
    add(_node("state_digest", "state_digest", "state_digest", digest_inputs))
    approvals = sorted(a.id for a in items(state.approvals))
    add(_node("actionable", "actionable", "actionable",
              ["readiness", "decision", "state_digest", "clock", state.readiness_policy.id, *approvals],
              {"approvals": approvals}))

    for node in nodes.values():
        for parent in node.inputs:
            if parent not in nodes:
                raise CompileError(f"{node.id} depends on {parent}, which is not in the graph")
    return Graph(nodes, topo_order(nodes), state, base_dir, callables)


def topo_order(nodes: dict[str, Node]) -> tuple[str, ...]:
    """Kahn's algorithm with ties broken by id, so the order is deterministic."""
    indegree = {nid: 0 for nid in nodes}
    children: dict[str, list[str]] = defaultdict(list)
    for node in nodes.values():
        for parent in node.inputs:
            indegree[node.id] += 1
            children[parent].append(node.id)
    import heapq

    ready = [nid for nid, d in indegree.items() if d == 0]
    heapq.heapify(ready)
    order: list[str] = []
    while ready:
        nid = heapq.heappop(ready)
        order.append(nid)
        for child in children[nid]:
            indegree[child] -= 1
            if indegree[child] == 0:
                heapq.heappush(ready, child)
    if len(order) != len(nodes):
        raise CycleError(_find_cycle(nodes, {n for n, d in indegree.items() if d > 0}))
    return tuple(order)


def _find_cycle(nodes: dict[str, Node], remaining: set[str]) -> list[str]:
    start = min(remaining)
    path, seen = [start], {start}
    current = start
    while True:
        nxt = min(p for p in nodes[current].inputs if p in remaining)
        if nxt in seen:
            return path[path.index(nxt):] + [nxt]
        path.append(nxt)
        seen.add(nxt)
        current = nxt


@dataclass(frozen=True)
class Delta:
    changed: frozenset[str]  # in both graphs, with a different local signature or input list
    added: frozenset[str]
    deleted: frozenset[str]

    @property
    def roots(self) -> frozenset[str]:
        return self.changed | self.added | self.deleted


def delta(old: Graph, new: Graph) -> Delta:
    changed = {nid for nid, node in new.nodes.items() if nid in old.nodes
               and (old.nodes[nid].local_sig != node.local_sig or old.nodes[nid].inputs != node.inputs)}
    added, deleted = set(new.nodes) - set(old.nodes), set(old.nodes) - set(new.nodes)
    return Delta(frozenset(changed), frozenset(added), frozenset(deleted))


def impact_set(old: Graph, new: Graph, d: Delta | None = None) -> set[str]:
    """I = Δ ∪ Descendants(Δ; G_old ∪ G_new), restricted to the new graph.

    The union matters when an edge is removed or an evaluator replaced: a node that lost an input in
    the new graph is still reached through the old one.
    """
    d = d or delta(old, new)
    union: dict[str, set[str]] = defaultdict(set)
    for graph in (old, new):
        for node in graph.nodes.values():
            for parent in node.inputs:
                union[parent].add(node.id)
    seen = set(d.roots)
    queue = deque(d.roots)
    while queue:
        nid = queue.popleft()
        for child in union[nid]:
            if child not in seen:
                seen.add(child)
                queue.append(child)
    return seen & set(new.nodes)
