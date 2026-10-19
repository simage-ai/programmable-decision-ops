"""Pending actions: what people must do before a decision is actionable, routed to the owning role.

With one user these are a to-do list; in a collaborative harness each role is a person to notify.
Action ids are content digests of (kind, target), so the same open action keeps its id across runs and
episodes and notifications can be deduplicated.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from pdo.canonical import digest
from pdo.load import index, items
from pdo.run import RunResult


@dataclass(frozen=True)
class PendingAction:
    id: str
    kind: str
    target: str
    role: str | None
    reason: str
    source: str  # the node that raised it

    def to_dict(self) -> dict:
        return asdict(self)


def pending_actions(result: RunResult, role: str | None = None) -> list[PendingAction]:
    state = result.graph.state
    objects = index(state)
    decision = result.decision
    involved = set(decision["recommended"]) | set(decision["unresolved"])
    policy_roles = sorted(items(state.readiness_policy.approval_roles)) if state.readiness_policy else []

    raised: list[tuple[str, dict, str | None]] = []  # (source node, action, fallback role)
    for nid in result.graph.order:
        node = result.graph.nodes[nid]
        value = result.value(nid)
        if not isinstance(value, dict) or not value.get("actions"):
            continue
        if node.kind == "condition":
            if value.get("status") == "pass":
                continue
            fallback = getattr(objects.get(node.subject), "owner_role", None)
        elif node.kind == "measure":
            if node.alternative not in involved:
                continue
            fallback = None
        elif node.kind == "actionable":
            fallback = policy_roles[0] if policy_roles else None
        else:
            continue
        for action in value["actions"]:
            raised.append((nid, action, fallback))

    out: dict[tuple[str, str], PendingAction] = {}
    for source, action, fallback in raised:
        key = (action["kind"], action["target"])
        if key in out:
            continue
        target = objects.get(action["target"])
        owner = getattr(target, "owner_role", None)
        if owner is None and action["kind"] in ("review_locator", "review_applicability") and target is not None:
            # whoever owns the evidence: the person who may accept or re-anchor a value taken from it
            owner = getattr(objects.get(getattr(target, "evidence", None)), "owner_role", None)
        owner = fallback if action["kind"] == "approve" else (owner or fallback)
        out[key] = PendingAction(digest(*key), action["kind"], action["target"], owner, action["reason"], source)
    actions = sorted(out.values(), key=lambda a: (a.role or "", a.kind, a.target))
    return [a for a in actions if role is None or a.role == role]
