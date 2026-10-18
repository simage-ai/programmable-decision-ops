"""Agent tools: the kernel as plain functions that return JSON-ready dicts.

Each tool takes paths and plain values (strings, numbers, dicts or YAML text) and returns
{"ok": true, ...} on success, or {"ok": false, "error": ..., "hint": ...} when the request cannot be
carried out: a missing path, an unknown episode, node or role, a change set that does not validate. A
tool never raises for a user error, so an agent can read the hint and try again. A verdict is data,
not an error: an invalid state is {"ok": true, "valid": false, "errors": [...]}, and a refresh in
which some selective result differs from its full recomputation is {"ok": true, "all_verified": false}.

The same functions back the MCP server (pdo.mcp_server, one tool pdo_<name> per function) and can be
called from Python. They compute nothing themselves: every number they return comes from a node
evaluation, through pdo.report, pdo.actions, pdo.flip, pdo.package, pdo.episodes, pdo.sysml and
pdo.uncertainty.

Write tools never edit an existing file. apply_change and record_human_action preview by default; with
write=True each adds one new change-set file to the program's changes directory, a child of the newest
episode unless told otherwise, and keeps it only if the program loads back and the new episode
validates. package and import_sysml write only when given `out`.
"""

from __future__ import annotations

import dataclasses
import difflib
import functools
import math
import re
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ValidationError

from pdo import model as m
from pdo import sysml
from pdo.actions import pending_actions as kernel_pending_actions
from pdo.episodes import ChangeError, EpisodeEvaluation, Program
from pdo.evaluate import is_program
from pdo.flip import is_robust, scan_parameters, scenario_sweep, weight_flips
from pdo.graph import DECISION_KINDS, CompileError, compile_graph, delta
from pdo.load import LoadError, check_state, executable_gaps, index, items, load_state
from pdo.package import episode_record, record_json, render_markdown, validate_record_json, write_package
from pdo.report import explain_node, run_summary
from pdo.run import RunResult
from pdo.run import run as run_graph
from pdo.uncertainty import analyze_path
from pdo.uncertainty import summary as uncertainty_summary

_HERE = Path(__file__).resolve().parent
# An installed wheel ships the schema and the examples inside the package; a source checkout has them
# at the repository root.
SCHEMA_FILES = (_HERE / "schema" / "pdo.yaml", _HERE.parents[1] / "schema" / "pdo.yaml")
EXAMPLE_DIRS = (_HERE / "examples_data", _HERE.parents[1] / "examples")
COPY_IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store")

PATH_HINT = ("Pass a decision state file (.yaml), a directory of state files, or a program directory "
             "(with program.yaml). Relative paths resolve against the working directory, {cwd}; "
             "pdo_list_examples gives example paths.")
PROGRAM_HINT = ("Episodes, change sets and recorded human actions need a decision program: a directory with "
                "program.yaml (id, baseline_episode), the baseline state in baseline/ and change sets in "
                "changes/. examples/vehicle is one (pdo_list_examples).")
LOAD_HINT = ("The files do not form a valid decision state. pdo_validate lists every problem; "
             "pdo_describe_schema shows a class's slots.")
COMPILE_HINT = ("The state does not compile into an evaluation graph (missing framing, an unresolved evaluator, "
                "or a cycle). pdo_validate lists what is missing.")
CHANGE_HINT = ("Nothing was written. Fix each problem listed: targets must exist in the parent episode, a slot "
               "must belong to its object's class (pdo_describe_schema), and the resulting state must validate. "
               "Operations apply in the order remove, add, assign.")
NODE_HINT = ("Node ids: decision, readiness, actionable, state_digest, clock, U/<alt>, F/<alt>, g/<req>/<alt>, "
             "v/<crit>/<alt>, y/<alt>/<measure>, run/<spec>/<alt>, cond/<condition>, and each object id "
             "(criteria as <crit>#vf and <crit>#w). Start from decision and follow its inputs.")
SYSML_HINT = ("Pass SysML v2 textual files (.sysml) or directories holding them, and optionally a mapping file "
              "(path, YAML text or a dict). skills/sysml-to-decision/SKILL.md describes the procedure.")
SENSITIVITY_HINT = ("Inputs are '<object id>.<slot>': observation or assumption values, requirement "
                    "thresholds, criterion anchors or evaluator parameters. Run without param to see them "
                    "all (the tornado lists every one).")
DOCS_HINT = ("Documents are .docx, .pdf, .md or .txt files (Word and PDF need the docs extra: uv sync --extra docs). "
             "Quote values exactly as pdo_docs_read prints the section text; never paraphrase.")
UNCERTAINTY_HINT = ("Uncertain inputs are observations or assumptions with an `uncertainty` distribution; samples "
                    "must be at least 1 and the rule's chance_alpha in [0, 1).")

# Human actions recorded as change sets: the object class acted on, the slot set, its allowed values,
# the default value (None: the caller must choose), the episode trigger, and the slot that takes a
# rationale. Approvals and reviews are built by Program.human_action_change.
HUMAN_ACTIONS: dict[str, dict[str, Any]] = {
    "approval": {"cls": m.Alternative, "slot": None, "values": (), "default": None, "trigger": "approval"},
    "review": {"cls": m.HumanReview, "slot": "review_status", "values": tuple(e.value for e in m.ReviewStatus),
               "default": "pass", "trigger": "review", "rationale": "rationale"},
    "applicability": {"cls": m.Observation, "slot": "applicability",
                      "values": tuple(e.value for e in m.Applicability), "default": "accepted",
                      "trigger": "evidence", "rationale": "applicability_rationale"},
    "risk_treatment": {"cls": m.Risk, "slot": "treatment_status",
                       "values": tuple(e.value for e in m.TreatmentStatus), "default": None, "trigger": "review"},
    "assumption_validation": {"cls": m.Assumption, "slot": "validation_status",
                              "values": tuple(e.value for e in m.ValidationStatus), "default": "validated",
                              "trigger": "assumption"},
}
# What resolves each pending-action kind the kernel raises: a recorded human action, or a change set.
RESOLVED_BY = {"approve": "approval", "complete_review": "review", "review_applicability": "applicability",
               "treat_risk": "risk_treatment", "validate_assumption": "assumption_validation"}


class ToolError(Exception):
    """A request the tools cannot carry out, with a hint for the caller."""

    def __init__(self, error: str, hint: str):
        super().__init__(error)
        self.error, self.hint = error, hint


# --- results ------------------------------------------------------------------------------------

def plain(value: Any) -> Any:
    """A JSON-ready copy: models and dataclasses as dicts, dates as ISO strings, sets as sorted lists."""
    if isinstance(value, BaseModel):
        return plain(value.model_dump(mode="json", exclude_none=True))
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return plain(dataclasses.asdict(value))
    if isinstance(value, dict):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [plain(v) for v in value]
    if isinstance(value, set | frozenset):
        return sorted((plain(v) for v in value), key=str)
    if isinstance(value, Enum):
        return plain(value.value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)  # JSON has no infinity or NaN
    if value is None or isinstance(value, str | int | float | bool):
        return value
    return str(value)


def _ok(**data: Any) -> dict:
    return {"ok": True, **plain(data)}


