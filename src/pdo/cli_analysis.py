"""`pdo flip`, `pdo scenarios`, `pdo package` and `pdo replay`: what flips the decision, whether it holds
over the declared scenarios, the Decision Package, and replaying a package's record.

Paths and episodes resolve through pdo.evaluate.evaluate, like `pdo run`; heavy imports stay inside the
command bodies so `pdo --help` stays fast.
"""

from __future__ import annotations

from pathlib import Path

import typer

PathArg = typer.Argument(..., help="A state file, a state directory, or a program directory.")
EpisodeOpt = typer.Option(None, "--episode", "-e", help="Program episode (default: the baseline).")
JsonOpt = typer.Option(False, "--json", help="Print JSON.")
ParamOpt = typer.Option(None, "--param", "-p", help="Only this input, as ID.slot (e.g. obs:B.cost.value) or an id; "
                        "a criterion's weight is crit:ID.weight. Repeatable.")
SpanOpt = typer.Option(0.5, help="Scan +/- this fraction of a value (assumptions use their plausible range).")
GridOpt = typer.Option(17, help="Grid points per scanned input.")


def _evaluated(path: Path, episode: str | None, as_json: bool):
    """(run, episode id, parent id) for a path, resolved like `pdo run`."""
    from pdo.cli import load_run
    from pdo.evaluate import is_program

    result = load_run(path, episode, as_json)
    if not is_program(path):
        return result, episode, None
    from pdo.episodes import Program

    program = Program.load(path)
    ep = episode or program.baseline_episode
    return result, ep, program.parent(ep)


def _analyse(result, params: list[str] | None, span: float, grid: int):
    """(scans, flips) over numeric inputs and criterion weights, filtered by --param."""
    from pdo.flip import scan_parameters, weight_flips

    state, base_dir = result.graph.state, result.graph.base_dir
    weight_ids = {p.rsplit(".", 1)[0] for p in params or [] if p.endswith(".weight")}
    others = [p for p in params or [] if not p.endswith(".weight")]
    scans, flips = [], []
    if params is None or others:
        scans, flips = scan_parameters(state, base_dir, others or None, span, grid, base=result)
    if params is None or weight_ids:
        flips += weight_flips(state, base_dir, base=result, criteria=weight_ids or None)
    return scans, flips


def _flip_line(f: dict) -> str:
    name = f["parameter"] + (f" (from {f['counterpart']})" if f.get("counterpart") else "")
    prev, new = ", ".join(f["previous_recommendation"]) or "none", ", ".join(f["new_recommendation"]) or "none"
    mark = "" if f.get("verified", True) else "  (not verified)"
    return (f"{name}: {f['direction']} {f['flip_number']:.6g} (now {f['baseline_number']:.6g})  {prev} -> {new}  "
            f"[{f['boundary_kind']}, {f['method']}]{mark}")


