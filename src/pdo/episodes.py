"""Decision programs: a baseline episode and change sets that produce later episodes.

A program is a tree of episodes. The baseline (episode zero) is loaded from baseline/; every change set
in changes/ names its parent and produces a new episode by removing, adding and assigning objects, in
that order, and optionally moving the clock (`as_of`). Episodes are never rewritten: a correction is a
new change set. A change is kept only if the resulting state validates (schema and referential checks);
otherwise it is rejected with a ChangeError that lists every problem.

Stamping: authors never write digests. When a review or approval enters a state (in the baseline, or
when a change set adds or assigns it), the kernel records the episode and the digest it covered:
a review's covered_digest (pdo.graph.covered_digest) and an approval's state_digest
(pdo.graph.state_digest). Later changes to what they covered make the review stale and the approval
no longer match.

Refresh: the baseline runs fully. Every other episode is compiled and run selectively against its
parent's run (pdo.run in impact or cutoff mode) and can be verified against a full recomputation of
the same state. Verification is the kernel's correctness claim made checkable on every episode:
selective re-evaluation equals full recomputation.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import yaml
from pydantic import ValidationError

from pdo import model as m
from pdo.graph import compile_graph, covered_digest, impact_set, state_digest
from pdo.load import (
    LIST_SLOTS,
    SINGLE_OBJECTS,
    STAMPED_SLOTS,
    LoadError,
    authored_stamps,
    check_state,
    index,
    items,
    iter_objects,
    load_dir,
    read_yaml,
)
from pdo.run import MODES, RunResult, equivalent, run

HUMAN_ACTIONS = (m.HumanReview, m.HumanApproval)
# Assigning one of these records a new human judgement, so the action is stamped against the new state.
# Any other assignment (prose, covers) leaves the stamp alone: a stale review or approval stays stale.
JUDGEMENT_SLOTS = {"HumanReview": {"review_status"},
                   "HumanApproval": {"approved_alternative", "approver_role", "valid_until"}}
RECORD_KINDS = ("approval", "review")


class ChangeError(LoadError):
    """A change set cannot be applied: an operation is invalid, or the resulting state does not validate."""

    def __init__(self, episode: str, issues: list[str], where: str | None = None):
        self.episode, self.issues, self.where = episode, list(issues), where
        label = f"{episode} ({where})" if where else episode
        super().__init__(f"change set {label} is rejected:\n" + "\n".join(f"  - {i}" for i in self.issues))


@dataclass
class EpisodeEvaluation:
    episode: str
    parent: str | None
    state: m.DecisionState
    run: RunResult
    impact: set[str] | None = None
    verified: bool | None = None  # selective result equals a full recomputation (when checked)
    change: m.ChangeSet | None = None  # the change set that produced this episode (None for the baseline)
    mismatched: list[str] = field(default_factory=list)  # nodes where selective and full runs disagree

    def summary(self) -> dict:
        """One row of a program report: the outcome, and what the refresh executed versus reused."""
        graph = self.run.graph
        computed = [nid for nid, node in graph.nodes.items() if node.fn != "source"]
        reused = sum(self.run.results[nid].exec_status in ("reused", "cutoff_reused") for nid in computed)
        change = self.change
        return {
            "episode": self.episode, "parent": self.parent, "as_of": str(self.state.as_of),
            "trigger": change.trigger if change else None,
            "description": change.change_description if change else None,
            **self.run.summary(), "approval": self.run.actionable.get("approval"), "mode": self.run.mode,
            "executed": len(self.run.executed), "reused": reused, "computed_nodes": len(computed),
            "impact_size": None if self.impact is None else len(self.impact), "total_nodes": len(graph.nodes),
            "verified": self.verified, "mismatched": self.mismatched,
        }


def stamp_human_actions(state: m.DecisionState, episode: str, only: set[str] | None = None) -> m.DecisionState:
    """Stamp reviews and approvals that lack a digest (or whose ids are in `only`) with this episode.

    An approval without valid_until expires approval_validity_days after the episode's as_of, when the
    policy sets it, so the policy applies however the approval was recorded.
    """
    policy = state.readiness_policy
    days = policy.approval_validity_days if policy is not None else None
    for review in items(state.reviews):
        if review.covered_digest is None or (only is not None and review.id in only):
            review.covered_digest = covered_digest(state, review)
            review.recorded_in_episode = episode
    for approval in items(state.approvals):
        if approval.state_digest is None or (only is not None and approval.id in only):
            if approval.valid_until is None and days and state.as_of is not None:
                approval.valid_until = state.as_of + timedelta(days=days)
            approval.state_digest = state_digest(state)
            approval.recorded_in_episode = episode
    return state





def apply_change(state: m.DecisionState, change: m.ChangeSet) -> m.DecisionState:
    """The state after one change set (validated, with new or reassigned human actions stamped).

    `state` is not modified. Raises ChangeError listing every problem: a removal or assignment whose
    target does not exist, an unknown slot, an id added twice, or a result that fails the schema or
    pdo.load.check_state.
    """
    out = state.model_copy(deep=True)
    issues: list[str] = []
    for oid in items(change.remove):
        if not _remove(out, oid):
            issues.append(f"remove {oid}: no such object{_close(oid, index(out))}")
    if change.add is not None:
        issues.extend(f"add {issue}" for issue in authored_stamps(change.add))
    added = _add(out, change.add, issues) if change.add is not None else []
    assignments = _assign(out, items(change.assign), issues)
    assigned = [target for target, _ in assignments]
    if change.as_of is not None:
        out.as_of = change.as_of
    if issues:
        raise ChangeError(change.id, issues)
    try:
        out = m.DecisionState.model_validate(out.model_dump())
    except ValidationError as exc:
        raise ChangeError(change.id, _validation_lines(exc)) from exc
    # Label each issue by where the object came from, so an author can tell their own mistake from a
    # dangling reference their removal left behind in an inherited object.
    origins = {oid: f"from {change.parent}" for oid in index(out)}
    origins.update({oid: "assigned" for oid in assigned})
    origins.update({oid: "added" for oid in added})
    errors = [f"{i.where}: {i.message}" for i in check_state(out, origins) if i.severity == "error"]
    if errors:
        raise ChangeError(change.id, errors)
    objects = index(out)
    human = {oid for oid in added if isinstance(objects.get(oid), HUMAN_ACTIONS)}
    human |= {target for target, slot in assignments
              if slot in JUDGEMENT_SLOTS.get(type(objects.get(target)).__name__, ())}
    return stamp_human_actions(out, change.id, only=human)


def _remove(state: m.DecisionState, oid: str) -> bool:
    for slot in LIST_SLOTS:
        objects = items(getattr(state, slot))
        for i, obj in enumerate(objects):
            if obj.id == oid:
                del objects[i]
                return True
    for slot in SINGLE_OBJECTS:
        obj = getattr(state, slot)
        if obj is not None and obj.id == oid:
            setattr(state, slot, None)
            return True
    return False


def _add(state: m.DecisionState, add: m.DecisionState, issues: list[str]) -> list[str]:
    """Append the objects of a partial state; returns the ids added."""
    existing = set(index(state))
    added: list[str] = []
    if add.as_of is not None:
        issues.append("add: as_of is not an object; set the change set's own as_of to move the clock")
    if add.question is not None:
        if state.question is None:
            state.question = add.question
        else:
            issues.append("add: question is already set")
    for slot, obj in iter_objects(add):
        if obj.id in existing:
            issues.append(f"add {obj.id}: this id already exists (to replace the object, also remove it)")
            continue
        if slot in SINGLE_OBJECTS:
            current = getattr(state, slot)
            if current is not None:
                issues.append(f"add {obj.id}: {slot} is already {current.id}; remove it to replace it")
                continue
            setattr(state, slot, obj.model_copy(deep=True))
        else:
            setattr(state, slot, [*items(getattr(state, slot)), obj.model_copy(deep=True)])
        existing.add(obj.id)
        added.append(obj.id)
    return added


def _assign(state: m.DecisionState, assignments: list[m.SlotAssignment],
            issues: list[str]) -> list[tuple[str, str]]:
    """Set one slot of one existing object per assignment (type-checked); returns (target, slot) pairs."""
    objects = index(state)
    assigned: list[tuple[str, str]] = []
    for a in assignments:
        where = f"assign {a.target}.{a.slot}"
        obj = objects.get(a.target)
        if obj is None:
            issues.append(f"{where}: no object {a.target}{_close(a.target, objects)}")
            continue
        slots = type(obj).model_fields
        if a.slot not in slots:
            issues.append(f"{where}: a {type(obj).__name__} has no slot {a.slot!r}{_close(a.slot, slots)}")
            continue
        if a.slot == "id":
            issues.append(f"{where}: ids are stable; remove the object and add it under the new id")
            continue
        if a.slot in STAMPED_SLOTS:
            issues.append(f"{where}: stamped by the kernel; authors never write digests")
            continue
        try:
            setattr(obj, a.slot, a.new_value)
        except ValidationError as exc:
            issues.append(f"{where}: {exc.errors()[0]['msg']} (got {a.new_value!r})")
            continue
        assigned.append((a.target, a.slot))
    return assigned


def _close(name: str, candidates) -> str:
    match = difflib.get_close_matches(name, list(candidates), n=1)
    return f"; did you mean {match[0]!r}?" if match else ""


def _validation_lines(exc: ValidationError) -> list[str]:
    return [f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()]


def evaluate_child(parent: EpisodeEvaluation, change: m.ChangeSet, state: m.DecisionState, base_dir: Path,
                   mode: str = "cutoff", verify: bool = True) -> EpisodeEvaluation:
    """Compile a child episode's state and run it selectively against its parent's run."""
    graph = compile_graph(state, base_dir)
    impact = impact_set(parent.run.graph, graph)
    result = run(graph, mode, prev=parent.run, impact=impact)
    evaluation = EpisodeEvaluation(change.id, parent.episode, state, result, impact, change=change)
    if verify:
        verify_evaluation(evaluation)
    return evaluation


def verify_evaluation(evaluation: EpisodeEvaluation) -> EpisodeEvaluation:
    """Set `verified` by recomputing the episode fully and comparing every node's value digest.

    On a mismatch the full run replaces the selective one (and `mismatched` names the nodes), so a
    reuse error is reported and never reaches a result or a child episode.
    """
    if evaluation.parent is None or evaluation.run.mode == "full" or evaluation.verified is not None:
        return evaluation
    full = run(evaluation.run.graph)
    check = equivalent(evaluation.run, full)
    evaluation.verified = bool(check)
    evaluation.mismatched = check.mismatched + check.missing
    if not check:
        evaluation.run = full
    return evaluation


class Program:
    def __init__(self, path: Path, meta: m.DecisionProgram, baseline: m.DecisionState,
                 changes: dict[str, m.ChangeSet], origins: dict[str, str], files: dict[str, Path] | None = None):
        self.path = path
        self.meta = meta
        self.baseline = baseline
        self.changes = changes
        self.origins = origins
        self.files = files or {}  # episode -> the change-set file it came from
        self._states: dict[str, m.DecisionState] = {}
        self._evaluations: dict[tuple[str, str], EpisodeEvaluation] = {}

    @property
    def baseline_episode(self) -> str:
        return self.meta.baseline_episode

    @property
    def base_dir(self) -> Path:
        return self.path

    @property
    def changes_dir(self) -> Path:
        return self.path / (self.meta.changes_dir or "changes")

    @classmethod
    def load(cls, path: str | Path) -> Program:
        path = Path(path)
        try:
            meta = m.DecisionProgram.model_validate(read_yaml(path / "program.yaml"))
        except ValidationError as exc:
            raise LoadError("\n".join(f"program.yaml: {line}" for line in _validation_lines(exc))) from exc
        loaded = load_dir(path / (meta.baseline_dir or "baseline"), base_dir=path)
        problems = [str(i) for i in check_state(loaded.state, loaded.origins) if i.severity == "error"]
        if problems:
            raise LoadError("baseline is invalid:\n" + "\n".join(f"  - {p}" for p in problems))
        changes: dict[str, m.ChangeSet] = {}
        files: dict[str, Path] = {}
        changes_dir = path / (meta.changes_dir or "changes")
        for file in sorted(changes_dir.glob("*.yaml")) if changes_dir.exists() else []:
            try:
                change = m.ChangeSet.model_validate(read_yaml(file))
            except ValidationError as exc:
                where = file.relative_to(path)
                raise LoadError("\n".join(f"{where}: {line}" for line in _validation_lines(exc))) from exc
            if change.add is not None and authored_stamps(change.add):
                raise LoadError(f"{file.relative_to(path)}: " + "; ".join(authored_stamps(change.add)))
            if change.id in changes or change.id == meta.baseline_episode:
                raise LoadError(f"{file}: episode {change.id} is defined twice")
            changes[change.id] = change
            files[change.id] = file
        return cls(path, meta, loaded.state, changes, loaded.origins, files)

    def parent(self, episode: str) -> str | None:
        return None if episode == self.baseline_episode else self.changes[self._require(episode)].parent

    def _require(self, episode: str) -> str:
        if episode != self.baseline_episode and episode not in self.changes:
            known = [self.baseline_episode, *sorted(self.changes)]
            shown = ", ".join(known[:12]) + (", ..." if len(known) > 12 else "")
            raise LoadError(f"no episode {episode} in {self.meta.id} (episodes: {shown})")
        return episode

    def lineage(self, episode: str) -> list[str]:
        """The episodes from the baseline down to `episode`, inclusive."""
        chain = [self._require(episode)]
        while (parent := self.parent(chain[-1])) is not None:
            if parent in chain:
                raise LoadError("parent cycle: " + " -> ".join([*chain, parent]))
            if parent != self.baseline_episode and parent not in self.changes:
                raise LoadError(f"episode {chain[-1]}: parent {parent} does not exist")
            chain.append(parent)
        return chain[::-1]

    def episodes(self) -> list[str]:
        """Every episode, parents before children."""
        order, seen = [], set()
        for episode in [self.baseline_episode, *sorted(self.changes)]:
            for ep in self.lineage(episode):
                if ep not in seen:
                    seen.add(ep)
                    order.append(ep)
        return order

    def children(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {ep: [] for ep in self.episodes()}
        for ep in out:
            parent = self.parent(ep)
            if parent is not None:
                out[parent].append(ep)
        return out

    def as_of(self, episode: str) -> date | None:
        """An episode's clock: the last as_of set along its lineage (no change set is applied)."""
        clock = self.baseline.as_of
        for ep in self.lineage(episode)[1:]:
            clock = self.changes[ep].as_of or clock
        return clock

    def newest(self) -> str:
        """The tip new work should build on: among episodes without children, the latest as_of, then the
        last in program order."""
        order = self.episodes()
        leaves = [ep for ep, kids in self.children().items() if not kids]
        return max(leaves, key=lambda ep: (self.as_of(ep) or date.min, order.index(ep)))

    def materialize(self, episode: str) -> m.DecisionState:
        for ep in self.lineage(episode):
            if ep in self._states:
                continue
            if ep == self.baseline_episode:
                state = stamp_human_actions(self.baseline.model_copy(deep=True), ep)
            else:
                try:
                    state = apply_change(self._states[self.parent(ep)], self.changes[ep])
                except ChangeError as exc:
                    raise ChangeError(exc.episode, exc.issues, self._file_label(ep)) from None
            self._states[ep] = state
        return self._states[episode]

    def _file_label(self, episode: str) -> str | None:
        file = self.files.get(episode)
        if file is None:
            return None
        try:
            return str(file.relative_to(self.path))
        except ValueError:
            return str(file)

    def evaluate(self, episode: str, mode: str = "cutoff", verify: bool = True) -> EpisodeEvaluation:
        """Evaluate one episode: the baseline fully, any other episode selectively against its parent's
        run in the same mode (ancestors first). With `verify`, also recompute it fully and compare."""
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        for ep in self.lineage(episode):
            if (ep, mode) in self._evaluations:
                continue
            state = self.materialize(ep)
            if ep == self.baseline_episode:
                evaluation = EpisodeEvaluation(ep, None, state, run(compile_graph(state, self.base_dir)))
            else:
                parent = self._evaluations[(self.parent(ep), mode)]
                evaluation = evaluate_child(parent, self.changes[ep], state, self.base_dir, mode, verify=False)
            self._evaluations[(ep, mode)] = evaluation
        evaluation = self._evaluations[(episode, mode)]
        return verify_evaluation(evaluation) if verify else evaluation

    def evaluate_all(self, mode: str = "cutoff", verify: bool = True) -> list[EpisodeEvaluation]:
        """Every episode, parents before children."""
        return [self.evaluate(ep, mode, verify) for ep in self.episodes()]

    def preview(self, change: m.ChangeSet, parent: str | None = None, mode: str = "cutoff",
                verify: bool = True) -> EpisodeEvaluation:
        """Dry run: evaluate a change set against `parent` (default: its own parent) without adding it."""
        parent = parent or change.parent
        if parent != change.parent:
            change = change.model_copy(update={"parent": parent})
        base = self.evaluate(parent, mode, verify=False)
        return evaluate_child(base, change, apply_change(base.state, change), self.base_dir, mode, verify)

    # --- recording human actions ---------------------------------------------------------------

    def next_episode_id(self, parent: str) -> str:
        """The next id in the program's numbering (ep:e07 -> ep:e08), or <parent>.<n> without one."""
        known = {self.baseline_episode, *self.changes}
        numbered = re.fullmatch(r"(.*?)(\d+)", parent) or re.fullmatch(r"(.*?)(\d+)", self.baseline_episode)
        if numbered:
            prefix, digits = numbered.groups()
            numbers = [int(x.group(1)) for ep in known if (x := re.fullmatch(re.escape(prefix) + r"(\d+)", ep))]
            return f"{prefix}{max(numbers, default=0) + 1:0{len(digits)}d}"
        n = 1
        while f"{parent}.{n}" in known:
            n += 1
        return f"{parent}.{n}"

    def human_action_change(self, kind: str, target: str, role: str, status: str | None = None,
                            episode: str | None = None, parent: str | None = None) -> m.ChangeSet:
        """A change set recording one human action, as a child of `parent` (default: the newest episode).

        approval: `role` approves alternative `target`. Its valid_until is the parent's as_of plus the
          policy's approval_validity_days, when both are set.
        review: `role` records existing review `target` as `status` (default pass). Assigning the
          status re-stamps the review against the current state, which is also how a stale review is
          redone.
        The kernel stamps either one when the episode is materialized.
        """
        if kind not in RECORD_KINDS:
            raise ValueError(f"kind must be one of {RECORD_KINDS}")
        parent = parent or self.newest()
        state = self.materialize(parent)
        objects = index(state)
        episode = episode or self.next_episode_id(parent)
        if episode == self.baseline_episode or episode in self.changes:
            raise ValueError(f"episode {episode} already exists")
        if not isinstance(objects.get(role), m.Role):
            raise ValueError(f"{role} is not a role in {parent}{_close(role, objects)}")
        local = episode.split(":", 1)[-1]
        if kind == "approval":
            if status is not None:
                raise ValueError("--status applies to reviews, not approvals")
            approval_id, n = f"appr:{local}-{target.split(':', 1)[-1]}", 1
            while approval_id in objects:
                n += 1
                approval_id = f"appr:{local}-{target.split(':', 1)[-1]}-{n}"
            approval = m.HumanApproval(id=approval_id, approved_alternative=target, approver_role=role)
            days = state.readiness_policy.approval_validity_days if state.readiness_policy else None
            if days and state.as_of:
                approval.valid_until = state.as_of + timedelta(days=days)
            return m.ChangeSet(id=episode, parent=parent, trigger="approval", author_role=role,
                               change_description=f"{role} approves {target}.",
                               add=m.DecisionState(approvals=[approval]))
        if not isinstance(objects.get(target), m.HumanReview):
            raise ValueError(f"{target} is not a review in {parent}{_close(target, objects)}")
        status = status or "pass"
        return m.ChangeSet(id=episode, parent=parent, trigger="review", author_role=role,
                           change_description=f"{role} records review {target} as {status}.",
                           assign=[m.SlotAssignment(target=target, slot="review_status", new_value=status)])

    def write_change(self, change: m.ChangeSet, name: str | None = None, header: str | None = None) -> Path:
        """Write a change set to changes/ (never overwriting a file) and add it to this program."""
        if change.id == self.baseline_episode or change.id in self.changes:
            raise ValueError(f"episode {change.id} already exists")
        slug = re.sub(r"[^A-Za-z0-9._-]+", "-", name or change.id.split(":", 1)[-1]).strip("-")
        path = self.changes_dir / f"{slug}.yaml"
        if path.exists():
            raise ValueError(f"{path} already exists; episodes are never rewritten")
        self.changes_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(change_yaml(change, header), encoding="utf-8")
        self.changes[change.id] = change
        self.files[change.id] = path
        return path


def change_yaml(change: m.ChangeSet, header: str | None = None) -> str:
    """A change set as YAML, with an optional comment header."""
    text = yaml.safe_dump(change.model_dump(exclude_none=True), sort_keys=False, allow_unicode=True, width=110)
    comment = "".join(f"# {line}\n" if line else "#\n" for line in (header or "").splitlines())
    return comment + text
