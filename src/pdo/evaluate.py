"""One entry point from a path on disk to an evaluated decision, shared by the CLI and MCP tools."""

from __future__ import annotations

from pathlib import Path

from pdo.graph import compile_graph
from pdo.load import load_state
from pdo.run import RunResult, run


def is_program(path: str | Path) -> bool:
    return Path(path).is_dir() and (Path(path) / "program.yaml").exists()


def evaluate(path: str | Path, episode: str | None = None) -> RunResult:
    """Run a state file, a state directory, or one episode of a program (default: its baseline)."""
    if episode is not None:
        if not is_program(path):
            raise ValueError(f"{path} is not a program directory (no program.yaml); --episode needs one")
        from pdo.episodes import Program

        program = Program.load(path)
        return program.evaluate(episode).run
    if is_program(path):
        from pdo.episodes import Program

        program = Program.load(path)
        return program.evaluate(program.baseline_episode).run
    loaded = load_state(path)
    return run(compile_graph(loaded.state, loaded.base_dir))
