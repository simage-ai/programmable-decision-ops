"""The pdo MCP server: the agent tools of pdo.tools over stdio, one tool pdo_<name> per function.

Start it with `pdo mcp` (or `python -m pdo.mcp_server`) and register that command with an MCP client.
Every tool returns its pdo.tools result as structured content: {"ok": true, ...}, or {"ok": false,
"error": ..., "hint": ...} for a request that cannot be carried out. A defect in pdo is also returned
as data (with the traceback logged), so an agent always gets an answer it can read.

stdout is the JSON-RPC stream, so nothing here prints: logs go to stderr. Evaluators are user code,
so each tool call runs with sys.stdout redirected to stderr, and while it serves the SDK points file
descriptor 1 at stderr: output from an evaluator cannot corrupt the stream. The tool functions are
synchronous; the SDK runs each call on a worker thread.
"""

from __future__ import annotations

import contextlib
import inspect
import logging
import sys
from typing import Annotated, Any, Literal

from pydantic import Field

from pdo import __version__, tools

log = logging.getLogger("pdo.mcp")

INSTRUCTIONS = """\
pdo runs decisions as programs. A decision state (YAML) holds alternatives, measures, requirements,
criteria, evidence, observations, assumptions, evaluators, readiness conditions, reviews, risks and
approvals; the kernel compiles it into a dependency graph and computes the recommendation,
feasibility, utilities, readiness for authorization and the pending human actions by role.

- Every number comes from the kernel. Quote tool results; never compute or adjust a utility, margin
  or status yourself. pdo_explain traces any number to its inputs.
- Start with pdo_validate, then pdo_run. pdo_readiness and pdo_pending_actions say what blocks
  authorization and who must act; each pending action names the tool call that resolves it.
- A program (program.yaml, baseline/, changes/) is a tree of episodes. Episodes are never edited:
  every change, including a person's approval, review or acceptance, is a new change set. Preview
  with pdo_impact or pdo_apply_change, and write only with the person's go-ahead (write=true).
  pdo_refresh verifies that selective re-evaluation equals full recomputation in every episode.
- Human judgements (approvals, reviews, accepting evidence, risk treatment, validating assumptions)
  belong to the role that owns them. Record them with pdo_record_human_action only when that person
  has made the judgement; never on your own initiative.
- SysML v2: pdo_import_sysml maps a model to a draft state and lists the gaps. Framing (criteria,
  anchors, weights, decision rule, readiness) comes from the decision's owner, never from the model.
- Reports (Word, PDF, Markdown): read sections with pdo_docs_index and pdo_docs_read, and propose each
  value with the exact passage that states it (pdo_docs_extract); a quote that is not in the document is
  refused. Values you propose are provisional until a person accepts them. When a report is revised,
  pdo_docs_revise moves the locators whose section is unchanged and flags the rest for a person.
- Paths are files or directories on the server's machine; relative paths resolve against the server's
  working directory. pdo_list_examples gives example paths.
"""

INTERNAL_HINT = "This is a defect in pdo, not in the request; the traceback is in the server log (stderr)."

StatePath = Annotated[str, Field(description="A decision state file (.yaml), a directory of state files, or a "
                                             "program directory (with program.yaml).")]
ProgramPath = Annotated[str, Field(description="A program directory: program.yaml, baseline/ and changes/.")]
Episode = Annotated[str | None, Field(description="Program episode id, e.g. ep:e05 (default: the baseline). "
                                                  "Only for program directories.")]
Change = Annotated[dict[str, Any] | str, Field(
    description="A change set: a mapping (or its YAML text, or a .yaml file path) with optional id, parent, "
                "as_of, trigger, change_description, author_role, and the operations assign (list of {target, "
                "slot, new_value}), add (a partial decision state of new objects) and remove (list of ids).")]
Write = Annotated[bool, Field(description="false (default): preview only. true: add the change set to the "
                                          "program's changes directory as a new file.")]


