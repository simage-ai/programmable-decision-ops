"""`pdo program ...` and `pdo impact`: maintain a decision program episode by episode.

    pdo program status PATH       the episode tree: recommendation, readiness and actionable per episode
    pdo program refresh PATH      re-evaluate every episode selectively and verify it against a full run
    pdo program record PATH ...   write a change set recording an approval or a review
    pdo impact PATH CHANGE.yaml   dry run: what a change set would touch, rerun and change

Refresh exits 1 when any episode's selective result differs from its full recomputation, so it can
gate CI. Record never edits an existing file: a human action is a new episode, a child of the newest
one (or of --parent), validated before it is written.
"""

from __future__ import annotations

import json
from pathlib import Path

import typer

KindOpt = typer.Option(..., "--kind", help="approval | review | applicability | risk_treatment | assumption_validation")
TargetOpt = typer.Option(..., "--target", help="The alternative approved, or the review, observation, risk or "
                                                "assumption acted on.")
RoleOpt = typer.Option(..., "--role", help="The role acting (approver_role for an approval).")
StatusOpt = typer.Option(None, "--status", help="Review: pass | fail | pending (default pass); applicability: accepted "
                                                "| provisional | rejected; risk_treatment: untreated | mitigated | "
                                                "accepted | unacceptable; assumption_validation: validated | ...")
IdOpt = typer.Option(None, "--id", help="Episode id of the new change set (default: the next in sequence).")
ParentOpt = typer.Option(None, "--parent", help="Parent episode (default: the newest episode).")


def register(app: typer.Typer) -> None:
    """Add the `program` group and the top-level `impact` command to the root app."""
    from pdo.cli import JsonOpt, PathArg, emit, fail

    group = typer.Typer(no_args_is_help=True, help="Decision programs: episodes, refresh, human actions.")
    app.add_typer(group, name="program")

    @group.command()
    def status(path: Path = PathArg, as_json: bool = JsonOpt) -> None:
        """The episode tree with each episode's recommendation, readiness and actionable."""
        from pdo.actions import pending_actions

        program = _load(path, as_json, fail)
        evaluations = _guard(lambda: program.evaluate_all(verify=False), as_json, fail)
        rows = {ev.episode: ev.summary() for ev in evaluations}
        tree = _tree(program)
        newest = program.newest()
        pending = [p.to_dict() for p in pending_actions(program.evaluate(newest, verify=False).run)]
        data = {"program": program.meta.id, "name": program.meta.name, "baseline": program.baseline_episode,
                "newest": newest, "episodes": [{**rows[ep], "depth": depth} for ep, depth, _ in tree],
                "pending_actions": pending}
        emit(data, as_json, _status_text(program, rows, tree, newest, pending))

    @group.command()
    def refresh(path: Path = PathArg, as_json: bool = JsonOpt) -> None:
        """Re-evaluate every episode selectively, verify each against a full run, and report the savings."""
        program = _load(path, as_json, fail)
        evaluations = _guard(lambda: program.evaluate_all(mode="cutoff", verify=True), as_json, fail)
        rows = [ev.summary() for ev in evaluations]
        children = [r for r in rows if r["parent"] is not None]
        failed = [r["episode"] for r in children if r["verified"] is False]
        data = {"program": program.meta.id, "episodes": rows, "all_verified": not failed, "unverified": failed,
                "executed": sum(r["executed"] for r in children),
                "computed_nodes": sum(r["computed_nodes"] for r in children)}
        if as_json:
            typer.echo(json.dumps({"ok": not failed, **data}, indent=1, default=str))
        else:
            typer.echo(_refresh_text(rows, data))
        if failed:
            raise typer.Exit(1)

    @group.command()
    def record(path: Path = PathArg, kind: str = KindOpt, target: str = TargetOpt, role: str = RoleOpt,
               status: str = StatusOpt, episode: str = IdOpt, parent: str = ParentOpt,
               as_json: bool = JsonOpt) -> None:
        """Record a person's action as a new change set, a child of the newest episode.

        approval: --role approves alternative --target. review: --role records existing review --target
        as --status (default pass), which also redoes a stale review. applicability, risk_treatment and
        assumption_validation set the observation's, risk's or assumption's status. The change set is
        validated before it is written to changes/, and the kernel stamps approvals and reviews.
        """
        program = _load(path, as_json, fail)
        if kind not in ("approval", "review"):
            from pdo import tools

            r = tools.record_human_action(path, kind, target, role, status, write=True, parent=parent)
            if not r.get("ok"):
                fail(r["error"] + (f"\nhint: {r['hint']}" if not as_json else ""), as_json)
            after = r.get("after") or {}
            text = (f"wrote {r['file']}: {r['episode']} (child of {r['parent']})\n"
                    f"{', '.join(after.get('recommended') or []) or 'no recommendation'}: readiness "
                    f"{after.get('readiness')}, actionable {'yes' if after.get('actionable') else 'no'}"
                    + "".join(f"\nwarning: {w}" for w in r.get("warnings") or []))
            emit({k: v for k, v in r.items() if k != "ok"}, as_json, text)
            return

        def build():
            change = program.human_action_change(kind, target, role, status, episode, parent)
            preview = program.preview(change, verify=False)  # validate before anything is written
            header = (f"Recorded by `pdo program record --kind {kind}`. Episodes are never rewritten:\n"
                      "to change this action, write a new change set.")
            return change, preview, program.write_change(change, f"{change.id.split(':', 1)[-1]}-{kind}", header)

        change, preview, file = _guard(build, as_json, fail)
        row = preview.summary()
        written = _relative(file, program.path)
        data = {"episode": change.id, "parent": change.parent, "file": written, "summary": row}
        rec = ", ".join(row["recommended"]) or "no recommendation"
        text = (f"wrote {written}: {change.id} (child of {change.parent})\n"
                f"{rec}: readiness {row['readiness']}, actionable {'yes' if row['actionable'] else 'no'}")
        emit(data, as_json, text)

    @app.command()
    def impact(path: Path = PathArg,
               changeset: Path = typer.Argument(..., help="A change-set YAML file (id and parent optional)."),
               episode: str = typer.Option(None, "--episode", "-e",
                                           help="Parent episode (default: the change set's parent, else the "
                                                "baseline)."),
               as_json: bool = JsonOpt) -> None:
        """Dry run a change set: its delta, impact set, nodes rerun versus reused, and before/after."""
        program = _load(path, as_json, fail)
        data = _guard(lambda: _impact(program, changeset, episode), as_json, fail)
        emit(data, as_json, _impact_text(data))


