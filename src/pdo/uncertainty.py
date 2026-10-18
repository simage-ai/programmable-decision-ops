"""Decisions under uncertainty: expected value under a joint chance constraint, over sampled inputs.

An observation or assumption may carry an UncertaintySpec. Its point `value` (the mode or the mean) is
what the deterministic kernel scores; this module samples the distributions and has the same kernel
evaluate every draw, so the decision under uncertainty is built on the deterministic one, not beside it.

Sampling. Every uncertain object id has its own stream of uniforms, random.Random seeded from
digest(seed, object id), and draw k of the object is the inverse CDF of the k-th uniform of its stream
(closed forms, and statistics.NormalDist.inv_cdf for normal and lognormal; never random.gauss, whose
output is not promised to stay the same across Python versions). These are common random numbers: all
alternatives and scenarios are evaluated on the same draws, a shared assumption takes the same value
for every alternative in draw k, and adding, removing or editing one uncertain object leaves every
other object's draws unchanged. Raising the sample count keeps the earlier draws as a prefix.

Evaluation. Draw k is the point state with the drawn values assigned. The kernel evaluates the
decision's part of its graph (measures, evaluator runs, checks, values, utilities, feasibility and the
decision node) in cutoff mode against the point run, so nodes that no uncertain value reaches are
reused, and every number below is aggregated from node evaluations. Readiness and authorization are
judged by the deterministic run, which is reported alongside for contrast.

Decision (rule_kind expected_mavt), over N draws:

    E[U](a)  = mean_k U(a, xi_k), with standard error sd / sqrt(N)
    P_F(a)   = share of draws in which every mandatory check of a passes (the joint chance)
    chance   pass if P_F(a) >= 1 - alpha; fail if P_F(a) + P_unknown(a) < 1 - alpha; otherwise unknown
    recommended = argmax E[U] over chance-feasible alternatives with a known E[U], ties within tie_tolerance

A draw in which a value is unknown (missing, unaccepted, or an evaluator failure) is neither a pass nor
a fail: it widens P_F to [pass, pass + unknown] and leaves E[U] unknown, bounded by the mean bounds.
Without chance_alpha the constraint is strict (alpha = 0: feasible in every draw).

Scenarios. A scenario's assignments are applied to the point state. Assigning an object's value
conditions on it (the object is fixed in that scenario) unless the scenario also assigns its
uncertainty. The same streams are used, so scenarios differ only in what they assign. As in
pdo.flip.scenario_sweep, the recommendation is robust when it is a single alternative that is still the
only one recommended in every scenario: a claim about the tested scenarios only.
"""

from __future__ import annotations

import base64
import math
import random
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from statistics import NormalDist
from typing import Any

from pydantic import ValidationError

from pdo import model as m
from pdo.builtins import DEFAULT_TOLERANCE
from pdo.canonical import digest
from pdo.graph import Graph, compile_graph, source_node, state_digest
from pdo.load import index, items
from pdo.run import RunResult, run

DEFAULT_SAMPLES = 1000
DEFAULT_SEED = 0
FAMILY_PARAMETERS = {
    "uniform": ("low", "high"),
    "triangular": ("low", "mode", "high"),
    "normal": ("mean", "sd"),
    "lognormal": ("mean", "sd"),
}
# random() can return exactly 0.0, where the normal quantile is infinite.
_U_EPS = 2.0 ** -53
# Absorbs float noise when a share of draws is compared with 1 - alpha.
_P_EPS = 1e-12


class UncertaintyError(ValueError):
    """An UncertaintySpec cannot be sampled, or a scenario assigns something that does not exist."""


# --- distributions ------------------------------------------------------------------------------

@dataclass(frozen=True)
class Uncertain:
    """One uncertain object: its id, its point value, and the distribution its draws come from."""

    id: str
    family: str
    params: dict[str, float]
    point: float | None

    def to_dict(self) -> dict:
        return {"id": self.id, "family": self.family, "params": self.params, "point": self.point}