def register(app: typer.Typer) -> None:
    """Add this group's commands to the root app."""

    @app.command()
    def flip(path: Path = PathArg, episode: str = EpisodeOpt, param: list[str] = ParamOpt, span: float = SpanOpt,
             grid: int = GridOpt, as_json: bool = JsonOpt) -> None:
        """What flips the decision: each numeric input's and each weight's flip points, others held fixed."""
        from pdo.cli import emit, fail

        result, _, _ = _evaluated(path, episode, as_json)
        try:
            scans, flips = _analyse(result, param or None, span, grid)
        except (KeyError, ValueError) as exc:
            fail(str(exc.args[0]) if exc.args else str(exc), as_json)
        stable = [f"{s['parameter']} [{s['scan_low']:.6g}, {s['scan_high']:.6g}]" for s in scans if not s["flip_count"]]
        lines = [f"Recommended: {', '.join(result.decision['recommended']) or 'none'}", ""]
        lines += [_flip_line(f) for f in flips] or ["no flip within the scanned ranges"]
        if stable:
            lines += ["", "no flip: " + "; ".join(stable)]
        emit({"recommended": result.decision["recommended"], "flips": flips, "scans": scans}, as_json,
             "\n".join(lines))

    @app.command()
    def scenarios(path: Path = PathArg, episode: str = EpisodeOpt, as_json: bool = JsonOpt) -> None:
        """Evaluate each declared scenario: does the recommendation hold in every one?"""
        from pdo.cli import emit, fail
        from pdo.flip import is_robust, scenario_sweep

        result, _, _ = _evaluated(path, episode, as_json)
        try:
            outcomes = scenario_sweep(result.graph.state, base=result)
        except ValueError as exc:
            fail(str(exc), as_json)
        robust = is_robust(outcomes)
        lines = [f"Recommended: {', '.join(result.decision['recommended']) or 'none'}", ""]
        for s in outcomes:
            us = ", ".join(f"{o['alternative']} {o['utility']:.6g}" if o["utility"] is not None
                           else f"{o['alternative']} ?" for o in s["outcomes"])
            holds = "holds" if s["robust"] else "FLIPS"
            lines.append(f"{s['scenario']}: {', '.join(s['recommended']) or 'none'}  "
                         f"(feasible {', '.join(s['feasible']) or 'none'}; U {us})  {holds}")
        lines += ["", "no scenarios declared" if robust is None else f"robust: {'yes' if robust else 'no'}"]
        emit({"recommended": result.decision["recommended"], "robust": robust, "scenarios": outcomes}, as_json,
             "\n".join(lines))

    @app.command()
    def package(path: Path = PathArg, episode: str = EpisodeOpt,
                out: Path = typer.Option(Path("decision-package"), "--out", "-o", help="Output directory."),
                span: float = SpanOpt, grid: int = GridOpt,
                sensitivity: bool = typer.Option(False, "--sensitivity",
                                                 help="Add a tornado and, for uncertain inputs, Sobol indices and "
                                                      "value of information (pdo sensitivity)."),
                samples: int = typer.Option(None, help="Draws per Saltelli matrix for --sensitivity."),
                as_json: bool = JsonOpt) -> None:
        """Write the Decision Package: package.md (for people) and record.json (validated, replayable)."""
        from pdo.cli import emit, fail
        from pdo.flip import scenario_sweep
        from pdo.package import episode_record, write_package

        result, ep, parent = _evaluated(path, episode, as_json)
        try:
            scans, flips = _analyse(result, None, span, grid)
            outcomes = scenario_sweep(result.graph.state, base=result)
            sens = None
            if sensitivity:
                from pdo.sensitivity import analyze as sensitivity_analysis

                sens = sensitivity_analysis(result.graph.state, result.graph.base_dir, samples=samples)
            record = episode_record(result, episode=ep, parent=parent, source=path.resolve(), flips=flips,
                                    sensitivity=sens,
                                    scenarios=outcomes, scans=scans)
            paths = write_package(record, out)
        except (KeyError, ValueError) as exc:
            fail(str(exc), as_json)
        data = {"package": str(paths["package"]), "record": str(paths["record"]), "episode": ep,
                "recommended": record.recommended, "robust": record.robust, "flips": len(record.flips),
                "runs": len(record.runs), "claims": len(record.claims)}
        emit(data, as_json, f"wrote {paths['package']} and {paths['record']}")

    @app.command()
    def replay(record_path: Path = typer.Argument(..., metavar="RECORD.json", help="A package's record.json."),
               as_json: bool = JsonOpt) -> None:
        """Re-evaluate a record's source in full and compare every node's value digest with the record."""
        import json

        from pdo.cli import fail
        from pdo.graph import CompileError
        from pdo.load import LoadError
        from pdo.package import load_record
        from pdo.package import replay as replay_record

        try:
            report = replay_record(load_record(record_path), record_path)
        except (OSError, LoadError, CompileError, ValueError, KeyError) as exc:
            fail(str(exc), as_json)
        ok = report["reproduced"]
        if as_json:
            typer.echo(json.dumps({"ok": ok, **report}, indent=1))
        else:
            lines = [f"{report['matched']} of {report['nodes']} node values reproduced"
                     + ("" if report["state_digest_matches"] else "; the state digest differs")]
            for key in ("mismatched", "missing", "extra"):
                if report[key]:
                    lines.append(f"{key}: {', '.join(report[key])}")
            lines.append("reproduced" if ok else "NOT reproduced")
            typer.echo("\n".join(lines))
        if not ok:
            raise typer.Exit(1)
