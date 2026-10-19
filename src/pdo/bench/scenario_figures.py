"""Paper figures for the scenarios (pdo.bench.scenarios): one per scenario, 6.5 in wide, as PNG and PDF, each
with a caption (caption.md) written from the same results.

The figures carry panel letters, axis labels and legends only; the caption says what they show. The
alternatives keep one color and marker each in every figure (the first three slots of a colorblind-validated
categorical palette, with markers so no series depends on color); readiness uses the status tints. Files
carry no dates or software metadata, so they are byte-identical across runs on one machine.
"""

from __future__ import annotations

import io
from pathlib import Path

from pdo.bench.report import write_if_changed

WIDTH = 6.5
SERIES = ("#2a78d6", "#eb6834", "#1baf7a")  # blue, orange, aqua: validated all-pairs (light surface)
MARKERS = ("o", "s", "^")
INK, INK2, MUTED, GRID, AXIS = "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7"
WARNING, CRITICAL, GOOD = "#fab219", "#d03b3b", "#0ca30c"
NEUTRAL = "#c3c2b7"


def _plt():
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update({
        "font.size": 7.5, "axes.titlesize": 8.5, "axes.titleweight": "bold", "axes.titlelocation": "left",
        "axes.labelsize": 7.5, "axes.labelcolor": INK2, "axes.edgecolor": AXIS, "axes.linewidth": 0.8,
        "axes.spines.top": False, "axes.spines.right": False, "xtick.color": INK2, "ytick.color": INK2,
        "xtick.labelsize": 7, "ytick.labelsize": 7, "xtick.major.size": 2.5, "ytick.major.size": 2.5,
        "legend.fontsize": 6.8, "legend.frameon": False, "lines.linewidth": 1.6, "lines.markersize": 4.5,
        "text.color": INK, "svg.hashsalt": "pdo", "pdf.fonttype": 42, "figure.dpi": 100,
    })
    return plt


def _panel(ax, letter: str) -> None:
    ax.set_title(f"({letter})", loc="left", pad=4)


def _grid(ax, axis: str = "y") -> None:
    ax.grid(axis=axis, color=GRID, linewidth=0.6)
    ax.set_axisbelow(True)


def _short(alt: str) -> str:
    return alt.split(":", 1)[-1]


def _end_label(ax, x, y, text: str) -> None:
    ax.annotate(text, (x, y), xytext=(3, 0), textcoords="offset points", va="center", fontsize=7, color=INK2)


def _save(fig, d: Path, name: str) -> list[Path]:
    out = []
    for ext, kw in (("png", {"dpi": 220, "metadata": {"Software": None}}),
                    ("pdf", {"metadata": {"CreationDate": None, "ModDate": None, "Producer": None,
                                          "Creator": None}})):
        buf = io.BytesIO()
        fig.savefig(buf, format=ext, bbox_inches="tight", pad_inches=0.04, **kw)
        path = d / f"{name}.{ext}"
        if write_if_changed(path, buf.getvalue()):
            out.append(path)
    import matplotlib.pyplot as plt

    plt.close(fig)
    return out


def write_figure(result, d: str | Path) -> list[Path]:
    """The scenario's figure (PNG, PDF) and caption.md; a scenario without a figure writes nothing."""
    draw = FIGURES.get(result.name)
    if draw is None:
        return []
    d = Path(d)
    written = _save(draw(result), d, result.name)
    if write_if_changed(d / "caption.md", CAPTIONS[result.name](result) + "\n"):
        written.append(d / "caption.md")
    return written


def _lines(ax, xs, ys_by_alt, feasible_by_alt, alts) -> None:
    """U per alternative against x: solid where feasible, dotted where not; a letter at the right end."""
    for i, a in enumerate(alts):
        ys, fs = ys_by_alt[a], feasible_by_alt[a]
        seg_x, seg_y, prev = [], [], None
        for x, y, f in zip(xs, ys, fs, strict=True):
            ok = f != "fail"
            if prev is not None and ok != prev:
                ax.plot(seg_x, seg_y, color=SERIES[i], linewidth=1.6 if prev else 1.1, linestyle="-" if prev else ":")
                seg_x, seg_y = seg_x[-1:], seg_y[-1:]
            seg_x.append(x)
            seg_y.append(y)
            prev = ok
        ax.plot(seg_x, seg_y, color=SERIES[i], linewidth=1.6 if prev else 1.1, linestyle="-" if prev else ":")
        _end_label(ax, xs[-1], ys[-1], _short(a))


