"""Evaluate a graph: fully, or selectively against a previous run.

Each result carries three digests:
  sig   = H(local_sig, parent sigs)        what was computed, from what: provenance and replay
  key   = H(local_sig, parent value digests)  equal keys give equal values (deterministic nodes)
  vdig  = H(value)

Modes:
  full    evaluate every node (the reference)
  impact  evaluate the impact set I (pdo.graph.impact_set); reuse every other result from `prev`
  cutoff  like impact, but inside I reuse a result whose key is unchanged (early cutoff)
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any

from pdo import builtins as b
from pdo.canonical import digest
from pdo.graph import Graph, Node, impact_set

MODES = ("full", "impact", "cutoff")


@dataclass(frozen=True)
class Result:
    node: str
    value: Any
    vdig: str
    sig: str
    key: str
    exec_status: str  # executed | reused | cutoff_reused | failed
    error: str | None = None
    duration_ms: float = 0.0


@dataclass
class RunResult:
    graph: Graph
    results: dict[str, Result]
    mode: str
    impact: set[str] | None = None
    executed: list[str] = field(default_factory=list)

    def value(self, nid: str) -> Any:
        return self.results[nid].value

    @property
    def decision(self) -> dict:
        return self.value("decision")

    @property
    def readiness(self) -> dict:
        return self.value("readiness")

    @property
    def actionable(self) -> dict:
        return self.value("actionable")

    def summary(self) -> dict:
        d, r, a = self.decision, self.readiness, self.actionable
        return {"recommended": d["recommended"], "feasible": d["feasible"], "infeasible": d["infeasible"],
                "unresolved": d["unresolved"], "readiness": r["state"], "actionable": a["actionable"]}


class EvaluatorFailure(RuntimeError):
    pass


def _python(graph: Graph, node: Node, inputs: dict[str, Any]) -> dict:
    if node.local.get("resolve_error"):
        raise EvaluatorFailure(node.local["resolve_error"])
    spec = inputs[node.subject]
    kwargs, unknown = b.bound_inputs(node, inputs)
    if unknown:
        return {"outputs": None, "why": f"unknown inputs: {', '.join(unknown)}"}
    params = b.spec_parameters(inputs, node.subject)
    outputs = [o["arg"] for o in spec["outputs"]]
    if node.fn == "python":
        raw = graph.callables[node.id](**kwargs, **params)
    else:  # linear_combination
        terms = (params.get(f"coefficient_{k}", 0.0) * v for k, v in kwargs.items())
        raw = params.get("intercept", 0.0) + math.fsum(terms)
    if not isinstance(raw, dict):
        if len(outputs) != 1:
            raise EvaluatorFailure(f"returned one number but declares outputs {outputs}")
        raw = {outputs[0]: raw}
    result = {}
    for arg in outputs:
        value = raw.get(arg)
        if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
            raise EvaluatorFailure(f"output {arg!r} is {value!r}, not a finite number")
        result[arg] = float(value)
    return {"outputs": result}


def execute(graph: Graph, node: Node, inputs: dict[str, Any]) -> tuple[Any, str | None]:
    """A node's value, and an error message if its evaluator failed."""
    if node.fn == "source":
        return node.local, None
    try:
        if node.kind == "run":
            value = _python(graph, node, inputs)
        else:
            value = b.BUILTINS[node.fn](node, inputs)
            if node.kind == "condition":
                value = {**value, "required": inputs[node.subject].get("required", True)}
        return value, None
    except Exception as exc:  # noqa: BLE001 - an evaluator failure is a recorded unknown, not a crash
        if node.kind != "run":
            raise
        message = f"{type(exc).__name__}: {exc}"
        return {"outputs": None, "why": "evaluator failed", "error": message,
                "actions": [{"kind": "fix_evaluator", "target": node.subject, "reason": message}]}, message


def run(graph: Graph, mode: str = "full", prev: RunResult | None = None, impact: set[str] | None = None) -> RunResult:
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    if mode != "full":
        if prev is None:
            raise ValueError(f"mode {mode!r} needs the previous run")
        if impact is None:
            impact = impact_set(prev.graph, graph)
    results: dict[str, Result] = {}
    executed: list[str] = []
    for nid in graph.order:
        node = graph.nodes[nid]
        parents = [results[p] for p in node.inputs]
        sig = digest(node.local_sig, [p.sig for p in parents])
        key = digest(node.local_sig, [p.vdig for p in parents])
        old = prev.results.get(nid) if prev is not None else None
        if mode != "full" and nid not in impact:
            if old is None:
                raise RuntimeError(f"{nid} is outside the impact set but has no previous result")
            results[nid] = Result(nid, old.value, old.vdig, sig, key, "reused", old.error)
            continue
        if mode == "cutoff" and old is not None and old.key == key and node.deterministic:
            results[nid] = Result(nid, old.value, old.vdig, sig, key, "cutoff_reused", old.error)
            continue
        inputs = {p: results[p].value for p in node.inputs}
        start = time.perf_counter()
        value, error = execute(graph, node, inputs)
        elapsed = (time.perf_counter() - start) * 1000
        results[nid] = Result(nid, value, digest(value), sig, key, "failed" if error else "executed", error, elapsed)
        if node.fn != "source":
            executed.append(nid)
    return RunResult(graph, results, mode, impact, executed)


@dataclass
class Equivalence:
    equal: bool
    mismatched: list[str]
    missing: list[str]

    def __bool__(self) -> bool:
        return self.equal


def equivalent(a: RunResult, b_: RunResult) -> Equivalence:
    """Do two runs of the same graph agree on every value?"""
    mismatched = sorted(n for n in b_.results if n in a.results and a.results[n].vdig != b_.results[n].vdig)
    missing = sorted(set(b_.results) ^ set(a.results))
    return Equivalence(not mismatched and not missing, mismatched, missing)
