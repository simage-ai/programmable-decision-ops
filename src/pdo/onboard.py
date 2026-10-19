"""Onboarding: from a folder of reports and SysML models to a validated, run and studied decision.

`init` scaffolds a program directory (inputs/docs, inputs/models, baseline/, changes/, program.yaml and a
README). `status` reads the directory and says where it stands in three funnels, with the single next step:

1. Construct the decision graph: the question and roles, the SysML import, values from the documents (each
   with a locator), the framing that only people supply, evaluator wiring, and `pdo validate` as the gate.
2. Run it: the recommendation, readiness and actionable of the newest episode, and the pending actions
   that change what is known (accepting a value, validating an assumption, re-anchoring a locator).
3. Study it: `pdo study` (flips, scenarios, sensitivity, value of information, uncertainty and the
   Decision Package) for the current state, then the authorization that makes the decision actionable.

Every check reads the files and the kernel's results; nothing here edits the decision. Each step that is
not done names who acts, the command, the MCP tool and the section of skills/decision-onboarding/SKILL.md.
The baseline is the decision under construction until the first episode is built on it; after that every
change is a change set.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

from pdo import model as m
from pdo.load import EXECUTABLE_REQUIRED, LoadError, items, load_state, read_yaml

FUNNELS = {"construct": "Construct the decision graph", "run": "Run it", "study": "Study it"}
DOC_SUFFIXES = (".docx", ".pdf", ".md", ".txt")
MODEL_SUFFIXES = (".sysml",)
INPUT_DIRS = {"docs": Path("inputs/docs"), "models": Path("inputs/models")}
MAPPING_FILE = "mapping.yaml"
REPORTS_DIR = "reports"
STUDY_FILE = "study.json"
SKILL = "skills/decision-onboarding/SKILL.md"
# Gap kinds that change which numbers the kernel computes; the decision is not run while they are open.
BLOCKING_GAPS = ("alternatives", "unit", "producer-conflict", "constraint")
# Pending actions that change what is known (Run it), and the ones that authorize (the end of Study it).
GOVERNANCE_ACTIONS = ("approve", "complete_review", "treat_risk", "run_uncertainty_analysis")
# Actions that name what to do come before the generic ones they often explain (an alternative needs a value
# because one of its observations awaits acceptance).
GENERIC_ACTIONS = ("provide_value", "revise_alternatives", "resolve_tie")
NOUNS = {"alternatives": "alternative", "measures": "measure", "requirements": "requirement",
         "evidence_items": "evidence item", "observations": "observation", "assumptions": "assumption",
         "evaluation_specifications": "evaluation specification"}
# A recorded human action per pending-action kind (pdo program record --kind ...), with the status to ask for.
RECORD = {"review_applicability": ("applicability", "accepted"), "validate_assumption": ("assumption_validation",
          "validated"), "complete_review": ("review", "pass"), "treat_risk": ("risk_treatment", "mitigated"),
          "approve": ("approval", None)}


class OnboardError(ValueError):
    def __init__(self, message: str, hint: str = ""):
        super().__init__(message)
        self.hint = hint


@dataclass
class Step:
    id: str
    funnel: str
    title: str
    state: str  # done | todo | waiting (on an earlier step) | skip (does not apply)
    summary: str = ""
    details: list[str] = field(default_factory=list)
    next: dict | None = None  # {who, do, command, tool, skill}

    def to_dict(self) -> dict:
        return asdict(self)


def _next(do: str, command: str | None = None, tool: str | None = None, who: str | None = None,
          section: str | None = None) -> dict:
    return {"who": who or "you", "do": do, "command": command, "tool": tool,
            "skill": f"{SKILL}#{section}" if section else SKILL}


def _sha256(path: Path) -> str:  # as pdo.sysml records a model file: line endings normalized
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def _files(directory: Path, suffixes: tuple[str, ...]) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(p for p in directory.rglob("*") if p.is_file() and p.suffix.lower() in suffixes)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "decision"


def _count(n: int, noun: str) -> str:
    return f"{n} {noun}{'' if n == 1 else 's'}"


def _q(path: Path | str) -> str:
    text = str(path)
    return f'"{text}"' if re.search(r"\s", text) else text


# --- init -----------------------------------------------------------------------------------------

README = """\
# {name}

