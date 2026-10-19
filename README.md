# programmable-decision-ops

Decisions as programs: an open-source decision schema, a deterministic kernel, and agent skills for
building engineering and acquisition decisions that are auditable and stay correct as their inputs
change.

A decision and its justification become an executable dependency graph over typed objects:
alternatives, requirements, criteria, evidence, assumptions, evaluations, readiness conditions,
reviews and approvals. When an input changes, the kernel finds what that change can affect,
re-evaluates only that, and records whether the recommendation, or the decision's readiness for
authorization, changed. Every number in a result traces back to the evaluation that produced it.

The neurosymbolic split is explicit: **agents propose and the kernel decides.** An agent elicits the
decision, extracts evidence from documents and SysML models, and writes the objects. The kernel checks
types, units and references, runs the evaluators, applies the decision rule and the readiness policy,
and refuses to treat anything unaccepted or missing as a pass.

```bash
uv sync --all-extras
uv run pdo run examples/vehicle
```

```
# alt:B

Readiness: READY   Actionable: no (alt:B has no approval)

| alternative | unit-cost [MUSD] | range [mi] | reliability [1] | U | feasible |
|---|---|---|---|---|---|
| alt:A | 1 | 320 | 0.97 | 0.466667 | pass |
| alt:B * | 1.2 | 360 | 0.98 | 0.533333 | pass |
| alt:C | 0.95 | 380 | 0.94 | 0.62 | fail: req:min-reliability (g=0.01) |
...
## Pending actions
- [role:mda] approve alt:B: approve alt:B (role:mda)
```

## What it does

- **Typed decisions.** A [LinkML schema](schema/) defines decision states, programs and episode
  records. Every slot is marked `hashable` (it is part of what the object is) or `unhashable` (prose,
  routing, stamps), in the style of DAPPER's identity profile, and content digests use the same
  `sha512t24u` function.
- **A compiled dependency graph.** Authors never write nodes or edges. The kernel compiles them from
  the typed objects by fixed rules (observations and evaluator runs, then measures, then checks and
  values, then feasibility and utility, then the decision, then readiness, then actionable), so impact
  analysis is sound by construction.
- **Unknowns stay unknown.** A missing value, an observation whose applicability nobody accepted, or a
  failed evaluator is unknown. It is never an invented pass or fail. A known violation dominates an
  unknown.
- **Readiness and authorization, kept separate.** Readiness conditions and bias checks (omitted
  options, double counting, framing review) evaluate to pass, fail or unknown, giving ready, blocked or
  pending. An approval binds to the digest of the state it covered: any later material change stops it
  matching, and prose edits do not.
- **Long-horizon programs.** A baseline episode plus change sets, each producing an episode.
  `pdo program refresh` re-evaluates each episode selectively and verifies it against full
  recomputation.
- **What flips the decision.** Exact thresholds where the crossing is affine, bisection elsewhere,
  closed-form weight trade-offs, and robustness over declared scenarios.
- **Decisions under uncertainty.** Monte Carlo over declared distributions with common random numbers
  and chance constraints (`rule_kind: expected_mavt`).
- **Sensitivity analysis.** Response curves through nonlinear evaluators (slope, elasticity, departure
  from linear), the response to one input's distribution (spread, P(win), regret), a tornado of the
  decision margin, Sobol first-order and total indices, and value of information (EVPI, and EVPPI per
  input with the evidence that would resolve it).
- **Values traced to report sections.** Word, PDF and Markdown reports are indexed into sections with
  content digests. Each value taken from a report carries a locator (document, section, page, exact
  quote) that `pdo docs` computes, refusing any quote that is not in the document, and `pdo validate`
  checks. When a report is revised, only the values whose section changed go back to a person, and the
  `locators_current` readiness condition holds the decision pending until they are reviewed. A
  revision changes no value, so it invalidates no approval. See [docs/semantics.md](docs/semantics.md).
- **Decision Packages.** `package.md` for people and `record.json` for machines (it conforms to the
  published record schema and replays node by node), with assessments expressed as claims whose basis
  is computed, semantic (agent interpretation) or human.
- **Pending actions by role.** With one person they are a to-do list. In a collaborative harness each
  role is a person to notify.
- **A read-only dashboard.** `pdo dashboard` writes one self-contained HTML page (and the JSON behind it)
  that shows the program in the kernel's own terms. The Decision view is a timeline of episodes (change
  sets, like commits); for each it shows `decision → [alt:A]`, the record of its outputs, the change set's
  operations and the outputs that changed against the parent, the decision matrix in `y`, `v`, `U`, `g` and
  `F`, readiness conditions and pending actions, flip points, and every observation with its source. An
  explain tree opens any value into the nodes it was computed from, with their formulas, down to the schema
  objects. The Engineering view shows the whole compiled graph: every node's status and whether the
  episode's refresh recomputed or reused it. The page draws kernel results and computes nothing. See
  [docs/dashboard.md](docs/dashboard.md).