def _failure(error: str, hint: str) -> dict:
    return {"ok": False, "error": error, "hint": hint}


def _message(exc: BaseException) -> str:
    if isinstance(exc, ValidationError):
        return "\n".join(f"{'.'.join(str(p) for p in e['loc']) or 'value'}: {e['msg']}" for e in exc.errors())
    if isinstance(exc, KeyError) and exc.args:
        return str(exc.args[0])
    return str(exc) or type(exc).__name__


def _hint(exc: BaseException, default: str) -> str:
    if getattr(exc, "hint", None):  # QuoteError, ProvenanceError
        return exc.hint
    if isinstance(exc, ChangeError):
        return CHANGE_HINT
    if isinstance(exc, sysml.SysMLError):
        return SYSML_HINT
    if isinstance(exc, CompileError):
        return COMPILE_HINT
    if isinstance(exc, LoadError):
        return LOAD_HINT
    if isinstance(exc, OSError):
        return PATH_HINT.format(cwd=Path.cwd())
    return default


def tool(hint: str) -> Callable:
    """Turn the kernel's expected errors into {"ok": false, "error", "hint"}; anything else is a defect and
    propagates."""

    def wrap(fn: Callable[..., dict]) -> Callable[..., dict]:
        @functools.wraps(fn)
        def inner(*args: Any, **kwargs: Any) -> dict:
            try:
                return fn(*args, **kwargs)
            except ToolError as exc:
                return _failure(exc.error, exc.hint)
            except (ValueError, KeyError, OSError, yaml.YAMLError, NotImplementedError) as exc:
                # LoadError, ChangeError, CompileError, SysMLError, UncertaintyError, EvaluatorError and
                # pydantic's ValidationError are all ValueErrors.
                return _failure(_message(exc), _hint(exc, hint))

        return inner

    return wrap


# --- resolving paths, programs and episodes ------------------------------------------------------

def _close(name: str, candidates) -> str:
    match = difflib.get_close_matches(name, [str(c) for c in candidates], n=3)
    return f"; did you mean {', '.join(match)}?" if match else ""


def _a(noun: str) -> str:
    return f"{'an' if noun[:1].lower() in 'aeiou' else 'a'} {noun}"


def _is_file(text: str | Path) -> bool:
    """Whether a string names an existing file; long YAML text can make the OS reject it as a name."""
    try:
        return Path(text).expanduser().is_file()
    except (OSError, ValueError):
        return False


def _path(path: str | Path | None) -> Path:
    if path is None or not str(path).strip():
        raise ToolError("no path given", PATH_HINT.format(cwd=Path.cwd()))
    p = Path(path).expanduser()
    if not p.exists():
        raise ToolError(f"{p}: no such file or directory", PATH_HINT.format(cwd=Path.cwd()))
    return p


def _program(path: str | Path) -> Program:
    p = _path(path)
    if not is_program(p):
        raise ToolError(f"{p} is not a program directory (no program.yaml)", PROGRAM_HINT)
    return Program.load(p)


def _episode(program: Program, episode: str) -> str:
    known = [program.baseline_episode, *sorted(program.changes)]
    if episode not in known:
        shown = ", ".join(known[:16]) + (", ..." if len(known) > 16 else "")
        raise ToolError(f"no episode {episode} in {program.meta.id}{_close(episode, known)}",
                        f"Episodes: {shown}. pdo_program_status shows the tree; omit the episode for the baseline.")
    return episode


@dataclass
class _Evaluated:
    result: RunResult
    path: Path
    episode: str | None = None
    parent: str | None = None
    evaluation: EpisodeEvaluation | None = None

    def where(self) -> dict:
        if self.evaluation is None:
            return {"path": str(self.path)}
        return {"path": str(self.path), "episode": self.episode, "parent": self.parent,
                "verified": self.evaluation.verified}


def _evaluated(path: str | Path, episode: str | None = None) -> _Evaluated:
    """A state or one program episode (default: the baseline), evaluated like `pdo run`."""
    p = _path(path)
    if is_program(p):
        program = Program.load(p)
        ep = _episode(program, episode or program.baseline_episode)
        evaluation = program.evaluate(ep)
        return _Evaluated(evaluation.run, p, ep, program.parent(ep), evaluation)
    if episode is not None:
        raise ToolError(f"{p} is not a program directory (no program.yaml), so it has no episodes",
                        "Omit the episode for a single state, or pass the program directory.")
    loaded = load_state(p)
    return _Evaluated(run_graph(compile_graph(loaded.state, loaded.base_dir)), p)


def _brief(result: RunResult) -> dict:
    """The decision and its readiness, as the kernel computed them."""
    d, r, a = result.decision, result.readiness, result.actionable
    return {"recommended": d["recommended"], "feasible": d["feasible"], "infeasible": d["infeasible"],
            "unresolved": d["unresolved"], "best_utility": d["best_utility"], "utilities": d["utilities"],
            "readiness": r["state"], "readiness_failed": r["failed"], "readiness_unknown": r["unknown"],
            "actionable": a["actionable"], "actionable_reason": a.get("reason")}


def _actions(result: RunResult, role: str | None = None) -> list[dict]:
    """Pending actions with the tool call that resolves each one."""
    out = []
    for action in kernel_pending_actions(result, role):
        row = action.to_dict()
        kind = RESOLVED_BY.get(action.kind)
        if kind is not None:
            row["resolve_with"] = {"tool": "pdo_record_human_action", "kind": kind, "target": action.target,
                                   "role": action.role}
        elif action.kind == "review_locator":  # a person confirms the value in the new version, then re-anchors it
            row["resolve_with"] = {"tool": "pdo_docs_extract", "target": action.target, "role": action.role}
        else:
            row["resolve_with"] = {"tool": "pdo_apply_change"}
        out.append(row)
    return out


# --- schema ---------------------------------------------------------------------------------------

@functools.lru_cache(maxsize=1)
def _schema_view():
    """The LinkML SchemaView of the pdo schema, or None without linkml-runtime or the schema files."""
    try:
        from linkml_runtime.utils.schemaview import SchemaView
    except ImportError:
        return None
    schema = next((p for p in SCHEMA_FILES if p.exists()), None)
    return SchemaView(str(schema)) if schema is not None else None


def _identity(mixins) -> str | None:
    marks = [x for x in mixins or [] if x in ("hashable", "unhashable")]
    return marks[0] if marks else None


def _linkml_classes(view) -> dict[str, dict]:
    out = {}
    for name, cls in view.all_classes().items():
        out[name] = {"name": name, "description": cls.description, "is_a": cls.is_a,
                     "abstract": bool(cls.abstract), "mixin": bool(cls.mixin)}
    return out


def _linkml_enums(view) -> dict[str, dict]:
    return {name: {"name": name, "description": e.description,
                   "values": {k: v.description for k, v in e.permissible_values.items()}}
            for name, e in view.all_enums().items()}