def _load(path: Path, as_json: bool, fail):
    from pdo.episodes import Program
    from pdo.evaluate import is_program

    if not is_program(path):
        fail(f"{path} is not a program directory (no program.yaml)", as_json)
    return _guard(lambda: Program.load(path), as_json, fail)


def _guard(thunk, as_json: bool, fail):
    """Run `thunk`, turning the kernel's expected errors into a clean CLI failure."""
    from pdo.graph import CompileError
    from pdo.load import LoadError

    try:
        return thunk()
    except (LoadError, CompileError, ValueError, KeyError) as exc:
        fail(str(exc), as_json)


def _relative(file: Path, root: Path) -> str:
    try:
        return str(file.relative_to(root))
    except ValueError:
        return str(file)


def _tree(program) -> list[tuple[str, int, str]]:
    """(episode, depth, prefix) in depth-first order, with box-drawing prefixes."""
    children = program.children()
    out: list[tuple[str, int, str]] = []

    def visit(ep: str, depth: int, lead: str, last: bool) -> None:
        prefix = "" if depth == 0 else lead + ("└─ " if last else "├─ ")
        out.append((ep, depth, prefix))
        kids = children[ep]
        for i, kid in enumerate(kids):
            visit(kid, depth + 1, "" if depth == 0 else lead + ("   " if last else "│  "), i == len(kids) - 1)

    visit(program.baseline_episode, 0, "", True)
    return out


def _table(header: list[str], rows: list[list[str]]) -> list[str]:
    widths = [max(len(str(c)) for c in col) for col in zip(header, *rows, strict=True)]
    return ["  ".join(str(c).ljust(w) for c, w in zip(row, widths, strict=True)).rstrip() for row in [header, *rows]]


def _status_text(program, rows: dict, tree, newest: str, pending: list[dict]) -> str:
    lines = [f"# {program.meta.name or program.meta.id}", ""]
    body = []
    for ep, _, prefix in tree:
        r = rows[ep]
        body.append([prefix + ep, r["as_of"], r["trigger"] or "baseline", ", ".join(r["recommended"]) or "none",
                     r["readiness"], "yes" if r["actionable"] else "no"])
    lines += _table(["episode", "as_of", "trigger", "recommended", "readiness", "actionable"], body)
    lines += ["", f"Newest episode: {newest}"]
    if pending:
        lines += [f"- [{p['role'] or 'unassigned'}] {p['kind']} {p['target']}: {p['reason']}" for p in pending]
    else:
        lines.append("No pending actions.")
    return "\n".join(lines)


