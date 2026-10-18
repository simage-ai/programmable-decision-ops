"""Benchmark tables and figures, written deterministically and only when their content changed.

Tables go to <results>/bench/<suite>/ as TSV: rows are sorted, numbers have fixed precision, an
undefined rate (0/0) is NA, and every machine-dependent number is confined to timing.tsv, so the other
files are byte-identical across runs and machines and can be committed and diffed.

    cases.tsv          one row per case and strategy: correctness, node counts, savings, detection
    labels.tsv         one row per case: each hand label next to the label the kernel computed
    summary.tsv        long format (scope, metric, value, numerator, denominator) for every aggregate
    edge_omission.tsv  per p (and family): how often an impact set built on dropped edges still equals full
    timeline.tsv       every program episode: outcome, U per alternative, refresh cost, verification
    flips.tsv          the baseline's flip points (pdo.flip): numeric inputs and weights
    timing.tsv         wall-clock times (machine-dependent)

Figures (matplotlib, Agg) go to <results>/figures/, drawn in grays with hatches and markers so they
read in grayscale.
"""

from __future__ import annotations

import io
import platform
import statistics
from collections import defaultdict
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from pdo.bench import metrics as mx
from pdo.bench import strategies as st
from pdo.bench.suite import SuiteResult, changed_objects

PRECISION = 6


def fmt(value: Any) -> str:
    if value is None:
        return "NA"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        text = f"{value:.{PRECISION}f}"
        return "0." + "0" * PRECISION if text == "-0." + "0" * PRECISION else text
    if isinstance(value, list | tuple | set | frozenset):
        return ",".join(fmt(v) for v in value) if value else "-"
    return str(value).replace("\t", " ").replace("\n", " ")


def tsv(header: Sequence[str], rows: Iterable[Sequence[Any]]) -> str:
    lines = ["\t".join(header)]
    lines += ["\t".join(fmt(v) for v in row) for row in rows]
    return "\n".join(lines) + "\n"


def write_if_changed(path: Path, content: str | bytes) -> bool:
    """Write `content` unless the file already holds exactly that; returns whether it wrote."""
    data = content.encode("utf-8") if isinstance(content, str) else content
    if path.exists() and path.read_bytes() == data:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return True


# --- tables -------------------------------------------------------------------------------------

CASE_COLUMNS = ("case", "family", "split", "strategy", "equivalent", "sound", "mismatched", "missed", "total_nodes",
                "computed", "changed", "scope", "executed", "unneeded", "savings", "savings_cost", "unneeded_frac",
                "overapprox", "recommended", "readiness", "actionable", "detects_winner_change",
                "detects_readiness_violation", "error")


def cases_table(result: SuiteResult) -> str:
    order = {name: i for i, name in enumerate(st.STRATEGIES)}
    rows = []
    for c in sorted(result.cases, key=lambda c: c.case.id):
        for name, s in sorted(c.strategies.items(), key=lambda kv: order[kv[0]]):
            rows.append((c.case.id, c.case.family, c.case.split, name, s.equivalent, s.sound, len(s.mismatched),
                         len(s.missed), s.total, s.computed, s.changed, s.scope, s.executed, s.unneeded,
                         s.savings.value, s.savings_cost.value, s.unneeded_rate.value, s.overapprox.value,
                         s.recommended, s.readiness, s.actionable, s.detects_winner_change,
                         s.detects_readiness_violation, s.error))
        if not c.strategies:
            rows.append((c.case.id, c.case.family, c.case.split, "none", *([None] * (len(CASE_COLUMNS) - 5)),
                         c.error))
    return tsv(CASE_COLUMNS, rows)


def labels_table(result: SuiteResult) -> str:
    header = ["case", "family", "split", "agrees"]
    for f in mx.LABEL_FIELDS:
        header += [f"{f}_expected", f"{f}_computed"]
    header += ["changed_objects", "error"]
    rows = []
    for c in sorted(result.cases, key=lambda c: c.case.id):
        row: list[Any] = [c.case.id, c.case.family, c.case.split, c.agrees]
        for f in mx.LABEL_FIELDS:
            want = c.case.expect.get(f)
            row += [sorted(want or []) if f == "recommended" else want, (c.computed or {}).get(f)]
        rows.append([*row, changed_objects(c.change), c.error])
    return tsv(header, rows)