def spec_params(oid: str, spec: m.UncertaintySpec) -> dict[str, float]:
    """The parameters a family needs, checked; an UncertaintyError names what is wrong."""
    family = spec.family
    if family not in FAMILY_PARAMETERS:
        raise UncertaintyError(f"{oid}: unsupported distribution family {family!r}")
    params = {}
    for name in FAMILY_PARAMETERS[family]:
        number = getattr(spec, name)
        if number is None or not math.isfinite(number):
            raise UncertaintyError(f"{oid}: a {family} distribution needs a finite {name}")
        params[name] = float(number)
    if family == "uniform" and not params["low"] < params["high"]:
        raise UncertaintyError(f"{oid}: uniform needs low < high")
    if family == "triangular" and not (params["low"] <= params["mode"] <= params["high"]
                                       and params["low"] < params["high"]):
        raise UncertaintyError(f"{oid}: triangular needs low <= mode <= high and low < high")
    if family in ("normal", "lognormal") and not params["sd"] > 0:
        raise UncertaintyError(f"{oid}: {family} needs sd > 0")
    if family == "lognormal" and not params["mean"] > 0:
        raise UncertaintyError(f"{oid}: lognormal needs mean > 0")
    return params


def inverse_cdf(family: str, params: dict[str, float], u: float) -> float:
    """The u-quantile of a distribution. A lognormal's mean and sd are those of the value, not of its log."""
    u = min(max(u, _U_EPS), 1.0 - _U_EPS)
    if family == "uniform":
        return params["low"] + u * (params["high"] - params["low"])
    if family == "triangular":
        low, mode, high = params["low"], params["mode"], params["high"]
        if u < (mode - low) / (high - low):
            return low + math.sqrt(u * (high - low) * (mode - low))
        return high - math.sqrt((1.0 - u) * (high - low) * (high - mode))
    if family == "normal":
        return NormalDist(params["mean"], params["sd"]).inv_cdf(u)
    if family == "lognormal":
        sigma2 = math.log1p((params["sd"] / params["mean"]) ** 2)
        mu = math.log(params["mean"]) - sigma2 / 2
        return math.exp(NormalDist(mu, math.sqrt(sigma2)).inv_cdf(u))
    raise UncertaintyError(f"unsupported distribution family {family!r}")


def distribution_mean(family: str, params: dict[str, float]) -> float:
    if family == "uniform":
        return (params["low"] + params["high"]) / 2
    if family == "triangular":
        return (params["low"] + params["mode"] + params["high"]) / 3
    return params["mean"]


def uncertain_objects(state: m.DecisionState, fixed: set[str] | frozenset[str] = frozenset()) -> list[Uncertain]:
    """Observations and assumptions with a non-point UncertaintySpec, except the ids in `fixed`, by id."""
    out = []
    for obj in [*items(state.observations), *items(state.assumptions)]:
        spec = obj.uncertainty
        if spec is None or spec.family == "point" or obj.id in fixed:
            continue
        out.append(Uncertain(obj.id, spec.family, spec_params(obj.id, spec), obj.value))
    return sorted(out, key=lambda u: u.id)


def point_warnings(uncertain: list[Uncertain]) -> list[str]:
    """Point values that are neither the mode nor the mean of their distribution."""
    warnings = []
    for u in uncertain:
        anchors = [distribution_mean(u.family, u.params), *([u.params["mode"]] if "mode" in u.params else [])]
        if u.point is None:
            warnings.append(f"{u.id}: no point value, so the deterministic kernel treats it as unknown")
        elif not any(math.isclose(u.point, a, rel_tol=1e-9, abs_tol=1e-12) for a in anchors):
            warnings.append(f"{u.id}: point value {u.point:g} is neither the mode nor the mean of its {u.family}")
    return warnings


def stream(seed: int, oid: str) -> random.Random:
    """One uncertain object's uniform stream, seeded from digest(seed, id) and independent of every other."""
    return random.Random(int.from_bytes(base64.urlsafe_b64decode(digest(seed, oid)), "big"))


