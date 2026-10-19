"""`pdo init`, `pdo onboard` and `pdo study`: the front door, from a folder of inputs to a studied decision.

    pdo init DIR [--question ...] [--owner role:...] [--doc FILE ...] [--model FILE ...]
    pdo onboard DIR      where the decision stands in the three funnels, and the single next step
    pdo study DIR        flips, scenarios, sensitivity, value of information, uncertainty and the package

skills/decision-onboarding/SKILL.md is the procedure an agent follows with the person.
"""

from __future__ import annotations

from pathlib import Path

import typer


def register(app: typer.Typer) -> None:
    from pdo.cli import JsonOpt, emit, fail

    @app.command()
    def init(directory: Path = typer.Argument(..., help="The new program directory."),
             question: str = typer.Option(None, "--question", "-q", help="The decision, in one sentence."),
             owner: str = typer.Option(None, "--owner", help="The decision owner's role id (role:...)."),
             name: str = typer.Option(None, "--name", help="A short name for the decision."),
             doc: list[Path] = typer.Option([], "--doc", help="A report (.docx, .pdf, .md, .txt) or a directory of "
                                                              "them, copied into inputs/docs. Repeatable."),
             model: list[Path] = typer.Option([], "--model", help="A SysML v2 file (.sysml, or its mapping .yaml) or a "
                                                                  "directory, copied into inputs/models. Repeatable."),
             as_json: bool = JsonOpt) -> None:
        """Scaffold a decision program: inputs/docs, inputs/models, baseline/, changes/, program.yaml."""
        from pdo.onboard import OnboardError, init_program

        try:
            r = init_program(directory, name=name, question=question, owner=owner, docs=doc, models=model)
        except OnboardError as exc:
            fail(f"{exc}" + (f"\nhint: {exc.hint}" if exc.hint and not as_json else ""), as_json)
        nxt = r["next"]
        text = "\n".join([f"Created {r['path']} ({r['program']}): {', '.join(r['created'])}",
                          *(f"  copied {c}" for c in r["copied"]),
                          f"Next ({nxt['who']}): {nxt['do']}" if nxt else "",
                          f"  {nxt['command']}" if nxt and nxt.get("command") else "",
                          f"pdo onboard {directory} shows every step."])
        emit(r, as_json, text.replace("\n\n", "\n"))

    @app.command()
    def onboard(directory: Path = typer.Argument(Path("."), help="A program directory (default: here)."),
                as_json: bool = JsonOpt) -> None:
        """Where the decision stands: construct the graph, run it, study it; and the single next step."""
        from pdo.graph import CompileError
        from pdo.load import LoadError
        from pdo.onboard import OnboardError, render, status

        try:
            r = status(directory)
        except (OnboardError, LoadError, CompileError, ValueError) as exc:
            hint = getattr(exc, "hint", "")
            fail(f"{exc}" + (f"\nhint: {hint}" if hint and not as_json else ""), as_json)
        emit(r, as_json, render(r))

    @app.command()
    def study(directory: Path = typer.Argument(Path("."), help="A program directory (default: here), or a state."),
              out: Path = typer.Option(None, "--out", "-o", help="Where to write (default: reports/ in the program)."),
              samples: int = typer.Option(None, help="Draws for sensitivity and uncertainty (default: the rule's)."),
              seed: int = typer.Option(None, help="Random seed (default: the decision rule's seed, else 0)."),
              figures: bool = typer.Option(True, "--figures/--no-figures", help="Write PNG figures (figures extra)."),
              as_json: bool = JsonOpt) -> None:
        """Study the newest episode: package, flips, scenarios, sensitivity, value of information, uncertainty."""
        from pdo.graph import CompileError
        from pdo.load import LoadError
        from pdo.study import study as run_study

        try:
            r = run_study(directory, out=out, samples=samples, seed=seed, figures=figures)
        except (LoadError, CompileError, ValueError, KeyError, ArithmeticError) as exc:
            fail(str(exc), as_json)
        text = "\n".join([f"{r['episode'] + ': ' if r['episode'] else ''}{r['headline']}",
                          *(f"  {k}: {v}" for k, v in r["findings"].items()),
                          f"Wrote {r['reports']}/: {', '.join(Path(f).name for f in r['files'])}"])
        emit(r, as_json, text)
