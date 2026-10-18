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
- **Decision Packages.** `package.md` for people and `record.json` for machines (it conforms to the
  published record schema and replays node by node), with assessments expressed as claims whose basis
  is computed, semantic (agent interpretation) or human.
- **Pending actions by role.** With one person they are a to-do list. In a collaborative harness each
  role is a person to notify.

## Examples

| Example | Shows |
|---|---|
| [examples/vehicle](examples/vehicle) | The research brief's synthetic vehicle trade study (episode `ep:e00`) and a 12-episode decision program built on it (cost revisions, a provisional test report, an unsourced quote, an expiry, a flip to A, re-approvals), plus the brief's four branches. `expected.yaml` holds the hand-computed outcome of every episode. |
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
| `pdo package PATH -o DIR` / `pdo replay RECORD.json` | Write a Decision Package; replay it node by node |
| `pdo sysml import FILES --map M --out DIR` / `pdo sysml gaps FILES` | Import SysML v2 into a draft state; list what did not map |
| `pdo bench run\|check bench/vehicle` | The representation benchmark |
| `pdo schema describe [CLASS]` / `pdo example list\|copy` | The schema; the bundled examples |
| `pdo mcp` | Serve every tool over MCP (stdio) |

## For agents

- **MCP.** `pdo mcp` serves the same operations as tools (`pdo_run`, `pdo_explain`, `pdo_flip`,
  `pdo_impact`, `pdo_apply_change`, `pdo_record_human_action`, `pdo_import_sysml`, `pdo_package`, ...).
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

Figures are in `results/figures/`.

## Layout

```
schema/            LinkML schema (core, problem, evidence, evaluation, governance, state, program, record)
src/pdo/           kernel: load, graph, builtins, run, episodes, actions, flip, uncertainty, package,
                   sysml, tools, mcp_server, cli*, bench/
docs/semantics.md  the specification the kernel and the benchmark labels are both written against
examples/          vehicle (trade study and program), fires_sensor (uncertainty)
skills/            agent skills
bench/             benchmark suites (independent labels)
results/           generated benchmark tables, figures and proposal values
project/           generated JSON Schema and Pydantic models (regenerate with `make gen-project`)
```

## Development

```bash
make install         # uv sync --all-extras
make test            # pytest (slow and live tests: -m slow, -m live)
make lint            # linkml-lint, identity markers, ruff
make gen-project     # regenerate models and the identity table after editing schema/
make bench           # run the benchmark
```

Python 3.11 or later. Generated files are committed. CI regenerates them and fails on a diff, and it
also runs `pdo bench check`.

## Roadmap

- **Sensitivity analysis.** Response curves across a variable's range, the response to a distribution
  over one variable through nonlinear evaluators, global (variance-based) indices, and value of
  information per input: which evidence is worth collecting next.
- **More skills.** Decision framing, evidence compilation from documents, evaluator authoring, program
  refresh, and audit.
- **The agent-extraction experiment and an LLM document baseline.** These test how well agents populate
  the representation, separately from the representation itself.
- **DAPPER-ID-1 export of decision records**, for provenance interchange.

## License

MIT