def _rate_row(scope: str, metric: str, rate: mx.Rate) -> tuple:
    return scope, metric, rate.value, rate.num, rate.den


def summary_rows(result: SuiteResult) -> list[tuple]:
    rows: list[tuple] = []
    suite = result.suite
    parent = result.parent_graph
    rows += [("suite", "cases", len(suite.cases), None, None), ("suite", "families", len(suite.families), None, None),
             ("suite", "parent_nodes", len(parent.nodes), None, None),
             ("suite", "parent_computed_nodes", len(mx.computed_nodes(parent)), None, None)]
    for split in sorted({c.case.split for c in result.cases}):
        rows.append(("suite", f"cases_{split}", sum(c.case.split == split for c in result.cases), None, None))
    rows.append(_rate_row("labels", "agreement", result.label_agreement))
    for f in mx.LABEL_FIELDS:
        rows.append(_rate_row("labels", f"agreement_{f}", mx.fraction(c.agreement.get(f, False) for c in result.cases)))
    for split in sorted({c.case.split for c in result.cases}):
        rows.append(_rate_row("labels", f"agreement_{split}",
                              mx.fraction(c.agrees for c in result.cases if c.case.split == split)))
    for outcome in mx.OUTCOMES:
        expected = sum(c.case.expect.get("outcome") == outcome for c in result.cases)
        computed = sum((c.computed or {}).get("outcome") == outcome for c in result.cases)
        rows += [("labels", f"expected_{outcome}", expected, None, None),
                 ("labels", f"computed_{outcome}", computed, None, None)]
    for name, summary in result.summary().items():
        for metric, value in summary.items():
            if isinstance(value, mx.Rate):
                rows.append(_rate_row(name, metric, value))
            else:
                rows.append((name, metric, value, None, None))
    timeline = [r for r in result.timeline if r.parent is not None]
    if result.timeline:
        rows += [("timeline", "episodes", len(result.timeline), None, None),
                 ("timeline", "flips", sum(r.outcome in ("changed_winner", "empty_feasible_set") for r in timeline),
                  None, None)]
        for mode in ("impact", "cutoff"):
            rows.append(_rate_row("timeline", f"verified_{mode}", mx.fraction(r.verified[mode] for r in timeline)))
            rows.append(_rate_row("timeline", f"savings_{mode}",
                                  mx.pooled((r.computed - r.executed[mode] for r in timeline),
                                            (r.computed for r in timeline))))
        checked = [r for r in result.timeline if r.expected is not None]
        rows.append(_rate_row("timeline", "matches_expected", mx.fraction(r.matches_expected for r in checked)))
    return rows


def summary_table(result: SuiteResult) -> str:
    return tsv(("scope", "metric", "value", "numerator", "denominator"), summary_rows(result))


def edge_rows(result: SuiteResult) -> list[dict]:
    """Aggregates per (p, family), with family "all" for the whole suite."""
    groups: dict[tuple[float, str], list] = defaultdict(list)
    for c in result.cases:
        for p, _seed, metrics, decision in c.edges:
            groups[(p, "all")].append((metrics, decision))
            groups[(p, c.case.family)].append((metrics, decision))
    out = []
    for (p, family), trials in sorted(groups.items()):
        ms = [m for m, _ in trials]
        out.append({"p": p, "family": family, "trials": len(trials),
                    "equivalent": mx.fraction(m.equivalent for m in ms),
                    "decision_equal": mx.fraction(d for _, d in trials), "sound": mx.fraction(m.sound for m in ms),
                    "dropped_edges_mean": mx.mean(m.dropped_edges for m in ms),
                    "scope_mean": mx.mean(m.scope for m in ms), "executed_mean": mx.mean(m.executed for m in ms)})
    return out