# --- 1. trade study ---------------------------------------------------------------------------------------

def figure_trade_study(r):
    plt = _plt()
    alts = r.data["alternatives"]
    fig, axes = plt.subplots(1, 3, figsize=(WIDTH, 2.1), gridspec_kw={"width_ratios": [0.8, 1.2, 1.2], "wspace": 0.4})
    ax = axes[0]
    rows = r.data["utilities"]
    ys = list(range(len(rows)))[::-1]
    for i, (y, row) in enumerate(zip(ys, rows, strict=True)):
        ok = row[2] == "pass"
        ax.barh(y, row[1], height=0.56, color=SERIES[i] if ok else "white", edgecolor=SERIES[i], linewidth=1.0,
                hatch=None if ok else "////")
    ax.set_yticks(ys, [_short(row[0]) for row in rows])
    ax.set_xlim(0, 0.7)
    ax.set_xticks([0, 0.2, 0.4, 0.6])
    ax.set_xlabel("U")
    _panel(ax, "a")
    _grid(ax, "x")

    n = len(alts)
    for ax, table, key, xlabel, letter in ((axes[1], "cost_response", "cost", "B unit cost (MUSD)", "b"),
                                           (axes[2], "weight_response", "weight", "cost weight", "c")):
        rows_ = r.tables[table][1]
        xs = [row[0] for row in rows_]
        u = {a: [row[1 + i] for row in rows_] for i, a in enumerate(alts)}
        if table == "cost_response":
            f = {a: [row[1 + n + i] for row in rows_] for i, a in enumerate(alts)}
        else:  # the weights change no requirement, so feasibility is the baseline's
            f = {a: [rows[i][2]] * len(xs) for i, a in enumerate(alts)}
        flip, now = r.data[key]["flip"], r.data[key]["now"]
        ax.axvspan(flip, xs[-1], color=SERIES[0], alpha=0.07, linewidth=0)
        ax.axvline(flip, color=INK, linewidth=0.8)
        _lines(ax, xs, u, f, alts)
        b = alts.index("alt:B")
        nearest = min(range(len(xs)), key=lambda k: abs(xs[k] - now))
        ax.plot(now, u["alt:B"][nearest], marker="o", markersize=5, markerfacecolor="white",
                markeredgecolor=SERIES[b], markeredgewidth=1.2, zorder=4)
        ax.set_xlabel(xlabel)
        ax.set_ylabel("U")
        _panel(ax, letter)
        _grid(ax)
    return fig


# --- 2. program over refresh cycles -----------------------------------------------------------------------