def uniforms(seed: int, oids: list[str], samples: int) -> dict[str, list[float]]:
    out = {}
    for oid in oids:
        rng = stream(seed, oid)
        out[oid] = [rng.random() for _ in range(samples)]
    return out


def sample(uncertain: list[Uncertain], draws: dict[str, list[float]]) -> dict[str, list[float]]:
    """Each object's values: the inverse CDF of its uniforms."""
    out = {}
    for u in uncertain:
        values = [inverse_cdf(u.family, u.params, x) for x in draws[u.id]]
        if not all(math.isfinite(v) for v in values):
            raise UncertaintyError(f"{u.id}: a draw is not finite")
        out[u.id] = values
    return out


# --- evaluating draws with the kernel -------------------------------------------------------------

def _closure(start: set[str], edges) -> set[str]:
    seen, queue = set(start), deque(start)
    while queue:
        for nxt in edges(queue.popleft()):
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    return seen


class DecisionModel:
    """The decision's part of a state's graph, compiled and run once on point values, then re-run per draw.

    A draw changes only the `value` of some source objects, so the graph's structure is fixed: a draw's
    graph is the point graph with those source nodes rebuilt from the drawn content (the nodes
    compile_graph builds for the assigned state), run in cutoff mode against the point run with the
    uncertain sources and their descendants as the impact set.
    """

    def __init__(self, state: m.DecisionState, base_dir: Path | str, uncertain: list[str]):
        full = compile_graph(state, base_dir)
        keep = _closure({"decision"}, lambda nid: full.nodes[nid].inputs)
        self.graph = Graph({n: full.nodes[n] for n in keep}, tuple(n for n in full.order if n in keep), state,
                           full.base_dir, {k: v for k, v in full.callables.items() if k in keep})
        self.point = run(self.graph)
        self.uncertain = [oid for oid in uncertain if oid in keep]
        children = self.graph.children()
        self.impact = _closure(set(self.uncertain), lambda nid: children[nid])

    def draw_graph(self, values: dict[str, float]) -> Graph:
        """The graph of one draw. Its `state` is still the point state: read the draw from node values."""
        nodes = dict(self.graph.nodes)
        for oid, x in values.items():
            nodes[oid] = source_node(oid, {**nodes[oid].local, "value": x}, subject=oid)
        g = self.graph
        return Graph(nodes, g.order, g.state, g.base_dir, g.callables)

    def evaluate(self, values: dict[str, float]) -> RunResult:
        return run(self.draw_graph(values), "cutoff", prev=self.point, impact=self.impact)


@dataclass
class Tally:
    """What one alternative's nodes produced over the draws."""

    utility: list[float | None] = field(default_factory=list)
    lower: list[float] = field(default_factory=list)
    upper: list[float] = field(default_factory=list)
    feasibility: dict[str, int] = field(default_factory=lambda: {"pass": 0, "fail": 0, "unknown": 0})
    passes: dict[str, int] = field(default_factory=dict)  # mandatory requirement -> draws passing
    wins: int = 0  # draws whose decision node (the point rule applied to the draw) recommends it


def run_draws(model: DecisionModel, values: dict[str, list[float]], samples: int) -> dict[str, Tally]:
    checks: dict[str, list[tuple[str, str]]] = {}
    for alt in sorted(a.id for a in model.graph.state.alternatives):
        checks[alt] = sorted((n.subject, n.id) for n in model.graph.nodes.values()
                             if n.kind == "check" and n.alternative == alt)
    tallies = {alt: Tally(passes={req: 0 for req, _ in pairs}) for alt, pairs in checks.items()}
    for k in range(samples):
        result = model.evaluate({oid: xs[k] for oid, xs in values.items()})
        for alt, t in tallies.items():
            u = result.value(f"U/{alt}")
            t.utility.append(u["U"])
            t.lower.append(u["lower"])
            t.upper.append(u["upper"])
            t.feasibility[result.value(f"F/{alt}")["status"]] += 1
            for req, nid in checks[alt]:
                t.passes[req] += result.value(nid)["status"] == "pass"
        for alt in result.decision["recommended"]:
            tallies[alt].wins += 1
    return tallies


