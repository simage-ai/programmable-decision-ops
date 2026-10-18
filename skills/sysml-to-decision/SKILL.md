---
name: sysml-to-decision
description: >-
  Turn SysML v2 textual models (.sysml) into a pdo decision the kernel can run and audit: configurations
  become alternatives, attribute values become observations with evidence, require constraints become
  requirements, analysis defs become evaluation placeholders, and everything else is reported as a gap.
  Use when someone has a SysML v2 model and wants a trade study or decision state from it, asks to import,
  convert or "compile" SysML into pdo or decision objects, wants to know what in a SysML model cannot be
  mapped, or needs to refresh a decision after the SysML model changed.
---

# SysML v2 to a runnable decision

Turning a SysML model into a decision has two halves, and they must stay separate:

1. **The mechanical half.** `pdo sysml import` reads the model and writes alternatives, measures,
   requirements, observations (backed by one evidence item per model file), assumptions and evaluation
   placeholders. It applies fixed rules, records on every object which element and rule produced it,
   and reports everything it cannot translate as a gap. It never guesses.
2. **The framing half.** Criteria, value-function anchors, weights, the decision rule, readiness
   conditions, bias checks, roles and approval policy are judgements. SysML does not contain them. They
   come from the people who own the decision. You draft and ask; they decide.

Your job is to run the first half, review its gaps with the person, and lead the second half without
inventing anything. Then the kernel runs the decision and explains its result.

## What you need

- The `pdo` command. In the repository it is `uv run pdo ...`; installed, plain `pdo ...`. Every
  command below takes `--json`. If your harness has the pdo MCP server connected, its
  `pdo_import_sysml` tool runs the same import (`pdo.sysml.import_sysml`) and can replace step 3; the
  other steps are the same.
- The model in SysML v2 **textual notation** (`.sysml` files). XMI and API JSON exports are not read;
  ask for the textual files.
- The person who owns the decision. With one user, that person holds every role.

## Ground rules

- **Never invent framing.** No criterion, anchor, weight, threshold, unit, readiness condition, bias
  check, role, approval policy, `as_of` date, evidence expiry, or assumption criticality or validation
  without the person stating it. Offer a draft clearly marked as a proposal and get each number
  confirmed. If they decline to decide something, leave it out. The kernel then reports it as missing,
  and that is the correct result.
- **Interpretation enters as provisional.** When you, not a mapping rule, associate a value with an
  alternative (an analysis result applied to a configuration, a number read from a report), record it
  as an observation with `applicability: provisional` and an `applicability_rationale`, or ask the
  person to fix the model. The kernel treats provisional values as unknown until a person accepts them.
  A text-only requirement you formalize gates feasibility only after the person confirms its measure,
  comparator and threshold.
- **Keep provenance.** Never strip `source_element`, `source_model`, `source_model_version`,
  `mapping_rule` or the observation comments. They are the audit trail back to the model.
- **The importer owns `sysml-*.yaml`.** Every import rewrites those files. Record ids, units and wiring
  in the mapping file, so a re-import reproduces them. Put the framing in your own files in the same
  directory.

## Procedure

### 1. Inspect the model

Read the `.sysml` files and find:

- the element that holds the alternatives: a `variation part` whose `variant`s are the configurations,
  or several part usages of one `part def`;
- the attributes with values on those configurations (`attribute :>> mass = 1750 [kg];`);
- `requirement`s with `subject` and `require constraint { ... }`, and any text-only requirements;
- `analysis def` and `verification def` elements;
- everything else (ports, connections, states, actions, calculations). These are reported as gaps.

Ask the person for the decision question in one sentence, and confirm which element holds the
alternatives. Then dry-run the import:

```sh
pdo sysml import model/ --json       # what maps, by which rule, and the gaps
pdo sysml gaps model/                # only the gaps
```

### 2. Write the mapping file