A pdo decision program, scaffolded by `pdo init`. `pdo onboard {dir}` shows where it stands and the next
step; skills/decision-onboarding/SKILL.md is the procedure an agent follows with you.

| Where | What goes there |
|---|---|
| `inputs/docs/` | Reports to take values from (Word, PDF, Markdown), each value with the passage that states it |
| `inputs/models/` | SysML v2 textual models (`.sysml`) and their `mapping.yaml` |
| `baseline/` | The decision under construction: `roles.yaml`, `sysml-*.yaml`, `documents.yaml`, `framing.yaml` |
| `evaluators.py` | Python functions behind computed measures, if the models have analyses |
| `changes/` | One change set per later episode: every change after the baseline, including approvals |
| `reports/` | What `pdo study` writes: the Decision Package, sensitivity and uncertainty results |

The three funnels:

1. **Construct the decision graph.** State the question and who holds which role; import the models
   (`pdo sysml import`); take values from the documents (`pdo docs extract --baseline`); frame the decision
   with its owner (criteria, anchors, weights, decision rule, readiness); wire the evaluators. `pdo validate`
   is the gate.
2. **Run it.** `pdo run`, `pdo explain`, and the pending actions by role, each recorded as an episode.
3. **Study it.** `pdo study`: flip points, scenarios, sensitivity, value of information, uncertainty and the
   Decision Package. Then the owner authorizes.

