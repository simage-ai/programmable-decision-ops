"""Proposal numbers: every value the SBIR proposal quotes, computed here and written as one TSV.

    uv run python -m scripts.proposal_values [--suite bench/vehicle] [--results results] [--proposal-dir DIR]

Writes <results>/proposal/pdo_values.tsv with columns key, value, unit, description, source and command,
sorted by key and rewritten only when its content changes. With --proposal-dir it also copies the table
there as pdo_values.tsv (the proposal repository's version directory; `make proposal-values` passes it).

Every number comes from this run: the representation benchmark (pdo.bench.suite.run_suite: every case
and strategy, the edge-omission sweep, the program timeline and the baseline's flip analysis), or from
counting the repository's own schema and example files. Nothing is typed in. `source` names the suite
digest (its cases and labels) and the kernel version, so a stale number is visible as a stale source.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

from pdo.bench import report
from pdo.bench.metrics import OUTCOMES, Rate
from pdo.bench.suite import SuiteResult, run_suite, suite_yaml_lines

ROOT = Path(__file__).resolve().parents[1]
COMMAND = "make proposal-values"
HEADER = ("key", "value", "unit", "description", "source", "command")
EDGE_KEYS = {0.02: "PDO_EDGE02_EQUIV_PCT", 0.05: "PDO_EDGE05_EQUIV_PCT", 0.10: "PDO_EDGE10_EQUIV_PCT",
             0.20: "PDO_EDGE20_EQUIV_PCT"}


def pct(rate: Rate) -> str:
    return "NA" if rate.pct is None else f"{rate.pct:.1f}"


def frac(rate: Rate | float | None, digits: int = 2) -> str:
    value = rate.value if isinstance(rate, Rate) else rate
    return "NA" if value is None else f"{value:.{digits}f}"


def counts(rate: Rate) -> str:
    return f"{rate.num:g} of {rate.den:g}"


def schema_classes(schema: Path) -> int:
    """Classes defined in a LinkML schema and the local schemas it imports (linkml:types defines none)."""
    seen: set[Path] = set()
    classes: set[str] = set()
    stack = [schema]
    while stack:
        path = stack.pop()
        if path in seen or not path.exists():
            continue
        seen.add(path)
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        classes.update((data.get("classes") or {}).keys())
        stack += [path.parent / f"{name}.yaml" for name in data.get("imports") or [] if ":" not in name]
    return len(classes)


def nearest_flip(flips: list[dict], parameter: str, counterpart: str | None = None) -> float | None:
    """The flip point of `parameter` nearest its baseline value."""
    found = [f for f in flips if f["parameter"] == parameter and f.get("counterpart") == counterpart]
    if not found:
        return None
    return min(found, key=lambda f: abs(f["flip_number"] - f["baseline_number"]))["flip_number"]


def values(result: SuiteResult, root: Path) -> list[tuple[str, str, str, str]]:
    """(key, value, unit, description) for every proposal number."""
    summary = result.summary()
    impact, cutoff = summary["impact"], summary["cutoff"]
    no_readiness = summary["no_readiness"]
    suite = result.suite
    parent = result.parent_graph
    out = [
        ("PDO_BENCH_CASES", str(len(result.cases)), "cases",
         f"Controlled perturbations in the {suite.name} representation benchmark, each a change set applied to "
         f"{suite.parent}"),
        ("PDO_BENCH_FAMILIES", str(len(suite.families)), "families",
         "Perturbation families (" + ", ".join(suite.families) + ")"),
        ("PDO_BENCH_HELDOUT_CASES", str(sum(c.case.split == "heldout" for c in result.cases)), "cases",
         "Cases in the held-out split, fixed before any kernel run"),
        ("PDO_LABEL_AGREE_PCT", pct(result.label_agreement), "%",
         f"Cases whose six hand labels (outcome class, recommended set, readiness, winner changed, readiness "
         f"violation, approval invalidated) all equal the kernel's ({counts(result.label_agreement)})"),
        ("PDO_EQUIV_PCT", pct(impact["equivalent"]), "%",
         f"Cases where impact-set re-evaluation gives every node value equal to full recomputation "
         f"({counts(impact['equivalent'])})"),
        ("PDO_CUTOFF_EQUIV_PCT", pct(cutoff["equivalent"]), "%",
         f"Cases where impact-set re-evaluation with early cutoff equals full recomputation "
         f"({counts(cutoff['equivalent'])})"),
        ("PDO_SOUND_PCT", pct(impact["sound"]), "%",
         f"Cases where every node whose value changed lies inside the impact set ({counts(impact['sound'])})"),
        ("PDO_GRAPH_NODES", str(len(parent.nodes)), "nodes",
         f"Nodes in the evaluation graph of the benchmark parent {suite.parent}, source objects included"),
        ("PDO_GRAPH_COMPUTED_NODES", str(len(report.mx.computed_nodes(parent))), "nodes",
         f"Computed nodes (all but source objects and the clock) in the graph of {suite.parent}"),
        ("PDO_IMPACT_MEDIAN_FRAC", frac(impact["scope_frac_median"]), "fraction",
         "Median over cases of the impact set's share of computed nodes"),
        ("PDO_OVERAPPROX_MEDIAN", frac(impact["overapprox_median"]), "ratio",
         "Median over cases with a changed node of impact-set size over changed nodes"),
        ("PDO_SAVINGS_PCT", pct(impact["savings"]), "%",
         f"Computed-node evaluations avoided by impact-set re-evaluation, pooled over cases "
         f"({impact['savings'].num:g} of {impact['savings'].den:g})"),
        ("PDO_SAVINGS_COST_PCT", pct(impact["savings_cost"]), "%",
         "Evaluation cost avoided by impact-set re-evaluation, weighted by node cost_units, pooled over cases"),
        ("PDO_CUTOFF_SAVINGS_PCT", pct(cutoff["savings"]), "%",
         f"Computed-node evaluations avoided with early cutoff, pooled over cases "
         f"({cutoff['savings'].num:g} of {cutoff['savings'].den:g})"),
        ("PDO_UNNEEDED_RECOMP_PCT", pct(impact["unneeded"]), "%",
         f"Share of impact-set evaluations that reproduced the parent's value, pooled "
         f"({counts(impact['unneeded'])})"),
        ("PDO_WINNER_P", frac(impact["winner_precision"]), "fraction",
         f"Precision of winner-change detection by impact-set re-evaluation against the labels "
         f"({counts(impact['winner_precision'])})"),
        ("PDO_WINNER_R", frac(impact["winner_recall"]), "fraction",
         f"Recall of winner-change detection by impact-set re-evaluation against the labels "
         f"({counts(impact['winner_recall'])})"),
        ("PDO_READY_VIOL_P", frac(impact["readiness_violation_precision"]), "fraction",
         f"Precision of readiness-violation detection (readiness not ready) by impact-set re-evaluation "
         f"({counts(impact['readiness_violation_precision'])})"),
        ("PDO_READY_VIOL_R", frac(impact["readiness_violation_recall"]), "fraction",
         f"Recall of readiness-violation detection by impact-set re-evaluation "
         f"({counts(impact['readiness_violation_recall'])})"),
        ("PDO_ABL_NO_READINESS_RECALL", frac(no_readiness["readiness_violation_recall"]), "fraction",
         f"Ablation without readiness predicates (conditions, readiness and actionable reused): recall of "
         f"readiness violations ({counts(no_readiness['readiness_violation_recall'])})"),
    ]
    for name in ("no_readiness", "delta_only", "one_hop"):
        key = f"PDO_ABL_{name.upper()}_EQUIV_PCT"
        rate = summary[name]["equivalent"]
        out.append((key, pct(rate), "%", f"Ablation {name}: cases equal to full recomputation ({counts(rate)})"))
    edges = {r["p"]: r for r in report.edge_rows(result) if r["family"] == "all"}
    for p, key in EDGE_KEYS.items():
        if p in edges:
            rate = edges[p]["equivalent"]
            out.append((key, pct(rate), "%",
                        f"Trials equal to full recomputation when each dependency edge is dropped from impact "
                        f"analysis with probability {p:.2f} ({counts(rate)}; {result.edge_seeds} seeds per case)"))
    if 0.10 in edges:
        rate = edges[0.10]["decision_equal"]
        out.append(("PDO_EDGE10_DECISION_PCT", pct(rate), "%",
                    f"Trials at edge-drop probability 0.10 whose recommendation, readiness and actionable still equal "
                    f"full recomputation ({counts(rate)})"))
    for outcome in OUTCOMES:
        n = sum((c.computed or {}).get("outcome") == outcome for c in result.cases)
        out.append((f"PDO_BENCH_OUTCOME_{outcome.upper()}", str(n), "cases",
                    f"Benchmark cases in outcome class {outcome}"))
    out += example_values(result)
    out += sensitivity_values(root)
    out += [("PDO_SCHEMA_CLASSES", str(schema_classes(root / "schema" / "pdo.yaml")), "classes",
             "Classes in the LinkML decision schema (schema/pdo.yaml and its imports)"),
            ("PDO_EXAMPLE_YAML_LINES", str(suite_yaml_lines(suite.program_dir)), "lines",
             "Non-blank, non-comment YAML lines authored for the vehicle program (program.yaml, baseline, "
             "change sets)")]
    return sorted(out)


def example_values(result: SuiteResult) -> list[tuple[str, str, str, str]]:
    out = []
    flips = result.flips
    if flips is not None:
        for alt, u in sorted(flips.utilities.items()):
            name = alt.split(":", 1)[-1]
            out.append((f"PDO_EX_U_{name}", frac(u, 6), "1",
                        f"Utility of {alt} in the worked trade study ({flips.episode})"))
        cost = nearest_flip(flips.flips, "obs:B.cost.value")
        out.append(("PDO_EX_FLIP_COST_B", frac(cost), "MUSD",
                    f"B unit cost at which the recommendation flips from B to A ({flips.episode}, pdo.flip)"))
        weight = nearest_flip(flips.weight_flips, "crit:cost.weight", "crit:reliability")
        out.append(("PDO_EX_FLIP_WEIGHT", frac(weight, 3), "1",
                    f"Cost weight, traded against reliability, at which the recommendation flips from B to A "
                    f"({flips.episode}, closed form)"))
    if result.timeline:
        children = [r for r in result.timeline if r.parent is not None]
        flipped = [r for r in children if r.outcome in ("changed_winner", "empty_feasible_set")]
        verified = report.mx.fraction(all(r.verified.values()) for r in children)
        matches = report.mx.fraction(r.matches_expected for r in result.timeline if r.expected is not None)
        cutoff = report.mx.pooled((r.computed - r.executed["cutoff"] for r in children), (r.computed for r in children))
        out += [("PDO_PROGRAM_EPISODES", str(len(result.timeline)), "episodes",
                 "Episodes in the vehicle decision program (baseline, main line and branches)"),
                ("PDO_PROGRAM_FLIPS", str(len(flipped)), "episodes",
                 "Program episodes whose recommended set differs from their parent's (" +
                 ", ".join(r.episode for r in flipped) + ")"),
                ("PDO_PROGRAM_VERIFIED_PCT", pct(verified), "%",
                 f"Program episodes whose selective refresh (impact and cutoff) equals full recomputation "
                 f"({counts(verified)})"),
                ("PDO_PROGRAM_EXPECTED_PCT", pct(matches), "%",
                 f"Program episodes matching their hand-computed expected outputs ({counts(matches)})"),
                ("PDO_PROGRAM_SAVINGS_PCT", pct(cutoff), "%",
                 "Computed-node evaluations avoided by cutoff refresh across the program's episodes")]
    return out


def sensitivity_values(root: Path) -> list[tuple[str, str, str, str]]:
    """pdo.sensitivity on the Fires example (seeded, so deterministic) and the vehicle tornado."""
    from pdo.episodes import Program
    from pdo.sensitivity import analyze

    fires = Program.load(root / "examples" / "fires_sensor")
    state = fires.materialize(fires.baseline_episode)
    full = analyze(state, fires.base_dir, samples=256)
    voi, glob, torn = full["value_of_information"], full["global"], full["tornado"]
    top = voi["parameters"][0]
    margin_index = next(r["margin"] for r in glob["indices"] if r["parameter"] == top["parameter"])
    rcs = analyze(state, fires.base_dir, "asm:threat-rcs-factor.value", samples=200)["response"]["shape"]
    shape = rcs["alt:new-array"]
    vehicle = Program.load(root / "examples" / "vehicle")
    vtorn = analyze(vehicle.materialize(vehicle.baseline_episode), vehicle.base_dir)["tornado"]
    where = "examples/fires_sensor, pdo sensitivity"
    return [
        ("PDO_SENS_FIRES_INPUTS", str(len(glob["indices"])), "inputs",
         f"Uncertain inputs analysed jointly in the Fires example ({where})"),
        ("PDO_SENS_FIRES_SAMPLES", str(glob["samples"]), "samples",
         f"Samples per Saltelli matrix ({glob['evaluations']} kernel probes; {where})"),
        ("PDO_SENS_FIRES_EVPI", f"{voi['evpi']:.4f}", "utility",
         f"Expected value of perfect information about every uncertain input ({where})"),
        ("PDO_SENS_FIRES_TOP_INPUT", top["parameter"], "input",
         f"Input whose uncertainty is most valuable to resolve (highest EVPPI; {where})"),
        ("PDO_SENS_FIRES_TOP_EVIDENCE", top["evidence"] or "", "evidence",
         f"Evidence item that would resolve the top input ({where})"),
        ("PDO_SENS_FIRES_TOP_EVPPI", f"{top['evppi']:.4f}", "utility",
         f"EVPPI of the top input ({where})"),
        ("PDO_SENS_FIRES_TOP_EVPPI_SHARE_PCT", f"{100 * top['evppi'] / voi['evpi']:.0f}", "%",
         f"Top input's EVPPI as a share of EVPI ({where})"),
        ("PDO_SENS_FIRES_TOP_TOTAL_INDEX", f"{margin_index['total']:.2f}", "fraction",
         f"Sobol total index of the top input for the decision margin ({where})"),
        ("PDO_SENS_FIRES_SIGNIFICANT_INPUTS", str(len(voi["collect_next"])), "inputs",
         f"Inputs whose EVPPI exceeds the shuffled-binning noise floor ({where})"),
        ("PDO_SENS_TORNADO_EXECUTED_PCT", f"{100 * torn['cost']['executed_fraction']:.1f}", "%",
         f"Share of computed nodes each one-at-a-time probe re-ran, against a full run ({where})"),
        ("PDO_SENS_RCS_ELASTICITY", f"{shape['elasticity']:.3f}", "1",
         "Elasticity of the new array's utility to the threat RCS factor (radar equation, range ~ RCS^0.25)"),
        ("PDO_SENS_RCS_NONLINEAR_PCT", f"{100 * shape['relative_deviation']:.0f}", "%",
         "Largest departure of the new array's utility from linear over the RCS range, as a share of its rise"),
        ("PDO_SENS_VEHICLE_TOP_INPUT", vtorn["rows"][0]["parameter"], "input",
         "Input with the largest swing of the vehicle decision margin (tornado, one at a time)"),
    ]


def table(result: SuiteResult, root: Path) -> str:
    return report.tsv(HEADER, [(*row, result.source, COMMAND) for row in values(result, root)])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--suite", type=Path, default=ROOT / "bench" / "vehicle")
    parser.add_argument("--results", type=Path, default=ROOT / "results")
    parser.add_argument("--proposal-dir", type=Path, default=None,
                        help="Also copy the table here as pdo_values.tsv.")
    args = parser.parse_args(argv)
    result = run_suite(args.suite)
    content = table(result, ROOT)
    path = args.results / "proposal" / "pdo_values.tsv"
    targets = [path] + ([args.proposal_dir / "pdo_values.tsv"] if args.proposal_dir else [])
    for target in targets:
        written = report.write_if_changed(target, content)
        print(f"{target}: {'written' if written else 'unchanged'} ({content.count(chr(10)) - 1} values)")
    if not result.passed:
        print("warning: the benchmark gate did not pass (see `pdo bench check`)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
