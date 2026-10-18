"""`pdo dashboard`: a read-only HTML rendering of a decision program."""

from __future__ import annotations

from pathlib import Path

import typer


def register(app: typer.Typer) -> None:
    from pdo.cli import JsonOpt, PathArg

    @app.command()
    def dashboard(
        path: Path = PathArg,
        out: Path = typer.Option(Path("dashboard"), "--out", "-o", help="Directory for index.html and dashboard.json."),
        flips: bool = typer.Option(True, "--flips/--no-flips", help="Flip points for every episode."),
        sensitivity: bool = typer.Option(True, "--sensitivity/--no-sensitivity",
                                         help="Tornado per episode; global indices and value of information "
                                              "for the baseline."),
        uncertainty: bool = typer.Option(True, "--uncertainty/--no-uncertainty",
                                         help="The decision under uncertainty, when the rule is expected_mavt."),
        samples: int = typer.Option(None, help="Draws per Saltelli matrix for the baseline's global indices."),
        fragment: bool = typer.Option(False, help="Also write fragment.html, the page without its document "
                                                  "wrapper, for hosts that wrap pages themselves."),
        as_json: bool = JsonOpt,
    ) -> None:
        """Render the decision graph, episodes and analyses as a self-contained HTML page. The page draws
        recorded kernel results and computes nothing."""
        from pdo.cli import emit, fail
        from pdo.dashboard import build, write

        try:
            data = build(path, flips=flips, sensitivity=sensitivity, uncertainty=uncertainty, samples=samples)
        except (ValueError, KeyError, ArithmeticError) as exc:
            fail(str(exc), as_json)
        try:
            paths = write(data, out, fragment=fragment)
        except FileExistsError as exc:
            fail(f"{exc}; choose another --out", as_json)
        last = data["episodes"][-1]
        result = {"episodes": len(data["episodes"]), "nodes": len(data["layout"]["nodes"]),
                  "edges": len(data["layout"]["edges"]), "files": {k: str(v) for k, v in paths.items()},
                  "newest": {"episode": last["id"], "recommended": last["recommended"],
                             "readiness": last["readiness"]}}
        size = paths["page"].stat().st_size / 1024
        text = (f"dashboard of {data['meta']['title']}: {result['episodes']} episode(s), {result['nodes']} nodes, "
                f"{result['edges']} edges\nwrote {paths['page']} ({size:.0f} KB)\nwrote {paths['data']}"
                + (f"\nwrote {paths['fragment']}" if "fragment" in paths else ""))
        emit(result, as_json, text)