# --- aggregating draws into the expected_mavt decision --------------------------------------------

@dataclass
class AlternativeStats:
    alternative: str
    expected_utility: float | None
    utility_se: float | None
    utility_bounds: tuple[float, float]
    p_feasible: float  # share of draws in which every mandatory check passes (the joint chance)
    p_infeasible: float
    p_unknown: float
    chance: str  # pass | fail | unknown against 1 - alpha
    p_pass: dict[str, float]  # per mandatory requirement: share of draws in which its check passes
    p_wins_draw: float  # share of draws in which the point rule, applied to the draw, recommends it
    point_utility: float | None
    point_feasibility: str

    def to_dict(self) -> dict:
        return {"alternative": self.alternative, "expected_utility": self.expected_utility,
                "utility_se": self.utility_se, "utility_bounds": list(self.utility_bounds),
                "p_feasible": self.p_feasible, "p_infeasible": self.p_infeasible, "p_unknown": self.p_unknown,
                "chance": self.chance, "p_pass": self.p_pass, "p_wins_draw": self.p_wins_draw,
                "point_utility": self.point_utility, "point_feasibility": self.point_feasibility}


@dataclass
class Analysis:
    """The expected_mavt decision for one state (the baseline, or one scenario)."""

    alternatives: dict[str, AlternativeStats]
    recommended: list[str]
    chance_feasible: list[str]
    chance_infeasible: list[str]
    unresolved: list[str]
    best_expected_utility: float | None
    unconstrained: list[str]  # argmax E[U] ignoring the chance constraint: what the constraint changed
    runner_up: dict | None  # the best other chance-feasible alternative, with the paired (CRN) margin
    point: dict  # the deterministic decision node of the same state
    uncertain: list[Uncertain]

    def to_dict(self) -> dict:
        return {"recommended": self.recommended, "chance_feasible": self.chance_feasible,
                "chance_infeasible": self.chance_infeasible, "unresolved": self.unresolved,
                "best_expected_utility": self.best_expected_utility, "unconstrained": self.unconstrained,
                "runner_up": self.runner_up, "point_recommended": self.point["recommended"],
                "alternatives": [s.to_dict() for _, s in sorted(self.alternatives.items())]}


def _mean_se(xs: list[float]) -> tuple[float, float]:
    n = len(xs)
    mean = math.fsum(xs) / n
    if n < 2:
        return mean, 0.0
    return mean, math.sqrt(math.fsum((x - mean) ** 2 for x in xs) / (n - 1) / n)


def _argmax(scores: dict[str, float], tol: float) -> tuple[list[str], float | None]:
    if not scores:
        return [], None
    best = max(scores.values())
    return sorted(a for a, x in scores.items() if x >= best - tol), best


def _chance(p_pass: float, p_unknown: float, alpha: float) -> str:
    level = 1.0 - alpha
    if p_pass >= level - _P_EPS:
        return "pass"
    if p_pass + p_unknown < level - _P_EPS:
        return "fail"
    return "unknown"


def _stats(alt: str, t: Tally, samples: int, alpha: float, point: RunResult) -> AlternativeStats:
    known = all(u is not None for u in t.utility)
    mean, se = _mean_se(t.utility) if known else (None, None)
    p = {status: count / samples for status, count in t.feasibility.items()}
    return AlternativeStats(alt, mean, se, (_mean_se(t.lower)[0], _mean_se(t.upper)[0]), p["pass"], p["fail"],
                            p["unknown"], _chance(p["pass"], p["unknown"], alpha),
                            {req: c / samples for req, c in t.passes.items()}, t.wins / samples,
                            point.value(f"U/{alt}")["U"], point.value(f"F/{alt}")["status"])