## Examples

| Example | Shows |
|---|---|
| [examples/vehicle](examples/vehicle) | The research brief's synthetic vehicle trade study (episode `ep:e00`) and a 12-episode decision program built on it (cost revisions, a provisional test report, an unsourced quote, an expiry, a flip to A, re-approvals), plus the brief's four branches. The configuration sheets (PDF) and the reliability report (Word) are indexed documents, and episodes `ep:r1`..`ep:r4` absorb two revisions of the report: the first flags only C's value and keeps B's approval, the second raises C to 0.96 and C wins. `expected.yaml` holds the hand-computed outcome of every episode. |
| [examples/vehicle/sysml](examples/vehicle/sysml) | The same three configurations as a SysML v2 model, with the mapping that imports it onto the baseline's ids. |
| [examples/fires_sensor](examples/fires_sensor) | A synthetic counterfire-radar upgrade choice under uncertainty. The detection range follows the radar equation (range ∝ RCS^¼), and the point-value winner fails the chance constraint, so `pdo uncertainty` recommends a different option. |

All numbers in the examples are synthetic and illustrative. They are not performance estimates for any
real system.

## Commands

Every command takes `--json` (`{"ok": true, ...}`, or `{"ok": false, "error": ...}` with exit code 1).

| Command | What it does |
|---|---|
| `pdo validate PATH` | Schema, references, units, weights, producers; compiles every episode of a program |
| `pdo run PATH [-e EP]` | Recommendation, feasibility, utilities, readiness, actionable, pending actions |
| `pdo explain PATH NODE` | Why a node has its value: inputs, implementation, signature |
| `pdo graph PATH` | The dependency graph (Mermaid or JSON) |
| `pdo actions PATH [--role R]` | Pending human actions by role |
| `pdo program status\|refresh\|record PATH` | The episode tree; selective refresh with verification; record an approval or review |
| `pdo impact PATH CHANGE.yaml` | Dry-run a change set: delta, impact set, rerun versus reused, before and after |
| `pdo flip PATH` / `pdo scenarios PATH` | Flip points and weight trade-offs; scenario robustness |
| `pdo uncertainty PATH` | E[U], Pr(feasible), the chance-constrained decision, scenario robustness |
| `pdo sensitivity PATH [-p ID.slot] [--figures DIR]` | Response curve and distribution of one input; or tornado, Sobol indices and value of information for all |
| `pdo docs index\|show FILE [SECTION]` | A report's sections (ids, heading paths, pages, digests); a section's text as quotes are matched |
| `pdo docs locate FILE QUOTE` | The locator of a quote; refused when the quote is not in the report |
| `pdo docs extract PATH PROPOSALS.yaml` / `pdo docs revise PATH DOC NEW_FILE` | Values with quotes as a change set (provisional unless accepted); absorb a revised report section by section |
| `pdo docs verify PATH` | Check every locator against its document |
| `pdo dashboard PATH [-o DIR] [--no-flips] [--no-sensitivity]` | Write the read-only dashboard: a self-contained `index.html` (offline it falls back to system fonts) and `dashboard.json` |
| `pdo package PATH -o DIR [--sensitivity]` / `pdo replay RECORD.json` | Write a Decision Package (optionally with sensitivity and value of information); replay it node by node |
| `pdo sysml import FILES --map M --out DIR` / `pdo sysml gaps FILES` | Import SysML v2 into a draft state; list what did not map |
| `pdo bench run\|check bench/vehicle` | The representation benchmark |
| `pdo schema describe [CLASS]` / `pdo example list\|copy` | The schema; the bundled examples |
| `pdo mcp` | Serve every tool over MCP (stdio) |

## For agents

- **MCP.** `pdo mcp` serves the same operations as tools (`pdo_run`, `pdo_explain`, `pdo_flip`,
  `pdo_impact`, `pdo_apply_change`, `pdo_record_human_action`, `pdo_import_sysml`, `pdo_package`,
  `pdo_docs_extract`, `pdo_docs_revise`, `pdo_dashboard`, ...).
  Each returns `{ok, ...}`, or `{ok: false, error, hint}`. Write tools preview by default and write a
  change set only when asked. To register it, for example with Claude Code:
  `claude mcp add pdo -- uv run --directory /path/to/programmable-decision-ops pdo mcp`. In any harness
  that runs local stdio servers, the command is `pdo mcp` once installed.
