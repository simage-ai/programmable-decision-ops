# What does not map, and what to do about it

Every element the importer does not translate becomes a gap: `element` (its qualified name, or
`(keyword, line n)` inside its owner when it has no name), `kind`, `reason`, and the file and line.
A gap is reported once, at the highest level where content was dropped, so the children of a dropped
element are not listed separately.

There are two families of gaps:

- **Constructs outside the subset.** The kind is the element's SysML keyword: `port`, `port def`,
  `interface def`, `connection`, `flow`, `item def`, `action`, `state def`, `transition`, `calc def`,
  `constraint`, `assert constraint`, `satisfy`, `allocate`, `metadata`, `use case`, `analysis`,
  `part`, `part def`, `attribute`, and so on.
- **Partial translations.** The kind is one of the problem kinds in the table below.

`pdo sysml gaps FILES... [--map mapping.yaml] [--json]` lists them. Each gap ends in one of four ways:
fix the model, extend the mapping, translate by hand (with provenance and `applicability: provisional`
for anything interpreted), or accept it as out of scope and say so. Never drop one silently.

## Problem kinds

| kind | means | what to do |
|---|---|---|
| `alternatives` | No unique set of alternatives: several candidate variations or definitions, or none. No alternatives were imported. | Ask the person which element holds the configurations, and set `alternatives: {from: <element>}` (or list them under `alternatives.ids`). Never pick one yourself. |
| `attribute-value` | An alternative's attribute has a value that is an expression, a reference (`= T1`), a boolean or a string. No observation. | Prefer fixing the model to a literal. If the value is a computation, model it as an `analysis def` and wire an evaluator. If the person tells you the number, add it as an observation by hand with its real source as evidence. |
| `unit` | A unit is missing (recorded as `"1"`), several units are used for one measure (no measure created), or a value or threshold is in a different unit from its measure (left out). | Ask for the unit and set `measures.<key>.unit` (or `assumptions.<key>.unit`). If the model mixes units, fix the model: pdo matches units exactly and never converts. Use `units:` only to respell, never to convert. |
| `requirement` | A requirement with nothing to check: text only, a variation, or one whose measure was not created. | For text-only requirements, ask the person for measure, comparator and threshold, and preferably add `require constraint { subject.attr >= value [unit] }` to the model. A requirement you formalize by hand needs the person's confirmation before it gates feasibility. |
| `requirement-subject` | The requirement's subject does not identify the alternatives: its type is missing from the imported files, it is about a part that is not an alternative, or it is untyped (then it was applied to every alternative). | Import the file that defines the subject's type, type the subject, or confirm with the person which alternatives it applies to and set `applies_to` by hand. |
| `constraint` | A require constraint outside the subset: `or`, `not`, `==`, arithmetic, a call, two attributes compared with each other, a name with no value, or a constraint by reference. | Rewrite it in the model as comparisons of one attribute with one literal, joined by `and`. A disjunction or a derived quantity needs a person: either model the quantity with an analysis def, or record the requirement by hand with the person's reading. |
| `assume-constraint` | An assumption pdo cannot record as an Assumption (anything but `subject.attr == literal`), or one on a requirement that was not imported. | Raise it with the person. It may be a scenario (`scenarios:`), a risk (`risks:`), or a condition for a review. The kernel checks requirements unconditionally, so an unrecorded assumption is lost. |
| `evaluation` | An analysis or verification def with no output bound to a subject attribute, or whose subject is not an alternative. Nothing imported. | Bind the result in the model (`return x = subject.attr;`). For a verification case, the test's measured values are evidence: record them as Evidence and Observations of the verified requirement's measure. |
| `evaluation-input` | An input bound to neither a subject attribute nor a literal (or a non-attribute parameter, or one whose measure or assumption was dropped). | Bind it in the model, or ask the person for the value (an Assumption) and its validation. Until it is bound the kernel refuses to run the evaluator (every function parameter must be a declared input or parameter, so a default can never stand in for a missing value) and the measure is unknown. |
| `evaluation-output` | An output not bound to a subject attribute, so it produces no measure. | Bind it (`return x = subject.attr;`). |
| `implementation` | An evaluation placeholder: the spec has inputs and outputs but no code. `pdo validate` fails until it is wired. | Get the computation from the person, write it as a Python function over the spec's input `arg`s, and set `implementation` and `implementation_version` under `evaluations:` in the mapping. Then import again. |
| `producer-conflict` | A measure is both observed on an alternative and computed by an evaluation. `pdo validate` rejects two producers. | The person decides which stands. Remove the attribute value from the model, or restrict the evaluation's subject. |
| `mapping` | A mapping key matches no element, or names more than one, or two keys map to one id. | Fix the key: use the qualified name (`Package::element`) to disambiguate. |
| `parse` | Text the parser could not read: an unmatched brace, an unterminated comment, an unexpected character, or nesting deeper than 100 levels. | Check the file in a SysML v2 tool. The rest of the file is still imported, but anything inside the unreadable part may be missing. |