def edge_table(result: SuiteResult) -> str:
    header = ("p", "family", "seeds", "trials", "equivalent", "equivalent_frac", "decision_equal",
              "decision_equal_frac", "sound", "sound_frac", "dropped_edges_mean", "scope_mean", "executed_mean")
    rows = [(r["p"], r["family"], result.edge_seeds, r["trials"], r["equivalent"].num, r["equivalent"].value,
             r["decision_equal"].num, r["decision_equal"].value, r["sound"].num, r["sound"].value,
             r["dropped_edges_mean"], r["scope_mean"], r["executed_mean"]) for r in edge_rows(result)]
    return tsv(header, rows)


def timeline_table(result: SuiteResult) -> str:
    alts = sorted({a for r in result.timeline for a in r.utilities})
    header = ["episode", "parent", "as_of", "trigger", "outcome", "expected_outcome", "recommended", "readiness",
              "actionable", *[f"U_{a}" for a in alts], "bounds", "total_nodes", "computed", "impact",
              "executed_impact", "executed_cutoff", "verified_impact", "verified_cutoff", "matches_expected",
              "mismatches"]
    rows = []
    for r in result.timeline:
        bounds = [f"{a}=[{fmt(lo)},{fmt(hi)}]" for a, (lo, hi) in sorted(r.bounds.items())]
        rows.append([r.episode, r.parent, r.as_of, r.trigger, r.outcome, (r.expected or {}).get("outcome"),
                     r.recommended, r.readiness, r.actionable, *[r.utilities.get(a) for a in alts], bounds, r.total,
                     r.computed, r.impact, r.executed["impact"], r.executed["cutoff"], r.verified["impact"],
                     r.verified["cutoff"], r.matches_expected, r.mismatches])
    return tsv(header, rows)


def flip_rows(result: SuiteResult) -> list[dict]:
    if result.flips is None:
        return []
    scans = {s["parameter"]: s for s in result.flips.scans}
    rows = []
    for f in result.flips.flips:
        scan = scans.get(f["parameter"], {})
        rows.append({**f, "counterpart": None, "scan_low": scan.get("scan_low"), "scan_high": scan.get("scan_high")})
    for f in result.flips.weight_flips:
        total = f["baseline_number"] + result.flips.weights[f["counterpart"]]
        rows.append({**f, "scan_low": 0.0, "scan_high": total})
    for scan in result.flips.scans:
        if not scan["flip_count"]:
            rows.append({"parameter": scan["parameter"], "counterpart": None, "direction": "none",
                         "baseline_number": scan["baseline_number"], "scan_low": scan["scan_low"],
                         "scan_high": scan["scan_high"]})
    return sorted(rows, key=lambda r: (r["parameter"], r.get("counterpart") or "", r["direction"],
                                       r.get("flip_number") or 0.0))


def flips_table(result: SuiteResult) -> str:
    header = ("parameter", "counterpart", "baseline", "scan_low", "scan_high", "direction", "flip_number",
              "relative_change", "method", "crossing", "previous_recommendation", "new_recommendation",
              "boundary_kind", "verified")
    rows = []
    for r in flip_rows(result):
        flip, base = r.get("flip_number"), r["baseline_number"]
        rel = None if flip is None or not base else flip / base - 1.0
        rows.append((r["parameter"], r.get("counterpart"), base, r["scan_low"], r["scan_high"], r["direction"], flip,
                     rel, r.get("method"), r.get("crossing"), r.get("previous_recommendation"),
                     r.get("new_recommendation"), r.get("boundary_kind"), r.get("verified")))
    return tsv(header, rows)