def _call(fn, *args: Any, **kwargs: Any) -> dict[str, Any]:
    # Tools run evaluator code. Its prints sit in sys.stdout's buffer and would reach the wire when the
    # buffer is flushed (at the latest at exit, after the SDK restores fd 1), so they go to stderr.
    with contextlib.redirect_stdout(sys.stderr):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 - a defect must still reach the agent as data
            log.exception("pdo tool %s failed", fn.__name__)
            return {"ok": False, "error": f"internal error in {fn.__name__}: {type(exc).__name__}: {exc}",
                    "hint": INTERNAL_HINT}


# --- read-only tools ------------------------------------------------------------------------------

def pdo_describe_schema(
    class_name: Annotated[str | None, Field(description="A class (e.g. Observation, ChangeSet) or enum "
                                                        "(e.g. TriggerKind); omit to list them all.")] = None,
) -> dict[str, Any]:
    """Describe the decision schema: every class and enum, or one class's slots (range, required,
    multivalued, hashable or unhashable, description) and the decision-state slot that holds it, or one
    enum's values. Use it before writing YAML for a class."""
    return _call(tools.describe_schema, class_name)


def pdo_validate(path: StatePath) -> dict[str, Any]:
    """Check a decision state or program: schema, references, units, weights that sum to 1, one producer
    per measure, and that it compiles (for a program, every episode too). Returns valid, errors and
    warnings; fix every error before trusting pdo_run."""
    return _call(tools.validate, path)


def pdo_run(path: StatePath, episode: Episode = None) -> dict[str, Any]:
    """Evaluate a decision: the recommended alternatives, feasibility with each requirement check's margin
    g (g <= 0 passes), measures, utilities with bounds, readiness conditions, whether it is actionable,
    and the pending actions by role. For a program episode, verified says whether the selective result
    equals a full recomputation."""
    return _call(tools.run, path, episode)


def pdo_explain(
    path: StatePath,
    node: Annotated[str, Field(description="Node id: decision, readiness, actionable, U/<alt>, F/<alt>, "
                                           "g/<req>/<alt>, v/<crit>/<alt>, y/<alt>/<measure>, cond/<condition>, "
                                           "or an object id.")],
    episode: Episode = None,
    depth: Annotated[int, Field(ge=1, le=6, description="Levels of inputs to expand.")] = 1,
) -> dict[str, Any]:
    """Explain why a node has its value: its function, its inputs with their values (expanded depth
    levels), its signature and value digest. Use it to trace any reported number to its evidence."""
    return _call(tools.explain, path, node, episode, depth)


def pdo_flip(
    path: StatePath,
    episode: Episode = None,
    param: Annotated[list[str] | str | None, Field(
        description="Inputs to scan as ID.slot (obs:B.cost.value, asm:terrain-factor.value, "
                    "req:min-range.threshold_value) or an object id, or a weight as crit:ID.weight; omit to scan "
                    "every numeric input and weight.")] = None,
    span: Annotated[float, Field(gt=0, description="Scan +/- this fraction of a value (assumptions use their "
                                                   "plausible range).")] = 0.5,
    grid: Annotated[int, Field(ge=2, le=200, description="Grid points per scanned input.")] = 17,
) -> dict[str, Any]:
    """What would change the recommendation: the flip points of numeric inputs and criterion weights, one
    at a time with everything else held fixed, each verified by re-running the kernel either side."""
    return _call(tools.flip, path, episode, param, span, grid)


def pdo_scenarios(path: StatePath, episode: Episode = None) -> dict[str, Any]:
    """Evaluate every declared scenario and say whether the recommendation holds in each (robust): a claim
    about the tested scenarios only."""
    return _call(tools.scenarios, path, episode)


def pdo_readiness(path: StatePath, episode: Episode = None) -> dict[str, Any]:
    """Readiness for authorization: ready, pending (some condition unknown) or blocked (some condition
    fails), each condition's status, reason and owning role, and whether the decision is actionable
    (ready, a unique recommendation, and an approval matching the current state)."""
    return _call(tools.readiness, path, episode)


def pdo_pending_actions(
    path: StatePath,
    episode: Episode = None,
    role: Annotated[str | None, Field(description="Only this role's actions (e.g. role:mda), or 'unassigned'.")]
    = None,
) -> dict[str, Any]:
    """What people must do before the decision is actionable, routed by owning role with the role's
    assignees. Each action names the tool call that resolves it (pdo_record_human_action or
    pdo_apply_change)."""
    return _call(tools.pending_actions, path, episode, role)