Nothing in this directory is a judgement pdo made: the framing, every acceptance and every approval come
from the people who hold the roles.
"""


def init_program(directory: str | Path, name: str | None = None, question: str | None = None,
                 owner: str | None = None, docs: list[str | Path] = (), models: list[str | Path] = (),
                 program_id: str | None = None) -> dict:
    """Scaffold a program directory and copy the inputs into it. Refuses a directory that already holds a
    program or a baseline. Writes only what the person stated: no criteria, weights or other framing."""
    root = Path(directory).expanduser()
    if (root / "program.yaml").exists() or (root / "baseline").exists():
        raise OnboardError(f"{root} already holds a decision (program.yaml or baseline/)",
                           f"Run `pdo onboard {_q(root)}` to see where it stands.")
    if owner is not None and not owner.startswith("role:"):
        raise OnboardError(f"{owner!r} is not a role id", "Role ids look like role:program-manager.")
    sources = {"docs": [Path(p).expanduser() for p in docs], "models": [Path(p).expanduser() for p in models]}
    copies: list[tuple[Path, Path]] = []
    for kind, paths in sources.items():
        suffixes = DOC_SUFFIXES if kind == "docs" else (*MODEL_SUFFIXES, ".yaml", ".yml")
        for src in paths:
            if not src.exists():
                raise OnboardError(f"{src}: no such file or directory")
            found = ([(f, f.relative_to(src)) for f in _files(src, suffixes)] if src.is_dir()
                     else [(src, Path(src.name))])
            for file, rel in found:
                if file.suffix.lower() not in suffixes:
                    raise OnboardError(f"{file}: not a {'report' if kind == 'docs' else 'SysML model'}",
                                       f"{kind} take {', '.join(suffixes)} files or directories of them.")
                copies.append((file, root / INPUT_DIRS[kind] / rel))
    created = []
    for d in (*INPUT_DIRS.values(), Path("baseline"), Path("changes")):
        (root / d).mkdir(parents=True, exist_ok=True)
        created.append(f"{d}/")
    for src, dest in copies:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
    title = name or root.resolve().name.replace("-", " ").replace("_", " ").strip().capitalize()
    meta: dict[str, Any] = {"id": program_id or f"prog:{_slug(root.resolve().name)}", "name": title}
    if question:
        meta["question"] = question
    if owner:
        meta["decision_owner_role"] = owner
    meta.update(baseline_episode="ep:e00", baseline_dir="baseline", changes_dir="changes")
    header = ("# Scaffolded by `pdo init`. The baseline (ep:e00) is the decision as first studied; every later\n"
              "# change is a change set in changes/. `pdo onboard` shows the next step.\n")
    (root / "program.yaml").write_text(header + yaml.safe_dump(meta, sort_keys=False, allow_unicode=True, width=110),
                                       encoding="utf-8")
    created.append("program.yaml")
    if owner:
        roles = {"roles": [{"id": owner, "description": "Owns the decision."}]}
        (root / "baseline" / "roles.yaml").write_text(
            "# Who holds which role. Add the approvers and the owners of each kind of evidence.\n"
            + yaml.safe_dump(roles, sort_keys=False, allow_unicode=True), encoding="utf-8")
        created.append("baseline/roles.yaml")
    (root / "README.md").write_text(README.format(name=title, dir=_q(root)), encoding="utf-8")
    created.append("README.md")
    return {"path": str(root), "program": meta["id"], "created": created,
            "copied": [str(dest.relative_to(root)) for _, dest in copies], **_where_next(status(root))}


def _where_next(report: dict) -> dict:
    return {"next": report["next"], "stage": report["stage"]}


# --- status ---------------------------------------------------------------------------------------

@dataclass
class _Context:
    root: Path
    shown: str  # the path as the person passed it, for commands
    is_program: bool
    meta: dict
    baseline_dir: Path
    changes: list[Path]
    state: m.DecisionState | None = None
    origins: dict = field(default_factory=dict)
    load_error: str | None = None

    def cmd(self, text: str) -> str:
        return text.replace("DIR", _q(self.shown))

    @property
    def owner(self) -> str | None:
        return self.meta.get("decision_owner_role")

    @property
    def constructing(self) -> bool:
        """The baseline can still change: no episode is built on it."""
        return not self.changes


def _context(path: str | Path) -> _Context:
    root = Path(path).expanduser()
    if not root.exists():
        raise OnboardError(f"{root}: no such directory", "Start one with `pdo init DIR --question ...`.")
    if root.is_file():
        raise OnboardError(f"{root} is a file", "pdo onboard reads a program directory (pdo init makes one).")
    is_program = (root / "program.yaml").exists()
    meta = read_yaml(root / "program.yaml") if is_program else {}
    baseline = root / (meta.get("baseline_dir") or "baseline") if is_program else root
    changes_dir = root / (meta.get("changes_dir") or "changes")
    changes = sorted(changes_dir.glob("*.yaml")) if is_program and changes_dir.is_dir() else []
    ctx = _Context(root, str(path), is_program, meta, baseline, changes)
    if baseline.is_dir() and any(baseline.glob("*.yaml")):
        try:
            loaded = load_state(root)
            ctx.state, ctx.origins = loaded.state, loaded.origins
        except LoadError as exc:
            ctx.load_error = str(exc)
    return ctx


def _question(ctx: _Context) -> Step:
    s = Step("question", "construct", "Question and owner", "done")
    if not ctx.is_program:
        s.state, s.summary = "skip", "a plain state directory: no program.yaml"
        return s
    missing = [k for k in ("question", "decision_owner_role") if not ctx.meta.get(k)]
    if missing:
        s.state, s.summary = "todo", f"program.yaml has no {' or '.join(missing)}"
        s.next = _next("Ask the decision owner for the decision in one sentence and their role id; set question and "
                       "decision_owner_role in DIR/program.yaml".replace("DIR", ctx.shown),
                       tool=None, who="the decision owner", section="1-intake")
        return s
    s.summary = f"{ctx.meta['question'].strip()} (owner {ctx.owner})"
    return s


def _roles(ctx: _Context) -> Step:
    s = Step("roles", "construct", "Roles", "done")
    roles = [r.id for r in items(ctx.state.roles)] if ctx.state else []
    if ctx.load_error and not roles:
        s.state, s.summary = "waiting", "the baseline does not load (see the validation step)"
        return s
    if not roles or (ctx.owner and ctx.owner not in roles):
        s.state = "todo"
        s.summary = "no roles yet" if not roles else f"{ctx.owner} is the owner but not a role in the baseline"
        s.next = _next("Ask who holds which role (the owner, the approvers, who owns cost, test and engineering "
                       "evidence) and list them in DIR/baseline/roles.yaml".replace("DIR", ctx.shown),
                       tool="pdo_describe_schema (Role)", who=ctx.owner or "the decision owner", section="1-intake")
        return s
    s.summary = ", ".join(roles)
    return s


def _inputs(ctx: _Context, docs: list[Path], models: list[Path]) -> Step:
    s = Step("inputs", "construct", "Inputs", "done")
    authored = bool(ctx.state and items(ctx.state.alternatives))
    if not docs and not models and not authored:
        s.state, s.summary = "todo", "no reports, no models and no alternatives yet"
        s.next = _next("Put the reports in DIR/inputs/docs and the SysML models in DIR/inputs/models (or write the "
                       "alternatives and measures by hand in the baseline)".replace("DIR", ctx.shown),
                       who="the decision owner", section="1-intake")
        return s
    parts = [_count(len(docs), "document"), _count(len(models), "model")]
    s.summary = ", ".join(parts) + ("" if docs or models else " (alternatives written by hand)")
    return s


def _models(ctx: _Context, models: list[Path]) -> Step:
    s = Step("models", "construct", "SysML import", "done")
    if not models:
        s.state, s.summary = "skip", "no SysML models in inputs/models"
        return s
    models_dir = ctx.root / INPUT_DIRS["models"]
    mapping = models_dir / MAPPING_FILE
    map_arg = f" --map {_q(Path(ctx.shown) / INPUT_DIRS['models'] / MAPPING_FILE)}" if mapping.exists() else ""
    import_cmd = ctx.cmd(f"pdo sysml import DIR/{INPUT_DIRS['models']}{map_arg} --out DIR/baseline")
    report_file = ctx.baseline_dir / "sysml-import.json"
    if not report_file.exists():
        s.state, s.summary = "todo", f"{len(models)} model{'s' if len(models) != 1 else ''} not imported"
        do = ("Import the models into the baseline" if mapping.exists() else
              f"Dry-run the import (pdo sysml import DIR/{INPUT_DIRS['models']} --json), write "
              f"DIR/{INPUT_DIRS['models']}/{MAPPING_FILE} with the person (decision ids, units, which element holds "
              "the alternatives), then import into the baseline").replace("DIR", ctx.shown)
        s.next = _next(do, import_cmd, "pdo_import_sysml", section="2-models")
        return s
    report = json.loads(report_file.read_text(encoding="utf-8"))
    imported = {src["sha256"] for src in report.get("sources", [])}
    stale = [p for p in models if _sha256(p) not in imported]
    gaps = report.get("gaps", [])
    blocking = [g for g in gaps if g["kind"] in BLOCKING_GAPS]
    counts = report.get("counts", {})
    s.summary = ", ".join(_count(n, NOUNS.get(k, k.replace("_", " "))) for k, n in counts.items() if n) \
        or "nothing mapped"
    s.details = [f"gap {g['kind']}: {g['element']}: {g['reason']}" for g in gaps if g["kind"] != "implementation"]
    if report.get("values_elsewhere"):
        measures = sorted({v["measure"] for v in report["values_elsewhere"]})
        s.details.append(f"values taken from documents, not the model: {', '.join(measures)}")
    if stale:
        s.state = "todo"
        s.summary = f"changed since the import: {', '.join(p.name for p in stale)}"
        s.next = _next("Import again (the sysml-*.yaml files are rewritten; your framing is left alone), and tell "
                       "the person what changed", import_cmd, "pdo_import_sysml", section="2-models")
    elif blocking:
        s.state = "todo"
        s.summary = f"{len(blocking)} gap{'s' if len(blocking) != 1 else ''} that change the numbers"
        g = blocking[0]
        s.next = _next(f"Resolve the {g['kind']} gap on {g['element']} with the person ({g['reason']}): fix the model "
                       "or extend the mapping, then import again", import_cmd, "pdo_import_sysml",
                       who="you, with the decision owner", section="2-models")
    return s


def _documents(ctx: _Context, docs: list[Path]) -> Step:
    s = Step("documents", "construct", "Values from documents", "done")
    if ctx.state is None:
        s.state = "waiting" if docs else "skip"
        s.summary = "the baseline does not load yet" if docs else "no documents"
        return s
    from pdo.provenance import verify

    rel = {p.resolve(): p.relative_to(ctx.root).as_posix() for p in docs}
    evidence = [e for e in items(ctx.state.evidence_items) if e.document_path]
    cited = {(ctx.root / e.document_path).resolve() for e in evidence}
    unregistered = [rel[p] for p in rel if p not in cited]
    if not docs and not evidence:
        s.state, s.summary = "skip", "no documents in inputs/docs"
        return s
    obs = list(items(ctx.state.observations))
    by_evidence = {e.id: [o for o in obs if o.evidence == e.id] for e in evidence}
    unused = [e.id for e in evidence if not by_evidence[e.id]]
    unlocated = [o.id for e in evidence for o in by_evidence[e.id] if o.locator is None]
    provisional = [o.id for o in obs if (o.applicability or "accepted") == "provisional"]
    problems = [x for x in verify(ctx.state, ctx.root) if x["severity"] == "error"]
    located = sum(1 for o in obs if o.locator is not None)
    paths = sorted({e.document_path for e in evidence})
    s.summary = (f"{located} located value{'s' if located != 1 else ''} from {len(paths)} document"
                 f"{'s' if len(paths) != 1 else ''}" + (f", {len(provisional)} provisional" if provisional else ""))
    for path in paths:
        evs = [e.id for e in evidence if e.document_path == path]
        n = sum(len(by_evidence[e]) for e in evs)
        s.details.append(f"{path}: {', '.join(evs)}: {n} value{'s' if n != 1 else ''}")
    extract = ctx.cmd("pdo docs extract DIR proposals.yaml --write --role ROLE" + (" --baseline" if ctx.constructing
                                                                                   and ctx.is_program else ""))
    if problems:
        s.state, s.summary = "todo", f"{len(problems)} locator problem{'s' if len(problems) != 1 else ''}"
        s.details = [f"{x['where']}: {x['message']}" for x in problems] + s.details
        s.next = _next("Fix the locators pdo docs verify reports (a quote no longer in its document)",
                       ctx.cmd("pdo docs verify DIR"), "pdo_docs_verify", section="3-documents")
    elif unregistered:
        s.state = "todo"
        s.summary = f"not yet a source: {', '.join(unregistered)}"
        doc = unregistered[0]
        s.next = _next(f"Read {doc} (pdo docs index, pdo docs show), ask who owns it and when it was issued, and "
                       "propose its values with exact quotes; register it in the proposals' evidence_items with "
                       f"document_path: {doc}", extract, "pdo_docs_extract", section="3-documents")
    elif unused or unlocated:
        s.state = "todo"
        s.summary = (f"no values yet from {', '.join(unused)}" if unused else
                     f"values from indexed documents without a locator: {', '.join(unlocated)}")
        s.next = _next("Propose the values with the exact passage that states each one", extract,
                       "pdo_docs_extract", section="3-documents")
    return s


def _framing(ctx: _Context) -> Step:
    s = Step("framing", "construct", "Framing", "done")
    if ctx.state is None:
        s.state, s.summary = "waiting", "the baseline does not load yet"
        return s
    structural = ("alternatives", "measures")
    missing = [slot for slot in EXECUTABLE_REQUIRED
               if slot not in structural and getattr(ctx.state, slot) in (None, [])]
    absent = [slot for slot in structural if getattr(ctx.state, slot) in (None, [])]
    if absent:
        s.state, s.summary = "waiting", f"no {' or '.join(absent)} yet (import the models or write them)"
        return s
    if missing:
        s.state = "todo"
        s.summary = f"missing {', '.join(missing)}"
        s.next = _next("Frame the decision with its owner: the criteria (which measures rank the alternatives, with "
                       "worst and best anchors), swing weights that sum to 1, the decision rule, the readiness policy "
                       "and as_of; write them in DIR/baseline/framing.yaml".replace("DIR", ctx.shown),
                       tool="pdo_describe_schema (Criterion, DecisionRule, ReadinessPolicy)",
                       who=ctx.owner or "the decision owner", section="4-framing")
        return s
    crit = items(ctx.state.criteria)
    s.summary = (f"{len(crit)} criteria ({', '.join(f'{c.id} {c.weight:g}' for c in crit if c.weight is not None)}), "
                 f"rule {ctx.state.decision_rule.rule_kind}")
    conditions = len(items(ctx.state.readiness_conditions)) + len(items(ctx.state.bias_checks))
    s.details.append(f"{conditions} readiness conditions and bias checks" if conditions else
                     "no readiness conditions or bias checks: readiness checks nothing (propose the standard set)")
    return s


def _evaluators(ctx: _Context) -> Step:
    s = Step("evaluators", "construct", "Evaluators", "done")
    specs = list(items(ctx.state.evaluation_specifications)) if ctx.state else []
    if not specs:
        s.state, s.summary = (("skip", "no computed measures") if ctx.state
                              else ("waiting", "the baseline does not load"))
        return s
    unwired = []
    for spec in specs:
        impl = spec.implementation
        kind = getattr(spec.spec_kind, "value", spec.spec_kind)
        if kind not in (None, "python"):
            continue
        if not impl:
            unwired.append(f"{spec.id} (no implementation)")
        elif ".py:" in impl and not (ctx.root / impl.split(":", 1)[0]).exists():
            unwired.append(f"{spec.id} ({impl.split(':', 1)[0]} does not exist)")
    s.summary = ", ".join(f"{x.id} = {x.implementation or 'placeholder'}" for x in specs)
    if unwired:
        s.state, s.summary = "todo", f"not runnable: {', '.join(unwired)}"
        s.next = _next("Ask the owner how the measure is computed, write it as a Python function in DIR/evaluators.py "
                       "(keyword arguments named by the spec's input args), and set implementation and "
                       "implementation_version under evaluations: in the mapping; then import again"
                       .replace("DIR", ctx.shown), tool="pdo_import_sysml", who="you, with the evaluator's owner",
                       section="5-evaluators")
    return s


def _valid(ctx: _Context, earlier: list[Step]) -> tuple[Step, dict | None]:
    from pdo import tools

    s = Step("valid", "construct", "Validation", "done")
    if ctx.load_error:
        s.state, s.summary = "todo", "the baseline does not load"
        s.details = ctx.load_error.splitlines()
        s.next = _next("Fix the files so they load", ctx.cmd("pdo validate DIR"), "pdo_validate", section="6-validate")
        return s, None
    if ctx.state is None:
        s.state, s.summary = "waiting", "nothing in the baseline yet"
        return s, None
    if any(x.state == "todo" for x in earlier):
        s.state, s.summary = "waiting", "finish the steps above first"
        return s, None
    result = tools.validate(ctx.root)
    if not result.get("ok") or not result.get("valid"):
        errors = result.get("errors") or [result.get("error", "invalid")]
        s.state, s.summary, s.details = "todo", f"{len(errors)} error{'s' if len(errors) != 1 else ''}", errors
        s.next = _next(f"Fix: {errors[0]}", ctx.cmd("pdo validate DIR"), "pdo_validate", section="6-validate")
        return s, None
    s.summary = f"valid: {_count(result.get('objects') or 0, 'object')}" + (
        f", {_count(result['episodes'], 'episode')}" if result.get("episodes") else "")
    s.details = result.get("warnings") or []
    return s, result


def _action_next(ctx: _Context, action: dict) -> dict:
    kind, target, role = action["kind"], action["target"], action.get("role")
    who = role or "the decision owner"
    if kind in RECORD:
        record, status = RECORD[kind]
        flag = f" --status {status}" if status else ""
        verb = {"approve": f"Approve {target} only when {who} has decided to",
                "review_applicability": f"Ask {who} whether to accept {target} ({action['reason']})",
                "validate_assumption": f"Ask {who} whether {target} is validated",
                "complete_review": f"Ask {who} for the outcome of {target}",
                "treat_risk": f"Ask {who} how {target} is treated"}[kind]
        return _next(f"{verb}; record what they say as an episode",
                     ctx.cmd(f"pdo program record DIR --kind {record} --target {target} --role {role or 'ROLE'}{flag}"),
                     "pdo_record_human_action", who=who, section="7-run")
    if kind == "review_locator":
        return _next(f"Show {who} the new section behind {target}; when they confirm the value, re-anchor it "
                     "(the observation's id, the confirmed value and the new quote, accept: true)",
                     ctx.cmd(f"pdo docs extract DIR proposals.yaml --write --role {role or 'ROLE'}"),
                     "pdo_docs_extract",
                     who=who, section="7-run")
    if kind == "run_uncertainty_analysis":
        return _next("Decide under the declared rule (expected utility with chance constraints)",
                     ctx.cmd("pdo study DIR"), "pdo_study", section="8-study")
    return _next(f"{kind.replace('_', ' ')} {target} ({action['reason']}): write a change set in DIR/changes/ with "
                 "the person's answer, preview it, then write it".replace("DIR", ctx.shown),
                 ctx.cmd("pdo impact DIR CHANGE.yaml"), "pdo_apply_change", who=who, section="7-run")


def _study_record(ctx: _Context) -> dict | None:
    file = ctx.root / REPORTS_DIR / STUDY_FILE
    if not file.exists():
        return None
    try:
        return json.loads(file.read_text(encoding="utf-8"))
    except ValueError:
        return None


def _run_and_study(ctx: _Context, valid: bool) -> list[Step]:
    from pdo import tools

    run = Step("run", "run", "Recommendation", "waiting", "the decision is not valid yet")
    open_ = Step("evidence", "run", "Open inputs", "waiting", "the decision is not valid yet")
    study = Step("study", "study", "Study", "waiting", "the decision is not valid yet")
    decide = Step("authorize", "study", "Authorization", "waiting", "the decision is not valid yet")
    if not valid:
        return [run, open_, study, decide]
    from pdo.episodes import Program
    from pdo.evaluate import evaluate

    if ctx.is_program:
        program = Program.load(ctx.root)
        episode = program.newest()
        result = program.evaluate(episode, verify=False).run
    else:
        episode, result = None, evaluate(ctx.root)
    d, readiness, actionable = result.decision, result.readiness, result.actionable
    alts = [a.id for a in items(result.graph.state.alternatives)]
    u = {a: (result.value(f"U/{a}") or {}).get("U") for a in alts}
    utilities = ", ".join(f"{a} {u[a]:.3f}" if u[a] is not None else f"{a} ?" for a in alts)
    run.state = "done"
    rec = ", ".join(d["recommended"]) or "no recommendation"
    run.summary = f"{episode + ': ' if episode else ''}{rec}; readiness {readiness['state']}; actionable " \
                  f"{'yes' if actionable['actionable'] else 'no'}"
    run.details = [f"utilities: {utilities}", f"feasible {d['feasible']}, infeasible {d['infeasible']}, "
                   f"unresolved {d['unresolved']}"]
    actions = tools._actions(result)
    evidence = sorted((a for a in actions if a["kind"] not in GOVERNANCE_ACTIONS),
                      key=lambda a: a["kind"] in GENERIC_ACTIONS)
    governance = [a for a in actions if a["kind"] in GOVERNANCE_ACTIONS]
    if evidence:
        open_.state = "todo"
        open_.summary = f"{len(evidence)} pending action{'s' if len(evidence) != 1 else ''} that change what is known"
        open_.details = [f"{a['kind']} {a['target']} ({a['role'] or 'unassigned'}): {a['reason']}" for a in evidence]
        open_.next = _action_next(ctx, evidence[0])
    else:
        open_.state, open_.summary = "done", "nothing pending that changes the inputs"
    digest = actionable.get("state_digest")
    record = _study_record(ctx)
    fresh = bool(record and record.get("state_digest") == digest and record.get("episode") == episode)
    if fresh:
        study.state = "done"
        study.summary = record.get("headline", "studied")
        study.details = [f"{k}: {v}" for k, v in (record.get("findings") or {}).items()]
        governance = [a for a in governance if a["kind"] != "run_uncertainty_analysis"]
    else:
        study.state = "todo"
        study.summary = ("no study yet" if record is None else
                         f"the study in {REPORTS_DIR}/ is for {record.get('episode') or 'the state'} at another state")
        study.next = _next("Study the decision: flip points, scenarios, sensitivity and value of information, "
                           "uncertainty under the declared rule, and the Decision Package", ctx.cmd("pdo study DIR"),
                           "pdo_study", section="8-study")
    if actionable["actionable"]:
        decide.state, decide.summary = "done", f"actionable: {actionable.get('reason') or 'approved'}"
    elif governance:
        decide.state = "todo"
        decide.summary = f"{len(governance)} action{'s' if len(governance) != 1 else ''} before it is actionable"
        decide.details = [f"{a['kind']} {a['target']} ({a['role'] or 'unassigned'}): {a['reason']}" for a in governance]
        decide.next = _action_next(ctx, governance[0])
    else:
        decide.state = "todo"
        decide.summary = f"not actionable: {actionable.get('reason')}"
        decide.next = _next(f"Not actionable ({actionable.get('reason')}); see what holds it",
                            ctx.cmd("pdo actions DIR"),
                            "pdo_pending_actions", section="9-authorize")
    return [run, open_, study, decide]


def status(path: str | Path) -> dict:
    """Where a decision stands in the three funnels, with the single next step. Reads only."""
    ctx = _context(path)
    docs = _files(ctx.root / INPUT_DIRS["docs"], DOC_SUFFIXES)
    models = _files(ctx.root / INPUT_DIRS["models"], MODEL_SUFFIXES)
    steps = [_question(ctx), _roles(ctx), _inputs(ctx, docs, models), _models(ctx, models), _documents(ctx, docs),
             _framing(ctx), _evaluators(ctx)]
    valid_step, validated = _valid(ctx, steps)
    steps.append(valid_step)
    steps += _run_and_study(ctx, validated is not None)
    todo = [s for s in steps if s.state == "todo"]
    nxt = todo[0] if todo else None
    stage = nxt.funnel if nxt else "done"
    funnels = []
    for key, title in FUNNELS.items():
        mine = [s for s in steps if s.funnel == key]
        applicable = [s for s in mine if s.state != "skip"]
        done = sum(1 for s in applicable if s.state == "done")
        funnels.append({"funnel": key, "title": title, "done": done, "of": len(applicable),
                        "complete": done == len(applicable)})
    finished = _next("Nothing is pending: the decision is actionable. When an input changes, write a change set "
                     "(preview it with pdo impact) and re-evaluate with pdo program refresh",
                     ctx.cmd("pdo program refresh DIR"), "pdo_refresh", section="10-maintain")
    return {"path": str(ctx.root), "program": ctx.meta.get("id"), "name": ctx.meta.get("name"),
            "constructing": ctx.constructing, "stage": stage, "funnels": funnels,
            "steps": [s.to_dict() for s in steps],
            "next": {**nxt.next, "step": nxt.id} if nxt and nxt.next else (None if nxt else finished),
            "then": [{"step": s.id, **s.next} for s in todo[1:4] if s.next]}


def render(report: dict) -> str:
    glyph = {"done": "✓", "todo": "→", "waiting": "·", "skip": "-"}
    lines = [f"{report.get('name') or report['path']}" + (f" ({report['program']})" if report.get("program") else "")]
    for i, f in enumerate(report["funnels"], 1):
        lines.append(f"\n{i} {f['title']}  [{f['done']}/{f['of']}]")
        for s in (x for x in report["steps"] if x["funnel"] == f["funnel"]):
            lines.append(f"  {glyph[s['state']]} {s['title']:<22} {s['summary']}")
            if s["state"] in ("todo", "done"):
                lines += [f"      {d}" for d in s["details"][:6]]
                if len(s["details"]) > 6:
                    lines.append(f"      ... and {len(s['details']) - 6} more")
    n = report["next"]
    if n:
        lines += ["", f"Next ({n['who']}): {n['do']}"]
        if n.get("command"):
            lines.append(f"  {n['command']}")
        if n.get("tool"):
            lines.append(f"  MCP: {n['tool']}   skill: {n['skill']}")
    for t in report.get("then", []):
        lines.append(f"Then: {t['do'][:110]}{'...' if len(t['do']) > 110 else ''}")
    return "\n".join(lines)