def figure_program_refresh(r):
    plt = _plt()
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch

    alts = r.data["alternatives"]
    chain = r.data["chain"]
    col = {h: i for i, h in enumerate(r.tables["main_line"][0])}
    xs = list(range(len(chain)))
    fig, (ax, bx) = plt.subplots(2, 1, figsize=(WIDTH, 3.6), sharex=True,
                                 gridspec_kw={"height_ratios": [1.4, 1], "hspace": 0.25})
    for x, row in zip(xs, chain, strict=True):
        state = row[col["readiness"]]
        if state != "ready":
            ax.axvspan(x - 0.5, x + 0.5, color=WARNING if state == "pending" else CRITICAL,
                       alpha=0.16 if state == "pending" else 0.13, linewidth=0, zorder=0)
    for i, a in enumerate(alts):
        ys = [row[col[f"U {a}"]] for row in chain]
        pts = [(x, y) for x, y in zip(xs, ys, strict=True) if y is not None]
        ax.plot(*zip(*pts, strict=True), color=SERIES[i], marker=MARKERS[i], markersize=3.6, linewidth=1.4,
                linestyle=":" if a == "alt:C" else "-", zorder=3)
        _end_label(ax, *pts[-1], _short(a))
        for x, row in zip(xs, chain, strict=True):
            bounds = r.data["bounds"].get(row[col["episode"]], {}).get(a)
            if bounds:
                ax.vlines(x, *bounds, color=SERIES[i], linewidth=1.2, zorder=3)
                ax.plot([x, x], bounds, linestyle="none", marker="_", markersize=6, color=SERIES[i], zorder=3)
            if a in str(row[col["recommended"]]).split(",") and row[col[f"U {a}"]] is not None:
                ax.plot(x, row[col[f"U {a}"]], marker="o", markersize=9, markerfacecolor="none",
                        markeredgecolor=INK, markeredgewidth=0.9, zorder=4)
    for x, row in zip(xs, chain, strict=True):
        if row[col["actionable"]]:
            ax.plot(x, 1.04, marker="o", markersize=4.5, color=GOOD, transform=ax.get_xaxis_transform(),
                    clip_on=False)
        if row[col["approval_invalidated"]]:
            ax.plot(x, 1.04, marker="x", markersize=5, markeredgewidth=1.4, color=CRITICAL,
                    transform=ax.get_xaxis_transform(), clip_on=False)
    ax.set_ylabel("U")
    _panel(ax, "a")
    _grid(ax)
    handles = [Line2D([], [], marker="o", markersize=8, markerfacecolor="none", markeredgecolor=INK, linestyle="none"),
               Line2D([], [], marker="o", markersize=4.5, color=GOOD, linestyle="none"),
               Line2D([], [], marker="x", markersize=5, markeredgewidth=1.4, color=CRITICAL, linestyle="none"),
               Patch(color=WARNING, alpha=0.3), Patch(color=CRITICAL, alpha=0.25)]
    ax.legend(handles, ["recommended", "actionable", "approval void", "pending", "blocked"], ncols=5,
              loc="lower right", bbox_to_anchor=(1.0, 1.08), handletextpad=0.3, columnspacing=1.2)

    computed = [row[col["computed"]] for row in chain]
    impact = [row[col["impact"]] if row[col["impact"]] is not None else row[col["computed"]] for row in chain]
    executed = [row[col["executed_cutoff"]] for row in chain]
    bx.bar(xs, computed, width=0.68, color=GRID, label="full")
    bx.bar(xs, impact, width=0.68, color=NEUTRAL, label="impact set")
    bx.bar(xs, executed, width=0.68, color=SERIES[0], label="re-evaluated")
    bx.set_ylabel("computed nodes")
    bx.set_ylim(0, max(computed) * 1.08)
    bx.legend(ncols=3, loc="lower right", bbox_to_anchor=(1.0, 1.0))
    _panel(bx, "b")
    _grid(bx)
    bx.set_xticks(xs, [row[col["episode"]].split(":", 1)[-1] for row in chain])
    bx.set_xlim(-0.6, len(chain) - 0.4)
    return fig


# --- 3. perturbations -------------------------------------------------------------------------------------

OUTCOME_ORDER = ("no_material_change", "readiness_only", "recompute_same_winner", "changed_winner",
                 "empty_feasible_set")