The mapping file says what the model does not: decision ids, units for unitless values,
`alternatives.from` when the model has more than one candidate set, which alternative is the incumbent,
and short observation suffixes. Start from the template in
[references/mapping.md](references/mapping.md#the-mapping-file). Every value in it is a statement by a
person, not an inference. Keep ids stable once people refer to them.

### 3. Import

```sh
pdo sysml import model/ --map mapping.yaml --out decision/
```

This writes `decision/sysml-*.yaml` (the draft state) and `decision/sysml-import.json` (sources with
their digests, rules used, gaps). Check the summary: each alternative is listed with the element and
rule it came from. If that is not the set the person means, fix `alternatives.from` and import again.

### 4. Review every gap with the person

Group the gaps by kind and go through them with [references/gaps.md](references/gaps.md). Each gap
ends in one of four ways:

- **fix the model** (best: the source of truth improves, and the next import picks it up);
- **extend the mapping** (an id, a unit, an alternatives source, evaluation wiring);
- **translate by hand** in your own file, with provenance in `comments` and `applicability: provisional`
  where you interpreted something;
- **accept it as out of scope**, and say so in your report.

Do not go on to running the decision while `alternatives`, `unit`, `producer-conflict` or `constraint`
gaps on mandatory requirements are open. Those change which numbers the kernel computes.

### 5. Frame the decision with the person

Write `decision/framing.yaml` (and, if you like, `decision/governance.yaml`). Ask for each item and
write down what the person says:

| item | what to ask |
|---|---|
| `question`, `as_of` | the decision in one sentence; the date the assessment is made |
| `objectives` | what the decision serves (performance, affordability, dependability, ...) |
| `criteria` | which measures rank the feasible alternatives. For each one: the worst value still worth considering and the best value that still adds value (fixed anchors, not the observed min and max) |
| weights | swing weights that sum to 1: "moving range from its worst to its best anchor, or cost from its worst to its best: which matters more, and by how much?" |
| `decision_rule` | normally `additive_mavt` |
| `roles` | who owns the decision, who approves, who owns cost, test and engineering evidence |
| `readiness_policy` | approval roles, risk-acceptance roles, risk score threshold, approval validity |
| `readiness_conditions`, `bias_checks` | propose the standard set (recommendation_exists, unique_recommendation, coverage, evidence_current, evidence_traceable, assumptions_validated, risks_treated; omitted_options, distinct_measures, a framing review) and let the person choose |
| `risks`, `reviews` | known risks with likelihood and consequence; any review already done |

Requirements are mandatory by default. Requirement owners (`owner_role`) and evidence expiry
(`valid_until`) go in the mapping file (`requirements:` and `evidence:` sections), so they survive
re-import. The vehicle example's framing is `examples/vehicle/baseline/decision.yaml` (criteria and
rule) and `governance.yaml`. Use it as a model of the shape, never as a source of numbers.

### 6. Record interpretations as provisional

When an association comes from you rather than from a mapping rule, write it in your own file:

```yaml
# decision/interpreted.yaml
evidence_items:
  - {id: ev:fuel-analysis-2026, name: City fuel economy analysis, source_ref: "analysis/city-fe-2026.pdf"}
observations:
  - id: obs:B.fuel-economy-city
    alternative: alt:B
    measure: meas:fuelEconomy_city
    value: 27.5
    unit: mpg
    evidence: ev:fuel-analysis-2026
    applicability: provisional
    applicability_rationale: >-
      Result of the SysML analysis usage cityFuelEconomyAnalysis (subject vehicle_c1), applied to
      configuration B because B is built on vehicle_c1. Needs the test engineer's acceptance.
    owner_role: role:test-engineer
```

A person accepts it by setting `applicability: accepted`. In a decision program, do this in a change
set so the acceptance is recorded as its own episode.

### 7. Wire the evaluation placeholders

