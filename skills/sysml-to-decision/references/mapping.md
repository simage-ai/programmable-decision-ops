# SysML v2 to pdo: the mapping

`pdo sysml import` (`src/pdo/sysml.py`) reads a documented subset of SysML v2 textual notation and
applies the rules below. Each rule has an id, and every object records it: in `mapping_rule` for
alternatives, requirements and evidence, and in `comments` for observations and assumptions. Anything
outside these rules is a gap ([gaps.md](gaps.md)).

## At a glance

| SysML v2 | pdo | rule |
|---|---|---|
| `variant` parts of one `variation part` (or `variation part def`) | Alternative | `alternative.variant` |
| two or more package-level part usages of one `part def` | Alternative | `alternative.same-definition` |
| package-level part usages specializing the part usage named in `alternatives.from` | Alternative | `alternative.specialization` |
| the elements listed under `alternatives.ids` (with no `from`) | Alternative | `alternative.listed` |
| an attribute of the alternatives (dotted path into nested parts) | Measure | `measure.attribute` |
| a literal number `[unit]` on an alternative's attribute | Observation | `observation.attribute-value` |
| each model file that supplies observed values | Evidence | `evidence.model-file` |
| `require constraint { subject.attr <op> literal }` in a `requirement` | Requirement | `requirement.require-constraint` |
| `in x = literal` / `in x default literal` of an analysis or verification def | Assumption (unvalidated) | `assumption.analysis-parameter` |
| `assume constraint { subject.attr == literal }` of an imported requirement | Assumption (unvalidated) | `assumption.assume-constraint` |
| `analysis def` with `return r = subject.attr` | EvaluationSpecification placeholder | `evaluation.analysis-def` |
| `verification def` with `return r = subject.attr` | EvaluationSpecification placeholder | `evaluation.verification-def` |
| `doc /* ... */` | `description` of the element it documents | (any) |

The importer never produces criteria, weights, anchors, a decision rule, roles, a readiness policy,
readiness conditions, bias checks, reviews, risks, approvals or `as_of`. Those are the framing.

## Reading the model

- **Declarations.** `[visibility] [prefixes] keyword [def] [<short name>] [name] [multiplicity]
  [: types] [:> specializes] [:>> redefines] [= value | default value] (; | { body })`. Prefixes
  include `abstract`, `variation`, `variant`, `in`, `out`, `return`, `require`, `assume`, `assert`.
  `subsets`, `specializes`, `redefines`, `defined by` and `::>` are read like their symbols.
- **Names** resolve from the enclosing namespaces outwards, then as a unique qualified-name suffix
  across all imported files. Imports and aliases are not interpreted. An unresolvable reference (a
  standard library type such as `ISQ::mass`) is treated as external, never as an error.
- **Inheritance.** An element inherits the attributes of its definitions (`:`), of what it specializes
  (`:>`), of its variation (for a variant), and of the usage a `variant q;` names, recursively. A more
  specific value overrides a general one: `attribute :>> mass = 1800 [kg];`, `attribute redefines mass
  = ...;`, `attribute mass redefines mass = ...;`, `:>> mass = ...;`. A `default` value counts as the
  value until something more specific overrides it. Cycles (`part p :> p;`) are cut, not followed.
- **Nested parts** contribute dotted keys: `part engine { attribute mass = 180 [kg]; }` inside an
  alternative gives the key `engine.mass`.
- **Values** are literal numbers with an optional unit: `40`, `-1.5e3`, `8.0 [mi/gal]`, `33 ['in']`. The
  unit is the bracket text with spaces removed (`[N * m]` gives `N*m`). Expressions, references
  (`= T1`), booleans and strings are not evaluated. They become `attribute-value` gaps.

## Alternatives

```sysml
part def Vehicle { attribute tankCapacity :> ISQ::volume; attribute unitCost : Real; }
variation part vehicleConfiguration : Vehicle {
    variant part configA { doc /* Current production derivative */ attribute :>> tankCapacity = 40 [gal]; }
    variant part configB { attribute :>> tankCapacity = 45 [gal]; }
}
```

gives `alt:configA` and `alt:configB` (rule `alternative.variant`), each with
`source_element: <package>::vehicleConfiguration::configA`, `source_model` (the file),
`source_model_version: sha256:<file digest>` and `description` from the doc.

Without a mapping, the importer picks alternatives only when the model has exactly one candidate set:
one part variation with variants, or one part definition with two or more package-level usages (a
definition that specializes another candidate counts in its place). With two or more candidates, or
none, it imports no alternatives and reports an `alternatives` gap listing the candidates. Name one
with `alternatives: {from: <element>}`. `from` may name a variation, a part definition (its usages), or
a part usage (the usages specializing it). `is_incumbent` comes only from the mapping.

