"""One entry point from a path on disk to an evaluated decision, shared by the CLI and MCP tools."""

from __future__ import annotations

from pathlib import Path

from pdo.graph import compile_graph
from pdo.load import LoadError, check_state, load_state
from pdo.run import RunResult, run

PLAIN_EPISODE = "state"  # the episode label stamped on human actions in a plain (non-program) state


def is_program(path: str | Path) -> bool:
    return Path(path).is_dir() and (Path(path) / "program.yaml").exists()


def evaluate(path: str | Path, episode: str | None = None) -> RunResult:
    """Run a state file, a state directory, or one episode of a program (default: its baseline).

    A program episode is refreshed selectively and verified against a full recomputation; on a mismatch
    the full result is returned. A plain state is checked like `pdo validate` first and treated as a
    baseline: its reviews and approvals are stamped against it (it has no history, so nothing in it can
    be stale; use a program to track changes).
    """
    if episode is not None and not is_program(path):
        raise ValueError(f"{path} is not a program directory (no program.yaml); --episode needs one")
    if is_program(path):
        from pdo.episodes import Program

        program = Program.load(path)
        return program.evaluate(episode or program.baseline_episode).run
    from pdo.episodes import stamp_human_actions

    loaded = load_state(path)
    problems = [str(i) for i in check_state(loaded.state, loaded.origins) if i.severity == "error"]
    if problems:
        raise LoadError(f"{path} is invalid (see `pdo validate`):\n" + "\n".join(f"  - {p}" for p in problems))
    state = stamp_human_actions(loaded.state, PLAIN_EPISODE)
    return run(compile_graph(state, loaded.base_dir))