def figure_perturbations(r):
    plt = _plt()
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch

    rows = r.tables["cases"][1]
    fig, (ax, bx) = plt.subplots(1, 2, figsize=(WIDTH, 2.3), gridspec_kw={"width_ratios": [1.45, 1], "wspace": 0.6})
    outcomes = [o for o in OUTCOME_ORDER if any(x[2] == o for x in rows)]
    for k, (name, marker, dy) in enumerate((("impact", "o", 0.14), ("cutoff", "s", -0.14))):
        for j, o in enumerate(outcomes):
            pts = sorted(100 * x[5] / x[4] for x in rows if x[2] == o and x[3] == name and x[4])
            ys = [len(outcomes) - 1 - j + dy + 0.05 * ((i % 3) - 1) for i in range(len(pts))]
            ax.plot(pts, ys, linestyle="none", marker=marker, markersize=3.6, color=SERIES[k], alpha=0.85,
                    markeredgecolor="white", markeredgewidth=0.4, label=name if j == 0 else None)
            if pts:
                med = pts[len(pts) // 2]
                ax.plot([med, med], [ys[0] - 0.1, ys[0] + 0.1], color=INK, linewidth=1.0)
    ax.set_yticks(range(len(outcomes))[::-1], [o.replace("_", " ") for o in outcomes])
    ax.axvline(100, color=MUTED, linewidth=0.8, linestyle="--")
    ax.set_xlim(0, 104)
    ax.set_xlabel("computed nodes re-evaluated (%)")
    ax.legend(loc="center right", handletextpad=0.2)
    _panel(ax, "a")
    _grid(ax, "x")

    names = list(r.data["equivalent"])
    ys = list(range(len(names)))[::-1]
    for y, n in zip(ys, names, strict=True):
        num, den = r.data["equivalent"][n]
        dnum, dden = r.data["decision"][n]
        bx.barh(y, 100 * num / den, height=0.55, color=SERIES[0] if n in ("impact", "cutoff") else NEUTRAL)
        bx.plot(100 * dnum / dden, y, marker="D", markersize=3.6, color=INK, linestyle="none")
    bx.set_yticks(ys, [n.replace("_", " ") for n in names])
    bx.set_xlim(0, 105)
    bx.set_xticks([0, 50, 100])
    bx.set_xlabel("cases equal to full (%)")
    bx.legend([Patch(color=NEUTRAL), Line2D([], [], marker="D", markersize=3.6, color=INK, linestyle="none")],
              ["all nodes", "decision outputs"], loc="lower right")
    _panel(bx, "b")
    _grid(bx, "x")
    return fig


# --- 4. report revisions ----------------------------------------------------------------------------------

def figure_report_revisions(r):
    plt = _plt()
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch

    fams = r.data["families"]
    names = sorted(fams, key=lambda f: -fams[f][0])
    fig, (ax, bx) = plt.subplots(1, 2, figsize=(WIDTH, 2.2), gridspec_kw={"width_ratios": [1.6, 1], "wspace": 0.45})
    ys = list(range(len(names)))[::-1]
    for y, f in zip(ys, names, strict=True):
        whole, flagged, changed, _ = fams[f]
        ax.barh(y, whole, height=0.62, color=GRID)
        ax.barh(y, flagged, height=0.62, color=SERIES[0])
        ax.plot(changed, y, marker="D", markersize=3.6, color=INK, linestyle="none")
    ax.set_yticks(ys, [f.replace("_", " ") for f in names])
    ax.set_xlabel("values sent for review")
    ax.legend([Patch(color=GRID), Patch(color=SERIES[0]), Line2D([], [], marker="D", color=INK, linestyle="none",
                                                                   markersize=3.6)],
              ["whole document", "changed section", "changed passage"], loc="lower right")
    _panel(ax, "a")
    _grid(ax, "x")

    values = list(r.data["totals"])
    bx.bar(range(3), values, width=0.6, color=[GRID, SERIES[0], INK2])
    for i, v in enumerate(values):
        bx.text(i, v + 1.5, str(v), ha="center", fontsize=7, color=INK)
    bx.set_xticks(range(3), ["whole\ndocument", "changed\nsection", "changed\npassage"])
    bx.set_ylabel("values")
    bx.set_ylim(0, values[0] * 1.12)
    _panel(bx, "b")
    _grid(bx)
    return fig


# --- 5. uncertainty ---------------------------------------------------------------------------------------

def figure_uncertainty(r):
    plt = _plt()
    from matplotlib.lines import Line2D

    rows = r.data["rows"]
    fig, axes = plt.subplots(1, 3, figsize=(WIDTH, 2.15), gridspec_kw={"width_ratios": [1, 0.75, 1.15], "wspace": 1.05})
    ax = axes[0]
    ys = list(range(len(rows)))[::-1]
    for i, (y, row) in enumerate(zip(ys, rows, strict=True)):
        point, eu, se = row[1], row[2], row[3]
        ax.plot([point, eu], [y, y], color=AXIS, linewidth=1.2, zorder=1)
        ax.plot(point, y, marker=MARKERS[i], markerfacecolor="white", markeredgecolor=SERIES[i], markersize=5,
                linestyle="none", zorder=2)
        ax.errorbar(eu, y, xerr=2 * se, fmt=MARKERS[i], color=SERIES[i], markersize=5, capsize=2, elinewidth=0.8,
                    zorder=3)
    ax.set_yticks(ys, [_short(row[0]) for row in rows])
    ax.set_ylim(-0.6, len(rows) - 0.4)
    ax.set_xlabel("U")
    ax.legend([Line2D([], [], marker="o", markerfacecolor="white", markeredgecolor=INK2, linestyle="none"),
               Line2D([], [], marker="o", color=INK2, linestyle="none")], ["point", "E[U]"], loc="lower right",
              handletextpad=0.1)
    _panel(ax, "a")
    _grid(ax, "x")

    ax = axes[1]
    for i, (y, row) in enumerate(zip(ys, rows, strict=True)):
        ok = row[5] == "pass"
        ax.barh(y, row[4], height=0.55, color=SERIES[i] if ok else "white", edgecolor=SERIES[i], linewidth=1.0,
                hatch=None if ok else "////")
    ax.axvline(1 - r.data["alpha"], color=INK, linewidth=0.8, linestyle="--")
    ax.set_yticks(ys, [_short(row[0]) for row in rows])
    ax.set_ylim(-0.6, len(rows) - 0.4)
    ax.set_xlim(0, 1.02)
    ax.set_xticks([0, 0.5, 1])
    ax.set_xlabel("Pr(feasible)")
    _panel(ax, "b")
    _grid(ax, "x")

    ax = axes[2]
    voi = r.data["voi"]
    ys = list(range(len(voi)))[::-1]
    for y, p in zip(ys, voi, strict=True):
        lo, hi = (p["interval"] or [p["evppi"], p["evppi"]])
        ax.barh(y, p["evppi"], height=0.55, color=SERIES[0] if p["above_noise"] else NEUTRAL)
        ax.plot([lo, hi], [y, y], color=INK, linewidth=0.8)
    ax.axvline(0, color=AXIS, linewidth=0.8)
    ax.set_yticks(ys, [p["parameter"].removeprefix("obs:").removeprefix("asm:").removesuffix(".value")
                       for p in voi], fontsize=6.5)
    ax.set_xlabel("EVPPI")
    _panel(ax, "c")
    _grid(ax, "x")
    return fig


FIGURES = {"trade-study": figure_trade_study, "program-refresh": figure_program_refresh,
           "perturbations": figure_perturbations, "report-revisions": figure_report_revisions,
           "uncertainty": figure_uncertainty}


# --- captions, from the same results ----------------------------------------------------------------------

def _claim(r, start: str) -> str:
    return next(c.observed for c in r.claims if c.claim.startswith(start))


def caption_trade_study(r) -> str:
    u = {row[0]: row[1] for row in r.data["utilities"]}
    return (f"**The vehicle trade study (baseline episode).** (a) Utility of each configuration: A {u['alt:A']:.3f}, "
            f"B {u['alt:B']:.3f}, C {u['alt:C']:.3f}. C (hatched) fails minimum reliability by 0.01 and is excluded, "
            f"so B is recommended. (b) Utility as B's unit cost varies (open circle: today's 1.20 MUSD). The "
            f"recommendation changes to A at {r.data['cost']['flip']:.2f} MUSD (vertical line), a threshold the kernel "
            f"finds exactly; above its {r.data['cost']['cap']:.2f} MUSD cost cap B is infeasible. (c) Utility as "
            f"weight moves from reliability to cost (range keeps 0.4): A overtakes B at w = "
            f"{r.data['weight']['flip']:.3f}. Shaded: A recommended. Dotted: infeasible.")


def caption_program_refresh(r) -> str:
    chain = r.data["chain"]
    return (f"**A decision program over refresh cycles.** The vehicle decision across its {len(chain)} main-line "
            "episodes, each a change set: approvals at e01, e03, e08 and e11; cost revisions at e02, e06 and e07; B's "
            "reliability report provisional at e04 and accepted at e05; B's quote expiring at e09; a re-quote at e10; "
            "a requirement change at e12. (a) Utility of each configuration. Rings mark the recommendation, the bar "
            "at e04 the bounds of a utility that is unknown, and shading the episodes whose readiness is pending or "
            "blocked. Green: the decision is actionable. Red ×: an approval stopped matching, at each material "
            "change and not at e09, where only readiness changes. The re-quote at e10 makes A the recommendation. "
            "(b) Computed nodes per refresh: a full recomputation, the impact set of the change, and the nodes "
            f"re-evaluated with early cutoff ({_claim(r, 'a refresh re-evaluates')} on the main line, pooled). Every "
            f"selective refresh equals its full recomputation ({_claim(r, 'every episode, selectively')} episodes, "
            "branches included).")


def caption_perturbations(r) -> str:
    eq = r.data["equivalent"]
    n = eq["impact"][1]
    return (f"**Selective re-evaluation on {n:g} controlled perturbations** of the approved vehicle baseline, each "
            "labeled by hand from the research brief's arithmetic; labels and kernel agree in every case. (a) Share "
            "of computed nodes each change re-evaluates, by outcome class (tick: median; full recomputation is 100%). "
            "(b) Cases whose result equals full recomputation, in every node (bar) and in the decision outputs "
            "(recommendation, readiness, actionable; diamond). The kernel's strategies (impact set; impact set with "
            "early cutoff) are exact in every case. Each ablation removes one ingredient and fails: re-running only "
            f"the changed nodes (delta only, {eq['delta_only'][0]:g}/{n:g}), only their direct children (one hop, "
            f"{eq['one_hop'][0]:g}/{n:g}), or omitting the readiness predicates (no readiness, "
            f"{eq['no_readiness'][0]:g}/{n:g}).")


def caption_report_revisions(r) -> str:
    whole, flagged, changed = r.data["totals"]
    return (f"**Revised reports.** {len(r.tables['cases'][1])} edits to the vehicle's Word reports (wording, tables, "
            "renumbering, moved and changed sections), absorbed by comparing section digests. (a) Values sent to a "
            "person for review, by kind of edit: every value the document supports (whole document), the values in a "
            "section whose text changed, and the values whose quoted passage changed (diamond). (b) All edits: "
            f"{whole} values under whole-document review, {flagged} under section digests "
            f"({100 * (1 - flagged / whole):.1f}% fewer), with every changed passage among them ({changed} of "
            f"{changed}). A revision changes no value, so no approval was invalidated.")


def caption_uncertainty(r) -> str:
    rows = {row[0]: row for row in r.data["rows"]}
    na = rows["alt:new-array"]
    return (f"**Deciding under uncertainty.** The counterfire radar decision with uncertain test, cost and schedule "
            f"estimates ({r.data['samples']} seeded samples). (a) Point-value utility and expected utility E[U] "
            "(± 2 standard errors): the new array ranks first on both. (b) Probability each option meets every "
            f"requirement; the decision rule requires {1 - r.data['alpha']:g} (dashed). The new array reaches "
            f"{na[4]:.2f} (hatched), so the rule recommends the {_short(r.data['recommended'][0])}, not the "
            "point-value winner. (c) Expected value of partial perfect information per input, with 90% bootstrap "
            "intervals: only the new array's schedule estimate is worth resolving before deciding.")


CAPTIONS = {"trade-study": caption_trade_study, "program-refresh": caption_program_refresh,
            "perturbations": caption_perturbations, "report-revisions": caption_report_revisions,
            "uncertainty": caption_uncertainty}