def analyze_state(state: m.DecisionState, base_dir: Path | str, rule: dict, draws: dict[str, list[float]],
                  fixed: set[str] | frozenset[str] = frozenset()) -> Analysis:
    """Evaluate every draw of one state with the kernel and aggregate the expected_mavt decision."""
    uncertain = uncertain_objects(state, fixed)
    model = DecisionModel(state, base_dir, [u.id for u in uncertain])
    values = sample([u for u in uncertain if u.id in model.uncertain], draws)
    tallies = run_draws(model, values, rule["samples"])
    stats = {alt: _stats(alt, t, rule["samples"], rule["chance_alpha"], model.point) for alt, t in tallies.items()}

    tol = rule["tie_tolerance"]
    chance_feasible = [a for a, s in stats.items() if s.chance == "pass"]
    ranked = {a: stats[a].expected_utility for a in chance_feasible if stats[a].expected_utility is not None}
    recommended, best = _argmax(ranked, tol)
    unconstrained, _ = _argmax({a: s.expected_utility for a, s in stats.items() if s.expected_utility is not None},
                               tol)
    unresolved = [a for a, s in stats.items() if s.chance == "unknown" or (s.chance == "pass" and a not in ranked)]
    runner_up = None
    others = {a: eu for a, eu in ranked.items() if a not in recommended}
    if recommended and others:
        top, second = recommended[0], max(sorted(others), key=others.get)
        diffs = [x - y for x, y in zip(tallies[top].utility, tallies[second].utility, strict=True)]
        margin, margin_se = _mean_se(diffs)
        runner_up = {"alternative": second, "margin": margin, "margin_se": margin_se}
    return Analysis(stats, recommended, chance_feasible, [a for a, s in stats.items() if s.chance == "fail"],
                    unresolved, best, unconstrained, runner_up, model.point.decision, uncertain)


# --- scenarios ----------------------------------------------------------------------------------

def apply_scenario(state: m.DecisionState, scenario: m.Scenario) -> tuple[m.DecisionState, set[str]]:
    """A copy of the state with the scenario's assignments applied, and the ids it conditions on."""
    out = state.model_copy(deep=True)
    objects = index(out)
    valued, respecified = set(), set()
    for a in items(scenario.assignments):
        obj = objects.get(a.target)
        if obj is None:
            raise UncertaintyError(f"{scenario.id}: assigns {a.target}, which does not exist")
        if a.slot not in type(obj).model_fields:
            raise UncertaintyError(f"{scenario.id}: {a.target} has no slot {a.slot!r}")
        try:
            setattr(obj, a.slot, a.new_value)
        except ValidationError as exc:
            raise UncertaintyError(f"{scenario.id}: {a.target}.{a.slot}: {exc.errors()[0]['msg']}") from exc
        if a.slot == "value":
            valued.add(a.target)
        elif a.slot == "uncertainty":
            respecified.add(a.target)
    return out, valued - respecified


@dataclass
class ScenarioResult:
    scenario: str
    name: str | None
    analysis: Analysis
    fixed: list[str]
    robust: bool

    def to_dict(self) -> dict:
        a = self.analysis
        return {"scenario": self.scenario, "name": self.name, "fixed": self.fixed, "robust": self.robust,
                "recommended": a.recommended, "chance_feasible": a.chance_feasible,
                "point_recommended": a.point["recommended"],
                "expected_utility": {k: s.expected_utility for k, s in sorted(a.alternatives.items())},
                "p_feasible": {k: s.p_feasible for k, s in sorted(a.alternatives.items())}}


# --- the whole analysis -------------------------------------------------------------------------

@dataclass
class UncertaintyResult:
    rule: dict
    baseline: Analysis
    deterministic: RunResult  # the full point run (what pdo run reports), for readiness and contrast
    scenarios: list[ScenarioResult] = field(default_factory=list)
    draws_digest: str = ""  # digest of every sampled value, so a replay can be checked
    warnings: list[str] = field(default_factory=list)

    @property
    def recommended(self) -> list[str]:
        return self.baseline.recommended

    @property
    def robust(self) -> bool | None:
        return all(s.robust for s in self.scenarios) if self.scenarios else None