def pdo_program_status(path: ProgramPath) -> dict[str, Any]:
    """The program's episode tree: each episode's trigger, recommendation, readiness and actionable, the
    newest episode (where new work builds), and its pending actions."""
    return _call(tools.program_status, path)


def pdo_refresh(path: ProgramPath) -> dict[str, Any]:
    """Re-evaluate every episode selectively and verify each against a full recomputation. Reports
    all_verified and how many computed nodes the child episodes executed versus reused."""
    return _call(tools.refresh, path)


def pdo_impact(path: ProgramPath, change: Change, episode: Episode = None) -> dict[str, Any]:
    """Dry run a change set on an episode (default: its parent, else the baseline): the changed objects,
    the impact set, nodes rerun versus reused, the decision before and after, the outcome class, and
    whether an approval stops matching. Nothing is written."""
    return _call(tools.impact, path, change, episode)


def pdo_uncertainty(
    path: StatePath,
    episode: Episode = None,
    samples: Annotated[int | None, Field(ge=1, description="Draws (default: the decision rule's samples).")]
    = None,
    seed: Annotated[int | None, Field(description="Seed (default: the decision rule's seed).")] = None,
) -> dict[str, Any]:
    """Decision under uncertainty: sample the inputs that carry a distribution and report E[U],
    Pr(feasible), the chance-constrained recommendation and scenario robustness, beside the
    deterministic decision."""
    return _call(tools.uncertainty, path, episode, samples, seed)


def pdo_sensitivity(
    path: StatePath,
    episode: Episode = None,
    param: Annotated[str | None, Field(description="One input as '<object id>.<slot>', e.g. obs:B.cost.value or "
                                       "req:min-range.threshold_value. Omit for every input at once.")] = None,
    samples: Annotated[int | None, Field(ge=8, description="Draws (default 1000 for one input, 256 per "
                                                           "Saltelli matrix).")] = None,
    seed: Annotated[int | None, Field(description="Seed (default: the decision rule's seed, else 0).")] = None,
) -> dict[str, Any]:
    """Sensitivity analysis beyond the flip point. With `param`: how each alternative's utility responds
    across the input's range (slope, elasticity, nonlinearity, where the recommendation changes) and to its
    distribution (spread, P(win), P(feasible), expected regret, P(decision changes)). Without: a tornado of
    the decision margin, Sobol first-order and total indices, and value of information (EVPI, EVPPI per
    input, and which evidence to collect next)."""
    return _call(tools.sensitivity, path, episode, param, samples, seed)


DocumentPath = Annotated[str, Field(description="A report: .docx, .pdf, .md or .txt.")]


def pdo_docs_index(path: DocumentPath) -> dict[str, Any]:
    """A report's sections: id, heading path, pages, size and content digest, and the document digest. Start
    here, then read the sections that hold values with pdo_docs_read."""
    return _call(tools.docs_index, path)


def pdo_docs_read(
    path: DocumentPath,
    section: Annotated[str | None, Field(description="Section id (e.g. 3.2), heading or heading path; omit for "
                                                     "every section.")] = None,
) -> dict[str, Any]:
    """Section text exactly as pdo matches quotes against it. Copy quotes from here, character for character."""
    return _call(tools.docs_read, path, section)


def pdo_docs_locate(
    path: DocumentPath,
    quote: Annotated[str, Field(description="The exact passage that states the value.")],
    section: Annotated[str | None, Field(description="Search only this section.")] = None,
) -> dict[str, Any]:
    """Locate a quote in a report: section, page, paragraph, character span and digests, and the numbers it
    states. Refused when the quote is not in the report or occurs more than once."""
    return _call(tools.docs_locate, path, quote, section)


def pdo_docs_verify(path: StatePath, episode: Episode = None) -> dict[str, Any]:
    """Check every locator in a state or program against its document: errors when a quote is not where it
    says, warnings when a file is not the indexed version or a quote does not state its value."""
    return _call(tools.docs_verify, path, episode)


def pdo_list_examples() -> dict[str, Any]:
    """The example decisions bundled with pdo, with their paths. Copy one with pdo_copy_example before
    changing it."""
    return _call(tools.list_examples)


