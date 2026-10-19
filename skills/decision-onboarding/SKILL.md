---
name: decision-onboarding
description: >-
  Take someone from nothing to a studied, auditable decision with pdo: scaffold a decision program from
  their reports (Word, PDF, Markdown) and SysML v2 models, construct the decision graph (SysML import,
  values from documents with exact quotes, framing with the decision owner), run it, study it (flip points,
  sensitivity, value of information, uncertainty, Decision Package) and see it through to authorization.
  Use when someone wants to start a new decision or trade study, asks how to get started with pdo, has a
  folder of reports or a SysML model and a decision to make, asks where their decision stands or what to
  do next, or picks up a decision program someone else started.
---

# Onboarding: from a folder of inputs to a studied decision

A decision in pdo is a program: a baseline (the decision as first studied) and a change set for every
later episode. Onboarding builds the baseline and carries it through three funnels:

1. **Construct the decision graph.** The question and the roles; the SysML import; values from the
   documents, each with the passage that states it; the framing, which only the decision owner supplies;
   the evaluators. `pdo validate` is the gate.
2. **Run it.** The recommendation, readiness and actionable, and the pending actions that change what is
   known, each recorded as an episode.
3. **Study it.** `pdo study`: flip points, scenarios, sensitivity, value of information, uncertainty and
   the Decision Package. Then the people who own the review and the approval act.

`pdo onboard DIR` reads the directory and says where it stands, step by step, and names the **single
next step**: who acts, what to do, the command and the MCP tool. Your loop is always the same:

```sh
pdo onboard DIR          # where it stands, and the next step
# do that step, with the person where it needs a judgement
pdo onboard DIR          # again
```

Never leave the person at a dead end: end every turn with the next step `pdo onboard` names, in plain
words, and the command.

## What you need

- The `pdo` command (`uv run pdo ...` in the repository; with the docs extra for Word and PDF:
  `uv sync --extra docs`, and the figures extra for charts). Every command takes `--json`. Over MCP the
  same operations are `pdo_init`, `pdo_onboard`, `pdo_import_sysml`, `pdo_docs_index`, `pdo_docs_read`,
  `pdo_docs_extract`, `pdo_validate`, `pdo_run`, `pdo_explain`, `pdo_pending_actions`,
  `pdo_record_human_action`, `pdo_apply_change` and `pdo_study`.
- The person who owns the decision. With one user, that person holds every role.

## Ground rules

- **Judgements belong to people.** Criteria, anchors, weights, thresholds, the decision rule, readiness
  conditions, roles, as_of, assumption criticality and validation, accepting a value, a review's outcome
  and an approval all come from the person who holds the role. Draft, ask, and write down what they say.
  If they decline to decide something, leave it out: the kernel reports it as missing, which is correct.
- **Every number is the kernel's.** Quote `pdo run`, `pdo explain` and `pdo study`; never compute or
  adjust a utility, margin or status yourself.
- **The baseline changes only until the first episode.** While `pdo onboard` says the decision is under
  construction, write into `baseline/` (and `pdo docs extract --baseline`). Once an episode exists,
  every change, including an acceptance or an approval, is a new change set; never edit the baseline or
  an episode.
- **Each tool owns its files.** `pdo sysml import` rewrites `baseline/sysml-*.yaml`; `pdo docs extract`
  rewrites `baseline/documents.yaml`. Put what the person says in files of your own (`roles.yaml`,
  `framing.yaml`), and record ids, units and wiring in `inputs/models/mapping.yaml`.
- The other skills hold the detail: [sysml-to-decision](../sysml-to-decision/SKILL.md) for the model and
  its gaps, [document-provenance](../document-provenance/SKILL.md) for quotes, locators and revisions.

## 1. Intake

Ask for the decision in one sentence, who owns it (a role id such as `role:program-manager`), and where
the reports and models are. Then scaffold the program:

```sh
pdo init DIR --question "Which vehicle configuration should the program carry into development?" \
  --owner role:program-manager --doc reports/ --model model/vehicle.sysml
```

`pdo init` creates `inputs/docs/`, `inputs/models/`, `baseline/`, `changes/`, `program.yaml` and a README,
and copies the inputs in. It writes only what the person stated. Then ask **who holds which role**: the
approvers (often a milestone decision authority), and the owners of cost, test and engineering evidence
and of any review. Write them in `baseline/roles.yaml`:

```yaml
roles:
  - {id: role:program-manager, name: Program manager (decision owner), assignees: [Dana]}
  - {id: role:test-engineer, name: Test engineer, assignees: [Lee]}
```

## 2. Models

With models in `inputs/models/`, follow [sysml-to-decision](../sysml-to-decision/SKILL.md): dry-run the
import (`pdo sysml import DIR/inputs/models --json`), write `inputs/models/mapping.yaml` with the person
(decision ids, units, which element holds the alternatives, evaluation wiring), and import into the
baseline:

```sh
pdo sysml import DIR/inputs/models --map DIR/inputs/models/mapping.yaml --out DIR/baseline
```

When a report states a value the model also has (a measured reliability, a fuel capacity from the
configuration sheet), ask which is the source of record. If it is the report, set `observation: false`
on that measure in the mapping and import again: the measure stays, the model's values for it are left
out (`values_elsewhere`), and you take them from the report in step 3. One value, one producer.

`pdo onboard` holds this step open while a model changed since the import, or while a gap that changes
the numbers is open (`alternatives`, `unit`, `producer-conflict`, `constraint`). Review every other gap
with the person too ([gaps.md](../sysml-to-decision/references/gaps.md)).

## 3. Documents

Follow [document-provenance](../document-provenance/SKILL.md). For each report in `inputs/docs/`: index
it (`pdo docs index`), read the sections that hold values (`pdo docs show FILE 3.2`), ask who owns it and
when it was issued, and propose each value with the exact passage that states it. Register the document
in the same proposals file, so its digest lives with its values:

