"""`pdo study`: the third onboarding funnel in one command, for the newest episode of a program (or a state).

It runs what a reviewer asks of a decision and writes it to reports/:

- the Decision Package (package/package.md and record.json): the recommendation and why, feasibility
  margins, flip points, scenario robustness, readiness, pending actions and one run per computed node;
- sensitivity.json: a tornado of the decision margin over every input's range and, when inputs declare
  distributions, Sobol indices and the value of information (which evidence to collect next);
- uncertainty.json, when the rule is expected_mavt or inputs declare distributions: expected utility,
  Pr(feasible), the chance-constrained decision and robustness over the scenarios;
- figures/ (with the figures extra): the tornado, and the global and value-of-information charts;
- study.md and study.json: the headline findings, with the episode and state digest they were computed
  for, so `pdo onboard` can tell whether the study is current.

Every number is the kernel's. The study is seeded, so it reproduces.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pdo import __version__
from pdo.load import items

REPORTS_DIR = "reports"


def _target(path: Path) -> tuple[Path, str | None]:
    from pdo.episodes import Program
    from pdo.evaluate import is_program

    if is_program(path):
        return path, Program.load(path).newest()
    return path, None


def _num(x: Any, digits: int = 3) -> str:
    return "?" if x is None else f"{x:.{digits}g}" if isinstance(x, float) else str(x)


def study(path: str | Path, out: str | Path | None = None, samples: int | None = None, seed: int | None = None,
          figures: bool = True) -> dict:
    """Study the newest episode and write reports/ (or `out`). Returns the findings and the files written."""
    from pdo import tools
    from pdo.sensitivity import analyze_path as sensitivity_path
    from pdo.uncertainty import analyze_path as uncertainty_path
    from pdo.uncertainty import summary as uncertainty_summary

    p = Path(path).expanduser()
    root, episode = _target(p)
    base = root if p.is_dir() else p.parent
    reports = Path(out).expanduser() if out is not None else base / REPORTS_DIR
    reports.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    pkg = tools.package(root, episode, reports / "package")
    if not pkg.get("ok"):
        raise ValueError(f"package: {pkg['error']}")
    written += [Path(pkg["package"]), Path(pkg["record"])]
    record = json.loads(Path(pkg["record"]).read_text(encoding="utf-8"))

    sens = sensitivity_path(root, episode, samples=samples, seed=seed)
    (reports / "sensitivity.json").write_text(json.dumps(tools.plain(sens), indent=1) + "\n", encoding="utf-8")
    written.append(reports / "sensitivity.json")

    ev = tools._evaluated(root, episode)
    state = ev.result.graph.state
    rule = state.decision_rule
    uncertain = [o for o in [*items(state.observations), *items(state.assumptions)] if o.uncertainty is not None]
    unc = None
    if (rule is not None and getattr(rule.rule_kind, "value", rule.rule_kind) == "expected_mavt") or uncertain:
        unc = uncertainty_summary(uncertainty_path(root, episode, samples, seed))
        (reports / "uncertainty.json").write_text(json.dumps(tools.plain(unc), indent=1) + "\n", encoding="utf-8")
        written.append(reports / "uncertainty.json")

    if figures:
        try:
            from pdo.sensitivity_plots import write_figures
        except ImportError:  # pragma: no cover - matplotlib missing
            pass
        else:
            written += write_figures(sens, reports / "figures", prefix="study")

    findings = _findings(record, sens, unc)
    expected = unc is not None and getattr(rule.rule_kind, "value", rule.rule_kind) == "expected_mavt"
    decided = unc["recommended"] if expected else record.get("recommended") or []
    headline = (f"{', '.join(decided) or 'no recommendation'}"
                + (" (expected utility)" if expected else "")
                + f"; readiness {pkg.get('readiness')}; {'robust' if record.get('robust') else 'not robust'} over "
                f"the scenarios")
    summary = {"episode": episode, "state_digest": record.get("state_digest"), "kernel_version": __version__,
               "headline": headline, "recommended": decided, "findings": findings,
               "files": sorted(str(f.relative_to(reports)) for f in written)}
    (reports / "study.json").write_text(json.dumps(tools.plain(summary), indent=1) + "\n", encoding="utf-8")
    (reports / "study.md").write_text(_markdown(record, summary, episode), encoding="utf-8")
    written += [reports / "study.json", reports / "study.md"]
    return {"path": str(root), "episode": episode, "reports": str(reports), **summary,
            "files": [str(f) for f in written]}


def _findings(record: dict, sens: dict, unc: dict | None) -> dict:
    out: dict[str, str] = {}
    flips = [f for f in record.get("flips") or [] if f.get("flip_number") is not None and not f.get("counterpart")]
    if flips:
        def distance(f: dict) -> float:
            base = f.get("baseline_number") or 0.0
            return abs(f["flip_number"] - base) / (abs(base) or 1.0)

        f = min(flips, key=distance)
        out["nearest flip"] = (f"{f.get('parameter')} at {_num(f['flip_number'], 6)} (now "
                               f"{_num(f.get('baseline_number'), 6)}) -> "
                               f"{', '.join(f.get('new_recommendation') or []) or 'none'}")
    else:
        out["nearest flip"] = "none within the scanned ranges"
    rows = (sens.get("tornado") or {}).get("rows") or []
    if rows and rows[0].get("swing") is not None:
        r = rows[0]
        out["largest swing"] = f"{r['parameter']} (margin {_num(r['margin_low'])} to {_num(r['margin_high'])})"
    voi = sens.get("value_of_information") or {}
    collect = voi.get("collect_next") or []
    if collect:
        c = collect[0]
        out["collect next"] = f"{c['parameter']} (EVPPI {_num(c['evppi'])}" + (f", evidence {c['evidence']})"
                                                                              if c.get("evidence") else ")")
    elif voi:
        out["collect next"] = "no input is worth resolving (every EVPPI interval includes zero)"
    if unc is not None:
        out["expected utility"] = (f"{', '.join(unc.get('recommended') or []) or 'none'}; agrees with point values: "
                                   f"{unc.get('agrees_with_deterministic')}")
    return out


def _markdown(record: dict, summary: dict, episode: str | None) -> str:
    lines = [f"# Study: {record.get('question') or 'decision'}", "",
             f"{episode + ', ' if episode else ''}as of {record.get('as_of')}; state digest "
             f"`{summary['state_digest']}`; pdo {summary['kernel_version']}", "",
             f"**{summary['headline']}**", ""]
    lines += [f"- {k}: {v}" for k, v in summary["findings"].items()]
    lines += ["", "Files: " + ", ".join(f"`{f}`" for f in summary["files"]), "",
              "The Decision Package (package/package.md) has the reasoning, the margins, the flip points, readiness "
              "and the run record; every number in it is the kernel's.", ""]
    return "\n".join(lines)
