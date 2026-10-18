"""`pdo`: run, explain and maintain programmable decisions from the command line.

Every command takes --json, which prints {"ok": true, ...} (or {"ok": false, "error": ...} and exits 1).
Feature groups register themselves from their own modules (cli_program, cli_analysis, bench.cli,
cli_tools, cli_uncertainty, cli_sysml, cli_sensitivity) so they can grow independently; heavy imports
stay inside command bodies.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import typer

app = typer.Typer(no_args_is_help=True, help="Programmable decisions: auditable, refreshable decisions as programs.")

PathArg = typer.Argument(..., help="A state file, a state directory, or a program directory.")
EpisodeOpt = typer.Option(None, "--episode", "-e", help="Program episode (default: the baseline).")
JsonOpt = typer.Option(False, "--json", help="Print JSON.")


def emit(data: dict, as_json: bool, text: str | None = None) -> None:
    if as_json:
        typer.echo(json.dumps({"ok": True, **data}, indent=1, default=str))
    else:
        typer.echo(text if text is not None else json.dumps(data, indent=1, default=str))


def fail(message: str, as_json: bool) -> None:
    if as_json:
        typer.echo(json.dumps({"ok": False, "error": message}, indent=1))
    else:
        typer.echo(f"error: {message}", err=True)
    raise typer.Exit(1)


def load_run(path: Path, episode: str | None, as_json: bool):
    from pdo.evaluate import evaluate
    from pdo.graph import CompileError
    from pdo.load import LoadError

    try:
        return evaluate(path, episode)
    except (LoadError, CompileError, ValueError, KeyError) as exc:
        fail(str(exc), as_json)


@app.command()
def validate(path: Path = PathArg, as_json: bool = JsonOpt) -> None:
    """Check a decision: schema, references, units, weights, producers, and that it compiles."""
    from pdo.evaluate import is_program
    from pdo.graph import CompileError, compile_graph
    from pdo.load import LoadError, check_state, executable_gaps, load_state

    try:
        loaded = load_state(path)
    except LoadError as exc:
        fail(str(exc), as_json)
    issues = check_state(loaded.state, loaded.origins)
    gaps = executable_gaps(loaded.state)
    compile_error = None
    if not any(i.severity == "error" for i in issues) and not gaps:
        try:
            compile_graph(loaded.state, loaded.base_dir)
        except CompileError as exc:
            compile_error = str(exc)
    if is_program(path) and not issues and not compile_error:
        try:
            from pdo.episodes import Program

            program = Program.load(path)
            for ep in program.episodes():
                compile_graph(program.materialize(ep), program.base_dir)
        except (LoadError, CompileError, ValueError) as exc:
            compile_error = f"episodes: {exc}"
    errors = [str(i) for i in issues if i.severity == "error"] + [str(g) for g in gaps]
    if compile_error:
        errors.append(compile_error)
    warnings = [str(i) for i in issues if i.severity == "warning"]
    ok = not errors
    data = {"valid": ok, "errors": errors, "warnings": warnings, "objects": len(loaded.origins)}
    if as_json:
        typer.echo(json.dumps({"ok": ok, **data}, indent=1))
    else:
        typer.echo("\n".join(errors + warnings) or f"valid ({len(loaded.origins)} objects)")
    if not ok:
        raise typer.Exit(1)


@app.command()
def run(path: Path = PathArg, episode: str = EpisodeOpt, as_json: bool = JsonOpt) -> None:
    """Evaluate a decision: recommendation, feasibility, utilities, readiness, pending actions."""
    from pdo.report import render_run, run_summary

    result = load_run(path, episode, as_json)
    emit(run_summary(result), as_json, render_run(result))


@app.command()
def explain(path: Path = PathArg, node: str = typer.Argument(..., help="Node id, e.g. U/alt:B or decision."),
            episode: str = EpisodeOpt, depth: int = typer.Option(1, help="Levels of inputs to expand."),
            as_json: bool = JsonOpt) -> None:
    """Why a node has its value: its inputs, implementation and signature."""
    from pdo.report import explain_node

    result = load_run(path, episode, as_json)
    try:
        data = explain_node(result, node, depth)
    except KeyError as exc:
        fail(str(exc.args[0]), as_json)
    emit(data, as_json)


@app.command()
def graph(path: Path = PathArg, episode: str = EpisodeOpt,
          fmt: str = typer.Option("mermaid", "--format", help="mermaid | json"),
          all_nodes: bool = typer.Option(False, "--all", help="Include source objects."),
          as_json: bool = JsonOpt) -> None:
    """The decision's dependency graph."""
    from pdo.report import graph_mermaid

    result = load_run(path, episode, as_json)
    if fmt == "json" or as_json:
        g = result.graph
        nodes = [{"id": n.id, "kind": n.kind, "fn": n.fn, "inputs": list(n.inputs)} for n in g.nodes.values()
                 if all_nodes or n.kind not in ("source", "clock")]
        emit({"nodes": nodes}, True)
    else:
        typer.echo(graph_mermaid(result, decision_only=not all_nodes), nl=False)


@app.command()
def actions(path: Path = PathArg, episode: str = EpisodeOpt,
            role: str = typer.Option(None, help="Only actions for this role."), as_json: bool = JsonOpt) -> None:
    """What people must do before the decision is actionable, by role."""
    from pdo.actions import pending_actions

    result = load_run(path, episode, as_json)
    items = [a.to_dict() for a in pending_actions(result, role)]
    text = "\n".join(f"[{a['role'] or 'unassigned'}] {a['kind']} {a['target']}: {a['reason']}" for a in items)
    emit({"pending_actions": items}, as_json, text or "no pending actions")


def _register_groups() -> None:
    from pdo import cli_analysis, cli_program, cli_sensitivity, cli_sysml, cli_tools, cli_uncertainty
    from pdo.bench import cli as bench_cli

    for module in (cli_program, cli_analysis, bench_cli, cli_tools, cli_uncertainty, cli_sysml, cli_sensitivity):
        module.register(app)


_register_groups()

if __name__ == "__main__":  # pragma: no cover
    sys.exit(app())