def _linkml_slots(view, class_name: str, enums: dict[str, dict]) -> list[dict]:
    classes = view.all_classes()
    slots = []
    for s in view.class_induced_slots(class_name):
        row = {"name": s.name, "range": s.range, "required": bool(s.required or s.identifier),
               "multivalued": bool(s.multivalued),
               "identity": "identifier" if s.identifier else _identity(s.mixins), "description": s.description}
        if s.range in enums:
            row["values"] = list(enums[s.range]["values"])
        if s.range in classes:
            row["inlined"] = bool(view.is_inlined(s))
        slots.append(row)
    return slots


def _model_classes() -> dict[str, type[BaseModel]]:
    return {name: obj for name, obj in vars(m).items() if isinstance(obj, type) and issubclass(obj, BaseModel)
            and obj is not m.ConfiguredBaseModel and issubclass(obj, m.ConfiguredBaseModel)}


def _pydantic_classes() -> dict[str, dict]:
    out = {}
    for name, cls in _model_classes().items():
        base = next((b.__name__ for b in cls.__bases__ if b is not m.ConfiguredBaseModel
                     and issubclass(b, m.ConfiguredBaseModel)), None)
        out[name] = {"name": name, "description": (cls.__doc__ or "").strip() or None, "is_a": base,
                     "abstract": False, "mixin": False}
    return out


def _pydantic_enums() -> dict[str, dict]:
    return {name: {"name": name, "description": (obj.__doc__ or "").strip() or None,
                   "values": {e.value: None for e in obj}}
            for name, obj in vars(m).items() if isinstance(obj, type) and issubclass(obj, Enum)
            and obj.__module__ == m.__name__}


def _type_name(annotation: Any) -> tuple[str, bool]:
    """(range, multivalued) from a generated annotation such as Optional[list[Observation]]."""
    text = re.sub(r"\b[a-z_]\w*\.|<class '|'>", "", str(annotation))  # drop module prefixes
    many = bool(re.search(r"\blist\[|\bList\[", text))
    names = [t for t in re.findall(r"[A-Za-z_]\w*", text) if t not in ("Optional", "Union", "list", "List",
                                                                          "dict", "Dict", "None", "NoneType")]
    return (names[0] if names else text), many


def _pydantic_slots(class_name: str, enums: dict[str, dict]) -> list[dict]:
    cls = _model_classes()[class_name]
    slots = []
    for name, field in cls.model_fields.items():
        extra = field.json_schema_extra if isinstance(field.json_schema_extra, dict) else {}
        meta = extra.get("linkml_meta") or {}
        rng, many = _type_name(field.annotation)
        row = {"name": name, "range": rng, "required": field.is_required(), "multivalued": many,
               "identity": "identifier" if name == "id" else _identity(meta.get("mixins")),
               "description": field.description}
        if rng in enums:
            row["values"] = list(enums[rng]["values"])
        slots.append(row)
    return slots


def _state_collection(slots: list[dict], class_name: str) -> str | None:
    """The DecisionState slot that holds objects of this class (where to write them in YAML)."""
    return next((s["name"] for s in slots if s["range"] == class_name), None)


@tool("pdo_describe_schema with no class lists every class and enum.")
def describe_schema(class_name: str | None = None) -> dict:
    """Classes and enums of the decision schema, or one class's slots (or one enum's values).

    Read from the LinkML schema when linkml-runtime is installed, else from the generated pydantic
    models. Each slot reports its range, whether it is required or multivalued, and its identity marker:
    hashable slots are content (changing one invalidates what depends on it), unhashable ones are prose,
    routing and provenance.
    """
    view = _schema_view()
    if view is not None:
        source, classes, enums = "linkml", _linkml_classes(view), _linkml_enums(view)
        state_slots = _linkml_slots(view, "DecisionState", enums)
    else:
        source, classes, enums = "pydantic", _pydantic_classes(), _pydantic_enums()
        state_slots = _pydantic_slots("DecisionState", enums)
    if class_name is None:
        return _ok(source=source, schema="pdo", classes=list(classes.values()), enums=list(enums.values()),
                   state_slots=[{"name": s["name"], "range": s["range"]} for s in state_slots])
    folded = {k.lower(): k for k in [*classes, *enums]}
    name = class_name if class_name in classes or class_name in enums else folded.get(class_name.lower())
    if name is None:
        raise ToolError(f"no class or enum {class_name!r} in the schema{_close(class_name, [*classes, *enums])}",
                        "Call pdo_describe_schema without a class to list them.")
    if name in enums:
        return _ok(source=source, kind="enum", **enums[name])
    slots = _linkml_slots(view, name, enums) if view is not None else _pydantic_slots(name, enums)
    return _ok(source=source, kind="class", **classes[name], state_collection=_state_collection(state_slots, name),
               slots=slots)


# --- reading a decision --------------------------------------------------------------------------

def _episode_errors(program_dir: Path) -> tuple[list[str], int, list[str]]:
    """(errors, episodes checked, episodes not checked because an ancestor is rejected)."""
    try:
        program = Program.load(program_dir)
        episodes = program.episodes()
    except LoadError as exc:
        return [f"program: {line}" for line in str(exc).splitlines()], 0, []
    errors: list[str] = []
    blocked: set[str] = set()  # rejected episodes and everything built on them
    skipped: list[str] = []
    for ep in episodes:
        if program.parent(ep) in blocked:
            blocked.add(ep)
            skipped.append(ep)
            continue
        try:
            compile_graph(program.materialize(ep), program.base_dir)
        except (LoadError, CompileError) as exc:
            errors.append(f"episode {ep}: {exc}")
            blocked.add(ep)
    return errors, len(episodes) - len(skipped), skipped


def _document_problems(path: Path) -> tuple[list[str], list[str]]:
    """Locator problems in every episode (or the state), each reported once with the episodes it appears in.
    A missing docs extra is a warning: the decision is still valid, its locators are just not checked."""
    from pdo.provenance import verify

    states = _states(path, None)
    if not any(e.document_path for _, state, _ in states for e in items(state.evidence_items)):
        return [], []
    seen: dict[tuple[str, str, str], list[str]] = {}
    for ep, state, base in states:
        for x in verify(state, base):
            seen.setdefault((x["severity"], x["where"], x["message"]), []).append(ep or "")
    errors, warnings = [], []
    if any("--extra docs" in message for _, _, message in seen):  # one warning, not one per object
        warnings.append("warning: documents: Word and PDF documents were not checked: install the docs extra "
                        "(uv sync --extra docs)")
    for (severity, where, message), eps in seen.items():
        if "--extra docs" in message:
            continue
        eps = [e for e in eps if e]
        text = f"{severity}: {where}: {message}" + (f" (in {eps[0]}" + (f" and {len(eps) - 1} more)" if len(eps) > 1
                                                                       else ")") if eps else "")
        (errors if severity == "error" else warnings).append(text)
    return errors, warnings