## Measures and observations

Each attribute key that has a literal value on some alternative, or that a requirement or an evaluation
uses, becomes a Measure: `meas:<key>` unless the mapping renames it. The name is the attribute's doc,
or else the key. The unit is decided in this order:

1. the mapping's `unit` for that measure;
2. the units written on the model's literals for that key (values and requirement thresholds), when
   they agree; if they disagree, there is a `unit` gap and no measure;
3. `"1"` when the attribute is typed by a scalar number type (`Real`, `Integer`, `Natural`, `Positive`,
   `Rational`, `Number`, bare or from `ScalarValues`);
4. otherwise `"1"`, with a `unit` gap asking a person to confirm.

Each literal value becomes an Observation `obs:<alternative>.<suffix>`. The alternative part is its id
without the prefix, and the suffix is the mapping's `observation`, or else the key. The observation has
the measure's unit and evidence for the file the value is written in, and `comments` naming the source
element and line. A value written without a unit takes the measure's unit when that unit came from the
mapping or a scalar type, or is `"1"`. Otherwise it is a `unit` gap. A value in a different unit is a
`unit` gap. Units are matched exactly and never converted. Observations from the model are accepted:
they are the model's own statements. Their applicability is the default.

```yaml
- id: obs:A.tank
  alternative: alt:A
  measure: meas:tank-capacity
  value: 40
  unit: gal
  evidence: ev:sysml-vehicle
  comments: ["observation.attribute-value: VehicleTradeStudy::vehicleConfiguration::configA::tankCapacity = 40 [gal] (examples/vehicle/sysml/vehicle.sysml:35)"]
```

## Evidence

One Evidence per model file that backs at least one observation: `ev:sysml-<file stem>`, `evidence_kind:
system model`, `source_ref: <path>@sha256:<digest>`, `content_sha256: <digest>`, plus the SourceRef
slots. The digest is SHA-256 of the file with line endings normalized, so editing the model is a
changed input and invalidates approvals. `valid_until`, `issued_on` and an owner come only from the
mapping's `evidence:` section. Paths are recorded relative to the current directory.

## Requirements

```sysml
requirement def ReliabilityRequirement {
    attribute reliabilityRequired : Real;
    attribute reliabilityActual : Real;
    require constraint { reliabilityActual >= reliabilityRequired }
}
requirement vehicleRequirements {
    subject vehicle : Vehicle;
    requirement minRange { require constraint { vehicle.range >= 300 [mi] } }
    requirement minReliability : ReliabilityRequirement {
        attribute :>> reliabilityRequired = 0.95;
        attribute :>> reliabilityActual = vehicle.reliability;
    }
}
```

gives `req:minRange` (meas:range ge 300 mi) and `req:minReliability` (meas:reliability ge 0.95 "1").
How a requirement is read:

- Every requirement usage in a package, part or requirement group is read. Definitions are read only
  through their usages. A requirement with no require constraint whose members are requirements (or
  `require <ref>;`) is a group, not a requirement itself; its subject and attributes are visible to the
  requirements inside it.
- Its `require constraint`s are its own plus those inherited from its definition and from the usage it
  specializes. The expression must be comparisons (`<=`, `>=`, `<`, `>`) joined by `and` or `&`, each
  between one attribute and one literal threshold. `or`, `xor`, `not`, `implies`, `==`, `!=`, arithmetic
  and calls are `constraint` gaps.
