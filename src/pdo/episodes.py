"""Decision programs: a baseline episode and change sets that produce later episodes.

STAGE 1 CONTRACT (owned by the episodes work in stage 2): this module loads a program, materializes
the baseline, stamps human actions, and evaluates episodes. Applying change sets (`apply_change`) and
selective refresh between episodes are still to be implemented; the signatures below are the contract
the CLI, MCP tools and benchmark rely on.

Stamping: authors never write digests. When a review or approval enters a state (in the baseline, or
when a change set adds or assigns it), the kernel records the episode and the digest it covered:
a review's covered_digest (pdo.graph.covered_digest) and an approval's state_digest
(pdo.graph.state_digest). Later changes to what they covered make the review stale and the approval
no longer match.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pdo import model as m
from pdo.graph import compile_graph, covered_digest, state_digest
from pdo.load import LoadError, items, load_dir, read_yaml
from pdo.run import RunResult, run


@dataclass
class EpisodeEvaluation:
    episode: str
    parent: str | None
    state: m.DecisionState
    run: RunResult
    impact: set[str] | None = None
    verified: bool | None = None  # selective result equals a full recomputation (when checked)


def stamp_human_actions(state: m.DecisionState, episode: str, only: set[str] | None = None) -> m.DecisionState:
    """Stamp reviews and approvals that lack a digest (or whose ids are in `only`) with this episode."""
    for review in items(state.reviews):
        if review.covered_digest is None or (only is not None and review.id in only):
            review.covered_digest = covered_digest(state, review)
            review.recorded_in_episode = episode
    for approval in items(state.approvals):
        if approval.state_digest is None or (only is not None and approval.id in only):
            approval.state_digest = state_digest(state)
            approval.recorded_in_episode = episode
    return state


def apply_change(state: m.DecisionState, change: m.ChangeSet) -> m.DecisionState:
    """The state after one change set (validated, with new or reassigned human actions stamped)."""
    raise NotImplementedError("apply_change: implemented in stage 2 (episodes)")


class Program:
    def __init__(self, path: Path, meta: m.DecisionProgram, baseline: m.DecisionState,
                 changes: dict[str, m.ChangeSet], origins: dict[str, str]):
        self.path = path
        self.meta = meta
        self.baseline = baseline
        self.changes = changes
        self.origins = origins
        self._states: dict[str, m.DecisionState] = {}
        self._evaluations: dict[str, EpisodeEvaluation] = {}

    @property
    def baseline_episode(self) -> str:
        return self.meta.baseline_episode

    @property
    def base_dir(self) -> Path:
        return self.path

    @classmethod
    def load(cls, path: str | Path) -> Program:
        path = Path(path)
        meta = m.DecisionProgram.model_validate(read_yaml(path / "program.yaml"))
        loaded = load_dir(path / (meta.baseline_dir or "baseline"), base_dir=path)
        changes: dict[str, m.ChangeSet] = {}
        changes_dir = path / (meta.changes_dir or "changes")
        for file in sorted(changes_dir.glob("*.yaml")) if changes_dir.exists() else []:
            change = m.ChangeSet.model_validate(read_yaml(file))
            if change.id in changes or change.id == meta.baseline_episode:
                raise LoadError(f"{file}: episode {change.id} is defined twice")
            changes[change.id] = change
        return cls(path, meta, loaded.state, changes, loaded.origins)

    def parent(self, episode: str) -> str | None:
        return None if episode == self.baseline_episode else self.changes[episode].parent

    def episodes(self) -> list[str]:
        """Every episode, parents before children."""
        order, seen = [], set()

        def visit(ep: str) -> None:
            if ep in seen:
                return
            parent = self.parent(ep)
            if parent is not None:
                if parent != self.baseline_episode and parent not in self.changes:
                    raise LoadError(f"episode {ep}: parent {parent} does not exist")
                visit(parent)
            seen.add(ep)
            order.append(ep)

        visit(self.baseline_episode)
        for ep in sorted(self.changes):
            visit(ep)
        return order

    def materialize(self, episode: str) -> m.DecisionState:
        if episode not in self._states:
            if episode == self.baseline_episode:
                state = stamp_human_actions(self.baseline.model_copy(deep=True), episode)
            else:
                state = apply_change(self.materialize(self.parent(episode)), self.changes[episode])
            self._states[episode] = state
        return self._states[episode]

    def evaluate(self, episode: str, mode: str = "cutoff", verify: bool = True) -> EpisodeEvaluation:
        if episode not in self._evaluations:
            state = self.materialize(episode)
            graph = compile_graph(state, self.base_dir)
            self._evaluations[episode] = EpisodeEvaluation(episode, self.parent(episode), state, run(graph))
        return self._evaluations[episode]