@tool(LOAD_HINT)
def validate(path: str | Path) -> dict:
    """Check a decision: schema, references, units, weights, producers, and that it compiles.

    For a program, every episode is materialized and compiled too. The verdict is `valid`, with every
    error and warning.
    """
    p = _path(path)
    try:
        loaded = load_state(p)
    except LoadError as exc:
        return _ok(path=str(p), valid=False, errors=str(exc).splitlines(), warnings=[], objects=0,
                   hint=LOAD_HINT)
    issues = check_state(loaded.state, loaded.origins)
    errors = [str(i) for i in issues if i.severity == "error"] + [str(g) for g in executable_gaps(loaded.state)]
    warnings = [str(i) for i in issues if i.severity == "warning"]
    if not errors:
        try:
            compile_graph(loaded.state, loaded.base_dir)
        except CompileError as exc:
            errors.append(str(exc))
    data: dict[str, Any] = {"path": str(p), "objects": len(loaded.origins)}
    if is_program(p) and not errors:
        episode_errors, checked, unchecked = _episode_errors(p)
        errors += episode_errors
        data.update(episodes=checked, unchecked_episodes=unchecked)
    if not errors:
        doc_errors, doc_warnings = _document_problems(p)
        errors += doc_errors
        warnings += doc_warnings
    data.update(valid=not errors, errors=errors, warnings=warnings)
    if errors:
        data["hint"] = LOAD_HINT
    return _ok(**data)


@tool(PATH_HINT.format(cwd="the server's working directory"))
def run(path: str | Path, episode: str | None = None) -> dict:
    """Evaluate a decision: recommendation, feasibility, utilities, readiness, pending actions.

    A program episode is evaluated selectively against its parent and checked against a full
    recomputation (`verified`).
    """
    ev = _evaluated(path, episode)
    return _ok(**ev.where(), **run_summary(ev.result))


@tool(NODE_HINT)
def explain(path: str | Path, node: str, episode: str | None = None, depth: int = 1) -> dict:
    """Why a node has its value: its function, inputs (expanded `depth` levels), signature and digest."""
    if depth < 1:
        raise ToolError(f"depth must be at least 1, not {depth}", "depth 1 lists a node's inputs with their values.")
    ev = _evaluated(path, episode)
    try:
        data = explain_node(ev.result, node, depth)
    except KeyError as exc:
        raise ToolError(_message(exc), NODE_HINT) from None
    return _ok(**ev.where(), **data)


def _analyse(result: RunResult, params: list[str] | None, span: float, grid: int) -> tuple[list, list]:
    """(scans, flips) over numeric inputs and criterion weights; a weight is selected as crit:ID.weight."""
    state, base_dir = result.graph.state, result.graph.base_dir
    weight_ids = {p.rsplit(".", 1)[0] for p in params or [] if p.endswith(".weight")}
    others = [p for p in params or [] if not p.endswith(".weight")]
    scans, flips = [], []
    if params is None or others:
        scans, flips = scan_parameters(state, base_dir, others or None, span, grid, base=result)
    if params is None or weight_ids:
        flips += weight_flips(state, base_dir, base=result, criteria=weight_ids or None)
    return scans, flips


@tool("param is an input as ID.slot (obs:B.cost.value, asm:terrain-factor.value, req:min-range.threshold_value), "
      "an object id, or a weight as crit:ID.weight; omit it to scan every input and weight.")
def flip(path: str | Path, episode: str | None = None, param: str | list[str] | None = None,
         span: float = 0.5, grid: int = 17) -> dict:
    """What flips the decision: each numeric input's and each weight's flip points, others held fixed."""
    if grid < 2:
        raise ToolError(f"grid must be at least 2, not {grid}", "The default is 17 grid points per input.")
    if span <= 0:
        raise ToolError(f"span must be positive, not {span}", "span is a fraction of the value, default 0.5.")
    params = [param] if isinstance(param, str) else (list(param) if param else None)
    ev = _evaluated(path, episode)
    scans, flips = _analyse(ev.result, params, span, grid)
    return _ok(**ev.where(), recommended=ev.result.decision["recommended"], flips=flips, scans=scans)


@tool("Scenarios are declared in the state's `scenarios` slot, each a list of slot assignments.")
def scenarios(path: str | Path, episode: str | None = None) -> dict:
    """Evaluate each declared scenario: does the recommendation hold in every one (robust)?"""
    ev = _evaluated(path, episode)
    outcomes = scenario_sweep(ev.result.graph.state, base=ev.result)
    return _ok(**ev.where(), recommended=ev.result.decision["recommended"], robust=is_robust(outcomes),
               scenarios=outcomes)


@tool(PATH_HINT.format(cwd="the server's working directory"))
def readiness(path: str | Path, episode: str | None = None) -> dict:
    """Readiness for authorization: each condition's status and reason, and whether the decision is
    actionable (ready, a unique recommendation, and a matching approval)."""
    ev = _evaluated(path, episode)
    summary = run_summary(ev.result)
    objects = index(ev.result.graph.state)
    conditions = []
    for c in summary["conditions"]:
        obj = objects.get(c["condition"])
        conditions.append({**c, "predicate": getattr(obj, "predicate", None),
                           "owner_role": getattr(obj, "owner_role", None)})
    r = ev.result.readiness
    return _ok(**ev.where(), readiness=r["state"], failed=r["failed"], unknown=r["unknown"], passed=r["passed"],
               conditions=conditions, recommended=summary["recommended"], actionable=summary["actionable"],
               actionable_reason=summary["actionable_reason"], approval=summary["approval"],
               state_digest=summary["state_digest"])


@tool(PATH_HINT.format(cwd="the server's working directory"))
def pending_actions(path: str | Path, episode: str | None = None, role: str | None = None) -> dict:
    """What people must do before the decision is actionable, routed by owning role.

    Each action names the tool call that resolves it. `role` filters to one role ("unassigned" for
    actions no role owns).
    """
    ev = _evaluated(path, episode)
    roles = {r.id: r for r in items(ev.result.graph.state.roles)}
    if role is not None and role != "unassigned" and role not in roles:
        raise ToolError(f"{role} is not a role in this decision{_close(role, roles)}",
                        f"Roles: {', '.join(sorted(roles)) or 'none'}, or 'unassigned'.")
    actions = _actions(ev.result)
    if role is not None:
        actions = [a for a in actions if (a["role"] or "unassigned") == role]
    by_role: dict[str, dict] = {}
    for a in actions:
        key = a["role"] or "unassigned"
        holder = roles.get(key)
        entry = by_role.setdefault(key, {"name": getattr(holder, "name", None),
                                         "assignees": list(getattr(holder, "assignees", None) or []), "actions": []})
        entry["actions"].append(a["id"])
    return _ok(**ev.where(), recommended=ev.result.decision["recommended"],
               readiness=ev.result.readiness["state"], actionable=ev.result.actionable["actionable"],
               pending_actions=actions, by_role=by_role)


# --- programs -------------------------------------------------------------------------------------

def _depths(program: Program) -> list[tuple[str, int]]:
    """Episodes depth-first from the baseline, with their depth in the tree."""
    children = program.children()
    out: list[tuple[str, int]] = []
    stack = [(program.baseline_episode, 0)]
    while stack:
        ep, depth = stack.pop()
        out.append((ep, depth))
        stack.extend((kid, depth + 1) for kid in reversed(children[ep]))
    return out


def _relative(file: Path | None, root: Path) -> str | None:
    if file is None:
        return None
    try:
        return str(file.relative_to(root))
    except ValueError:
        return str(file)


