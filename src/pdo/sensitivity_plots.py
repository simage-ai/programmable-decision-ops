"""Figures for a sensitivity result (matplotlib, optional extra `figures`).

Conventions: one value axis per chart, alternatives in a fixed color order (the validated first three
slots of the reference categorical palette: blue, orange, aqua; validated all-pairs for up to three
series), lines also told apart by dash pattern and direct labels so identity is never color alone, text
in neutral ink, hairline grid. More than three alternatives fold into gray.
"""

from __future__ import annotations

from pathlib import Path

SERIES = ["#2a78d6", "#eb6834", "#1baf7a"]
DASHES = ["-", "--", "-."]
OTHER = "#898781"
INK, INK_2, MUTED, GRID, AXIS, SURFACE = "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7", "#fcfcfb"


def _plt():
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update({
        "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
        "axes.edgecolor": AXIS, "axes.labelcolor": INK_2, "xtick.color": MUTED, "ytick.color": MUTED,
        "text.color": INK, "axes.titlecolor": INK, "axes.titlesize": 11, "axes.labelsize": 9,
        "xtick.labelsize": 8, "ytick.labelsize": 8, "legend.fontsize": 8, "legend.frameon": False,
        "axes.spines.top": False, "axes.spines.right": False, "axes.grid": True, "grid.color": GRID,
        "grid.linewidth": 0.6, "lines.linewidth": 2, "font.family": "DejaVu Sans", "axes.axisbelow": True,
    })
    return plt


def _style(i: int) -> tuple[str, str]:
    return (SERIES[i], DASHES[i]) if i < len(SERIES) else (OTHER, ":")


def _short(name: str) -> str:
    return name.split(":", 1)[-1]


def response_figure(result: dict, path: Path) -> Path:
    plt = _plt()
    resp = result["response"]
    p = resp["parameter"]
    rows = resp["rows"]
    alts = sorted(rows[0]["utility"])
    fig, ax = plt.subplots(figsize=(6.4, 3.8))
    for i, alt in enumerate(alts):
        color, dash = _style(i)
        xs = [r["x"] for r in rows if r["utility"][alt] is not None]
        ys = [r["utility"][alt] for r in rows if r["utility"][alt] is not None]
        ax.plot(xs, ys, color=color, linestyle=dash, label=_short(alt))
        bad = [(r["x"], r["utility"][alt]) for r in rows if r["feasibility"][alt] == "fail" and r["utility"][alt]
               is not None]
        if bad:
            ax.scatter([x for x, _ in bad], [y for _, y in bad], s=14, facecolors=SURFACE, edgecolors=color,
                       linewidths=1, zorder=3)
        if xs:
            ax.annotate(_short(alt), (xs[-1], ys[-1]), xytext=(4, 0), textcoords="offset points", fontsize=8,
                        color=INK_2, va="center")
    ax.axvline(p["base"], color=MUTED, linewidth=1, linestyle=":")
    ax.annotate("point value", (p["base"], 1), xycoords=("data", "axes fraction"), xytext=(3, -10),
                textcoords="offset points", fontsize=7, color=MUTED)
    for c in resp["changes"]:
        ax.axvline(c["at"], color=INK_2, linewidth=1, linestyle="--")
        ax.annotate(f"-> {', '.join(_short(a) for a in c['to']) or 'none'} at {c['at']:.4g}", (c["at"], 0),
                    xycoords=("data", "axes fraction"), xytext=(3, 6), textcoords="offset points", fontsize=7,
                    color=INK_2)
    ax.set_xlabel(p["parameter"])
    ax.set_ylabel("utility U")
    ax.set_title(f"Response of each alternative's utility to {p['parameter']}", loc="left")
    ax.legend(loc="best", title="hollow points: infeasible", title_fontsize=7)
    fig.tight_layout()
    fig.savefig(path, dpi=180)
    plt.close(fig)
    return path


def distribution_figure(result: dict, path: Path) -> Path:
    plt = _plt()
    dist = result["distribution"]
    alts = sorted(dist["alternatives"])
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(7.2, 3.2), gridspec_kw={"width_ratios": [3, 2]})
    for i, alt in enumerate(alts):
        color, _ = _style(i)
        u = dist["alternatives"][alt]["utility"]
        if u.get("n"):
            ax1.plot([u["p5"], u["p95"]], [i, i], color=color, linewidth=2)
            ax1.scatter([u["mean"]], [i], s=40, color=color, edgecolors=SURFACE, linewidths=1.5, zorder=3)
    ax1.set_yticks(range(len(alts)), [_short(a) for a in alts])
    ax1.set_xlabel("utility U (mean, 5th-95th percentile)")
    ax1.set_title("Spread of utility", loc="left")
    wins = [dist["alternatives"][a]["p_win"] for a in alts]
    ax2.barh(range(len(alts)), wins, color=[_style(i)[0] for i in range(len(alts))], height=0.55)
    for i, w in enumerate(wins):
        ax2.annotate(f"{w:.2f}", (w, i), xytext=(3, 0), textcoords="offset points", va="center", fontsize=8,
                     color=INK_2)
    ax2.set_yticks(range(len(alts)), [""] * len(alts))
    ax2.set_xlim(0, 1.1)
    ax2.set_xlabel("P(recommended)")
    ax2.set_title("Probability of winning", loc="left")
    fig.suptitle(f"{dist['parameter']['parameter']} ~ {dist['distribution']} "
                 f"(P(decision changes) = {dist['p_decision_changes']:.2f})", fontsize=9, color=INK_2, x=0.01,
                 ha="left")
    fig.tight_layout()
    fig.savefig(path, dpi=180)
    plt.close(fig)
    return path