```yaml
evidence_items:
  - {id: ev:reliability-B, name: Reliability test report B, evidence_kind: test report,
     source_ref: "Reliability growth test, vehicle B, phase 1", document_path: inputs/docs/reliability-test-report.docx,
     issued_on: 2026-12-10, owner_role: role:test-engineer}
observations:
  - {id: obs:B.reliability, alternative: alt:B, measure: meas:reliability, value: 0.98, evidence: ev:reliability-B,
     section: "3.2", quote: "Its demonstrated mission reliability is 0.98"}
```

```sh
pdo docs extract DIR proposals.yaml --baseline                                   # preview
pdo docs extract DIR proposals.yaml --baseline --write --role role:test-engineer
```

Your values are provisional (unknown to the kernel) until the evidence's owner accepts them. When the owner
is recording the values themselves, add `accept: true` with their `--role`; otherwise leave them
provisional and the acceptance becomes a pending action in step 7.

## 4. Framing

Only the decision owner can frame the decision. Ask, item by item, and write `baseline/framing.yaml`:
`as_of`; `objectives`; `criteria` (which measures rank the feasible alternatives, each with a worst value
still worth considering and a best value that still adds value, as fixed anchors); swing weights that sum
to 1; `decision_rule` (normally `additive_mavt`; `expected_mavt` when inputs carry distributions);
`readiness_policy` (approval roles, risk-acceptance roles); `readiness_conditions` and `bias_checks`
(propose the standard set: recommendation_exists, unique_recommendation, coverage, evidence_current,
evidence_traceable, locators_current, assumptions_validated, risks_treated; omitted_options,
distinct_measures, a framing review) and let the owner choose; known `risks`; `scenarios` to test the
recommendation against. A review not yet done enters as `review_status: pending`.

`pdo_describe_schema` gives each class's slots. Use `examples/vehicle/baseline/decision.yaml` and
`governance.yaml` as a model of the shape, never as a source of numbers.

## 5. Evaluators

A computed measure (an `analysis def` in the model) needs its computation from the person: a Python
function in `DIR/evaluators.py` taking the spec's inputs as keyword arguments, wired in the mapping
(`evaluations: {RangeModel: {id: spec:range-model, implementation: evaluators.py:range_model,
implementation_version: "1.0"}}`), then import again. Record the person's judgement on each imported
assumption (critical, validated, plausible range) in the mapping too.

## 6. Validate

```sh
pdo validate DIR
```

It must pass before a result means anything: schema, references, units, weights, one producer per
value, the graph compiles, and every quote is in its document. Fix each error at its source (the model,
the mapping, your files), never by editing what a tool wrote.

## 7. Run

```sh
pdo run DIR
pdo explain DIR decision
pdo actions DIR
```

Tell the person the recommendation, each alternative's feasibility (the requirement that fails and its
margin), the utilities (with bounds where a value is unknown), readiness and why. `pdo onboard` then
names the pending actions that change what is known, one at a time, most specific first. Each is a
person's judgement, recorded as an episode only once they have made it:

| pending action | ask | record |
|---|---|---|
| `review_applicability obs:...` | the evidence's owner: accept the value? | `pdo program record DIR --kind applicability --target obs:... --role ROLE --status accepted` |
| `validate_assumption asm:...` | the owner: is it validated? | `... --kind assumption_validation --status validated` |
| `review_locator obs:...` | the evidence's owner, with the new section in front of them | `pdo docs extract DIR proposals.yaml --write --role ROLE` (the observation's id, the confirmed value, the new quote, `accept: true`) |
| `provide_value`, `refresh_evidence`, `state_source`, ... | the owner, for the value or source | a change set in `changes/`, previewed with `pdo impact DIR CHANGE.yaml` (or `pdo_apply_change`) |

## 8. Study

```sh
pdo study DIR
```

It studies the newest episode and writes `reports/`: the Decision Package (`package/package.md` and a
replayable `record.json`), sensitivity (a tornado over every input's range; Sobol indices and value of
information when inputs carry distributions), uncertainty under `expected_mavt`, figures, and
`study.md` with the headline findings: the nearest flip point, the input with the largest swing, the
evidence most worth collecting next. Walk the person through them, starting from `study.md`. A study is
tied to the state it was computed for: after any new episode, `pdo onboard` asks for it again, so the
person always acts on a current study.

## 9. Authorize

What remains is governance: a review to complete, a risk to treat, the approval. `pdo onboard` names the
role. Record each with `pdo program record` (`--kind review`, `--kind risk_treatment`, `--kind approval`)
only when that person has decided. The decision is actionable when it is ready and the approval matches
the current state.

## 10. Maintain

When an input changes later (a new quote, a revised report, a changed requirement), write a change set
(`pdo impact` previews it; `pdo docs revise` absorbs a revised report) and run `pdo program refresh DIR`:
it re-evaluates only what changed and verifies the result against a full recomputation. Any material
change invalidates the approval, and `pdo onboard` walks the person back through run, study and
authorization.

## Worked example

`tests/test_onboard.py` builds the vehicle trade study from raw inputs: `pdo init` with the configuration
sheets (PDF), the reliability report (Word) and `vehicle.sysml`; the roles; the mapping (tank capacity,
fuel economy and reliability come from the reports; cost from the model; the range model wired); nine
located values; the framing. The test engineer's acceptance of B's reliability is episode `ep:e01`, and
the decision reproduces the reference trade study: B recommended, U = 0.467, 0.533 and 0.62, C infeasible
on reliability. Then `pdo study`, the bias reviewer's review, the authority's approval, and the decision
is actionable.