- **Skills.** [skills/sysml-to-decision](skills/sysml-to-decision/SKILL.md) teaches an agent to turn a
  SysML v2 model into a decision the kernel can run:
  1. import the mechanical part;
  2. review the mapping gaps;
  3. frame criteria, weights and readiness with the person, never inventing them;
  4. record the agent's own interpretations as provisional;
  5. validate, run, and explain.

  [skills/document-provenance](skills/document-provenance/SKILL.md) teaches it to take values from
  reports with exact quotes (provisional until a person accepts them), and to absorb revised reports so
  that only values in changed sections go back to the person.

  Skills follow the Agent Skills format (`SKILL.md` with frontmatter) and ship in the wheel under
  `pdo/skills`.

## The benchmark

`bench/vehicle/perturbations.yaml` holds 45 controlled perturbations of the approved vehicle baseline:

- 10 families (cost, requirement, evidence, assumption, model version, preference, review, provenance,
  time, no change);
- all five outcome classes (empty feasible set, changed winner, recompute with the same winner,
  readiness only, no material change);
- 14 cases held out.

The labels, and the expected outcome of every timeline episode, were computed by hand from
[docs/semantics.md](docs/semantics.md) by an agent that never read the kernel. They were committed
before any kernel output was compared with them.

`pdo bench check bench/vehicle` fails unless all of the following hold for impact and cutoff re-evaluation alike:

- label agreement 45/45;
- equivalence with full recomputation 45/45;
- soundness (every changed node inside the impact set) 45/45.

`make proposal-values` writes `results/proposal/pdo_values.tsv`, the numbers the proposal quotes,
including:

- equivalence and soundness;
- recomputation savings, about 75% of computed nodes for impact and 79% for cutoff;
- winner-change and readiness-violation detection;
- ablations that break equivalence (delta only, one hop, no readiness);
- an edge-omission curve that simulates compilation errors.

`pdo bench provenance bench/provenance` (also run by `make bench`, and gated in CI with `--check`)
applies 23 hand-labelled section-level edits to the example's two reports (including three where a value's
old text survives in another section) and absorbs each with `pdo docs revise`. Every case flags exactly the
labelled values, and its own changed-passage detection agrees with the labels; 18 values go back for review
against 93 under a whole-document policy; every value whose quote left its section is flagged (12 of 12),
and the other 6 flagged values share a changed section; no approval is invalidated and no decision node is
recomputed in any case. The labels were written by the implementer from the edits; an independent agent
re-derived the first 20 blind and agreed on all of them, and checked the other 3 afterwards.

Figures are in `results/figures/` (sensitivity figures from `make sensitivity-figures`).

## Layout

```
schema/            LinkML schema (core, problem, evidence, evaluation, governance, state, program, record)
src/pdo/           kernel: load, graph, builtins, run, episodes, actions, flip, uncertainty, package,
                   sensitivity, dashboard (+ dashboard.html), docs, provenance, sysml, tools,
                   mcp_server, cli*, bench/
docs/semantics.md  the specification the kernel and the benchmark labels are both written against
docs/dashboard.md  what the dashboard shows and the shape of dashboard.json
examples/          vehicle (trade study and program), fires_sensor (uncertainty)
skills/            agent skills
bench/             benchmark suites (independent labels): vehicle (perturbations), provenance (report edits)
results/           generated benchmark tables, figures and proposal values
project/           generated JSON Schema and Pydantic models (regenerate with `make gen-project`)
```

## Development

```bash
make install         # uv sync --all-extras
make test            # pytest (slow and live tests: -m slow, -m live)
make lint            # linkml-lint, identity markers, ruff
make gen-project     # regenerate models and the identity table after editing schema/
make bench           # run the benchmarks (perturbations and provenance)
make example-docs    # rebuild the example's reports from Markdown (needs pandoc and xelatex)
make dashboards      # dashboards of both examples, in site/
```

Python 3.11 or later. Generated files are committed. CI regenerates them and fails on a diff, and it
also runs `pdo bench check`.

## Roadmap

- **Dashboard follow-ups.** Response curves in the node details, and an onboarding progress view.
- **Finer provenance.** Paragraph- or sentence-level digests to flag fewer unchanged values that share a
  changed section; locators for assumptions and evaluator parameters; scanned PDFs (OCR).
- **Onboarding.** `pdo init` and an onboarding skill that take someone from a folder of reports and SysML
  models through three funnels (construct the decision graph, run it, study it), always offering the
  next step.
- **More skills.** Decision framing, evidence compilation from documents, evaluator authoring, program
  refresh, and audit.
- **The agent-extraction experiment and an LLM document baseline.** These test how well agents populate
  the representation, separately from the representation itself.
- **DAPPER-ID-1 export of decision records**, for provenance interchange.

## License

MIT