def timing_table(result: SuiteResult) -> str:
    header = ("scope", "runs", "total_ms", "median_ms", "max_ms", "machine", "python")
    machine, python = f"{platform.system()}-{platform.machine()}", platform.python_version()
    durations: dict[str, list[float]] = defaultdict(list)
    for c in result.cases:
        for name, s in c.strategies.items():
            durations[name].append(s.duration_ms)
        for p, _seed, s, _ in c.edges:
            durations[f"edge_omission@{p:.2f}"].append(s.duration_ms)
    order = {name: i for i, name in enumerate(st.STRATEGIES)}
    rows = []
    for name in sorted(durations, key=lambda n: (order.get(n, len(order)), n)):
        ds = durations[name]
        rows.append((name, len(ds), sum(ds), statistics.median(ds), max(ds), machine, python))
    rows.append(("suite_wall", 1, result.seconds * 1000, result.seconds * 1000, result.seconds * 1000, machine, python))
    return tsv(header, rows)


TABLES = {"cases.tsv": cases_table, "labels.tsv": labels_table, "summary.tsv": summary_table,
          "edge_omission.tsv": edge_table, "timeline.tsv": timeline_table, "flips.tsv": flips_table,
          "timing.tsv": timing_table}


def write_tables(result: SuiteResult, results_dir: Path) -> dict[str, bool]:
    """Write every table to results_dir/bench/<suite>/; returns {path: written}."""
    out_dir = Path(results_dir) / "bench" / result.suite.name
    written = {}
    for name, render in TABLES.items():
        if name == "flips.tsv" and result.flips is None:
            continue
        if name == "timeline.tsv" and not result.timeline:
            continue
        if name == "edge_omission.tsv" and not result.edge_p:
            continue
        path = out_dir / name
        written[str(path)] = write_if_changed(path, render(result))
    return written


# --- figures ------------------------------------------------------------------------------------

GRAYS = ("0.15", "0.45", "0.7", "0.88")
HATCHES = ("", "//", "..", "xx")
MARKERS = ("o", "s", "^", "D", "v", "P")
LINESTYLES = ("-", "--", ":", "-.")
READINESS_SHADE = {"ready": "white", "pending": "0.88", "blocked": "0.7"}
READINESS_HATCH = {"ready": "", "pending": "", "blocked": "///"}


def _pyplot():
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update({"font.size": 9, "axes.spines.top": False, "axes.spines.right": False,
                         "svg.hashsalt": "pdo", "figure.dpi": 100})
    return plt


def _png(fig) -> bytes:
    buf = io.BytesIO()
    # No Software/date metadata, so the bytes depend only on the drawing.
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight", metadata={"Software": None})
    return buf.getvalue()


def _short(alt: str) -> str:
    return alt.split(":", 1)[-1]


def figure_impact(result: SuiteResult):
    plt = _pyplot()
    cases = sorted(result.cases, key=lambda c: (c.case.family, c.case.id))
    cases = [c for c in cases if "impact" in c.strategies]
    fig, ax = plt.subplots(figsize=(7.5, 0.22 * len(cases) + 1.4))
    ys = list(range(len(cases)))[::-1]
    h = 0.27
    series = [("computed nodes", [c.strategies["impact"].computed for c in cases]),
              ("impact set", [c.strategies["impact"].scope for c in cases]),
              ("changed", [c.strategies["impact"].changed for c in cases])]
    for i, (label, values) in enumerate(series):
        ax.barh([y + (1 - i) * h for y in ys], values, height=h, color=GRAYS[2 - i], edgecolor="0.1",
                linewidth=0.4, hatch=HATCHES[i], label=label)
    ax.set_yticks(ys, [f"{c.case.id} [{c.case.family}]" for c in cases], fontsize=7)
    ax.set_xlabel("nodes (computed nodes only: sources and the clock excluded)")
    ax.set_title(f"Impact set vs changed nodes per case ({result.suite.name}, parent {result.suite.parent})", pad=22)
    ax.legend(loc="lower center", bbox_to_anchor=(0.5, 1.0), ncols=3, frameon=False)
    ax.set_ylim(-0.6, len(cases) - 0.4)
    return fig


