# The dashboard

`pdo dashboard PATH -o DIR` writes a read-only rendering of a decision state or a decision program:

- `index.html`: one self-contained page with the data embedded. Open it directly, attach it, or publish
  it. It loads IBM Plex from Google Fonts and falls back to system fonts offline.
- `dashboard.json`: the same data, for other tools.
- `fragment.html` (with `--fragment`): the page without its `<!doctype>`/`<html>`/`<body>` wrapper,
  for hosts that supply the wrapper themselves.

The page computes nothing. `pdo.dashboard.build` evaluates the program the way `pdo program refresh` does:
each episode selectively against its parent, verified against a full recomputation. It then records what
the page shows. The MCP tool `pdo_dashboard` writes the same files. Neither replaces a page in the output
directory unless an earlier dashboard wrote it.

## Two views

The page has two views of the same program, switched by the tabs under the title, and a calculation panel
that opens from either. The URL hash links to an episode in either view: `#e06` (Decision) or `#e06.engineering`.

Everything on the page is a structured object. An episode is a change set applied to its parent, like a
git commit. Each reference to the decision state is a chip with the object's schema class and id, such as
`Observation obs:B.cost`, `Requirement req:min-range` or `Role role:test-engineer`, and selecting it opens
the calculation at that object. Main-line episodes are named by their position from the baseline ("Episode 0
(baseline)", "Episode 10"), and episodes off the main line by their id ("Branch r1"), always with the change
set id (`ep:e10`) beside the name.

### Decision (the default): for decision makers and reviewers

The program as a story, in plain words. A **timeline** runs down the left: every main-line episode in date
order, with its date, trigger and description. Each entry carries chips for its recommendation, readiness and
approval, and an outlined chip when the recommendation changed ("B → A"). Branches (what-ifs and side
histories) are folded under the episode they fork from. Selecting an entry fills the panels on the right:

| Panel | Content |
|---|---|
| Verdict | The recommendation with the alternative's name, a status pill that says what readiness means here ("approved · can act", "ready for approval", "waiting on Test engineer", "blocked: Evidence … names its source"), and one sentence on what it waits on and whether the approval still holds. Under `expected_mavt` it is the expected-score choice. A button opens the episode's calculation; chips name the nodes that computed the verdict (`decision`, `readiness`, `actionable`) and the matching approval. |
| What changed | The change set itself: its id, parent, date, trigger and author role, its description, its effect against the parent (recommendation, readiness, approval still matching or not, a revised report's values carried over or awaiting review), and its operations (assign, add, remove), each with the object, slot, before and after. |
| Why this recommendation | A sentence: the recommended alternative's score against the runner-up's, the criteria it leads and trails on (weighted points), any alternative that scores higher but fails a requirement and why, and any that cannot be ranked yet. Below it, the decision matrix: each criterion's weight and each alternative's measured value with its 0-to-1 score as a bar, and the overall score. Then the must-meet requirements, each alternative's value with the room to spare or the shortfall. Every cell opens the node that computed it (`v/…`, `U/…`, `g/…`). |
| Before anyone can act | The readiness checks that do not pass, with their reasons; who the decision waits on, by role, in words ("Re-check C's mission reliability against its source"); and the checks that pass, folded. |
| What would change it | The smallest single changes that would change the recommendation, in words and nearest first ("If B's unit acquisition cost rises above 1.27 MUSD (now 1.22, a 4% change), A would be recommended instead"). For an uncertain decision, the input most worth pinning down next. |
| Scores over time | Each alternative's overall score along the main line, with a readiness band and approval marks. |
| Where the numbers come from | Every value with its source: the report, section, page and exact quote when it is located, else the evidence citation; and whether it is accepted, provisional or awaiting review. |

### The calculation panel: one episode's graph, top to bottom

A side panel shows how one episode was computed, from the schema objects at the top to the authorization at
the bottom. It opens from the verdict's button, from any matrix cell, and from any object or node chip:

1. **Inputs:** the schema objects, as chips grouped by class: each alternative's own objects (its
   `Alternative`, its `Observation`s) in its lane, the program-wide ones (requirements, criteria,
   evidence, governance) beside them.
2. **Evaluator runs**, 3. **Measures**, 4. **Requirement checks**, 5. **Value scores**, 6. **Overall score and
   feasibility:** one lane per alternative.
7. **Decision**, 8. **Readiness checks**, 9. **Readiness and authorization.**

Each node is a card with its kind, its value and status, and whether this episode re-ran it (a blue border
and ↻) or reused it. "Only what this episode re-ran" fades the rest. Selecting a node outlines everything it
depends on (solid) and everything that depends on it (dashed), and the inspector shows:

- **Calculation:** the node's formula with the kernel's own numbers, e.g. `g = threshold − y = 300 − 360 = -60`,
  `U = 0.4 × 0.6 + 0.4 × 0.333 + 0.2 × 0.8 = 0.5333`, or the evaluator call with its arguments and outputs.
  The page shows the arithmetic; the result is always the kernel's.
- **In this episode:** re-ran or reused, and the value's content digest.
- **Inputs** and **feeds**, as chips that move the selection.
- For a schema object: its class's description and every slot with its value and meaning. Hashable slots
  (the ones that feed the calculation) are marked apart from provenance and prose.
- The raw node as the kernel recorded it (id, kind, function, inputs, execution, value digest, value) and the
  raw object, as JSON.

### Engineering: for engineers who monitor the compiled graph

| Panel | Content |
|---|---|
| Episode strip | The main line and the branches as chips (date, trigger, recommendation, readiness, approval). |
| Summary | The recommendation, readiness, authorization with its reason, how many computed nodes the refresh re-evaluated and whether that matched a full run, and the state digest an approval binds to. |
| Dependency graph | Every node of the selected episode, in layers. Fill is the status (pass, unknown, fail, value), with a glyph so color is never the only cue. The outline shows how the refresh treated the node: solid if re-evaluated, dashed if reused because its inputs did not change. A bar on the left marks the alternative. "Show inputs" adds the source objects. "Highlight what this episode recomputed" fades everything outside the impact set. Hovering a node highlights its edges; clicking it lists its value, its inputs with their values, what it feeds and, for a value taken from a report, the document, section, page and quote. `¶` marks such a value; `¶?` marks one whose section changed in a revision and awaits review. |
| Decision table | U (or its bounds when an input is unknown), feasibility with each failed or unknown requirement and its margin g, and each measure. |
| Readiness conditions, pending actions | Each condition's status and the kernel's reason; pending actions by role with their kinds and targets. |
| Refresh work | Per episode, the computed nodes re-evaluated versus reused. |
| Flips, tornado | Flip points and weight trade-offs per episode. The tornado shows the decision margin with each input at the low and high ends of its range. |
| Uncertainty, sensitivity | Under `expected_mavt`: E[U], Pr(feasible), the chance constraint and Pr(wins a draw). For the baseline episode, when inputs declare distributions: Sobol total indices of the margin and EVPPI per input, with intervals. |

## Layout

Columns follow the kernel's compilation rules. From left to right:

1. Model inputs
2. Evaluator runs
3. Measures
4. Checks and values
5. Utility and feasibility
6. Decision
7. Readiness conditions
8. Readiness
9. Authorization

A node is placed as late as possible, but never right of its kind's column. So a measure that feeds an
evaluator lands in "Model inputs", left of the run that reads it, and every edge points right. The
layout is computed once over the union of every episode's nodes, so a node keeps its place as you move
between episodes. Within a column, rows are grouped by alternative.

## dashboard.json

```
meta          title, question, path, kernel_version, baseline, main_line, rule_kind
alternatives  ids; alternative_names, roles, objects: id -> name
measures, requirements, criteria
layout        columns (names), nodes: id -> {col, row, kind, label, fn, alternative, subject},
              edges: [parent, child], geometry
values        value digest -> the node value the kernel recorded (each distinct value once)
object_pool   object key -> {class, ...slots} (each distinct object once, across every episode)
schema        class -> {description, slots {slot: description}, hashable [slots]}
episodes[]    id, parent, as_of, trigger, description,
              recommended, feasible, infeasible, unresolved, readiness, actionable, actionable_reason,
              approval, state_digest, conditions, pending (actions), table (decision table rows),
              nodes: id -> {s: status, v: value text, e: exec status, h: value digest, d?: detail,
                            src?: [{observation, document, section, section_path, page, quote, current}]},
              impact (node ids, null for the baseline), verified, cost {executed, computed, total},
              author_role, change_set (the ChangeSet as recorded), objects {id: object key},
              changes [{op: set|add|remove, target, slot, before, after, provenance, kind}],
              weights {criterion: w}, requirements {id: {comparator, threshold, unit, measure, mandatory}},
              evidence [{observation, alternative, measure, value, unit, applicability, located, current,
              source}], flips (each with label, unit, counterpart_label), sensitivity {tornado, global,
              value_of_information, notes}, uncertainty
              (decision table rows also carry values {criterion: v} and checks {requirement: status})
```

Non-finite numbers are written as `null`. Names and descriptions from the YAML are drawn as text,
never as markup.
