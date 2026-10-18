"""`pdo uncertainty`: the expected-value decision under a joint chance constraint (see pdo.uncertainty)."""

from __future__ import annotations

from pathlib import Path

import typer


def register(app: typer.Typer) -> None:
    """Add this group's commands to the root app."""
    from pdo.cli import EpisodeOpt, JsonOpt, PathArg

    @app.command()
    def uncertainty(path: Path = PathArg, episode: str = EpisodeOpt,
                    samples: int = typer.Option(None, "--samples", "-n", help="Draws (default: the rule's samples)."),
                    seed: int = typer.Option(None, "--seed", help="Seed (default: the rule's seed)."),
                    as_json: bool = JsonOpt) -> None:
        """Sample uncertain inputs: E[U], Pr(feasible), the chance-constrained decision and scenario robustness."""
        from pdo.cli import emit, fail
        from pdo.graph import CompileError
        from pdo.load import LoadError
        from pdo.uncertainty import analyze_path, render, summary

        try:
            result = analyze_path(path, episode, samples, seed)
        except NotImplementedError as exc:
            fail(f"episode {episode}: {exc}", as_json)
        except (LoadError, CompileError, ValueError, KeyError) as exc:
            fail(str(exc), as_json)
        emit(summary(result), as_json, render(result))