@tool(PROGRAM_HINT)
def program_status(path: str | Path) -> dict:
    """The episode tree with each episode's recommendation, readiness and actionable, the newest episode
    (where new work builds), and its pending actions."""
    program = _program(path)
    rows = {ev.episode: ev.summary() for ev in program.evaluate_all(verify=False)}
    newest = program.newest()
    episodes = [{**rows[ep], "depth": depth, "file": _relative(program.files.get(ep), program.path)}
                for ep, depth in _depths(program)]
    return _ok(path=str(program.path), program=program.meta.id, name=program.meta.name,
               question=program.meta.question, baseline=program.baseline_episode, newest=newest, episodes=episodes,
               pending_actions=_actions(program.evaluate(newest, verify=False).run))


@tool(PROGRAM_HINT)
def refresh(path: str | Path) -> dict:
    """Re-evaluate every episode selectively (cutoff mode) and verify each against a full recomputation.

    `all_verified` is the kernel's correctness claim on this program: selective re-evaluation equals full
    recomputation. `executed` out of `computed_nodes` is what the child episodes actually recomputed.
    """
    program = _program(path)
    rows = [ev.summary() for ev in program.evaluate_all(mode="cutoff", verify=True)]
    children = [r for r in rows if r["parent"] is not None]
    unverified = [r["episode"] for r in children if r["verified"] is False]
    return _ok(path=str(program.path), program=program.meta.id, episodes=rows, all_verified=not unverified,
               unverified=unverified, executed=sum(r["executed"] for r in children),
               reused=sum(r["reused"] for r in children), computed_nodes=sum(r["computed_nodes"] for r in children))


def _change_data(change: Any, program: Program) -> dict:
    """A change set given as a dict, YAML text, a ChangeSet, or the path of a change-set file."""
    if isinstance(change, m.ChangeSet):
        return change.model_dump(exclude_none=True)
    if isinstance(change, dict):
        return dict(change)
    if not isinstance(change, str) or not change.strip():
        raise ToolError("no change set given", "Pass the change set as a mapping, YAML text, or a .yaml file path.")
    text = change
    if "\n" not in change and change.strip().endswith((".yaml", ".yml")):
        candidates = [Path(change.strip()).expanduser(), program.path / change.strip()]
        file = next((c for c in candidates if _is_file(c)), None)
        if file is None:
            raise ToolError(f"{change}: no such change-set file", PATH_HINT.format(cwd=Path.cwd()))
        text = file.read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ToolError("a change set must be a mapping with assign, add and/or remove",
                        "Example: {assign: [{target: obs:B.cost, slot: value, new_value: 1.32}], trigger: cost}")
    return data


def _change_model(data: dict) -> m.ChangeSet:
    try:
        return m.ChangeSet.model_validate(data)
    except ValidationError as exc:
        raise ToolError(f"not a valid change set:\n{_message(exc)}",
                        "A change set has id, parent, and optionally as_of, trigger, change_description, "
                        "author_role, assign, add and remove (pdo_describe_schema ChangeSet).") from None


def _preview(program: Program, change: m.ChangeSet, parent: str) -> dict:
    """Dry run a change set on `parent`: its delta, impact, what was rerun, and before against after."""
    from pdo.bench.metrics import approval_invalidated, outcome_class

    before = program.evaluate(parent, verify=False)
    after = program.preview(change, parent=parent, mode="cutoff", verify=True)
    d = delta(before.run.graph, after.run.graph)
    kinds = DECISION_KINDS | {"condition", "readiness", "actionable"}
    value_changes = sorted(nid for nid, node in after.run.graph.nodes.items() if node.kind in kinds
                           and nid in before.run.results
                           and before.run.results[nid].vdig != after.run.results[nid].vdig)
    row = after.summary()
    b, a = _brief(before.run), _brief(after.run)
    changed = {k: {"before": b[k], "after": a[k]} for k in ("recommended", "feasible", "infeasible", "unresolved",
                                                            "readiness", "actionable") if b[k] != a[k]}
    return {"episode": change.id, "parent": parent,
            "delta": {"changed": sorted(d.changed), "added": sorted(d.added), "deleted": sorted(d.deleted)},
            **{k: row[k] for k in ("impact_size", "total_nodes", "executed", "reused", "computed_nodes", "verified",
                                   "mismatched")},
            "outcome": outcome_class(before.run, after.run),
            "approval_invalidated": approval_invalidated(before.run, after.run),
            "changed": changed, "value_changes": value_changes, "before": b, "after": a,
            "pending_actions_after": _actions(after.run)}


@tool(CHANGE_HINT)
def impact(path: str | Path, change: dict | str, episode: str | None = None) -> dict:
    """Dry run a change set: its delta, impact set, nodes rerun versus reused, and the decision before
    and after. Nothing is written.

    The parent is `episode`, else the change set's own parent, else the baseline. `outcome` is the
    benchmark outcome class (docs/semantics.md) of the child against its parent.
    """
    program = _program(path)
    data = _change_data(change, program)
    parent = _episode(program, episode or data.get("parent") or program.baseline_episode)
    data.setdefault("id", "ep:dry-run")
    data["parent"] = parent
    return _ok(**_preview(program, _change_model(data), parent))


def _write(program: Program, change: m.ChangeSet, name: str, header: str) -> Path:
    """Add the change set as a new file, and remove it again unless the program loads back with it."""
    file = program.write_change(change, name, header)
    try:
        reloaded = Program.load(program.path)
        compile_graph(reloaded.materialize(change.id), reloaded.base_dir)
    except (LoadError, CompileError, ValueError) as exc:
        file.unlink(missing_ok=True)
        raise ToolError(f"the written change set did not load back, so it was removed: {_message(exc)}",
                        CHANGE_HINT) from None
    return file


def _no_typed_provenance(data: dict) -> None:
    """Locators and document digests come from the pdo docs tools, which compute them from the documents."""
    typed = [f"assign {a.get('target')}.{a.get('slot')}" for a in data.get("assign") or []
             if isinstance(a, dict) and a.get("slot") in ("locator", "document_digest")]
    add = data.get("add") or {}
    typed += [f"add {o.get('id')} with a locator" for o in add.get("observations") or [] if o.get("locator")]
    typed += [f"add {e.get('id')} with a document_digest" for e in add.get("evidence_items") or []
              if e.get("document_digest")]
    if typed:
        raise ToolError("locators and document digests are computed by the pdo docs tools, never typed: "
                        + ", ".join(typed), "Use pdo docs extract (values with quotes) or pdo docs revise (a new "
                        "version of a document).")


def _new_episode(program: Program, data: dict) -> tuple[dict, str]:
    """Default the parent to the newest episode and the id to the next in the program's numbering."""
    parent = _episode(program, data.get("parent") or program.newest())
    data["parent"] = parent
    data.setdefault("id", program.next_episode_id(parent))
    roles = sorted(r.id for r in items(program.materialize(parent).roles))
    if data.get("author_role") and data["author_role"] not in roles:
        raise ToolError(f"author_role {data['author_role']} is not a role in {parent}", f"Roles: {', '.join(roles)}.")
    if data["id"] == program.baseline_episode or data["id"] in program.changes:
        raise ToolError(f"episode {data['id']} already exists",
                        f"Episodes are never rewritten. Omit id to take the next one "
                        f"({program.next_episode_id(parent)}).")
    return data, parent