Each `implementation` gap is an `analysis def` that became an EvaluationSpecification with inputs and
outputs but no code. SysML says what an analysis reads and produces, not how it computes. Ask the person
for the computation, write it as a Python function that takes the spec's inputs as keyword arguments
(named by `arg`, which are the SysML parameter names) and returns a number, or a dict keyed by output
`arg`. Then record the wiring in the mapping file and import again:

```yaml
evaluations:
  RangeModel: {id: spec:range-model, implementation: evaluators.py:range_model, implementation_version: "1.0"}
```

`module.py:function` paths are relative to the decision directory (the one you pass to `pdo run`);
`package.module:function` imports a module. Analysis inputs with literal values (`in terrain_factor :
Real default 1.0;`) were imported as Assumptions with `validation_status: unvalidated`. Ask the person
whether each one is critical, whether it is validated, and what its plausible range is, and record
their answers under `assumptions:` in the mapping file.

### 8. Validate, run, explain

```sh
pdo validate decision/
pdo run decision/
pdo actions decision/
pdo explain decision/ decision
pdo explain decision/ U/alt:B --depth 2
pdo explain decision/ g/req:min-range/alt:A
```

`pdo validate` must pass before the result means anything. Common errors right after an import are an
unwired placeholder ("a python spec needs implementation") and missing framing ("criteria: required to
run"). Report to the person:

- the recommendation, and each alternative's feasibility, naming the requirement that fails and its
  margin `g`;
- the utilities, with bounds where a value is unknown;
- readiness, and which condition is pending or blocked and why;
- pending actions, by role;
- what is still open: unresolved gaps, provisional observations, unvalidated assumptions.

Use `pdo explain` to answer "why": every number traces back through the graph to an observation, an
evidence item, and the SysML element named in its comments.

### 9. When the model changes

Import again with the same mapping into the same directory: the `sysml-*.yaml` files are rewritten and
your framing files are left alone. Compare the new files with the previous import (in version control,
or with `diff` against a copy) and tell the person what changed. Every changed object is a changed
input, and the model file's digest changes too. Then validate and run. For a decision program (`program.yaml` with `baseline/` and `changes/`), write
the difference as a change set (`assign`, `add`, `remove`) instead of editing the baseline. Expect
approvals to stop matching: a changed evidence digest is a changed decision state.

## Worked example: the vehicle trade study

`examples/vehicle/sysml/vehicle.sysml` models three configurations, three requirements and the range
analysis. `mapping.yaml` maps them onto the ids of `examples/vehicle/baseline`.

```sh
pdo sysml import examples/vehicle/sysml/vehicle.sysml --map examples/vehicle/sysml/mapping.yaml --out decision/
```

The import produces 3 alternatives (`alt:A` is the incumbent, from the mapping), 5 measures, 3
requirements (`req:min-range` range ge 300 mi, `req:min-reliability` reliability ge 0.95,
`req:max-cost` unit cost le 1.40 MUSD), 12 observations backed by `ev:sysml-vehicle`, the assumption
`asm:terrain-factor` (1.0, unvalidated), and the placeholder `spec:range-model` (inputs tank, fuel
economy and terrain factor; output range). It reports 3 gaps: the port and port definition (out of
scope), and the range model's implementation.

To finish it: wire `RangeModel` to `evaluators.py:range_model` in the mapping (and copy
`examples/vehicle/evaluators.py` into `decision/`), record the person's judgement on the terrain
assumption, and write the framing with them. With the baseline's criteria (range 300 to 400 mi, weight
0.4; cost 1.40 to 0.80 MUSD, weight 0.4; reliability 0.94 to 0.99, weight 0.2), `pdo run decision/`
gives U = 0.467, 0.533 and 0.62. C fails min reliability by 0.01, so B is recommended.

## References

- [references/mapping.md](references/mapping.md): the SysML v2 to pdo mapping, construct by construct,
  with rule ids, examples and the mapping file format.
- [references/gaps.md](references/gaps.md): every gap kind, what it means, and what to do about it.
