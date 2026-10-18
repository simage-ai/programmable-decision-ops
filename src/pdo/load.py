"""Load a decision state from disk and check it beyond what the schema can express.

A state is a single YAML file, or a directory whose *.yaml files each hold part of one DecisionState;
list slots are concatenated and single-valued slots must appear once. A program directory (with
program.yaml) loads its baseline here; episodes are materialized by pdo.episodes.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from pydantic import BaseModel, ValidationError

from pdo import model as m

LIST_SLOTS = (
    "roles", "objectives", "alternatives", "measures", "criteria", "requirements", "scenarios", "evidence_items",
    "observations", "assumptions", "evaluation_specifications", "readiness_conditions", "bias_checks", "reviews",
    "risks", "approvals",
)
SINGLE_SLOTS = ("as_of", "question", "decision_rule", "readiness_policy")
SINGLE_OBJECTS = ("decision_rule", "readiness_policy")
EXECUTABLE_REQUIRED = ("as_of", "alternatives", "measures", "criteria", "decision_rule", "readiness_policy")


class LoadError(ValueError):
    """The files could not be read or merged into a valid DecisionState."""


@dataclass(frozen=True)
class Issue:
    severity: str  # "error" | "warning"
    where: str
    message: str

    def __str__(self) -> str:
        return f"{self.severity}: {self.where}: {self.message}"


@dataclass
class Loaded:
    state: m.DecisionState
    base_dir: Path
    origins: dict[str, str] = field(default_factory=dict)  # object id -> file it came from


def items(value):
    return value or []


def iter_objects(state: m.DecisionState):
    """Every identified object in the state, as (collection, object)."""
    for slot in LIST_SLOTS:
        for obj in items(getattr(state, slot)):
            yield slot, obj
    for slot in SINGLE_OBJECTS:
        obj = getattr(state, slot)
        if obj is not None:
            yield slot, obj


def index(state: m.DecisionState) -> dict[str, BaseModel]:
    return {obj.id: obj for _, obj in iter_objects(state)}


def read_yaml(path: Path) -> dict:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise LoadError(f"{path}: {exc}") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise LoadError(f"{path}: expected a mapping at the top level")
    return data


def merge_parts(parts: list[tuple[str, dict]]) -> tuple[dict, dict[str, str]]:
    merged: dict = {}
    origins: dict[str, str] = {}
    duplicates: list[str] = []
    for origin, data in parts:
        unknown = set(data) - set(LIST_SLOTS) - set(SINGLE_SLOTS)
        if unknown:
            raise LoadError(f"{origin}: unknown top-level keys {sorted(unknown)}")
        for key, value in data.items():
            if key in SINGLE_SLOTS:
                if key in merged:
                    raise LoadError(f"{origin}: {key} is already set in another file")
                merged[key] = value
                if key in SINGLE_OBJECTS and isinstance(value, dict) and "id" in value:
                    origins[value["id"]] = origin
            else:
                if not isinstance(value, list):
                    raise LoadError(f"{origin}: {key} must be a list")
                merged.setdefault(key, []).extend(value)
                for obj in value:
                    oid = obj.get("id") if isinstance(obj, dict) else None
                    if oid is None:
                        continue
                    if oid in origins:
                        duplicates.append(f"{oid} ({origins[oid]} and {origin})")
                    origins[oid] = origin
    if duplicates:
        raise LoadError("duplicate ids: " + "; ".join(duplicates))
    return merged, origins


def validate_state(data: dict, where: str = "state") -> m.DecisionState:
    try:
        return m.DecisionState.model_validate(data)
    except ValidationError as exc:
        lines = [f"{where}: {'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()]
        raise LoadError("\n".join(lines)) from exc


STAMPED_SLOTS = ("covered_digest", "state_digest", "recorded_in_episode")


def authored_stamps(state: m.DecisionState) -> list[str]:
    """Kernel stamps written by an author. Never allowed: a digest an author can write can be forged."""
    found = []
    for obj in [*items(state.reviews), *items(state.approvals)]:
        for slot in STAMPED_SLOTS:
            if getattr(obj, slot, None) is not None:
                found.append(f"{obj.id}.{slot}: stamped by the kernel; authors never write digests")
    return found


def _reject_stamps(state: m.DecisionState, where: str) -> m.DecisionState:
    found = authored_stamps(state)
    if found:
        raise LoadError("\n".join(f"{where}: {f}" for f in found))
    return state


def state_files(directory: Path) -> list[Path]:
    return sorted(p for p in directory.glob("*.yaml") if p.name != "program.yaml")


def load_state(path: str | Path) -> Loaded:
    """Load a state file, a directory of partial states, or a program's baseline."""
    path = Path(path)
    if path.is_dir() and (path / "program.yaml").exists():
        program = read_yaml(path / "program.yaml")
        directory = path / program.get("baseline_dir", "baseline")
        return load_dir(directory, base_dir=path)
    if path.is_dir():
        return load_dir(path, base_dir=path)
    if not path.exists():
        raise LoadError(f"{path}: no such file or directory")
    merged, origins = merge_parts([(path.name, read_yaml(path))])
    return Loaded(_reject_stamps(validate_state(merged, path.name), path.name), path.parent, origins)