@tool(CHANGE_HINT)
def apply_change(path: str | Path, change: dict | str, write: bool = False) -> dict:
    """Preview a change set as a new episode and, with write=True, add it to the program's changes.

    The parent defaults to the newest episode and the id to the next in sequence. The change set is
    validated before anything is written; a new file is added (never an edit), and kept only if the
    program loads back with it.
    """
    program = _program(path)
    data, parent = _new_episode(program, _change_data(change, program))
    _no_typed_provenance(data)
    model = _change_model(data)
    preview = _preview(program, model, parent)
    file = None
    if write:
        local = model.id.split(":", 1)[-1]
        name = f"{local}-{model.trigger}" if model.trigger else local
        file = _write(program, model, name, "Written by pdo_apply_change. Episodes are never rewritten:\n"
                                            "to change this, write a new change set.")
    return _ok(written=write, file=_relative(file, program.path), change=model, **preview)


def _human_action(program: Program, kind: str, target: str, role: str, status: str | None,
                  rationale: str | None, parent: str) -> tuple[m.ChangeSet, list[str]]:
    """The change set recording one human action, and warnings about roles the policy will not count."""
    spec = HUMAN_ACTIONS[kind]
    state = program.materialize(parent)
    objects = index(state)
    if not isinstance(objects.get(role), m.Role):
        roles = sorted(r.id for r in items(state.roles))
        raise ToolError(f"{role} is not a role in {parent}{_close(role, roles)}", f"Roles: {', '.join(roles)}.")
    cls = spec["cls"]
    if not isinstance(objects.get(target), cls):
        ids = sorted(oid for oid, obj in objects.items() if isinstance(obj, cls))
        raise ToolError(f"{target} is not {_a(cls.__name__)} in {parent}{_close(target, ids)}",
                        f"{kind} targets {_a(cls.__name__)}: {', '.join(ids) or 'none in this episode'}.")
    if kind == "approval" and status is not None:
        raise ToolError("status applies to reviews, applicability, risks and assumptions, not approvals",
                        "Omit status: an approval approves its target alternative.")
    status = status or spec["default"]
    if kind != "approval" and status not in spec["values"]:
        raise ToolError(f"{kind} needs status: one of {', '.join(spec['values'])}" if status is None
                        else f"{status!r} is not a {kind} status", f"Choose one of {', '.join(spec['values'])}.")
    policy = state.readiness_policy
    warnings = []
    if kind == "approval" and policy is not None and role not in items(policy.approval_roles):
        warnings.append(f"{role} is not in the policy's approval_roles; the kernel will not count this approval")
    if kind == "risk_treatment" and status == "accepted" and role not in items(
            policy.risk_acceptance_roles if policy else None):
        warnings.append(f"{role} is not in the policy's risk_acceptance_roles; risks_treated fails on this risk")

    if kind in ("approval", "review"):
        change = program.human_action_change(kind, target, role, status if kind == "review" else None,
                                             parent=parent)
        if rationale and kind == "approval":
            change.add.approvals[0].rationale = rationale
        elif rationale:
            change.assign = [*items(change.assign), m.SlotAssignment(target=target, slot=spec["rationale"],
                                                                     new_value=rationale)]
        return change, warnings
    assign = [m.SlotAssignment(target=target, slot=spec["slot"], new_value=status)]
    if kind == "risk_treatment" and status == "accepted":
        assign.append(m.SlotAssignment(target=target, slot="accepted_by_role", new_value=role))
    if rationale and spec.get("rationale"):
        assign.append(m.SlotAssignment(target=target, slot=spec["rationale"], new_value=rationale))
    description = f"{role} records {target} {spec['slot']} as {status}." + (f" {rationale}" if rationale else "")
    change = m.ChangeSet(id=program.next_episode_id(parent), parent=parent, trigger=spec["trigger"],
                         author_role=role, change_description=description, assign=assign)
    return change, warnings


@tool(CHANGE_HINT)
def record_human_action(path: str | Path, kind: str, target: str, role: str, status: str | None = None,
                        rationale: str | None = None, write: bool = False, parent: str | None = None) -> dict:
    """Record a person's action as a new change set, a child of the newest episode (or of `parent`).

    kind and target:
      approval               `role` approves alternative `target`
      review                 review `target` is recorded as `status` (pass, fail, pending; default pass),
                             which also redoes a stale review
      applicability          observation `target` is accepted, provisional or rejected (default accepted)
      risk_treatment         risk `target` is untreated, mitigated, accepted (by `role`) or unacceptable
      assumption_validation  assumption `target` is validated, unvalidated or invalid (default validated)
    Previews the outcome; with write=True the change set is added to the program's changes directory.
    The kernel stamps approvals and reviews with the digest they cover.
    """
    if kind not in HUMAN_ACTIONS:
        raise ToolError(f"unknown kind {kind!r}{_close(kind, HUMAN_ACTIONS)}",
                        f"kind is one of {', '.join(HUMAN_ACTIONS)}.")
    program = _program(path)
    base = _episode(program, parent or program.newest())
    change, warnings = _human_action(program, kind, target, role, status, rationale, base)
    preview = _preview(program, change, base)
    file = None
    if write:
        header = (f"Recorded by pdo_record_human_action (kind {kind}). Episodes are never rewritten:\n"
                  "to change this action, write a new change set.")
        file = _write(program, change, f"{change.id.split(':', 1)[-1]}-{kind.replace('_', '-')}", header)
    return _ok(kind=kind, target=target, role=role, status=status or HUMAN_ACTIONS[kind]["default"], written=write,
               file=_relative(file, program.path), warnings=warnings, change=change, **preview)


# --- packages, SysML and uncertainty ---------------------------------------------------------------

@tool(PATH_HINT.format(cwd="the server's working directory"))
def package(path: str | Path, episode: str | None = None, out: str | Path | None = None,
            span: float = 0.5, grid: int = 17) -> dict:
    """The Decision Package of one episode: flips, scenarios, claims and one run per computed node.

    With `out`, writes package.md (for people) and record.json (validated against the record schema,
    replayable with `pdo replay`); without it, nothing is written and the Markdown is returned.
    """
    ev = _evaluated(path, episode)
    scans, flips = _analyse(ev.result, None, span, grid)
    outcomes = scenario_sweep(ev.result.graph.state, base=ev.result)
    record = episode_record(ev.result, episode=ev.episode, parent=ev.parent, source=str(ev.path.resolve()),
                            flips=flips, scenarios=outcomes, scans=scans)
    from pdo.provenance import contradicted, verify

    problems = verify(ev.result.graph.state, ev.result.graph.base_dir)
    broken = contradicted(problems)
    if broken:
        raise ToolError("locators do not match their documents: " + "; ".join(broken),
                        "Nothing was packaged. Fix them first (pdo docs verify lists them).")
    data = {**ev.where(), "recommended": record.recommended, "robust": record.robust,
            "document_problems": problems,
            "readiness": record.readiness.readiness_state if record.readiness else None,
            "actionable": record.actionable, "flips": len(record.flips or []), "runs": len(record.runs or []),
            "claims": len(record.claims or [])}
    if out is None:
        validate_record_json(record_json(record))
        return _ok(**data, written=False, markdown=render_markdown(record))
    paths = write_package(record, Path(out).expanduser())
    return _ok(**data, written=True, package=paths["package"], record=paths["record"])