# --- tools that can write -------------------------------------------------------------------------

def pdo_apply_change(path: ProgramPath, change: Change, write: Write = False) -> dict[str, Any]:
    """Add a change set to a program as a new episode. The parent defaults to the newest episode and the
    id to the next in sequence. Always previews the outcome (as pdo_impact); with write=true, writes a new
    file under changes/, never editing an existing one, and keeps it only if the program still validates."""
    return _call(tools.apply_change, path, change, write)


def pdo_record_human_action(
    path: ProgramPath,
    kind: Annotated[Literal["approval", "review", "applicability", "risk_treatment", "assumption_validation"],
                    Field(description="What the person did.")],
    target: Annotated[str, Field(description="approval: the alternative approved; review: the review id; "
                                             "applicability: the observation; risk_treatment: the risk; "
                                             "assumption_validation: the assumption.")],
    role: Annotated[str, Field(description="The role acting, e.g. role:mda. For an approval, the approver role.")],
    status: Annotated[str | None, Field(
        description="review: pass | fail | pending (default pass); applicability: accepted | provisional | "
                    "rejected (default accepted); risk_treatment (required): untreated | mitigated | accepted | "
                    "unacceptable; assumption_validation: validated | unvalidated | invalid (default validated). "
                    "Not used for approvals.")] = None,
    rationale: Annotated[str | None, Field(description="The person's reason, recorded with the action.")] = None,
    write: Write = False,
    parent: Annotated[str | None, Field(description="Parent episode (default: the newest).")] = None,
) -> dict[str, Any]:
    """Record a person's judgement as a new change set: an approval, a review result, accepting or
    rejecting an observation's applicability, a risk treatment, or an assumption's validation. Only for
    a judgement the person holding that role has made. Previews the outcome; with write=true, writes it.
    The kernel stamps approvals and reviews with the state they cover, so a later change invalidates them."""
    return _call(tools.record_human_action, path, kind, target, role, status, rationale, write, parent)


def pdo_package(
    path: StatePath,
    episode: Episode = None,
    out: Annotated[str | None, Field(description="Directory to write package.md and record.json into; omit to "
                                                 "write nothing and return the Markdown.")] = None,
) -> dict[str, Any]:
    """The Decision Package of one episode: the recommendation and why, feasibility margins, flip points,
    scenario robustness, readiness, pending actions, and a validated, replayable record with one run per
    computed node."""
    return _call(tools.package, path, episode, out)


def pdo_docs_extract(
    path: StatePath,
    proposals: Annotated[dict[str, Any] | str, Field(
        description="Mapping (or YAML text, or a .yaml path): evidence (default evidence id), accept (true only "
                    "when the person recording it accepts the values), optional evidence_items to add (each with "
                    "document_path), and observations: [{id, alternative, measure, value, unit, quote, section, "
                    "evidence}]. An existing observation id updates its value and locator.")],
    write: Write = False,
    role: Annotated[str | None, Field(description="The role recording the change (author_role).")] = None,
    parent: Annotated[str | None, Field(description="Parent episode (default: the newest).")] = None,
    episode_id: Annotated[str | None, Field(description="The new episode's id (default: the next in sequence).")]
    = None,
    as_of: Annotated[str | None, Field(description="The new episode's date, YYYY-MM-DD (default: the parent's).")]
    = None,
) -> dict[str, Any]:
    """Propose values from reports as a change set. Each quote is located in its evidence's document and
    refused if it is not there; new or changed values are provisional unless accept is true. Previews the
    episode; with write=true, adds it. Re-anchoring a value in a changed section is a person's judgement:
    do it only when that person has confirmed the value."""
    return _call(tools.docs_extract, path, proposals, write, role, parent, episode_id, as_of)


