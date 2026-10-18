"""`pdo sensitivity`: how a decision responds to its inputs."""

from __future__ import annotations

from pathlib import Path

import typer


def register(app: typer.Typer) -> None:
    from pdo.cli import EpisodeOpt, JsonOpt, PathArg

    @app.command()
    def sensitivity(
        path: Path = PathArg,
        episode: str = EpisodeOpt,
        param: str = typer.Option(None, "--param", "-p",
                                  help="One input, e.g. obs:B.cost.value: response curve and distribution. "
                                       "Without it: tornado, global indices and value of information."),
        points: int = typer.Option(41, help="Points on a response curve."),
        samples: int = typer.Option(None, help="Draws (default 1000 for one input, 256 per Saltelli matrix)."),
        seed: int = typer.Option(None, help="Random seed (default: the decision rule's seed, else 0)."),
        span: float = typer.Option(0.10, help="Relative range for inputs without a distribution or plausible range."),
        low: float = typer.Option(None, help="Lower end of --param's range."),
        high: float = typer.Option(None, help="Upper end of --param's range."),
        infeasible_value: float = typer.Option(0.0, help="Value of choosing an infeasible alternative."),
        figures: Path = typer.Option(None, help="Write PNG figures to this directory (needs the figures extra)."),
        as_json: bool = JsonOpt,
    ) -> None:
        """How the decision responds to its inputs: response curves, distributions, tornado, Sobol indices
        and value of information."""
        from pdo.cli import emit, fail
        from pdo.graph import CompileError
        from pdo.load import LoadError
        from pdo.sensitivity import SensitivityError, analyze_path, render
        from pdo.uncertainty import UncertaintyError

        try:
            result = analyze_path(path, episode, param, span=span, points=points, samples=samples, seed=seed,
                                  low=low, high=high, infeasible_value=infeasible_value)
        except (SensitivityError, UncertaintyError, LoadError, CompileError, ValueError, KeyError) as exc:
            fail(str(exc), as_json)
        if figures is not None:
            try:
                from pdo.sensitivity_plots import write_figures
            except ImportError:  # pragma: no cover - matplotlib missing
                fail("figures need matplotlib: uv sync --extra figures", as_json)
            result["figures"] = [str(p) for p in write_figures(result, figures)]
        text = render(result) + "".join(f"\nwrote {p}" for p in result.get("figures", []))
        emit(result, as_json, text)