def rule_settings(state: m.DecisionState, samples: int | None = None, seed: int | None = None) -> dict:
    """The decision rule's sampling settings, with command-line overrides and defaults applied."""
    rule = state.decision_rule

    def pick(override, declared, default):
        return override if override is not None else (declared if declared is not None else default)

    out = {"id": rule.id, "rule_kind": rule.rule_kind,
           "samples": pick(samples, rule.samples, DEFAULT_SAMPLES), "seed": pick(seed, rule.seed, DEFAULT_SEED),
           "chance_alpha": pick(None, rule.chance_alpha, 0.0),
           "tie_tolerance": pick(None, rule.tie_tolerance, DEFAULT_TOLERANCE)}
    if out["samples"] < 1:
        raise UncertaintyError("samples must be at least 1")
    if not 0.0 <= out["chance_alpha"] < 1.0:
        raise UncertaintyError("chance_alpha must be in [0, 1)")
    return out


def analyze(state: m.DecisionState, base_dir: Path | str, samples: int | None = None, seed: int | None = None,
            scenarios: bool = True) -> UncertaintyResult:
    """The expected_mavt decision, the deterministic one, and robustness over the state's scenarios."""
    rule = rule_settings(state, samples, seed)
    scenario_states = [(sc, *apply_scenario(state, sc)) for sc in (items(state.scenarios) if scenarios else [])]
    oids = {u.id for u in uncertain_objects(state)}
    for _, s_state, fixed in scenario_states:
        oids |= {u.id for u in uncertain_objects(s_state, fixed)}
    draws = uniforms(rule["seed"], sorted(oids), rule["samples"])

    baseline = analyze_state(state, base_dir, rule, draws)
    deterministic = run(compile_graph(state, base_dir))
    # A scenario that changes nothing (e.g. "nominal") is the baseline; do not sample it twice.
    cache = {(state_digest(state), frozenset()): baseline}
    results = []
    for sc, s_state, fixed in scenario_states:
        key = (state_digest(s_state), frozenset(fixed))
        if key not in cache:
            cache[key] = analyze_state(s_state, base_dir, rule, draws, fixed)
        analysis = cache[key]
        # The same definition as pdo.flip.scenario_sweep: a unique baseline choice, still uniquely chosen.
        robust = len(baseline.recommended) == 1 and analysis.recommended == baseline.recommended
        results.append(ScenarioResult(sc.id, sc.name, analysis, sorted(fixed), robust))
    warnings = point_warnings(baseline.uncertain)
    if rule["rule_kind"] != "expected_mavt":
        warnings.append(f"{rule['id']} is {rule['rule_kind']}: the kernel's decision is the point-value one, "
                        "and this analysis is advisory")
    sampled = sample(baseline.uncertain, draws)
    return UncertaintyResult(rule, baseline, deterministic, results, digest(sorted(sampled.items())), warnings)


def analyze_path(path: str | Path, episode: str | None = None, samples: int | None = None,
                 seed: int | None = None, scenarios: bool = True) -> UncertaintyResult:
    """Analyze a state file, a state directory, or one episode of a program (default: its baseline)."""
    from pdo.evaluate import is_program
    from pdo.load import load_state

    if episode is not None and not is_program(path):
        raise ValueError(f"{path} is not a program directory (no program.yaml); --episode needs one")
    if is_program(path):
        from pdo.episodes import Program

        program = Program.load(path)
        state = program.materialize(episode or program.baseline_episode)
        return analyze(state, program.base_dir, samples, seed, scenarios)
    loaded = load_state(path)
    return analyze(loaded.state, loaded.base_dir, samples, seed, scenarios)


# --- reporting ----------------------------------------------------------------------------------