- Names resolve in this order: the subject (own, inherited, or the enclosing requirement group's), then
  an attribute of the requirement or an enclosing group that has a value (followed through as many
  redefinitions as needed: `reliabilityActual` to `vehicle.reliability`), then a part usage in the
  model. A subject bound to a part (`subject = vehicle_b;`, `subject :>> mass = vehicle.mass;`) is
  followed to that part.
- `applies_to`: a subject typed by a definition applies to the alternatives that are or specialize it.
  It is left empty when that is all of them, which means all. A constraint on a named part applies to
  that alternative. A subject whose type is not in the imported files is a `requirement-subject` gap
  when there are alternatives to check it against (import the file that defines the type). An untyped
  subject, or one whose type is missing while there are no alternatives yet, is applied to every
  alternative, with a `requirement-subject` gap so a person confirms it.
- The id is `req:<name>`, or the mapping's. With several comparisons the ids are `req:<name>.1`,
  `.2`, ... in source order. A threshold on the left (`300 [mi] <= vehicle.range`) reverses the
  comparator, and `mapping_rule` says so. `mapping_rule` also quotes the constraint and names the
  attributes it went through.
- Requirements are mandatory by default (the schema's default). `owner_role`, `mandatory` and
  `check_tolerance` come only from the mapping.
- `assume constraint { subject.attr == literal }` on an imported requirement becomes an Assumption
  `asm:<requirement>.<attr>` (`validation_status: unvalidated`). The kernel checks requirements
  unconditionally. The assumption records the condition so that a person can judge it; it does not
  qualify the check. Any other assume constraint is an `assume-constraint` gap.

## Evaluation placeholders

```sysml
analysis def RangeModel {
    subject vehicle : Vehicle;
    in tank_gal = vehicle.tankCapacity;
    in mpg = vehicle.fuelEconomy;
    in terrain_factor : Real default 1.0;
    return range_mi = vehicle.range;
}
```

gives `spec:RangeModel`: `spec_kind: python`, inputs `tank_gal` (meas:tankCapacity), `mpg`
(meas:fuelEconomy) and `terrain_factor` (ref to the Assumption `asm:RangeModel.terrain_factor`, 1.0,
unvalidated), output `range_mi` (meas:range), applying to the alternatives that are Vehicles. It has
**no implementation** (an `implementation` gap) until the mapping names one. The parameter names become
the `arg`s, which are the keyword arguments the evaluator receives.

- `in x = subject.attr;` binds an input to a measure. `in x = literal;` or `in x default literal;`
  binds it to a new Assumption. Anything else is an `evaluation-input` gap.
- `return x = subject.attr;` (or `out x = subject.attr;`) declares an output measure. A return not
  bound to a subject attribute is an `evaluation-output` gap. A def with no bound output is not imported
  (an `evaluation` gap), because a spec without outputs computes nothing. Verification defs are usually
  in this case: their verdict is evidence, not a measure.
- The body (objective, actions, calculations, constraints) is not translated.
- A measure that an evaluation computes and that an alternative also states a value for is a
  `producer-conflict` gap. The kernel accepts only one producer per alternative and measure.

## The mapping file

Optional. It records what the model does not say. Keys name elements by simple name, qualified name or
qualified-name suffix (`RangeModel::terrain_factor`). A value is an id, or a mapping with `id` and the
listed slots. Unknown keys are errors. A key that matches nothing, or names more than one element, is a
`mapping` gap.

```yaml
alternatives:
  from: vehicleConfiguration           # a variation, part definition or part usage
  ids:                                 # element: id, or {id, name, description, is_incumbent}
    configA: {id: alt:A, is_incumbent: true}
    configB: alt:B
measures:                              # attribute key: id, or {id, unit, observation, name, description}
  tankCapacity: {id: meas:tank-capacity, observation: tank}
  unitCost: {id: meas:unit-cost, unit: MUSD, observation: cost}
units:                                 # SysML unit text -> pdo unit (a spelling, never a conversion)
  "N*m": N.m
requirements:                          # element: id, or {id, name, description, owner_role, mandatory, check_tolerance}
  minRange: {id: req:min-range, owner_role: role:systems-engineer}
evaluations:                           # element: id, or {id, name, description, implementation,
  RangeModel:                          #   implementation_version, owner_role, cost_units, deterministic,
    id: spec:range-model               #   cacheable, environment, parameters}
    implementation: evaluators.py:range_model
    implementation_version: "1.0"
assumptions:                           # element: id, or {id, name, description, unit, critical,
  RangeModel::terrain_factor:          #   validation_status, plausible_low, plausible_high, uncertainty, owner_role}
    id: asm:terrain-factor
    critical: true                     # a person's judgement
    validation_status: validated       # a person's judgement
evidence:                              # file (path or name): id, or {id, name, description, evidence_kind,
  vehicle.sysml: {id: ev:vehicle-model, owner_role: role:systems-engineer}   # owner_role, issued_on, valid_until, document_version}
```

Assumption keys are the parameter's qualified name (`RangeModel::terrain_factor`), or
`<requirement>::<attribute key>` for an assume constraint. `examples/vehicle/sysml/mapping.yaml` is a
complete example that reproduces the vehicle baseline's ids.

## Python API

```python
from pdo.sysml import import_sysml, write_state

result = import_sysml(["model/"], mapping="mapping.yaml")  # or a dict; paths may be files or directories
result.state          # partial DecisionState (dict), schema-valid
result.gaps           # [MappingGap(element, kind, reason, source_model, line)]
result.rules_used     # rule ids applied
result.to_dict()      # what `pdo sysml import --json` prints, besides "ok" and "written"
write_state(result, "decision/")   # sysml-*.yaml + sysml-import.json
```
