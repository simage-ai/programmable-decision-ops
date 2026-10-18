"""Load a perturbation suite and run it: every case, every strategy, against one parent episode.

A suite is a directory with perturbations.yaml: the program it perturbs (a path relative to the file),
the parent episode, and cases. Each case has a family, a dev/heldout split, a change (the body of a
change set) and hand labels (`expect`) computed from docs/semantics.md, never from the kernel.

For each case the runner applies the change as change set ep:bench-<case id> (parent: the suite's
parent) to the materialized parent episode, compiles the child, runs it fully (the reference) and runs
every strategy against the parent's full run (pdo.bench.strategies), then scores each one
(pdo.bench.metrics). The labels are compared with what the kernel computes; a disagreement is a
finding to report, never a reason to edit either side.

The suite result also carries the program timeline (every episode refreshed selectively in impact
and cutoff mode, verified against full recomputation, and compared with the program's expected.yaml)
and the baseline's flip points (pdo.flip), so one run yields every number the report needs.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from pdo import __version__
from pdo import model as m
from pdo.bench import metrics as mx
from pdo.bench import strategies as st
from pdo.canonical import digest
from pdo.episodes import Program, apply_change
from pdo.graph import Delta, Graph, compile_graph, delta
from pdo.load import LoadError, items, read_yaml
from pdo.run import RunResult, run

SUITE_FILE = "perturbations.yaml"
CASE_KEYS = {"id", "family", "split", "description", "change", "expect", "labeled_by"}
SPLITS = ("dev", "heldout")
EXPECTED_FILE = "expected.yaml"
UTILITY_ATOL = 5e-7  # expected.yaml rounds utilities to 6 decimals


class SuiteError(LoadError):
    """The suite file is malformed."""


@dataclass(frozen=True)
class Case:
    id: str
    family: str
    split: str
    description: str
    change: dict
    expect: dict
    labeled_by: str | None = None


@dataclass
class Suite:
    name: str
    file: Path
    program_dir: Path
    parent: str
    cases: list[Case]
    digest: str  # content digest of the suite file (labels and changes), independent of formatting

    @property
    def families(self) -> list[str]:
        return sorted({c.family for c in self.cases})


def load_suite(path: str | Path) -> Suite:
    path = Path(path)
    file = path / SUITE_FILE if path.is_dir() else path
    if not file.exists():
        raise SuiteError(f"{file}: no such file")
    data = read_yaml(file)
    problems = [f"missing {key}" for key in ("suite", "program", "parent", "cases") if key not in data]
    cases, seen = [], set()
    for i, raw in enumerate(data.get("cases") or []):
        where = f"cases[{i}]" + (f" ({raw.get('id')})" if isinstance(raw, dict) and raw.get("id") else "")
        if not isinstance(raw, dict):
            problems.append(f"{where}: not a mapping")
            continue
        missing = sorted({"id", "family", "split", "change", "expect"} - set(raw))
        unknown = sorted(set(raw) - CASE_KEYS)
        if missing or unknown:
            problems.append(f"{where}: " + "; ".join([*(f"missing {k}" for k in missing),
                                                       *(f"unknown key {k}" for k in unknown)]))
            continue
        if raw["id"] in seen:
            problems.append(f"{where}: duplicate case id")
        if raw["split"] not in SPLITS:
            problems.append(f"{where}: split must be one of {SPLITS}")
        bad = sorted(set(raw["expect"]) - set(mx.LABEL_FIELDS))
        if bad:
            problems.append(f"{where}: unknown expect fields {bad}")
        seen.add(raw["id"])
        cases.append(Case(raw["id"], raw["family"], raw["split"], raw.get("description") or "", raw["change"] or {},
                          raw["expect"], raw.get("labeled_by")))
    if problems:
        raise SuiteError(f"{file}: " + "; ".join(problems))
    return Suite(str(data["suite"]), file, (file.parent / data["program"]).resolve(), data["parent"], cases,
                 digest(data))


# --- one case -----------------------------------------------------------------------------------

@dataclass
class CaseResult:
    case: Case
    change: m.ChangeSet | None
    full: RunResult | None
    delta: Delta | None
    changed: frozenset[str] = frozenset()
    computed: dict | None = None  # labels computed from full(parent) vs full(child)
    agreement: dict[str, bool] = field(default_factory=dict)
    strategies: dict[str, mx.StrategyMetrics] = field(default_factory=dict)
    edges: list[tuple[float, int, mx.StrategyMetrics, bool]] = field(default_factory=list)  # p, seed, metrics, decision
    error: str | None = None

    @property
    def agrees(self) -> bool:
        return bool(self.agreement) and all(self.agreement.values())


def change_set(suite: Suite, case: Case) -> m.ChangeSet:
    return m.ChangeSet.model_validate({**case.change, "id": f"ep:bench-{case.id}", "parent": suite.parent})


def run_case(suite: Suite, case: Case, parent_state: m.DecisionState, parent: RunResult, base_dir: Path,
             edge_p: tuple[float, ...] = st.EDGE_P, edge_seeds: int = st.EDGE_SEEDS) -> CaseResult:
    try:
        change = change_set(suite, case)
        graph = compile_graph(apply_change(parent_state, change), base_dir)
    except (LoadError, ValueError) as exc:
        return CaseResult(case, None, None, None, error=f"{type(exc).__name__}: {exc}",
                          agreement=mx.agreement(case.expect, None))
    d = delta(parent.graph, graph)
    reference = st.run_strategy("full", parent.graph, graph, d, parent)
    full = reference.result
    if full is None:
        return CaseResult(case, change, None, d, error=reference.error, agreement=mx.agreement(case.expect, None))
    changed = mx.changed_nodes(parent, full)
    computed = mx.labels(parent, full)
    out = CaseResult(case, change, full, d, changed, computed, mx.agreement(case.expect, computed))
    for name in st.STRATEGIES:
        srun = reference if name == "full" else st.run_strategy(name, parent.graph, graph, d, parent)
        out.strategies[name] = mx.strategy_metrics(srun, parent, full, changed)
    for p in edge_p:
        for seed in range(edge_seeds):
            srun = st.run_edge_omission(case.id, parent.graph, graph, d, parent, p, seed)
            metrics = mx.strategy_metrics(srun, parent, full, changed)
            out.edges.append((p, seed, metrics, mx.decision_matches(metrics, full)))
    return out


# --- the program timeline -----------------------------------------------------------------------

@dataclass
class EpisodeRow:
    episode: str
    parent: str | None
    as_of: str
    trigger: str | None
    outcome: str | None
    recommended: list[str]
    readiness: str
    actionable: bool
    utilities: dict[str, float | None]
    bounds: dict[str, tuple[float, float]]
    feasibility: dict[str, str]
    total: int
    computed: int
    impact: int | None
    executed: dict[str, int]  # mode -> computed nodes executed
    verified: dict[str, bool | None]  # mode -> selective equals full
    expected: dict | None
    mismatches: list[str]  # fields that differ from expected.yaml

    @property
    def matches_expected(self) -> bool | None:
        return None if self.expected is None else not self.mismatches


def _close(a: float | None, b: float | None) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return math.isclose(a, b, abs_tol=UTILITY_ATOL)


def compare_expected(row: EpisodeRow, full: RunResult, want: dict) -> list[str]:
    d, r = full.decision, full.readiness
    got = {"parent": row.parent, "outcome": row.outcome, "recommended": d["recommended"], "feasible": d["feasible"],
           "infeasible": d["infeasible"], "unresolved": d["unresolved"], "readiness": r["state"],
           "failed_conditions": r["failed"], "unknown_conditions": r["unknown"], "actionable": row.actionable}
    out = [k for k, v in got.items() if k in want
           and ((sorted(want[k] or []) != sorted(v)) if isinstance(v, list) else want[k] != v)]
    if "utilities" in want:
        utilities = d["utilities"]
        if set(want["utilities"]) != set(utilities) or not all(_close(want["utilities"][a], utilities[a])
                                                               for a in utilities):
            out.append("utilities")
    want_bounds = want.get("utility_bounds") or {}
    if set(want_bounds) != set(row.bounds) or not all(
            _close(want_bounds[a][0], row.bounds[a][0]) and _close(want_bounds[a][1], row.bounds[a][1])
            for a in row.bounds):
        out.append("utility_bounds")
    return out


def program_timeline(program: Program) -> list[EpisodeRow]:
    """Every episode, parents first: refreshed in impact and cutoff mode, verified, checked against expected.yaml."""
    expected_file = program.path / EXPECTED_FILE
    expected = (read_yaml(expected_file).get("episodes") or {}) if expected_file.exists() else {}
    rows = []
    for ep in program.episodes():
        full = program.evaluate(ep, "full", verify=False).run
        selective = {mode: program.evaluate(ep, mode, verify=True) for mode in ("impact", "cutoff")}
        parent = program.parent(ep)
        parent_full = program.evaluate(parent, "full", verify=False).run if parent else None
        change = program.changes.get(ep)
        bounds = {a: (full.value(f"U/{a}")["lower"], full.value(f"U/{a}")["upper"])
                  for a, u in full.decision["utilities"].items() if u is None}
        computed = mx.computed_nodes(full.graph)
        cutoff = selective["cutoff"]
        row = EpisodeRow(
            ep, parent, str(full.graph.state.as_of), change.trigger if change else None,
            mx.outcome_class(parent_full, full) if parent_full else None, full.decision["recommended"],
            full.readiness["state"], bool(full.actionable["actionable"]), dict(full.decision["utilities"]), bounds,
            {a: full.value(f"F/{a}")["status"] for a in full.decision["utilities"]}, len(full.graph.nodes),
            len(computed), None if cutoff.impact is None else len(cutoff.impact & computed),
            {mode: len(ev.run.executed) for mode, ev in selective.items()},
            {mode: ev.verified for mode, ev in selective.items()}, expected.get(ep), [])
        if row.expected is not None:
            row.mismatches = compare_expected(row, full, row.expected)
        rows.append(row)
    return rows


# --- flips of the baseline ----------------------------------------------------------------------

@dataclass
class FlipAnalysis:
    episode: str
    scans: list[dict]
    flips: list[dict]
    weight_flips: list[dict]
    utilities: dict[str, float | None]
    weights: dict[str, float]


def baseline_flips(program: Program) -> FlipAnalysis:
    from pdo.flip import scan_parameters, weight_flips

    base = program.evaluate(program.baseline_episode, "full", verify=False).run
    state = base.graph.state
    scans, flips = scan_parameters(state, program.base_dir, base=base)
    return FlipAnalysis(program.baseline_episode, scans, flips, weight_flips(state, program.base_dir, base=base),
                        dict(base.decision["utilities"]), {c.id: c.weight for c in state.criteria})


# --- the whole suite ----------------------------------------------------------------------------

@dataclass
class SuiteResult:
    suite: Suite
    parent: RunResult
    cases: list[CaseResult]
    timeline: list[EpisodeRow]
    flips: FlipAnalysis | None
    edge_p: tuple[float, ...]
    edge_seeds: int
    seconds: float
    kernel_version: str = __version__

    @property
    def source(self) -> str:
        return f"suite {self.suite.name} {self.suite.digest}; pdo {self.kernel_version}"

    @property
    def parent_graph(self) -> Graph:
        return self.parent.graph

    def label_rows(self) -> list[tuple[dict, CaseResult]]:
        return [(c.case.expect, c) for c in self.cases]

    def strategy_rows(self, name: str) -> list[tuple[dict, mx.StrategyMetrics]]:
        return [(c.case.expect, c.strategies[name]) for c in self.cases if name in c.strategies]

    def summary(self) -> dict[str, dict]:
        return {name: mx.summarize_strategy(self.strategy_rows(name)) for name in st.STRATEGIES}

    @property
    def label_agreement(self) -> mx.Rate:
        return mx.fraction(c.agrees for c in self.cases)

    def gate(self) -> dict[str, mx.Rate]:
        """What `pdo bench check` holds to 100%: label agreement, and equivalence and soundness of the kernel's
        own strategies."""
        out = {"label_agreement": self.label_agreement}
        summary = self.summary()
        for name in st.KERNEL_STRATEGIES:
            out[f"{name}_equivalent"] = summary[name]["equivalent"]
            out[f"{name}_sound"] = summary[name]["sound"]
        return out

    @property
    def passed(self) -> bool:
        return all(rate.den and rate.num == rate.den for rate in self.gate().values())


def run_suite(path: str | Path, edge_p: tuple[float, ...] = st.EDGE_P, edge_seeds: int = st.EDGE_SEEDS,
              timeline: bool = True, flips: bool = True) -> SuiteResult:
    start = time.perf_counter()
    suite = load_suite(path)
    program = Program.load(suite.program_dir)
    parent_state = program.materialize(suite.parent)
    parent = run(compile_graph(parent_state, program.base_dir))
    cases = [run_case(suite, case, parent_state, parent, program.base_dir, edge_p, edge_seeds) for case in suite.cases]
    rows = program_timeline(program) if timeline else []
    analysis = baseline_flips(program) if flips else None
    return SuiteResult(suite, parent, cases, rows, analysis, tuple(edge_p), edge_seeds, time.perf_counter() - start)


def disagreements(result: SuiteResult) -> list[dict]:
    """Every case whose computed labels differ from its hand labels (or that failed to run)."""
    out = []
    for c in result.cases:
        if c.agrees:
            continue
        fields = [f for f, ok in c.agreement.items() if not ok]
        out.append({"case": c.case.id, "family": c.case.family, "split": c.case.split, "fields": fields,
                    "expected": {f: c.case.expect.get(f) for f in fields},
                    "computed": {f: (c.computed or {}).get(f) for f in fields}, "error": c.error,
                    "description": c.case.description})
    return out


def suite_yaml_lines(program_dir: Path) -> int:
    """Non-blank, non-comment YAML lines authored for a program: program.yaml, the baseline and change sets.

    What tools write is not authored: change sets written by a pdo tool (their header says so) and source
    locators (computed by pdo docs, in flow or block style) are left out."""
    files = [program_dir / "program.yaml"]
    meta = yaml.safe_load((program_dir / "program.yaml").read_text(encoding="utf-8")) or {}
    for sub in (meta.get("baseline_dir") or "baseline", meta.get("changes_dir") or "changes"):
        files += sorted((program_dir / sub).glob("*.yaml"))
    lines = 0
    for file in files:
        text = file.read_text(encoding="utf-8")
        if "# Written by pdo" in text:
            continue
        depth, block_indent = 0, None  # inside a flow locator (brace depth) or a block locator (its indent)
        for line in text.splitlines():
            stripped = line.strip()
            indent = len(line) - len(line.lstrip())
            if block_indent is not None:
                if stripped and indent > block_indent:
                    continue
                block_indent = None
            if depth:
                depth += line.count("{") - line.count("}")
                depth = max(depth, 0) if depth > 0 else 0
                continue
            if stripped.startswith("locator:"):
                rest = stripped[len("locator:"):].strip()
                if rest.startswith("{"):
                    depth = rest.count("{") - rest.count("}")
                    depth = depth - 1 if depth > 0 and rest.endswith("}}") else depth  # closes its observation too
                else:
                    block_indent = indent
                continue
            if stripped and not stripped.startswith("#"):
                lines += 1
    return lines


def changed_objects(change: m.ChangeSet | None) -> int:
    """How many objects a change set touches (for reporting only)."""
    if change is None:
        return 0
    added = 0
    if change.add is not None:
        from pdo.load import iter_objects

        added = sum(1 for _ in iter_objects(change.add))
    return len(items(change.remove)) + added + len({a.target for a in items(change.assign)})
