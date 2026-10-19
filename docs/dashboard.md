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

## What it shows

| Panel | Content |
|---|---|
| Episode strip | The main line (baseline to newest episode) and the branches. Each chip shows the date, trigger, recommendation and readiness, and marks an approved episode. Selecting one updates every panel; the URL hash (`#e06`) links to it. |
| Summary | The recommendation (under `expected_mavt`, the expected-utility choice with the point-value choice beside it), readiness, authorization with its reason, how many computed nodes the refresh re-evaluated and whether that matched a full run, and the state digest an approval binds to. |
| Dependency graph | Every node of the selected episode, in layers. Fill is the status (pass, unknown, fail, value), with a glyph so color is never the only cue. The outline shows how the refresh treated the node: solid if re-evaluated, dashed if reused because its inputs did not change. A bar on the left marks the alternative. "Show inputs" adds the source objects. "Highlight what this episode recomputed" fades everything outside the impact set. Hovering a node highlights its edges; clicking it lists its value, its inputs with their values, what it feeds and, for a value taken from a report, the document, section, page and quote. `¶` marks such a value; `¶?` marks one whose section changed in a revision and awaits review. |
| Decision table | U (or its bounds when an input is unknown), feasibility with each failed or unknown requirement and its margin g, and each measure. |
| Readiness conditions, pending actions | Each condition's status and reason; what people must do, grouped by owning role. |
| Utility across the program | U per alternative along the main line, with a readiness band and approval marks. A gap means the utility was unknown in that episode. |
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
layout        columns (names), nodes: id -> {col, row, kind, label, alternative, subject},
              edges: [parent, child], geometry
episodes[]    id, parent, as_of, trigger, description,
              recommended, feasible, infeasible, unresolved, readiness, actionable, actionable_reason,
              approval, state_digest, conditions, pending (actions), table (decision table rows),
              nodes: id -> {s: status, v: value text, e: exec status, d?: detail,
                            src?: [{observation, document, section, section_path, page, quote, current}]},
              impact (node ids, null for the baseline), verified, cost {executed, computed, total},
              flips, sensitivity {tornado, global, value_of_information, notes}, uncertainty
```

Non-finite numbers are written as `null`. Names and descriptions from the YAML are drawn as text,
never as markup.