def _mapping(mapping: Any) -> Any:
    """A mapping file path, its YAML text, or its parsed content, as pdo.sysml.load_mapping takes it."""
    if not isinstance(mapping, str):
        return mapping
    if _is_file(mapping):
        return Path(mapping).expanduser()
    try:
        parsed = yaml.safe_load(mapping)
    except yaml.YAMLError:
        return mapping
    return parsed if isinstance(parsed, dict) else mapping


@tool(SYSML_HINT)
def import_sysml(paths: str | Path | list[str | Path], mapping: Any = None, out: str | Path | None = None) -> dict:
    """Import SysML v2 textual models into a draft decision state, with every gap the import cannot map.

    The draft holds alternatives, measures, requirements, observations with evidence, assumptions and
    evaluation placeholders, each traced to its source element. With `out`, writes sysml-*.yaml and
    sysml-import.json there (only those files are rewritten). Framing (criteria, weights, decision rule,
    readiness) is never imported: it comes from the people who own the decision.
    """
    targets = [paths] if isinstance(paths, str | Path) else list(paths or [])
    if not targets:
        raise ToolError("no SysML files given", SYSML_HINT)
    result = sysml.import_sysml([Path(p).expanduser() for p in targets], _mapping(mapping))
    written = sysml.write_state(result, Path(out).expanduser()) if out is not None else []
    steps = []
    if result.gaps:
        steps.append(f"review the {len(result.gaps)} gaps with the decision owner (fix the model, extend the "
                     "mapping, translate by hand as provisional, or accept as out of scope)")
    if result.missing_to_run():
        steps.append(f"frame the decision with its owner: {', '.join(result.missing_to_run())}")
    if out is None:
        steps.append("pass out to write the draft state")
    else:
        steps.append(f"pdo_validate {out}, then pdo_run")
    return _ok(**result.to_dict(), written=written, next_steps=steps)


@tool(UNCERTAINTY_HINT)
def uncertainty(path: str | Path, episode: str | None = None, samples: int | None = None,
                seed: int | None = None) -> dict:
    """Sample the uncertain inputs: E[U], Pr(feasible), the chance-constrained decision, and scenario
    robustness, beside the deterministic decision."""
    p = _path(path)
    if episode is not None and is_program(p):
        _episode(Program.load(p), episode)
    elif episode is not None:
        raise ToolError(f"{p} is not a program directory (no program.yaml), so it has no episodes",
                        "Omit the episode for a single state, or pass the program directory.")
    where = {"path": str(p), "episode": episode} if is_program(p) else {"path": str(p)}
    return _ok(**where, **uncertainty_summary(analyze_path(p, episode, samples, seed)))


@tool(SENSITIVITY_HINT)
def sensitivity(path: str | Path, episode: str | None = None, param: str | None = None,
                samples: int | None = None, seed: int | None = None, span: float = 0.10) -> dict:
    """How the decision responds to its inputs. With `param` (e.g. obs:B.cost.value): its response curve
    and the response to its distribution. Without: a tornado of the decision margin, global (Sobol)
    indices and value of information per input, when inputs declare distributions."""
    from pdo.sensitivity import analyze_path as sensitivity_path

    p = _path(path)
    if episode is not None and is_program(p):
        _episode(Program.load(p), episode)
    elif episode is not None:
        raise ToolError(f"{p} is not a program directory (no program.yaml), so it has no episodes",
                        "Omit the episode for a single state, or pass the program directory.")
    result = sensitivity_path(p, episode, param, samples=samples, seed=seed, span=span)
    if "response" in result:  # the curve's rows are long; agents get the summary and the change points
        result["response"] = {k: v for k, v in result["response"].items() if k != "rows"}
    return _ok(**result)


@tool(LOAD_HINT)
def dashboard(path: str | Path, out: str | Path, flips: bool = True, sensitivity: bool = True,
              samples: int | None = None) -> dict:
    """Write the read-only dashboard (index.html and dashboard.json) of a state or a program: every episode's
    dependency graph with what its refresh recomputed, the decision table, readiness, pending actions, the
    utility history, flips, a tornado, and uncertainty and value of information where inputs declare
    distributions. Replaces only a previous dashboard's own files."""
    from pdo.dashboard import build, write

    p = _path(path)
    target = Path(out).expanduser()
    data = build(p, flips=flips, sensitivity=sensitivity, samples=samples)
    try:
        paths = write(data, target)
    except FileExistsError as exc:
        raise ToolError(str(exc), "Choose a new directory, or one that holds only a previous dashboard.") from None
    last = data["episodes"][-1]
    return _ok(path=str(p), page=paths["page"], data=paths["data"], episodes=len(data["episodes"]),
               nodes=len(data["layout"]["nodes"]), newest={"episode": last["id"], "recommended": last["recommended"],
                                                          "readiness": last["readiness"]})


# --- documents ------------------------------------------------------------------------------------

def _document(path: str | Path) -> Path:
    p = Path(path).expanduser()
    if not p.is_file():
        raise ToolError(f"no document at {p}", DOCS_HINT)
    return p


@tool(DOCS_HINT)
def docs_index(path: str | Path) -> dict:
    """A document's sections: id, heading path, pages, size and content digest, and the document digest."""
    from pdo.provenance import load_index

    return _ok(**{**load_index(_document(path)).summary(), "path": str(path)})


@tool(DOCS_HINT)
def docs_read(path: str | Path, section: str | None = None) -> dict:
    """The text of one section (by id, heading or heading path), or of every section, as pdo matches quotes
    against it."""
    from pdo.provenance import load_index

    ix = load_index(_document(path))
    chosen = [ix.section(section)] if section else ix.sections
    return _ok(path=str(path), digest=ix.digest,
               sections=[{**s.summary(), "text": s.paragraphs} for s in chosen])


@tool(DOCS_HINT)
def docs_locate(path: str | Path, quote: str, section: str | None = None) -> dict:
    """The locator of a quote: section, page, paragraph, character span and digests. Refused when the quote is
    not in the document, or occurs more than once (quote more, or name the section)."""
    from pdo.docs import locate
    from pdo.provenance import load_index, numbers_in

    loc = locate(load_index(_document(path)), quote, section)
    return _ok(locator=loc, numbers=numbers_in(loc["quote"]))


def _states(path: str | Path, episode: str | None) -> list[tuple[str | None, m.DecisionState, Path]]:
    p = _path(path)
    if is_program(p):
        program = _program(p)
        episodes = [_episode(program, episode)] if episode else program.episodes()
        return [(ep, program.materialize(ep), program.base_dir) for ep in episodes]
    if episode is not None:
        raise ToolError(f"{p} is not a program directory (no program.yaml), so it has no episodes",
                        "Omit the episode for a single state, or pass the program directory.")
    loaded = load_state(p)
    return [(None, loaded.state, loaded.base_dir)]