def figure_savings(result: SuiteResult):
    plt = _pyplot()
    families = result.suite.families
    by_family: dict[str, dict[str, list]] = {f: defaultdict(list) for f in families}
    for c in result.cases:
        for name in ("impact", "cutoff"):
            if name in c.strategies:
                by_family[c.case.family][name].append(c.strategies[name])
    bars = [("impact, nodes", "impact", "savings"), ("impact, cost-weighted", "impact", "savings_cost"),
            ("cutoff, nodes", "cutoff", "savings"), ("cutoff, cost-weighted", "cutoff", "savings_cost")]
    fig, ax = plt.subplots(figsize=(9, 4))
    width = 0.8 / len(bars)
    for i, (label, name, attr) in enumerate(bars):
        values = []
        for f in families:
            ms = by_family[f][name]
            num = sum(getattr(m, attr).num for m in ms)
            den = sum(getattr(m, attr).den for m in ms)
            values.append(100.0 * num / den if den else 0.0)
        xs = [j + (i - (len(bars) - 1) / 2) * width for j in range(len(families))]
        ax.bar(xs, values, width=width, color=GRAYS[i], edgecolor="0.1", linewidth=0.4, hatch=HATCHES[i], label=label)
    ax.set_xticks(range(len(families)), [f"{f.replace('_', ' ')} (n={sum(c.case.family == f for c in result.cases)})"
                                         for f in families], fontsize=8, rotation=25, ha="right",
                  rotation_mode="anchor")
    ax.set_ylabel("recomputation avoided (%)")
    ax.set_ylim(0, 100)
    ax.set_title("Savings over full recomputation by perturbation family (pooled over cases)", pad=22)
    ax.legend(ncols=4, frameon=False, fontsize=8, loc="lower center", bbox_to_anchor=(0.5, 1.0))
    return fig


def figure_edges(result: SuiteResult):
    plt = _pyplot()
    rows = [r for r in edge_rows(result) if r["family"] == "all"]
    impact = result.summary()["impact"]
    decision0 = mx.fraction(mx.decision_matches(c.strategies["impact"], c.full) for c in result.cases
                            if "impact" in c.strategies)
    ps = [0.0, *[r["p"] for r in rows]]
    fig, ax = plt.subplots(figsize=(5.5, 3.6))
    series = [("all node values equal full", impact["equivalent"], "equivalent"),
              ("recommendation, readiness, actionable equal full", decision0, "decision_equal"),
              ("sound (no changed node reused)", impact["sound"], "sound")]
    # In impact mode a run is equivalent exactly when it is sound, so the two curves coincide; draw one.
    if impact["sound"] == impact["equivalent"] and all(r["sound"] == r["equivalent"] for r in rows):
        series = [("all node values equal full (= sound)", *series[0][1:]), series[1]]
    for i, (label, at_zero, key) in enumerate(series):
        ys = [at_zero.pct, *[r[key].pct for r in rows]]
        ax.plot([100 * p for p in ps], ys, color=GRAYS[min(i, 2)], linestyle=LINESTYLES[i], marker=MARKERS[i],
                label=label)
    trials = rows[0]["trials"] if rows else 0
    ax.set_xlabel("probability each dependency edge is dropped from impact analysis (%)")
    ax.set_ylabel(f"trials (%), {trials} per p")
    ax.set_ylim(0, 102)
    ax.set_xticks([100 * p for p in ps])
    ax.set_title("Selective re-evaluation with missing dependency edges")
    ax.legend(frameon=False, fontsize=8, loc="lower left")
    return fig


def _mainline(result: SuiteResult) -> list:
    """The episodes from the baseline to the last one in program order that has no children (the main line)."""
    rows = {r.episode: r for r in result.timeline}
    children = defaultdict(list)
    for r in result.timeline:
        if r.parent:
            children[r.parent].append(r.episode)
    tips = [r for r in result.timeline if not children[r.episode]]
    if not tips:
        return []
    tip = max(tips, key=lambda r: (r.as_of, r.episode))
    chain = [tip]
    while chain[-1].parent:
        chain.append(rows[chain[-1].parent])
    return chain[::-1]