def tornado_figure(result: dict, path: Path, top: int = 12) -> Path:
    plt = _plt()
    t = result["tornado"]
    rows = [r for r in t["rows"] if r["swing"] is not None][:top][::-1]
    m0 = t["margin"]
    fig, ax = plt.subplots(figsize=(6.8, 0.38 * len(rows) + 1.2))
    # Low and high ends side by side (not stacked), so both stay visible when they move the same way.
    for i, r in enumerate(rows):
        ax.barh(i + 0.17, r["margin_low"] - m0, left=m0, color=SERIES[0], height=0.32,
                label="input at low end" if i == 0 else None)
        ax.barh(i - 0.17, r["margin_high"] - m0, left=m0, color=SERIES[1], height=0.32,
                label="input at high end" if i == 0 else None)
    ax.axvline(m0, color=INK_2, linewidth=1)
    ax.axvline(0, color=INK, linewidth=1, linestyle="--")
    ax.annotate("margin < 0: another alternative wins", (0, 0), xycoords=("data", "axes fraction"),
                xytext=(-4, 4), textcoords="offset points", fontsize=7, color=INK_2, ha="right")
    ax.set_yticks(range(len(rows)), [r["parameter"] for r in rows])
    ax.set_xlabel(f"decision margin of {', '.join(_short(a) for a in t['baseline'])} (baseline {m0:.3g})")
    ax.set_title("Tornado: each input across its range, one at a time", loc="left")
    ax.legend(loc="lower right")
    fig.tight_layout()
    fig.savefig(path, dpi=180)
    plt.close(fig)
    return path


def voi_figure(result: dict, path: Path, top: int = 12) -> Path:
    plt = _plt()
    voi = result["value_of_information"]
    rows = voi["parameters"][:top][::-1]
    fig, ax = plt.subplots(figsize=(6.8, 0.38 * len(rows) + 1.2))
    ax.barh(range(len(rows)), [r["evppi"] for r in rows], color=SERIES[0], height=0.6)
    for i, r in enumerate(rows):
        label = f"{r['evppi']:.3g}" + (f"  ({r['evidence']})" if r["evidence"] else "")
        ax.annotate(label, (r["evppi"], i), xytext=(3, 0), textcoords="offset points", va="center", fontsize=7,
                    color=INK_2)
    if voi["noise_floor"] > 0:
        ax.axvline(voi["noise_floor"], color=MUTED, linewidth=1, linestyle=":")
    ax.set_yticks(range(len(rows)), [r["parameter"] for r in rows])
    ax.set_xlabel("expected value of resolving this input (utility points)")
    ax.set_title(f"Value of information: EVPI {voi['evpi']:.3g}, by input (EVPPI)", loc="left")
    ax.set_xlim(0, max([r["evppi"] for r in rows] + [1e-6]) * 1.6)
    fig.tight_layout()
    fig.savefig(path, dpi=180)
    plt.close(fig)
    return path


def global_figure(result: dict, path: Path, top: int = 12) -> Path:
    plt = _plt()
    g = result["global"]
    target = "margin" if "margin" in g["targets"] else g["targets"][0]
    rows = g["indices"][:top][::-1]
    fig, ax = plt.subplots(figsize=(6.8, 0.42 * len(rows) + 1.2))
    ys = range(len(rows))
    ax.barh([y + 0.17 for y in ys], [max(0.0, r[target]["total"] or 0) for r in rows], height=0.32,
            color=SERIES[0], label="total index")
    ax.barh([y - 0.17 for y in ys], [max(0.0, r[target]["first_order"] or 0) for r in rows], height=0.32,
            color=SERIES[1], label="first-order index")
    ax.set_yticks(list(ys), [r["parameter"] for r in rows])
    ax.set_xlim(0, 1)
    ax.set_xlabel(f"share of the variance of the {target} (negative estimates shown as 0)")
    ax.set_title(f"Global sensitivity (Sobol, {g['samples']} samples)", loc="left")
    ax.legend(loc="lower right")
    fig.tight_layout()
    fig.savefig(path, dpi=180)
    plt.close(fig)
    return path


def write_figures(result: dict, out_dir: str | Path, prefix: str = "sensitivity") -> list[Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    written = []
    if result.get("response"):
        written.append(response_figure(result, out / f"{prefix}_response.png"))
        written.append(distribution_figure(result, out / f"{prefix}_distribution.png"))
    if result.get("tornado"):
        written.append(tornado_figure(result, out / f"{prefix}_tornado.png"))
    if result.get("global"):
        written.append(global_figure(result, out / f"{prefix}_global.png"))
    if result.get("value_of_information"):
        written.append(voi_figure(result, out / f"{prefix}_voi.png"))
    return written