## Constructs outside the subset

| keywords | why | what to do |
|---|---|---|
| `port`, `interface`, `connection`, `flow`, `item`, `bind`, `succession`, `allocation`, `message` | Structure has no counterpart in a decision state. | Usually out of scope. If an interface property matters to the choice (a connector standard, a power budget), state it as an attribute with a value and a requirement on it. |
| `action`, `state`, `transition`, `perform`, `exhibit`, `use case` | Behavior is not part of a decision state. | If behavior determines a measure (mission time, energy use), model that measure with an analysis def whose return is bound to a subject attribute, and wire an evaluator. |
| `occurrence` (`individual def`, `snapshot`, `timeslice`) | Specific instances, not definitions or configurations. | If an individual is a configuration under consideration, model it as a part usage with its values. |
| `calc def`, `calc` | Calculations are not executed. | Implement the calculation as a Python evaluator and expose it through an analysis def with bound `in` and `return` parameters. |
| `constraint`, `constraint def`, `assert constraint` | Constraints outside a requirement are not checked. | If it is mandatory, state it as a requirement (`require constraint`). If it is a modeling invariant (a mass roll-up), it is out of scope. |
| `satisfy`, `verify`, `allocate`, `dependency`, `frame`, `concern`, `view` | Relationships between model elements. pdo requirements reach alternatives through `applies_to`. | A `satisfy r by p` where p is an alternative may tell you `applies_to`; confirm with the person. |
| `metadata`, `@Annotation` | Metadata is not imported. | Owners, status and rationale (`@StatusInfo`, `@Rationale`, `@Risk`) are framing inputs. Ask the person, and record owners as `owner_role`, risks as `risks:`, and rationale as review `rationale`. |
| `analysis`, `verification` (usages) | An analysis or verification run is a source of evidence, not a definition of how to compute. | If it reports a result for a configuration, record the result as an observation with its report as evidence and `applicability: provisional`, with a rationale naming the usage. A person accepts it. |
| `part`, `part def`, `attribute`, `attribute def`, `requirement def`, `enum` | Not reached by any rule: not an alternative, not the definition of one, not a requirement subject, or a requirement definition with no usage. | Often context (subsystems, value types). If it should be an alternative, set `alternatives.from`. If it is an unused requirement definition, add a usage. |
| a variation nested inside an alternative | A further choice inside a configuration. | Decide it separately (its own decision), or bind a variant in the model so the configuration is fully specified. |

## What a person must add, gap or not

The importer reports gaps in what the model says. It cannot report the framing, which the model never
says: criteria, anchors, weights, the decision rule, roles, the readiness policy and conditions, bias
checks, risks, `as_of`, evidence validity dates, and the criticality and validation of assumptions.
`pdo sysml import` lists the top-level slots still missing ("To run it, ... add: as_of, criteria,
decision_rule, readiness_policy"). Those come from framing the decision with its owner (SKILL.md, step 5).
