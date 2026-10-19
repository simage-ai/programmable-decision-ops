# Onboarding

`pdo init` scaffolds a decision program, `pdo onboard` says where it stands, and `pdo study` studies it.
[skills/decision-onboarding](../skills/decision-onboarding/SKILL.md) is the procedure an agent follows with
the person; this page describes what the commands check and write.

## pdo init

```sh
pdo init DIR [--question TEXT] [--owner ROLE] [--name TEXT] [--doc PATH ...] [--model PATH ...]
```

Creates:

```
DIR/
  program.yaml        id, name, question, decision_owner_role, baseline_episode (ep:e00), baseline_dir, changes_dir
  README.md           the layout and the three funnels
  inputs/docs/        the reports (--doc: .docx, .pdf, .md, .txt, or directories of them)
  inputs/models/      the SysML v2 models (--model: .sysml, a mapping .yaml, or directories)
  baseline/           roles.yaml (with --owner); later sysml-*.yaml, documents.yaml, framing.yaml
  changes/            one change set per later episode
```

It refuses a directory that already holds `program.yaml` or `baseline/`, and writes nothing a person did
not state: no criteria, weights or other framing.

## pdo onboard

```sh
pdo onboard DIR [--json]
```

Reads the files and the kernel's results, and edits nothing. Each step is `done`, `todo`, `waiting` (on an
earlier step) or `skip` (it does not apply). The first `todo` is the next step; `--json` gives every step
and, for the next one, `who` acts, what to `do`, the `command`, the MCP `tool` and the skill section.

| Funnel | Step | Done when |
|---|---|---|
| Construct | Question and owner | `program.yaml` has `question` and `decision_owner_role` |
| | Roles | the baseline lists roles, including the owner's |
| | Inputs | there are reports or models in `inputs/`, or alternatives written by hand |
| | SysML import | every model in `inputs/models/` is imported at its current digest (`baseline/sysml-import.json`), with no open `alternatives`, `unit`, `producer-conflict` or `constraint` gap |
| | Values from documents | every report in `inputs/docs/` is cited by evidence, each such evidence has values, every value from an indexed report has a locator, and every locator verifies |
| | Framing | `as_of`, `criteria`, `decision_rule` and `readiness_policy` are present (it notes when there are no readiness conditions) |
| | Evaluators | every python evaluation specification has an implementation whose file exists |
| | Validation | `pdo validate` passes, for every episode |
| Run | Recommendation | the newest episode evaluates (recommendation, utilities, readiness, actionable) |
| | Open inputs | no pending action that changes what is known (accepting a value, validating an assumption, re-anchoring a locator, providing a value); specific actions are named before the generic ones they explain |
| Study | Study | `reports/study.json` is for the newest episode and its current state digest |
| | Authorization | the decision is actionable; until then, the review, risk treatment or approval still to record |

While no change set exists the baseline is under construction, and the commands it names write into it
(`pdo docs extract --baseline`). After the first episode, every change is a change set. When nothing is
left, the next step is to maintain the program: change sets and `pdo program refresh`.

## pdo study

```sh
pdo study DIR [-o OUT] [--samples N] [--seed S] [--no-figures]
```

Studies the newest episode (or a plain state) and writes `reports/` (or `OUT`):

| File | Content |
|---|---|
| `package/package.md`, `package/record.json` | The Decision Package: recommendation and why, margins, flip points, scenario robustness, readiness, pending actions, one run per computed node; the record validates against the record schema and replays with `pdo replay` |
| `sensitivity.json` | A tornado of the decision margin over every input's range; Sobol indices and value of information (EVPI, EVPPI per input, what to collect next) when inputs declare distributions |
| `uncertainty.json` | Under `expected_mavt` or with uncertain inputs: E[U], Pr(feasible), the chance-constrained decision, scenario robustness |
| `figures/` | With the figures extra: the tornado and, with distributions, the Sobol and value-of-information charts |
| `study.md`, `study.json` | The headline (the recommendation, readiness, robustness) and findings (the nearest flip point, the largest swing, what to collect next, the expected-utility decision), with the episode, state digest and kernel version they were computed for |

The study is seeded, so it reproduces. `pdo onboard` asks for a new study after every episode, so the
people who review and approve always act on a current one.

## Taking values from the model or from a report

When a SysML model and a report both state a value, one of them is the source of record. A mapping entry
`observation: false` on a measure says its values come from other evidence: the import creates the measure
but not its observations, and lists the values it left out (`values_elsewhere`). `pdo docs extract` then
takes them from the report, each with a locator, into `baseline/documents.yaml`, the file it owns (as
`pdo sysml import` owns `sysml-*.yaml`). An extraction that would change an object defined in another file
is refused.