def figure_timeline(result: SuiteResult):
    plt = _pyplot()
    from matplotlib.patches import Patch

    chain = _mainline(result)
    alts = sorted({a for r in chain for a in r.utilities})
    fig, ax = plt.subplots(figsize=(8, 3.8))
    xs = list(range(len(chain)))
    for x, r in zip(xs, chain, strict=True):
        ax.axvspan(x - 0.5, x + 0.5, facecolor=READINESS_SHADE[r.readiness], hatch=READINESS_HATCH[r.readiness],
                   edgecolor="0.55", linewidth=0, zorder=0)
    for i, alt in enumerate(alts):
        gray = GRAYS[min(i, 2)]
        ys = [float("nan") if r.utilities.get(alt) is None else r.utilities[alt] for r in chain]
        ax.plot(xs, ys, color=gray, linestyle=LINESTYLES[i], label=f"U {_short(alt)}", zorder=3)
        for filled in (True, False):  # open marker: the alternative is infeasible in that episode
            pts = [(x, y) for x, y, r in zip(xs, ys, chain, strict=True)
                   if (r.feasibility.get(alt) != "fail") == filled]
            if pts:
                ax.plot(*zip(*pts, strict=True), linestyle="none", marker=MARKERS[i], markersize=5, color=gray,
                        markerfacecolor=gray if filled else "white", zorder=3)
        for x, r in zip(xs, chain, strict=True):
            if alt in r.bounds:
                lo, hi = r.bounds[alt]
                ax.vlines(x, lo, hi, color=GRAYS[min(i, 2)], linestyle=LINESTYLES[i], linewidth=1.6, zorder=3)
                ax.plot([x, x], [lo, hi], linestyle="none", marker="_", color=GRAYS[min(i, 2)], zorder=3)
            if alt in r.recommended:
                y = r.utilities.get(alt)
                ax.plot(x, y, marker="o", markersize=11, markerfacecolor="none", markeredgecolor="black", zorder=4)
    ax.set_xticks(xs, [f"{r.episode.split(':', 1)[-1]} {r.trigger or 'baseline'}" for r in chain], fontsize=7,
                  rotation=35, ha="right", rotation_mode="anchor")
    for x, r in zip(xs, chain, strict=True):
        if r.actionable:
            ax.annotate("A", (x, 1.0), xycoords=("data", "axes fraction"), ha="center", va="bottom", fontsize=8,
                        fontweight="bold")
    ax.set_ylabel("utility U (bar: bounds when unknown)")
    ax.set_xlim(-0.5, len(chain) - 0.5)
    ax.set_title("Program timeline: utility by episode\n(ring = recommended, open marker = infeasible, "
                 "A = actionable)", pad=14)
    handles, labels_ = ax.get_legend_handles_labels()
    for state in ("ready", "pending", "blocked"):
        handles.append(Patch(facecolor=READINESS_SHADE[state], edgecolor="0.4", hatch=READINESS_HATCH[state]))
        labels_.append(state)
    ax.legend(handles, labels_, frameon=False, fontsize=7, loc="upper left", bbox_to_anchor=(1.0, 1.0))
    return fig