def _refresh_text(rows: list[dict], data: dict) -> str:
    body = []
    for r in rows:
        impact = "-" if r["impact_size"] is None else f"{r['impact_size']}/{r['total_nodes']}"
        verified = {None: "-", True: "yes", False: "NO"}[r["verified"]]
        body.append([r["episode"], r["parent"] or "-", r["trigger"] or "baseline", impact,
                     f"{r['executed']}/{r['computed_nodes']}", verified])
    lines = _table(["episode", "parent", "trigger", "impact/nodes", "executed/computed", "verified"], body)
    children = [r for r in rows if r["parent"] is not None]
    lines.append("")
    if data["computed_nodes"]:
        share = 100 * data["executed"] / data["computed_nodes"]
        lines.append(f"{len(children)} child episodes executed {data['executed']} of {data['computed_nodes']} "
                     f"computed nodes ({share:.0f}%); the rest were reused.")
    if data["all_verified"]:
        lines.append("Every selective result equals its full recomputation.")
    else:
        for r in children:
            if r["verified"] is False:
                lines.append(f"NOT VERIFIED {r['episode']}: differs from full recomputation at "
                             f"{', '.join(r['mismatched'][:8])}")
    return "\n".join(lines)


def _cell(value) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    return (", ".join(value) or "-") if isinstance(value, list) else str(value)


def _impact(program, changeset: Path, episode: str | None) -> dict:
    from pydantic import ValidationError

    from pdo import model as m
    from pdo.graph import DECISION_KINDS, delta
    from pdo.load import read_yaml

    data = read_yaml(changeset)
    data.setdefault("id", "ep:dry-run")
    data.setdefault("parent", episode or program.baseline_episode)
    try:
        change = m.ChangeSet.model_validate(data)
    except ValidationError as exc:
        lines = [f"{changeset}: {'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()]
        raise ValueError("\n".join(lines)) from exc
    parent = episode or change.parent
    before = program.evaluate(parent, verify=False)
    after = program.preview(change, parent=parent, mode="cutoff", verify=True)
    d = delta(before.run.graph, after.run.graph)
    kinds = DECISION_KINDS | {"condition", "readiness", "actionable"}
    value_changes = sorted(nid for nid, node in after.run.graph.nodes.items() if node.kind in kinds
                           and nid in before.run.results
                           and before.run.results[nid].vdig != after.run.results[nid].vdig)
    row = after.summary()
    return {"episode": change.id, "parent": parent, "delta": {"changed": sorted(d.changed), "added": sorted(d.added),
                                                              "deleted": sorted(d.deleted)},
            **{k: row[k] for k in ("impact_size", "total_nodes", "executed", "reused", "computed_nodes",
                                   "verified", "mismatched")},
            "value_changes": value_changes, "before": before.run.summary(), "after": after.run.summary()}


def _impact_text(data: dict) -> str:
    d = data["delta"]
    roots = d["changed"] + d["added"] + d["deleted"]
    lines = [f"# {data['episode']} on {data['parent']} (dry run)", "",
             f"Delta: {len(d['changed'])} changed, {len(d['added'])} added, {len(d['deleted'])} deleted"
             + (f": {', '.join(roots[:10])}{', ...' if len(roots) > 10 else ''}" if roots else ""),
             f"Impact: {data['impact_size']} of {data['total_nodes']} nodes; executed {data['executed']} of "
             f"{data['computed_nodes']} computed nodes ({data['reused']} reused)",
             "Verified: selective equals full recomputation" if data["verified"]
             else f"NOT VERIFIED: differs at {', '.join(data['mismatched'][:8])}", ""]
    before, after = data["before"], data["after"]
    body = []
    for key in ("recommended", "feasible", "infeasible", "unresolved", "readiness", "actionable"):
        b, a = before[key], after[key]
        body.append([key, _cell(b), _cell(a), "" if b == a else "changed"])
    lines += _table(["", f"before ({data['parent']})", f"after ({data['episode']})", ""], body)
    if data["value_changes"]:
        lines += ["", f"Values that change: {', '.join(data['value_changes'][:16])}"
                  + (", ..." if len(data["value_changes"]) > 16 else "")]
    return "\n".join(lines)