def summary(result: UncertaintyResult) -> dict:
    """A JSON-ready summary (the shape `pdo uncertainty --json` returns)."""
    det = result.deterministic
    d = det.decision
    return {
        "rule": result.rule,
        "uncertain": [u.to_dict() for u in result.baseline.uncertain],
        "draws_digest": result.draws_digest,
        **result.baseline.to_dict(),
        "deterministic": {"recommended": d["recommended"], "feasible": d["feasible"], "infeasible": d["infeasible"],
                          "unresolved": d["unresolved"], "best_utility": d["best_utility"],
                          "readiness": det.readiness["state"], "actionable": det.actionable["actionable"]},
        "agrees_with_deterministic": result.baseline.recommended == d["recommended"],
        "scenarios": [s.to_dict() for s in result.scenarios],
        "robust": result.robust,
        "warnings": result.warnings,
    }


def _num(x: Any, digits: int = 4) -> str:
    return "?" if x is None else f"{x:.{digits}f}"


def _names(alts: list[str]) -> str:
    return ", ".join(alts) or "-"


def render(result: UncertaintyResult) -> str:
    s = summary(result)
    rule, det = s["rule"], s["deterministic"]
    level = 1 - rule["chance_alpha"]
    lines = [f"# {_names(s['recommended']) if s['recommended'] else 'no recommendation'}", "",
             f"Rule {rule['id']} ({rule['rule_kind']}): {rule['samples']} draws, seed {rule['seed']}, "
             f"Pr(all mandatory requirements) >= {level:g}, {len(s['uncertain'])} uncertain inputs",
             f"Point values (pdo run): {_names(det['recommended'])}, readiness {det['readiness'].upper()}"
             + ("" if s["agrees_with_deterministic"] else "  <- differs from the expected-value decision"),
             f"Without the chance constraint: {_names(s['unconstrained'])}",
             f"draws digest {s['draws_digest']}", ""]
    reqs = sorted({r for a in s["alternatives"] for r in a["p_pass"]})
    header = ["alternative", "E[U]", "se", "point U", "Pr(feasible)", "chance",
              *[f"Pr({r.split(':', 1)[-1]})" for r in reqs], "Pr(wins draw)"]
    lines += ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    for a in s["alternatives"]:
        name = a["alternative"] + (" *" if a["alternative"] in s["recommended"] else "")
        low, high = a["utility_bounds"]
        eu = _num(a["expected_utility"]) if a["expected_utility"] is not None else f"[{_num(low)}, {_num(high)}]"
        lines.append("| " + " | ".join([
            name, eu, _num(a["utility_se"]), _num(a["point_utility"]), _num(a["p_feasible"], 3), a["chance"],
            *[_num(a["p_pass"][r], 3) if r in a["p_pass"] else "-" for r in reqs], _num(a["p_wins_draw"], 3),
        ]) + " |")
    notes = []
    for a in s["alternatives"]:
        if a["chance"] != "pass" and a["p_pass"]:
            weakest = min(sorted(a["p_pass"]), key=a["p_pass"].get)
            notes.append(f"{a['alternative']}: meets every mandatory requirement in {_num(a['p_feasible'], 3)} of "
                         f"draws (< {level:g}); weakest {weakest} at {_num(a['p_pass'][weakest], 3)}")
    if s["runner_up"]:
        r = s["runner_up"]
        notes.append(f"margin over {r['alternative']}: {_num(r['margin'])} (paired se {_num(r['margin_se'])})")
    if notes:
        lines += ["", *[f"- {n}" for n in notes]]
    if s["scenarios"]:
        robust = "" if s["robust"] is None else f" (robust: {'yes' if s['robust'] else 'no'})"
        lines += ["", f"## Scenarios{robust}", "",
                  "| scenario | expected-value choice | chance-feasible | point-value choice | robust |",
                  "|---|---|---|---|---|"]
        for sc in s["scenarios"]:
            lines.append(f"| {sc['scenario']} | {_names(sc['recommended'])} | {_names(sc['chance_feasible'])} | "
                         f"{_names(sc['point_recommended'])} | {'yes' if sc['robust'] else 'no'} |")
    if s["warnings"]:
        lines += ["", "## Warnings", "", *[f"- {w}" for w in s["warnings"]]]
    return "\n".join(lines) + "\n"