def load_dir(directory: Path, base_dir: Path) -> Loaded:
    files = state_files(directory)
    if not files:
        raise LoadError(f"{directory}: no *.yaml files")
    parts = [(str(p.relative_to(base_dir)), read_yaml(p)) for p in files]
    merged, origins = merge_parts(parts)
    return Loaded(_reject_stamps(validate_state(merged, str(directory)), str(directory)), base_dir, origins)


def _where(origins: dict[str, str], oid: str, detail: str = "") -> str:
    origin = origins.get(oid, "?")
    return f"{origin}: {oid}{'.' + detail if detail else ''}"


def check_state(state: m.DecisionState, origins: dict[str, str] | None = None) -> list[Issue]:
    """Referential, unit and preference-model checks the schema cannot express."""
    origins = origins or {}
    issues: list[Issue] = []
    idx = index(state)

    def err(oid: str, detail: str, message: str, severity: str = "error") -> None:
        issues.append(Issue(severity, _where(origins, oid, detail), message))

    def ref(oid: str, detail: str, target: str | None, cls: type | tuple[type, ...]) -> BaseModel | None:
        if target is None:
            return None
        obj = idx.get(target)
        if obj is None:
            err(oid, detail, f"refers to {target}, which does not exist")
            return None
        if not isinstance(obj, cls):
            names = cls.__name__ if isinstance(cls, type) else "/".join(c.__name__ for c in cls)
            err(oid, detail, f"refers to {target}, a {type(obj).__name__}, not a {names}")
            return None
        return obj

    for _, obj in iter_objects(state):
        if getattr(obj, "owner_role", None):
            ref(obj.id, "owner_role", obj.owner_role, m.Role)

    for c in items(state.criteria):
        measure = ref(c.id, "measure", c.measure, m.Measure)
        ref(c.id, "objective", c.objective, m.Objective)
        if math.isclose(c.anchor_worst, c.anchor_best):
            err(c.id, "anchor_best", "anchor_best equals anchor_worst")
        if measure is not None and c.points:
            xs = [p.x for p in c.points]
            if xs != sorted(xs) or len(set(xs)) != len(xs):
                err(c.id, "points", "points must have strictly increasing x")
    weights = [c.weight for c in items(state.criteria)]
    if weights and not math.isclose(math.fsum(weights), 1.0, abs_tol=1e-9):
        issues.append(Issue("error", "criteria", f"weights sum to {math.fsum(weights):.12g}, not 1"))

    for r in items(state.requirements):
        measure = ref(r.id, "measure", r.measure, m.Measure)
        if measure is not None and r.unit != measure.unit:
            err(r.id, "unit", f"unit {r.unit!r} does not match {measure.id} unit {measure.unit!r}")
        for alt in items(r.applies_to):
            ref(r.id, "applies_to", alt, m.Alternative)

    for o in items(state.observations):
        ref(o.id, "alternative", o.alternative, m.Alternative)
        measure = ref(o.id, "measure", o.measure, m.Measure)
        evidence = ref(o.id, "evidence", o.evidence, m.Evidence)
        if measure is not None and o.unit != measure.unit:
            err(o.id, "unit", f"unit {o.unit!r} does not match {measure.id} unit {measure.unit!r}")
        if o.locator is not None and evidence is not None and not evidence.document_digest:
            err(o.id, "locator", f"has a locator, but {evidence.id} is not an indexed document (no document_digest), "
                "so locators_current ignores it", "warning")
    for e in items(state.evidence_items):
        if e.document_digest and not e.document_path:
            err(e.id, "document_path", "has a document_digest but no document_path, so its locators cannot be "
                "verified", "warning")

    alternatives = [a.id for a in items(state.alternatives)]
    produced: Counter = Counter()
    for o in items(state.observations):
        produced[(o.alternative, o.measure, "observation")] += 1
    for s in items(state.evaluation_specifications):
        for alt in items(s.applies_to):
            ref(s.id, "applies_to", alt, m.Alternative)
        if s.spec_kind == "python" and not s.implementation:
            err(s.id, "implementation", "a python spec needs implementation: 'module.py:function'")
        for b in items(s.inputs):
            if (b.measure is None) == (b.ref is None):
                err(s.id, f"inputs.{b.arg}", "bind exactly one of measure or ref")
                continue
            if b.measure is not None:
                measure = ref(s.id, f"inputs.{b.arg}", b.measure, m.Measure)
                if measure is not None and b.unit != measure.unit:
                    err(s.id, f"inputs.{b.arg}", f"unit {b.unit!r} does not match {measure.id} unit {measure.unit!r}")
            else:
                source = ref(s.id, f"inputs.{b.arg}", b.ref, m.Assumption)
                if source is not None and b.unit != source.unit:
                    err(s.id, f"inputs.{b.arg}", f"unit {b.unit!r} does not match {source.id} unit {source.unit!r}")
        for out in items(s.outputs):
            measure = ref(s.id, f"outputs.{out.arg}", out.measure, m.Measure)
            if measure is not None and out.unit != measure.unit:
                err(s.id, f"outputs.{out.arg}", f"unit {out.unit!r} does not match {measure.id} unit {measure.unit!r}")
            for alt in items(s.applies_to) or alternatives:
                produced[(alt, out.measure, s.id)] += 1
    producers: dict[tuple[str, str], list[str]] = {}
    for (alt, measure, source), _ in produced.items():
        producers.setdefault((alt, measure), []).append(source)
    for (alt, measure), sources in sorted(producers.items()):
        if len(sources) > 1:
            issues.append(Issue("error", f"{alt}/{measure}", f"produced by more than one source: {sorted(sources)}"))

    for c in [*items(state.readiness_conditions), *items(state.bias_checks)]:
        if c.predicate == "review_status":
            if c.review is None:
                err(c.id, "review", "a review_status condition needs review: <HumanReview id>")
            else:
                ref(c.id, "review", c.review, m.HumanReview)
    # Scenarios are analysis inputs, not part of the decision: a dangling one is a warning (and is
    # reported by the scenario sweep), so removing an object does not force editing every scenario.
    for sc in items(state.scenarios):
        for a in items(sc.assignments):
            target = idx.get(a.target)
            if target is None:
                err(sc.id, "assignments", f"assigns {a.target}, which does not exist", "warning")
            elif a.slot not in type(target).model_fields:
                err(sc.id, "assignments", f"a {type(target).__name__} has no slot {a.slot!r}")
    collections = set(LIST_SLOTS) - {"reviews", "approvals"}  # a review cannot cover reviews or approvals
    for rv in items(state.reviews):
        for target in items(rv.covers):
            if target.startswith("all:"):
                if target[4:] not in collections:
                    err(rv.id, "covers", f"unknown selector {target}; use all:<collection> "
                        "(reviews and approvals cannot be covered)")
            elif target not in idx:
                err(rv.id, "covers", f"covers {target}, which does not exist")
            elif target == rv.id:
                err(rv.id, "covers", "a review cannot cover itself")
    for rk in items(state.risks):
        for target in items(rk.affects):
            if target not in idx:
                err(rk.id, "affects", f"affects {target}, which does not exist")
        ref(rk.id, "accepted_by_role", rk.accepted_by_role, m.Role)
    for ap in items(state.approvals):
        ref(ap.id, "approved_alternative", ap.approved_alternative, m.Alternative)
        ref(ap.id, "approver_role", ap.approver_role, m.Role)
    policy = state.readiness_policy
    if policy is not None:
        for role in [*items(policy.approval_roles), *items(policy.risk_acceptance_roles)]:
            ref(policy.id, "roles", role, m.Role)
    return issues


def executable_gaps(state: m.DecisionState) -> list[Issue]:
    """What must be added before the kernel can run this state."""
    return [Issue("error", slot, "required to run") for slot in EXECUTABLE_REQUIRED
            if getattr(state, slot) in (None, [])]
