"""`pdo bench`: run a perturbation suite, write its tables and figures, and gate on it.

    pdo bench run SUITE_DIR      every case and strategy, the edge-omission sweep, the program timeline
                                 and the baseline's flips; writes results/bench/<suite>/*.tsv and
                                 results/figures/*.png (each only if its content changed)
    pdo bench check SUITE_DIR    exit 1 unless label agreement, and equivalence and soundness of the
                                 impact and cutoff strategies, are all 100%; writes nothing
"""

from __future__ import annotations

import json
from pathlib import Path

import typer

SuiteArg = typer.Argument(..., help="A suite directory (with perturbations.yaml) or the suite file itself.")
ResultsOpt = typer.Option(Path("results"), "--results", help="Results root: tables under bench/<suite>/, figures under "
                                                            "figures/.")


def register(app: typer.Typer) -> None:
    """Add the `bench` group to the root app."""
    from pdo.cli import JsonOpt, fail

    group = typer.Typer(no_args_is_help=True, help="Representation benchmark: perturbations, strategies, labels.")
    app.add_typer(group, name="bench")

    @group.command("run")
    def run_cmd(suite: Path = SuiteArg, results: Path = ResultsOpt,
                figures: bool = typer.Option(True, "--figures/--no-figures", help="Draw figures (needs matplotlib)."),
                seeds: int = typer.Option(None, "--seeds", help="Edge-omission seeds per p (default 20)."),
                as_json: bool = JsonOpt) -> None:
        """Run every case and strategy, then write the tables (and figures)."""
        from pdo.bench import report
        from pdo.bench import strategies as st

        result = _run(suite, as_json, fail, edge_seeds=st.EDGE_SEEDS if seeds is None else seeds)
        written = report.write_tables(result, results)
        if figures:
            try:
                written.update(report.write_figures(result, results))
            except ImportError:
                if not as_json:
                    typer.echo("matplotlib is not installed; skipping figures (uv sync --extra figures)", err=True)
        changed = sorted(p for p, w in written.items() if w)
        if as_json:
            data = {**report.summary_dict(result), "written": changed, "unchanged": sorted(set(written) - set(changed))}
            typer.echo(json.dumps({"ok": True, **data}, indent=1, default=str))
            return
        typer.echo(report.summary_text(result), nl=False)
        typer.echo(f"\nwrote {len(changed)} of {len(written)} files under {results}/ (the rest were unchanged)")

    @group.command("check")
    def check_cmd(suite: Path = SuiteArg, as_json: bool = JsonOpt) -> None:
        """Exit 1 unless labels agree and impact and cutoff are equivalent and sound on every case."""
        from pdo.bench.suite import disagreements

        result = _run(suite, as_json, fail, edge_p=(), timeline=False, flips=False)
        gate = result.gate()
        if as_json:
            data = {"passed": result.passed, "gate": {k: {"value": r.value, "numerator": r.num, "denominator": r.den}
                                                      for k, r in gate.items()},
                    "disagreements": disagreements(result)}
            typer.echo(json.dumps({"ok": result.passed, **data}, indent=1, default=str))
        else:
            for name, rate in gate.items():
                mark = "ok  " if rate.den and rate.num == rate.den else "FAIL"
                typer.echo(f"{mark} {name}: {rate.num:g}/{rate.den:g}")
            for d in disagreements(result):
                typer.echo(f"     {d['case']}: {', '.join(d['fields'])}" + (f" ({d['error']})" if d["error"] else ""))
        if not result.passed:
            raise typer.Exit(1)


def _run(suite: Path, as_json: bool, fail, **kw):
    from pdo.bench.suite import run_suite
    from pdo.load import LoadError

    try:
        return run_suite(suite, **kw)
    except (LoadError, ValueError) as exc:
        fail(str(exc), as_json)