@tool(DOCS_HINT)
def docs_verify(path: str | Path, episode: str | None = None) -> dict:
    """Check every locator against its document: the quote must be in the cited section of the indexed
    version (errors), and should state the value (warnings). For a program, every episode unless one is given."""
    from pdo.provenance import verify

    out = []
    for ep, state, base in _states(path, episode):
        for problem in verify(state, base):
            out.append({"episode": ep, **problem} if ep else problem)
    unchecked = sum(1 for x in out if "--extra docs" in x["message"])  # an explicit verify that checked nothing fails
    errors = sum(1 for x in out if x["severity"] == "error") + unchecked
    return _ok(path=str(path), errors=errors, warnings=len(out) - errors, unchecked=unchecked, problems=out)


def _proposals(proposals: dict | str) -> dict:
    if isinstance(proposals, dict):
        return dict(proposals)
    text = proposals
    if isinstance(proposals, str) and "\n" not in proposals and proposals.strip().endswith((".yaml", ".yml")):
        file = Path(proposals.strip()).expanduser()
        if not file.is_file():
            raise ToolError(f"{proposals}: no such proposals file", DOCS_HINT)
        text = file.read_text(encoding="utf-8")
    data = yaml.safe_load(text) if isinstance(text, str) else None
    if not isinstance(data, dict) or not data.get("observations"):
        raise ToolError("proposals must be a mapping with observations",
                        "Example: {evidence: ev:reliability-B, observations: [{id: obs:B.reliability, value: 0.98, "
                        "quote: 'Its demonstrated mission reliability is 0.98.'}]}")
    return data


def _document_change(program: Program, data: dict, role: str | None, parent: str | None, episode_id: str | None,
                     as_of: str | None) -> tuple[dict, str]:
    data = {**data, "parent": parent}
    if episode_id:
        data["id"] = episode_id
    if as_of:
        data["as_of"] = as_of
    data, parent = _new_episode(program, data)
    if role:
        data["author_role"] = role
    return data, parent


@tool(CHANGE_HINT)
def docs_extract(path: str | Path, proposals: dict | str, write: bool = False, role: str | None = None,
                 parent: str | None = None, episode_id: str | None = None, as_of: str | None = None) -> dict:
    """Turn proposed values with quotes into a change set: each quote is located in its evidence's document
    (a quote not in it is refused), new values are provisional unless proposals say accept: true, and an
    existing observation id updates that observation's value and locator. For a program, previews the new
    episode and with write=True adds it; for a plain state, returns the change without writing."""
    from pdo.provenance import extract

    props = _proposals(proposals)
    role = role or props.get("author_role")
    p = _path(path)
    if not is_program(p):
        loaded = load_state(p)
        change, rows = extract(loaded.state, loaded.base_dir, props, role)
        return _ok(written=False, change=change, rows=rows,
                   note="A plain state has no episodes: add these objects to its files, or use a program.")
    program = _program(p)
    base = _episode(program, parent or program.newest())
    change, rows = extract(program.materialize(base), program.base_dir, props, role)
    located = ", ".join(f"{r['observation']} (section {r['section']})" for r in rows)
    change.setdefault("change_description", props.get("change_description") or
                      f"Values located in their documents: {located}.")
    data, base = _document_change(program, change, role, base, episode_id, as_of)
    model = _change_model(data)
    preview = _preview(program, model, base)
    file = None
    if write:
        file = _write(program, model, f"{model.id.split(':', 1)[-1]}-extract",
                      "Written by pdo docs extract: each locator was computed from its document, never typed.\n"
                      "Episodes are never rewritten: to change this, write a new change set.")
    return _ok(written=write, file=_relative(file, program.path), rows=rows, change=model, **preview)


@tool(CHANGE_HINT)
def docs_revise(path: str | Path, document: str, new_file: str | Path, write: bool = False, role: str | None = None,
                parent: str | None = None, episode_id: str | None = None, as_of: str | None = None) -> dict:
    """Absorb a new version of a document (named by an evidence id or its document_path): every evidence item
    on it moves to the new file, locators whose section text is unchanged move with it, and locators in
    changed sections stay on the old version, so locators_current keeps readiness pending until a person
    re-anchors those values (pdo docs extract with the observation id). Previews; with write=True, writes."""
    from pdo.provenance import describe_revision, revise

    program = _program(path)
    base = _episode(program, parent or program.newest())
    state = program.materialize(base)
    change, report = revise(state, program.base_dir, document, new_file)
    change["change_description"] = describe_revision(report, state)
    data, base = _document_change(program, change, role, base, episode_id, as_of)
    model = _change_model(data)
    preview = _preview(program, model, base)
    file = None
    if write:
        file = _write(program, model, f"{model.id.split(':', 1)[-1]}-revision",
                      "Written by pdo docs revise: locators move only where the cited section's text is unchanged.\n"
                      "Episodes are never rewritten: to change this, write a new change set.")
    return _ok(written=write, file=_relative(file, program.path), revision=report, change=model, **preview)


# --- examples -------------------------------------------------------------------------------------

def examples_dir() -> Path | None:
    """Where the bundled examples are: inside the installed package, or the checkout's examples/."""
    return next((d for d in EXAMPLE_DIRS if d.is_dir()), None)


def _example(directory: Path) -> dict:
    row: dict[str, Any] = {"name": directory.name, "path": str(directory)}
    if is_program(directory):
        meta = yaml.safe_load((directory / "program.yaml").read_text(encoding="utf-8")) or {}
        changes = directory / meta.get("changes_dir", "changes")
        row.update(kind="program", title=meta.get("name"), question=meta.get("question"),
                   change_sets=len(list(changes.glob("*.yaml"))) if changes.is_dir() else 0)
    else:
        row.update(kind="state")
    return row


@tool("Examples ship with pdo; in a source checkout they are the repository's examples/.")
def list_examples() -> dict:
    """The bundled example decisions, with their paths (pass a path to any other tool)."""
    root = examples_dir()
    if root is None:
        raise ToolError("no examples directory found", "Reinstall pdo, or use a source checkout.")
    found = sorted(d for d in root.iterdir() if d.is_dir() and (is_program(d) or any(d.glob("*.yaml"))))
    return _ok(directory=str(root), examples=[_example(d) for d in found])


@tool("pdo_list_examples lists the names; dest must not exist yet (or be an empty directory).")
def copy_example(name: str, dest: str | Path) -> dict:
    """Copy a bundled example to `dest` to work on it (examples themselves are never modified)."""
    root = examples_dir()
    known = sorted(d.name for d in root.iterdir() if d.is_dir() and not d.name.startswith((".", "_"))) if root else []
    if name not in known:
        raise ToolError(f"no example {name!r}{_close(name, known)}", f"Examples: {', '.join(known) or 'none'}.")
    source = root / name
    target = Path(dest).expanduser()
    if target.exists() and (not target.is_dir() or any(target.iterdir())):
        raise ToolError(f"{target} already exists and is not an empty directory",
                        "Choose a new directory; nothing is overwritten.")
    shutil.copytree(source, target, ignore=COPY_IGNORE, dirs_exist_ok=True)
    return _ok(example=name, path=target, files=sorted(str(p.relative_to(target)) for p in target.rglob("*")
                                                       if p.is_file()))

