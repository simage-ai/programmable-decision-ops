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

The page has two views of the same program, switched by the tabs under the title, and an explain tree that
opens from either. The URL hash links to an episode in either view: `#e06` (Decision) or `#e06.engineering`.

Everything on the page is a structured object. An episode is a change set applied to its parent, like a
git commit. Each reference to the decision state is a chip with the object's schema class and id, such as
`Observation obs:B.cost`, `Requirement req:min-range` or `Role role:test-engineer`, and selecting it opens
the explain tree at that object. Main-line episodes are named by their position from the baseline ("Episode 0
(baseline)", "Episode 10"), and episodes off the main line by their id ("Branch r1"), always with the change
set id (`ep:e10`) beside the name.

### Decision (the default): for decision makers and reviewers

The program as a timeline of episodes, shown in the kernel's own terms: ids, values and formulas, with no
generated sentences. The only prose is what people wrote (each change set's description, object names).

The **timeline** lists every main-line episode in date order: its name and change set id, date, trigger and
description; a chip with `decision.recommended` (outlined when it changed from the parent:
`[alt:B] → [alt:A]`); and the readiness state with the condition holding it (`pending · rc:evidence-current`,
`blocked · rc:evidence-traceable`) or the matching approval (`actionable · appr:e08-b`). Branches are folded
under the episode they fork from. Selecting an entry fills the panels:

| Panel | Content |
|---|---|
| Verdict | `decision → [alt:A]` and a record of the episode's outputs: `U` per alternative, `feasible`, `infeasible`, `unresolved`, `readiness` with the conditions not passing, `actionable` with the kernel's reason, and `approval`. `explain(decision)` opens the explain tree. |
| ChangeSet | The change set's `id`, `parent`, `as_of`, `trigger` and `author_role`, its description, and its operations (assign, add, remove: object, slot, before, after). Below them, the outputs against the parent's (`decision.recommended`, `feasible`, `U/…`, `readiness`, `actionable`, `approval`), with changed rows marked. |
| U and F | The decision matrix: for each criterion its weight `w`, and for each alternative `y` (the measured value) and `v` (its score, also as a bar); then `U = Σ w·v` and `F`. Below it, each mandatory requirement's test (`y ≥ 300 mi`) and each alternative's margin `g` (pass when `g ≤ 0`). The formulas are in the panel's header. Every cell opens its node in the explain tree. |
| Readiness | Each readiness condition and bias check with its status and the kernel's reason (the failing and unknown ones first), and the pending actions: `kind`, `target`, `role`. |
| Flip points | The nearest flip points: the parameter (`obs:B.cost.value`, or a weight against its counterpart), its value now, where the recommendation flips, the change as a percentage, and the new `decision.recommended`. For an uncertain decision, the inputs with the highest EVPPI and the evidence that would resolve them. |
| U by episode | `U` per alternative along the main line, with a readiness band and approval marks. |
| Observations | Every observation with its value, applicability, evidence and, when located, its report passage. |

### The explain tree

A side panel, `explain(decision)`, opens from the verdict, from any matrix cell and from any chip; opened at a
value, it shows the path to it already expanded. Its roots are `decision` and `actionable`, and each line is a
node: its id, its value, and the formula that produced it, with the kernel's numbers. Opening a line shows the
nodes it was computed from:

```
decision → [alt:A]  argmax U over F = pass          A 0.47 · B 0.43 · C 0.62 (F fail)
  U/alt:A 0.47  = Σ w·v = 0.4×0.67 + 0.4×0.2 + 0.2×0.6
    v/crit:range/alt:A 0.20  = (y − 300) / (400 − 300), y = 320
      y/alt:A/meas:range 320 mi  = range_model(tank_gal=40, mpg=8, terrain_factor=1)
      Criterion crit:range { measure: meas:range, anchor_worst: 300, anchor_best: 400, weight: 0.4 }
  ✕ F/alt:C fail  failed [req:min-reliability]
actionable false  = ready ∧ approval matches state_digest ∧ current
```

Evaluator runs are folded into the measure they produce, and weights appear in the sum. The leaves are the
schema objects, one line each: class, id and the hashable slots; an observation from a report also shows its
passage and opens into its evidence. ⓘ on any line shows the class's description and every slot (hashable
slots marked apart from provenance and prose), or the node's raw value. A dot marks the nodes this episode
re-ran. The page shows the arithmetic; every value is the kernel's.

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