def figure_tornado(result: SuiteResult):
    plt = _pyplot()
    rows = flip_rows(result)
    nearest: dict[str, dict[str, dict]] = defaultdict(dict)
    scan: dict[str, tuple[float, float, float]] = {}
    for r in rows:
        key = r["parameter"] + (f" vs {r['counterpart']}" if r.get("counterpart") else "")
        base = r["baseline_number"]
        scan[key] = (base, r["scan_low"], r["scan_high"])
        if r["direction"] in ("above", "below"):
            current = nearest[key].get(r["direction"])
            if current is None or abs(r["flip_number"] - base) < abs(current["flip_number"] - base):
                nearest[key][r["direction"]] = r

    def rel(x: float, base: float) -> float:
        return 100.0 * (x / base - 1.0) if base else 100.0 * x

    def reach(key: str) -> float:
        base = scan[key][0]
        found = [abs(rel(f["flip_number"], base)) for f in nearest[key].values()]
        return min(found) if found else float("inf")

    keys = sorted(scan, key=lambda k: (reach(k), k), reverse=True)
    fig, ax = plt.subplots(figsize=(8, 0.34 * len(keys) + 1.4))
    for y, key in enumerate(keys):
        base, low, high = scan[key]
        ax.barh(y, rel(high, base) - rel(low, base), left=rel(low, base), height=0.7, color="0.94", edgecolor="0.75",
                linewidth=0.5, zorder=1)
        if not nearest[key]:
            ax.annotate("no flip in the scanned range", (0, y), xytext=(4, 0), textcoords="offset points",
                        va="center", fontsize=7, color="0.35")
        for direction, flip in nearest[key].items():
            x = rel(flip["flip_number"], base)
            new = ",".join(_short(a) for a in flip.get("new_recommendation") or []) or "none"
            hatch = "" if flip.get("boundary_kind") == "preference" else "///"
            ax.barh(y, x, height=0.7, color="0.35" if direction == "above" else "0.6", edgecolor="0.1",
                    linewidth=0.4, hatch=hatch, zorder=2)
            ax.annotate(f"{flip['flip_number']:.4g} -> {new}", (x, y), xytext=(3 if x >= 0 else -3, 0),
                        textcoords="offset points", ha="left" if x >= 0 else "right", va="center", fontsize=7)
    ax.axvline(0, color="black", linewidth=0.8)
    ax.set_yticks(range(len(keys)), keys, fontsize=7)
    ax.set_xlabel("change from baseline value at the nearest flip (%); light band = scanned range")
    episode = result.flips.episode if result.flips else ""
    ax.set_title(f"What flips the recommendation at {episode} (hatched: feasibility or empty-set boundary)")
    lim = max([30.0, *[abs(rel(f["flip_number"], scan[k][0])) * 1.45 for k in keys for f in nearest[k].values()]])
    ax.set_xlim(-lim, lim)
    return fig


FIGURES = {"impact_per_case.png": figure_impact, "savings_by_family.png": figure_savings,
           "edge_omission.png": figure_edges, "timeline.png": figure_timeline, "flip_tornado.png": figure_tornado}


def write_figures(result: SuiteResult, results_dir: Path) -> dict[str, bool]:
    """Draw every figure to results_dir/figures/; returns {path: written}."""
    plt = _pyplot()
    out_dir = Path(results_dir) / "figures"
    written = {}
    for name, draw in FIGURES.items():
        if (name == "flip_tornado.png" and result.flips is None) or (name == "timeline.png" and not result.timeline) \
                or (name == "edge_omission.png" and not result.edge_p):
            continue
        fig = draw(result)
        try:
            written[str(out_dir / name)] = write_if_changed(out_dir / name, _png(fig))
        finally:
            plt.close(fig)
    return written


# --- JSON and text summaries ----------------------------------------------------------------------

def _jsonable(value: Any) -> Any:
    if isinstance(value, mx.Rate):
        return {"value": value.value, "numerator": value.num, "denominator": value.den}
    return value


def summary_dict(result: SuiteResult) -> dict:
    """The suite's headline numbers as JSON-ready data (what `pdo bench run --json` prints)."""
    from pdo.bench.suite import disagreements

    timeline = [r for r in result.timeline if r.parent is not None]
    return {
        "suite": result.suite.name, "suite_digest": result.suite.digest, "kernel_version": result.kernel_version,
        "program": str(result.suite.program_dir), "parent": result.suite.parent, "cases": len(result.cases),
        "families": result.suite.families, "parent_nodes": len(result.parent_graph.nodes),
        "parent_computed_nodes": len(mx.computed_nodes(result.parent_graph)),
        "label_agreement": _jsonable(result.label_agreement), "disagreements": disagreements(result),
        "strategies": {name: {k: _jsonable(v) for k, v in s.items()} for name, s in result.summary().items()},
        "edge_omission": [{k: _jsonable(v) for k, v in r.items()} for r in edge_rows(result) if r["family"] == "all"],
        "timeline": {"episodes": len(result.timeline),
                     "verified": {mode: _jsonable(mx.fraction(r.verified[mode] for r in timeline))
                                  for mode in ("impact", "cutoff")},
                     "matches_expected": _jsonable(mx.fraction(r.matches_expected for r in result.timeline
                                                               if r.expected is not None)),
                     "mismatches": {r.episode: r.mismatches for r in result.timeline if r.mismatches}},
        "gate": {k: _jsonable(v) for k, v in result.gate().items()}, "passed": result.passed,
        "seconds": result.seconds,
    }