def pdo_docs_revise(
    path: ProgramPath,
    document: Annotated[str, Field(description="The evidence id of the report, or its document_path.")],
    new_file: Annotated[str, Field(description="The new version of the report, inside the program directory.")],
    write: Write = False,
    role: Annotated[str | None, Field(description="The role recording the change (author_role).")] = None,
    parent: Annotated[str | None, Field(description="Parent episode (default: the newest).")] = None,
    episode_id: Annotated[str | None, Field(description="The new episode's id (default: the next in sequence).")]
    = None,
    as_of: Annotated[str | None, Field(description="The new episode's date, YYYY-MM-DD (default: the parent's).")]
    = None,
) -> dict[str, Any]:
    """Absorb a new version of a report. Locators whose section text is unchanged move to it; values in
    changed sections are flagged, and readiness stays pending (locators_current) until a person re-anchors
    them. No value changes, so no approval is invalidated. Previews; with write=true, writes."""
    return _call(tools.docs_revise, path, document, new_file, write, role, parent, episode_id, as_of)


def pdo_dashboard(
    path: StatePath,
    out: Annotated[str, Field(description="Directory for index.html and dashboard.json (new, or holding only a "
                                          "previous dashboard).")],
    flips: Annotated[bool, Field(description="Flip points for every episode.")] = True,
    sensitivity: Annotated[bool, Field(description="Tornado per episode; Sobol indices and value of information "
                                                   "for the baseline.")] = True,
) -> dict[str, Any]:
    """Write a read-only HTML dashboard of a state or program for people to look at: the dependency graph per
    episode (status of every node, and what each refresh recomputed versus reused), the decision table,
    readiness, pending actions by role, utility across episodes, flips, tornado, uncertainty and value of
    information. The page draws kernel results and computes nothing."""
    return _call(tools.dashboard, path, out, flips, sensitivity)


def pdo_import_sysml(
    paths: Annotated[list[str] | str, Field(description="SysML v2 textual files (.sysml), or directories of them.")],
    mapping: Annotated[dict[str, Any] | str | None, Field(
        description="Mapping file path, its YAML text, or its content: ids, units, the alternatives' source, "
                    "evaluation wiring.")] = None,
    out: Annotated[str | None, Field(description="Directory to write the draft (sysml-*.yaml and "
                                                 "sysml-import.json); omit to write nothing.")] = None,
) -> dict[str, Any]:
    """Import SysML v2 textual models into a draft decision state: alternatives, measures, requirements,
    observations with evidence, assumptions and evaluation placeholders, each traced to its element and
    mapping rule, plus every gap the import could not map. It never imports framing."""
    return _call(tools.import_sysml, paths, mapping, out)


def pdo_copy_example(
    name: Annotated[str, Field(description="Example name from pdo_list_examples, e.g. vehicle.")],
    dest: Annotated[str, Field(description="New directory to copy it into (must not exist, or be empty).")],
) -> dict[str, Any]:
    """Copy a bundled example to a new directory to run or change it."""
    return _call(tools.copy_example, name, dest)


READ_ONLY = (pdo_describe_schema, pdo_validate, pdo_run, pdo_explain, pdo_flip, pdo_scenarios, pdo_readiness,
             pdo_pending_actions, pdo_program_status, pdo_refresh, pdo_impact, pdo_uncertainty, pdo_sensitivity,
             pdo_docs_index, pdo_docs_read, pdo_docs_locate, pdo_docs_verify, pdo_list_examples)
WRITES = (pdo_apply_change, pdo_record_human_action, pdo_docs_extract, pdo_docs_revise, pdo_package, pdo_dashboard,
          pdo_import_sysml, pdo_copy_example)


def build_server():
    """The MCPServer with every pdo tool registered (needs the mcp extra)."""
    from mcp.server.mcpserver import MCPServer
    from mcp.types import ToolAnnotations

    server = MCPServer(name="pdo", title="Programmable decision ops", version=__version__,
                       instructions=INSTRUCTIONS)
    reads = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)
    # Writes only add new files (a dashboard replaces only its own earlier ones), so they are not destructive.
    writes = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False,
                             open_world_hint=False)
    for fn in READ_ONLY:
        server.add_tool(fn, name=fn.__name__, description=inspect.cleandoc(fn.__doc__), annotations=reads)
    for fn in WRITES:
        server.add_tool(fn, name=fn.__name__, description=inspect.cleandoc(fn.__doc__), annotations=writes)
    return server


def main() -> None:
    """Serve over stdio until the client closes the stream."""
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    log.info("pdo %s MCP server on stdio", __version__)
    build_server().run("stdio")


if __name__ == "__main__":  # pragma: no cover
    main()