def _pct(rate: mx.Rate) -> str:
    return "NA" if rate.pct is None else f"{rate.pct:.1f}%"


def _pr(p: mx.Rate, r: mx.Rate) -> str:
    def one(x: mx.Rate) -> str:
        return "NA" if x.value is None else f"{x.value:.2f}"

    return f"{one(p)}/{one(r)} ({p.den:g},{r.den:g})"


def summary_text(result: SuiteResult) -> str:
    from pdo.bench.suite import disagreements

    parent = result.parent_graph
    lines = [f"suite {result.suite.name}: {len(result.cases)} cases in {len(result.suite.families)} families, parent "
             f"{result.suite.parent} ({len(parent.nodes)} nodes, {len(mx.computed_nodes(parent))} computed)",
             f"labels agree: {result.label_agreement.num:g}/{result.label_agreement.den:g} "
             f"({_pct(result.label_agreement)})", "",
             f"{'strategy':13} {'equiv':>7} {'sound':>7} {'savings':>8} {'cost':>7} {'unneeded':>8}  "
             f"{'winner P/R (n)':>18}  {'ready-viol P/R (n)':>18}"]
    for name, s in result.summary().items():
        lines.append(f"{name:13} {_pct(s['equivalent']):>7} {_pct(s['sound']):>7} {_pct(s['savings']):>8} "
                     f"{_pct(s['savings_cost']):>7} {_pct(s['unneeded']):>8}  "
                     f"{_pr(s['winner_precision'], s['winner_recall']):>18}  "
                     f"{_pr(s['readiness_violation_precision'], s['readiness_violation_recall']):>18}")
    edges = [r for r in edge_rows(result) if r["family"] == "all"]
    if edges:
        lines.append("")
        for r in edges:
            lines.append(f"edge omission p={r['p']:.2f}: equivalent {_pct(r['equivalent'])}, decision equal "
                         f"{_pct(r['decision_equal'])}, sound {_pct(r['sound'])} ({r['trials']} trials)")
    timeline = [r for r in result.timeline if r.parent is not None]
    if result.timeline:
        checked = [r for r in result.timeline if r.expected is not None]
        verified = {mode: mx.fraction(r.verified[mode] for r in timeline) for mode in ("impact", "cutoff")}
        matches = mx.fraction(r.matches_expected for r in checked)
        lines += ["", f"timeline: {len(result.timeline)} episodes; selective = full in impact "
                      f"{verified['impact'].num:g}/{verified['impact'].den:g}, cutoff "
                      f"{verified['cutoff'].num:g}/{verified['cutoff'].den:g}; matches expected.yaml "
                      f"{matches.num:g}/{matches.den:g}"]
        for r in result.timeline:
            if r.mismatches:
                lines.append(f"  {r.episode}: differs from expected.yaml in {', '.join(r.mismatches)}")
    found = disagreements(result)
    if found:
        lines += ["", "label disagreements (findings: neither labels nor kernel are edited):"]
        for d in found:
            detail = "; ".join(f"{f} expected {d['expected'][f]!r}, computed {d['computed'][f]!r}" for f in d["fields"])
            lines.append(f"  {d['case']} [{d['family']}/{d['split']}]: {detail}" + (f" ({d['error']})"
                                                                                  if d["error"] else ""))
    return "\n".join(lines) + "\n"
