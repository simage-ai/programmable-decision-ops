from __future__ import annotations

import re
import sys
from datetime import (
    date,
    datetime,
    time
)
from decimal import Decimal
from enum import Enum
from typing import (
    Any,
    ClassVar,
    Literal,
    Optional,
    Union
)

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    RootModel,
    SerializationInfo,
    SerializerFunctionWrapHandler,
    field_validator,
    model_serializer
)


metamodel_version = "1.12.0"
version = "0.1.0"


class ConfiguredBaseModel(BaseModel):
    model_config = ConfigDict(
        serialize_by_alias = True,
        validate_by_name = True,
        validate_assignment = True,
        validate_default = True,
        extra = "forbid",
        arbitrary_types_allowed = True,
        use_enum_values = True,
        strict = False,
    )





class LinkMLMeta(RootModel):
    root: dict[str, Any] = {}
    model_config = ConfigDict(frozen=True)

    def __getattr__(self, key:str):
        return getattr(self.root, key)

    def __getitem__(self, key:str):
        return self.root[key]

    def __setitem__(self, key:str, value):
        self.root[key] = value

    def __contains__(self, key:str) -> bool:
        return key in self.root


linkml_meta = LinkMLMeta({'default_prefix': 'pdo',
     'default_range': 'string',
     'description': 'Decisions as programs. A decision and its justification are '
                    'an executable dependency graph over typed objects: '
                    'alternatives, requirements, criteria, evidence, assumptions, '
                    'evaluations, readiness conditions, reviews and approvals. '
                    'When an input changes, the kernel finds what may be affected, '
                    're-evaluates it, and records whether the recommendation or '
                    'its readiness for authorization changed. The schema defines '
                    'the objects and their relationships; evaluators supply the '
                    'computation.',
     'id': 'https://w3id.org/pdo/schema',
     'imports': ['linkml:types',
                 'core',
                 'problem',
                 'evidence',
                 'evaluation',
                 'governance',
                 'state',
                 'program',
                 'record'],
     'license': 'MIT',
     'name': 'pdo',
     'prefixes': {'linkml': {'prefix_prefix': 'linkml',
                             'prefix_reference': 'https://w3id.org/linkml/'},
                  'pdo': {'prefix_prefix': 'pdo',
                          'prefix_reference': 'https://w3id.org/pdo/schema/'}},
     'source_file': 'schema/pdo.yaml',
     'title': 'Programmable decision schema'} )

class AssessmentStatus(str, Enum):
    """
    Outcome of a test. Unknown means the inputs needed to decide were missing or not accepted.
    """
    pass_ = "pass"
    fail = "fail"
    unknown = "unknown"


class Comparator(str, Enum):
    ge = "ge"
    """
    value >= threshold
    """
    gt = "gt"
    """
    value > threshold
    """
    le = "le"
    """
    value <= threshold
    """
    lt = "lt"
    """
    value < threshold
    """


class Applicability(str, Enum):
    """
    Whether an observation's evidence applies to the alternative it is attached to.
    """
    accepted = "accepted"
    provisional = "provisional"
    """
    Proposed (e.g. by an agent) and not yet reviewed; treated as unknown.
    """
    rejected = "rejected"


class ValidationStatus(str, Enum):
    validated = "validated"
    unvalidated = "unvalidated"
    invalid = "invalid"


class ReviewStatus(str, Enum):
    pending = "pending"
    pass_ = "pass"
    fail = "fail"


class TreatmentStatus(str, Enum):
    untreated = "untreated"
    mitigated = "mitigated"
    accepted = "accepted"
    unacceptable = "unacceptable"


class ReadinessState(str, Enum):
    ready = "ready"
    """
    Every required condition passes.
    """
    pending = "pending"
    """
    No required condition fails, but at least one is unknown.
    """
    blocked = "blocked"
    """
    At least one required condition fails.
    """


class TriggerKind(str, Enum):
    """
    What prompted a new decision episode.
    """
    evidence = "evidence"
    requirement = "requirement"
    assumption = "assumption"
    cost = "cost"
    model_version = "model_version"
    provenance = "provenance"
    review = "review"
    preference = "preference"
    alternative = "alternative"
    time = "time"
    approval = "approval"
    no_change = "no_change"


class DecisionRuleKind(str, Enum):
    additive_mavt = "additive_mavt"
    """
    Weighted additive multi-attribute value model.
    """
    expected_mavt = "expected_mavt"
    """
    Expected additive value over sampled uncertain inputs.
    """


class DistributionFamily(str, Enum):
    point = "point"
    uniform = "uniform"
    triangular = "triangular"
    normal = "normal"
    lognormal = "lognormal"


class SpecKind(str, Enum):
    python = "python"
    linear_combination = "linear_combination"


class ReadinessPredicate(str, Enum):
    recommendation_exists = "recommendation_exists"
    """
    At least one feasible alternative is recommended.
    """
    unique_recommendation = "unique_recommendation"
    """
    Exactly one alternative is recommended; a tie needs a person.
    """
    coverage = "coverage"
    """
    No unresolved alternative could still beat the recommendation.
    """
    evidence_current = "evidence_current"
    """
    Evidence behind the recommendation has not passed valid_until.
    """
    evidence_traceable = "evidence_traceable"
    """
    Evidence behind the recommendation names its source.
    """
    assumptions_validated = "assumptions_validated"
    """
    Critical assumptions are validated.
    """
    risks_treated = "risks_treated"
    """
    Risks at or above threshold are mitigated or accepted by an allowed role.
    """
    review_status = "review_status"
    """
    The referenced review passed and is not stale.
    """
    omitted_options = "omitted_options"
    """
    More than the incumbent is under consideration.
    """
    distinct_measures = "distinct_measures"
    """
    No two criteria score the same measure (double counting).
    """


class BiasKind(str, Enum):
    omitted_options = "omitted_options"
    double_counting = "double_counting"
    single_source = "single_source"
    anchoring = "anchoring"
    framing = "framing"


class BiasCategory(str, Enum):
    decision_framing = "decision_framing"
    model_output = "model_output"


class ExecutionStatus(str, Enum):
    executed = "executed"
    reused = "reused"
    """
    Outside the impact set; the previous result was kept.
    """
    cutoff_reused = "cutoff_reused"
    """
    In the impact set, but every input value was unchanged.
    """
    failed = "failed"
    """
    The evaluator raised or returned a non-finite value; the result is unknown.
    """


class ActionKind(str, Enum):
    provide_value = "provide_value"
    review_applicability = "review_applicability"
    validate_assumption = "validate_assumption"
    complete_review = "complete_review"
    treat_risk = "treat_risk"
    refresh_evidence = "refresh_evidence"
    state_source = "state_source"
    resolve_tie = "resolve_tie"
    revise_alternatives = "revise_alternatives"
    approve = "approve"
    fix_evaluator = "fix_evaluator"


class FlipMethod(str, Enum):
    affine_exact = "affine_exact"
    bisection = "bisection"
    closed_form = "closed_form"


class BoundaryKind(str, Enum):
    preference = "preference"
    """
    Another feasible alternative becomes preferred.
    """
    feasibility = "feasibility"
    """
    An alternative becomes feasible or infeasible.
    """
    empty_set = "empty_set"
    """
    No alternative remains feasible.
    """
    unresolved = "unresolved"
    """
    The recommendation becomes unresolved.
    """


class FlipDirection(str, Enum):
    """
    Whether the flip point lies above or below the baseline value.
    """
    above = "above"
    below = "below"


class ClaimBasis(str, Enum):
    computed = "computed"
    semantic = "semantic"
    human = "human"



class NamedThing(ConfiguredBaseModel):
    """
    Anything with a stable logical identifier that aligns it across decision episodes.
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'abstract': True, 'from_schema': 'https://w3id.org/pdo/schema/core'})

    id: str = Field(default=..., description="""Logical identifier, authored with a type prefix (alt:, req:, meas:, obs:, ev:, crit:, rc:, ...).""", json_schema_extra = { "linkml_meta": {'domain_of': ['NamedThing',
                       'ChangeSet',
                       'PendingAction',
                       'Proposition',
                       'Claim'],
         'slot_uri': 'dcterms:identifier'} })
    name: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['NamedThing'],
         'mixins': ['unhashable'],
         'slot_uri': 'rdfs:label'} })
    description: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['NamedThing'],
         'mixins': ['unhashable'],
         'slot_uri': 'dcterms:description'} })
    comments: Optional[list[str]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['NamedThing'], 'mixins': ['unhashable']} })


class Role(NamedThing):
    """
    A responsibility in the decision program (decision owner, cost analyst, test engineer, milestone decision authority). Objects that need a human carry an owner_role, so the kernel can list pending actions by role; with one user every role is assigned to that user.
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/pdo/schema/core'})

    assignees: Optional[list[str]] = Field(default=None, description="""People who hold the role.""", json_schema_extra = { "linkml_meta": {'domain_of': ['Role'], 'mixins': ['unhashable']} })
    id: str = Field(default=..., description="""Logical identifier, authored with a type prefix (alt:, req:, meas:, obs:, ev:, crit:, rc:, ...).""", json_schema_extra = { "linkml_meta": {'domain_of': ['NamedThing',
                       'ChangeSet',
                       'PendingAction',
                       'Proposition',
                       'Claim'],
         'slot_uri': 'dcterms:identifier'} })
    name: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['NamedThing'],
         'mixins': ['unhashable'],
         'slot_uri': 'rdfs:label'} })
    description: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['NamedThing'],
         'mixins': ['unhashable'],
         'slot_uri': 'dcterms:description'} })
    comments: Optional[list[str]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['NamedThing'], 'mixins': ['unhashable']} })


class SourceRef(ConfiguredBaseModel):
    """
    Where an object came from in an engineering model, kept when it is projected into the decision.
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/pdo/schema/core', 'mixin': True})

    source_element: Optional[str] = Field(default=None, description="""Identifier of the source element (e.g. a SysML qualified name).""", json_schema_extra = { "linkml_meta": {'domain_of': ['SourceRef'], 'mixins': ['unhashable']} })
    source_model: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['SourceRef'], 'mixins': ['unhashable']} })
    source_model_version: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['SourceRef'], 'mixins': ['unhashable']} })
    mapping_rule: Optional[str] = Field(default=None, description="""The mapping rule that produced this object, and any lossy interpretation.""", json_schema_extra = { "linkml_meta": {'domain_of': ['SourceRef'], 'mixins': ['unhashable']} })


class Parameter(ConfiguredBaseModel):
    """
    A named numeric parameter of an evaluator or readiness predicate.
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/pdo/schema/core',
         'slot_usage': {'arg': {'name': 'arg', 'required': True},
                        'number': {'name': 'number', 'required': True}}})

    arg: str = Field(default=..., description="""Argument name: the keyword an evaluator receives for this input, output or parameter. Unlike a display name it is part of the computation, so it is hashable.""", json_schema_extra = { "linkml_meta": {'domain_of': ['Parameter', 'InputBinding', 'OutputDeclaration'],
         'mixins': ['hashable']} })
    number: float = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['Parameter'], 'mixins': ['hashable']} })
    unit: Optional[str] = Field(default=None, description="""Unit string, matched exactly (no conversion). \"1\" means dimensionless.""", json_schema_extra = { "linkml_meta": {'domain_of': ['Parameter',
                       'Measure',
                       'Requirement',
                       'Observation',
                       'Assumption',
                       'InputBinding',
                       'OutputDeclaration',
                       'CheckOutcome'],
         'mixins': ['hashable']} })


class Objective(NamedThing):
    """
    A goal the decision serves (performance, affordability, dependability).
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/pdo/schema/problem'})

    id: str = Field(default=..., description="""Logical identifier, authored with a type prefix (alt:, req:, meas:, obs:, ev:, crit:, rc:, ...).""", json_schema_extra = { "linkml_meta": {'domain_of': ['NamedThing',
                       'ChangeSet',
                       'PendingAction',
                       'Proposition',
                       'Claim'],
         'slot_uri': 'dcterms:identifier'} })
    name: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['NamedThing'],
         'mixins': ['unhashable'],
         'slot_uri': 'rdfs:label'} })
    description: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['NamedThing'],
         'mixins': ['unhashable'],
         'slot_uri': 'dcterms:description'} })
    comments: Optional[list[str]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['NamedThing'], 'mixins': ['unhashable']} })


class Alternative(SourceRef, NamedThing):
    """
    A candidate the decision chooses among (a configuration, a source, a design).
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/pdo/schema/problem', 'mixins': ['SourceRef']})

    is_incumbent: Optional[bool] = Field(default=None, description="""The design or source already in place; used by the omitted-options bias check.""", json_schema_extra = { "linkml_meta": {'domain_of': ['Alternative'], 'mixins': ['hashable']} })
    source_element: Optional[str] = Field(default=None, description="""Identifier of the source element (e.g. a SysML qualified name).""", json_schema_extra = { "linkml_meta": {'domain_of': ['SourceRef'], 'mixins': ['unhashable']} })
    source_model: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['SourceRef'], 'mixins': ['unhashable']} })
    source_model_version: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['SourceRef'], 'mixins': ['unhashable']} })
    mapping_rule: Optional[str] = Field(default=None, description="""The mapping rule that produced this object, and any lossy interpretation.""", json_schema_extra = { "linkml_meta": {'domain_of': ['SourceRef'], 'mixins': ['unhashable']} })
    id: str = Field(default=..., description="""Logical identifier, authored with a type prefix (alt:, req:, meas:, obs:, ev:, crit:, rc:, ...).""", json_schema_extra = { "linkml_meta": {'domain_of': ['NamedThing',
                       'ChangeSet',
                       'PendingAction',
                       'Proposition',
                       'Claim'],
         'slot_uri': 'dcterms:identifier'} })
    name: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['NamedThing'],
         'mixins': ['unhashable'],
         'slot_uri': 'rdfs:label'} })
    description: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['NamedThing'],
         'mixins': ['unhashable'],
         'slot_uri': 'dcterms:description'} })
    comments: Optional[list[str]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['NamedThing'], 'mixins': ['unhashable']} })


class Measure(NamedThing):
    """
    A quantity alternatives are measured on; every value of it carries this unit.
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/pdo/schema/problem',
         'slot_usage': {'unit': {'name': 'unit', 'required': True}}})

    unit: str = Field(default=..., description="""Unit string, matched exactly (no conversion). \"1\" means dimensionless.""", json_schema_extra = { "linkml_meta": {'domain_of': ['Parameter',
                       'Measure',
                       'Requirement',
                       'Observation',
                       'Assumption',
                       'InputBinding',
                       'OutputDeclaration',
                       'CheckOutcome'],
         'mixins': ['hashable']} })
    id: str = Field(default=..., description="""Logical identifier, authored with a type prefix (alt:, req:, meas:, obs:, ev:, crit:, rc:, ...).""", json_schema_extra = { "linkml_meta": {'domain_of': ['NamedThing',
                       'ChangeSet',
                       'PendingAction',
                       'Proposition',
                       'Claim'],
         'slot_uri': 'dcterms:identifier'} })
    name: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['NamedThing'],
         'mixins': ['unhashable'],
         'slot_uri': 'rdfs:label'} })
    description: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['NamedThing'],
         'mixins': ['unhashable'],
         'slot_uri': 'dcterms:description'} })
    comments: Optional[list[str]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['NamedThing'], 'mixins': ['unhashable']} })


class Criterion(NamedThing):
    """
    A preference criterion: a value function v mapping a measure onto [0, 1] with fixed anchors, and a weight. Linear by default, v = (y - anchor_worst) / (anchor_best - anchor_worst), so a cost criterion has anchor_worst above anchor_best. Fixed anchors keep scores from moving when an alternative is added. Weights express trade-offs over these scales and must sum to 1.
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/pdo/schema/problem',
         'slot_usage': {'anchor_best': {'name': 'anchor_best', 'required': True},
                        'anchor_worst': {'name': 'anchor_worst', 'required': True},
                        'measure': {'name': 'measure', 'required': True},
                        'weight': {'name': 'weight', 'required': True}}})

    objective: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Criterion'], 'mixins': ['unhashable']} })
    measure: str = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['Criterion',
                       'Requirement',
                       'Observation',
                       'InputBinding',
                       'OutputDeclaration'],
         'mixins': ['hashable']} })
    anchor_worst: float = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['Criterion'], 'mixins': ['hashable']} })
    anchor_best: float = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['Criterion'], 'mixins': ['hashable']} })
    points: Optional[list[PiecewisePoint]] = Field(default=None, description="""Optional piecewise-linear value function; overrides the linear form between the anchors.""", json_schema_extra = { "linkml_meta": {'domain_of': ['Criterion'], 'mixins': ['hashable']} })
    clip: Optional[bool] = Field(default=None, description="""Clip values to [0, 1] outside the anchors (default true).""", json_schema_extra = { "linkml_meta": {'domain_of': ['Criterion'], 'mixins': ['hashable']} })
    weight: float = Field(default=..., ge=0, le=1, json_schema_extra = { "linkml_meta": {'domain_of': ['Criterion', 'CriterionValue'], 'mixins': ['hashable']} })
    id: str = Field(default=..., description="""Logical identifier, authored with a type prefix (alt:, req:, meas:, obs:, ev:, crit:, rc:, ...).""", json_schema_extra = { "linkml_meta": {'domain_of': ['NamedThing',
                       'ChangeSet',
                       'PendingAction',
                       'Proposition',
                       'Claim'],
         'slot_uri': 'dcterms:identifier'} })
    name: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['NamedThing'],
         'mixins': ['unhashable'],
         'slot_uri': 'rdfs:label'} })
    description: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['NamedThing'],
         'mixins': ['unhashable'],
         'slot_uri': 'dcterms:description'} })
    comments: Optional[list[str]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['NamedThing'], 'mixins': ['unhashable']} })


class PiecewisePoint(ConfiguredBaseModel):
    """
    One point (x, v) of a piecewise-linear value function; points are sorted by x.
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/pdo/schema/problem',
         'slot_usage': {'v': {'name': 'v', 'required': True},
                        'x': {'name': 'x', 'required': True}}})

    x: float = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['PiecewisePoint'], 'mixins': ['hashable']} })
    v: float = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['PiecewisePoint'], 'mixins': ['hashable']} })


class Requirement(SourceRef, NamedThing):
    """
    A constraint on a measure. A mandatory requirement must be known to pass for an alternative to be feasible; an unknown result leaves the alternative unresolved, never infeasible by default.
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/pdo/schema/problem',
         'mixins': ['SourceRef'],
         'slot_usage': {'comparator': {'name': 'comparator', 'required': True},
                        'measure': {'name': 'measure', 'required': True},
                        'threshold_value': {'name': 'threshold_value',
                                            'required': True},
                        'unit': {'name': 'unit', 'required': True}}})

    measure: str = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['Criterion',
                       'Requirement',
                       'Observation',
                       'InputBinding',
                       'OutputDeclaration'],
         'mixins': ['hashable']} })
    comparator: Comparator = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['Requirement', 'CheckOutcome'], 'mixins': ['hashable']} })
    threshold_value: float = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['Requirement', 'CheckOutcome'], 'mixins': ['hashable']} })
    unit: str = Field(default=..., description="""Unit string, matched exactly (no conversion). \"1\" means dimensionless.""", json_schema_extra = { "linkml_meta": {'domain_of': ['Parameter',
                       'Measure',
                       'Requirement',
                       'Observation',
                       'Assumption',
                       'InputBinding',
                       'OutputDeclaration',
                       'CheckOutcome'],
         'mixins': ['hashable']} })
    mandatory: Optional[bool] = Field(default=None, description="""Mandatory requirements gate feasibility (default true).""", json_schema_extra = { "linkml_meta": {'domain_of': ['Requirement'], 'mixins': ['hashable']} })
    applies_to: Optional[list[str]] = Field(default=None, description="""Alternatives this applies to; empty means all.""", json_schema_extra = { "linkml_meta": {'domain_of': ['Requirement', 'EvaluationSpecification'],
         'mixins': ['hashable']} })
    check_tolerance: Optional[float] = Field(default=None, description="""A check passes when g <= check_tolerance (default 1.0e-9), so float noise cannot fail a boundary case.""", json_schema_extra = { "linkml_meta": {'domain_of': ['Requirement'], 'mixins': ['hashable']} })
    owner_role: Optional[str] = Field(default=None, description="""The role that must act when this object needs a human (review, acceptance, data).""", json_schema_extra = { "linkml_meta": {'domain_of': ['Requirement',
                       'Evidence',
                       'Observation',
                       'Assumption',
                       'EvaluationSpecification',
                       'ReadinessCondition',
                       'HumanReview',
                       'Risk'],
         'mixins': ['unhashable']} })
    source_element: Optional[str] = Field(default=None, description="""Identifier of the source element (e.g. a SysML qualified name).""", json_schema_extra = { "linkml_meta": {'domain_of': ['SourceRef'], 'mixins': ['unhashable']} })
    source_model: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['SourceRef'], 'mixins': ['unhashable']} })
    source_model_version: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['SourceRef'], 'mixins': ['unhashable']} })
    mapping_rule: Optional[str] = Field(default=None, description="""The mapping rule that produced this object, and any lossy interpretation.""", json_schema_extra = { "linkml_meta": {'domain_of': ['SourceRef'], 'mixins': ['unhashable']} })
    id: str = Field(default=..., description="""Logical identifier, authored with a type prefix (alt:, req:, meas:, obs:, ev:, crit:, rc:, ...).""", json_schema_extra = { "linkml_meta": {'domain_of': ['NamedThing',
                       'ChangeSet',
                       'PendingAction',
                       'Proposition',
                       'Claim'],
         'slot_uri': 'dcterms:identifier'} })
    name: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['NamedThing'],
         'mixins': ['unhashable'],
         'slot_uri': 'rdfs:label'} })
    description: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['NamedThing'],
         'mixins': ['unhashable'],
         'slot_uri': 'dcterms:description'} })
    comments: Optional[list[str]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['NamedThing'], 'mixins': ['unhashable']} })


class DecisionRule(NamedThing):
    """
    How feasible alternatives are ranked. additive_mavt maximizes U(a) = sum_k w_k v_k(y_k(a)) and returns the set of alternatives within tie_tolerance of the maximum.
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/pdo/schema/problem',
         'slot_usage': {'rule_kind': {'name': 'rule_kind', 'required': True}}})

    rule_kind: DecisionRuleKind = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['DecisionRule'], 'mixins': ['hashable']} })
    tie_tolerance: Optional[float] = Field(default=None, description="""Alternatives within this utility of the maximum are tied (default 1.0e-9).""", json_schema_extra = { "linkml_meta": {'domain_of': ['DecisionRule'], 'mixins': ['hashable']} })
    samples: Optional[int] = Field(default=None, description="""Monte Carlo samples for expected_mavt.""", json_schema_extra = { "linkml_meta": {'domain_of': ['DecisionRule'], 'mixins': ['hashable']} })
    seed: Optional[int] = Field(default=None, description="""Random seed for expected_mavt; recorded so runs replay exactly.""", json_schema_extra = { "linkml_meta": {'domain_of': ['DecisionRule'], 'mixins': ['hashable']} })
    chance_alpha: Optional[float] = Field(default=None, description="""Joint chance constraint tolerance; feasible iff Pr(all mandatory checks pass) >= 1 - alpha.""", json_schema_extra = { "linkml_meta": {'domain_of': ['DecisionRule'], 'mixins': ['hashable']} })
    id: str = Field(default=..., description="""Logical identifier, authored with a type prefix (alt:, req:, meas:, obs:, ev:, crit:, rc:, ...).""", json_schema_extra = { "linkml_meta": {'domain_of': ['NamedThing',
                       'ChangeSet',
                       'PendingAction',
                       'Proposition',
                       'Claim'],
         'slot_uri': 'dcterms:identifier'} })
    name: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['NamedThing'],
         'mixins': ['unhashable'],
         'slot_uri': 'rdfs:label'} })
    description: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['NamedThing'],
         'mixins': ['unhashable'],
         'slot_uri': 'dcterms:description'} })
    comments: Optional[list[str]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['NamedThing'], 'mixins': ['unhashable']} })


class Scenario(NamedThing):
    """
    A named set of assignments to test the recommendation against (robustness over a scenario set).
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/pdo/schema/problem'})

    assignments: Optional[list[SlotAssignment]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Scenario'], 'mixins': ['hashable']} })
    id: str = Field(default=..., description="""Logical identifier, authored with a type prefix (alt:, req:, meas:, obs:, ev:, crit:, rc:, ...).""", json_schema_extra = { "linkml_meta": {'domain_of': ['NamedThing',
                       'ChangeSet',
                       'PendingAction',
                       'Proposition',
                       'Claim'],
         'slot_uri': 'dcterms:identifier'} })
    name: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['NamedThing'],
         'mixins': ['unhashable'],
         'slot_uri': 'rdfs:label'} })
    description: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['NamedThing'],
         'mixins': ['unhashable'],
         'slot_uri': 'dcterms:description'} })
    comments: Optional[list[str]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['NamedThing'], 'mixins': ['unhashable']} })


class SlotAssignment(ConfiguredBaseModel):
    """
    Set one slot of one object, identified by id, to a new value.
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/pdo/schema/problem',
         'slot_usage': {'slot': {'name': 'slot', 'required': True},
                        'target': {'name': 'target', 'required': True}}})

    target: str = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['SlotAssignment', 'PendingAction', 'FlipPoint', 'ParameterScan'],
         'mixins': ['hashable']} })
    slot: str = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['SlotAssignment', 'FlipPoint', 'ParameterScan'],
         'mixins': ['hashable']} })
    new_value: Optional[Any] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['SlotAssignment'], 'mixins': ['hashable']} })


class Evidence(SourceRef, NamedThing):
    """
    A source document or data product. An empty source_ref means the source is unstated.
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/pdo/schema/evidence', 'mixins': ['SourceRef']})

    evidence_kind: Optional[str] = Field(default=None, description="""Kind of source (cost estimate, test report, specification, simulation output).""", json_schema_extra = { "linkml_meta": {'domain_of': ['Evidence'], 'mixins': ['unhashable']} })
    source_ref: Optional[str] = Field(default=None, description="""Citation or locator of the source; empty means unstated (fails evidence traceability).""", json_schema_extra = { "linkml_meta": {'domain_of': ['Evidence'], 'mixins': ['hashable']} })
    document_version: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Evidence'], 'mixins': ['hashable']} })
    issued_on: Optional[date] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Evidence'], 'mixins': ['unhashable']} })
    valid_until: Optional[date] = Field(default=None, description="""After this date the evidence is stale and readiness becomes pending until it is refreshed.""", json_schema_extra = { "linkml_meta": {'domain_of': ['Evidence', 'HumanApproval'], 'mixins': ['hashable']} })
    content_sha256: Optional[str] = Field(default=None, description="""Digest of the source content, so a changed file is a changed input.""", json_schema_extra = { "linkml_meta": {'domain_of': ['Evidence'], 'mixins': ['hashable']} })
    owner_role: Optional[str] = Field(default=None, description="""The role that must act when this object needs a human (review, acceptance, data).""", json_schema_extra = { "linkml_meta": {'domain_of': ['Requirement',
                       'Evidence',
                       'Observation',
                       'Assumption',
                       'EvaluationSpecification',
                       'ReadinessCondition',
                       'HumanReview',
                       'Risk'],
         'mixins': ['unhashable']} })
    source_element: Optional[str] = Field(default=None, description="""Identifier of the source element (e.g. a SysML qualified name).""", json_schema_extra = { "linkml_meta": {'domain_of': ['SourceRef'], 'mixins': ['unhashable']} })
    source_model: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['SourceRef'], 'mixins': ['unhashable']} })
    source_model_version: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['SourceRef'], 'mixins': ['unhashable']} })
    mapping_rule: Optional[str] = Field(default=None, description="""The mapping rule that produced this object, and any lossy interpretation.""", json_schema_extra = { "linkml_meta": {'domain_of': ['SourceRef'], 'mixins': ['unhashable']} })
    id: str = Field(default=..., description="""Logical identifier, authored with a type prefix (alt:, req:, meas:, obs:, ev:, crit:, rc:, ...).""", json_schema_extra = { "linkml_meta": {'domain_of': ['NamedThing',
                       'ChangeSet',
                       'PendingAction',
                       'Proposition',
                       'Claim'],
         'slot_uri': 'dcterms:identifier'} })
    name: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['NamedThing'],
         'mixins': ['unhashable'],
         'slot_uri': 'rdfs:label'} })
    description: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['NamedThing'],
         'mixins': ['unhashable'],
         'slot_uri': 'dcterms:description'} })
    comments: Optional[list[str]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['NamedThing'], 'mixins': ['unhashable']} })


class Observation(NamedThing):
    """
    One value of a measure for an alternative, taken from evidence. A missing value, or an applicability other than accepted, makes the value unknown rather than assumed.
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/pdo/schema/evidence',
         'slot_usage': {'alternative': {'name': 'alternative', 'required': True},
                        'evidence': {'name': 'evidence', 'required': True},
                        'measure': {'name': 'measure', 'required': True},
                        'unit': {'name': 'unit', 'required': True}}})

    alternative: str = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['Observation', 'AlternativeOutcome'], 'mixins': ['hashable']} })
    measure: str = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['Criterion',
                       'Requirement',
                       'Observation',
                       'InputBinding',
                       'OutputDeclaration'],
         'mixins': ['hashable']} })
    value: Optional[float] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Observation', 'Assumption'], 'mixins': ['hashable']} })
    unit: str = Field(default=..., description="""Unit string, matched exactly (no conversion). \"1\" means dimensionless.""", json_schema_extra = { "linkml_meta": {'domain_of': ['Parameter',
                       'Measure',
                       'Requirement',
                       'Observation',
                       'Assumption',
                       'InputBinding',
                       'OutputDeclaration',
                       'CheckOutcome'],
         'mixins': ['hashable']} })
    evidence: str = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['Observation'], 'mixins': ['hashable']} })
    applicability: Optional[Applicability] = Field(default=None, description="""Default accepted.""", json_schema_extra = { "linkml_meta": {'domain_of': ['Observation'], 'mixins': ['hashable']} })
    applicability_rationale: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Observation'], 'mixins': ['unhashable']} })
    uncertainty: Optional[UncertaintySpec] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Observation', 'Assumption'], 'mixins': ['hashable']} })
    owner_role: Optional[str] = Field(default=None, description="""The role that must act when this object needs a human (review, acceptance, data).""", json_schema_extra = { "linkml_meta": {'domain_of': ['Requirement',
                       'Evidence',
                       'Observation',
                       'Assumption',
                       'EvaluationSpecification',
                       'ReadinessCondition',
                       'HumanReview',
                       'Risk'],
         'mixins': ['unhashable']} })
    id: str = Field(default=..., description="""Logical identifier, authored with a type prefix (alt:, req:, meas:, obs:, ev:, crit:, rc:, ...).""", json_schema_extra = { "linkml_meta": {'domain_of': ['NamedThing',
                       'ChangeSet',
                       'PendingAction',
                       'Proposition',
                       'Claim'],
         'slot_uri': 'dcterms:identifier'} })
    name: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['NamedThing'],
         'mixins': ['unhashable'],
         'slot_uri': 'rdfs:label'} })
    description: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['NamedThing'],
         'mixins': ['unhashable'],
         'slot_uri': 'dcterms:description'} })
    comments: Optional[list[str]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['NamedThing'], 'mixins': ['unhashable']} })


class Assumption(NamedThing):
    """
    A parameter taken as given. Critical assumptions must be validated before authorization.
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/pdo/schema/evidence',
         'slot_usage': {'unit': {'name': 'unit', 'required': True},
                        'value': {'name': 'value', 'required': True}}})

    value: float = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['Observation', 'Assumption'], 'mixins': ['hashable']} })
    unit: str = Field(default=..., description="""Unit string, matched exactly (no conversion). \"1\" means dimensionless.""", json_schema_extra = { "linkml_meta": {'domain_of': ['Parameter',
                       'Measure',
                       'Requirement',
                       'Observation',
                       'Assumption',
                       'InputBinding',
                       'OutputDeclaration',
                       'CheckOutcome'],
         'mixins': ['hashable']} })
    critical: Optional[bool] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Assumption'], 'mixins': ['hashable']} })
    validation_status: Optional[ValidationStatus] = Field(default=None, description="""Default validated.""", json_schema_extra = { "linkml_meta": {'domain_of': ['Assumption'], 'mixins': ['hashable']} })
    plausible_low: Optional[float] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Assumption'], 'mixins': ['hashable']} })
    plausible_high: Optional[float] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Assumption'], 'mixins': ['hashable']} })
    uncertainty: Optional[UncertaintySpec] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Observation', 'Assumption'], 'mixins': ['hashable']} })
    owner_role: Optional[str] = Field(default=None, description="""The role that must act when this object needs a human (review, acceptance, data).""", json_schema_extra = { "linkml_meta": {'domain_of': ['Requirement',
                       'Evidence',
                       'Observation',
                       'Assumption',
                       'EvaluationSpecification',
                       'ReadinessCondition',
                       'HumanReview',
                       'Risk'],
         'mixins': ['unhashable']} })
    id: str = Field(default=..., description="""Logical identifier, authored with a type prefix (alt:, req:, meas:, obs:, ev:, crit:, rc:, ...).""", json_schema_extra = { "linkml_meta": {'domain_of': ['NamedThing',
                       'ChangeSet',
                       'PendingAction',
                       'Proposition',
                       'Claim'],
         'slot_uri': 'dcterms:identifier'} })
    name: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['NamedThing'],
         'mixins': ['unhashable'],
         'slot_uri': 'rdfs:label'} })
    description: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['NamedThing'],
         'mixins': ['unhashable'],
         'slot_uri': 'dcterms:description'} })
    comments: Optional[list[str]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['NamedThing'], 'mixins': ['unhashable']} })


class UncertaintySpec(ConfiguredBaseModel):
    """
    A distribution for an uncertain value, used by expected_mavt and chance constraints.
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/pdo/schema/evidence',
         'slot_usage': {'family': {'name': 'family', 'required': True}}})

    family: DistributionFamily = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['UncertaintySpec'], 'mixins': ['hashable']} })
    low: Optional[float] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['UncertaintySpec'], 'mixins': ['hashable']} })
    mode: Optional[float] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['UncertaintySpec'], 'mixins': ['hashable']} })
    high: Optional[float] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['UncertaintySpec'], 'mixins': ['hashable']} })
    mean: Optional[float] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['UncertaintySpec'], 'mixins': ['hashable']} })
    sd: Optional[float] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['UncertaintySpec'], 'mixins': ['hashable']} })


class EvaluationSpecification(NamedThing):
    """
    A computation producing one or more measures for each alternative it applies to. A python spec's implementation is \"path/to/module.py:function\" (relative to the decision's directory) or \"package.module:function\"; the function receives the bound inputs and parameters as keyword arguments (by arg) and returns a number, or a dict keyed by output arg. A linear_combination spec returns intercept + sum(coefficient_<arg> * input), with intercept and coefficients given as parameters.
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/pdo/schema/evaluation',
         'slot_usage': {'outputs': {'name': 'outputs', 'required': True},
                        'spec_kind': {'name': 'spec_kind', 'required': True}}})

    spec_kind: SpecKind = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['EvaluationSpecification'], 'mixins': ['hashable']} })
    implementation: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['EvaluationSpecification', 'EvaluationRun'],
         'mixins': ['hashable']} })
    implementation_version: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['EvaluationSpecification'], 'mixins': ['hashable']} })
    inputs: Optional[list[InputBinding]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['EvaluationSpecification'], 'mixins': ['hashable']} })
    outputs: list[OutputDeclaration] = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['EvaluationSpecification'], 'mixins': ['hashable']} })
    parameters: Optional[list[Parameter]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['EvaluationSpecification', 'ReadinessCondition'],
         'mixins': ['hashable']} })
    applies_to: Optional[list[str]] = Field(default=None, description="""Alternatives this applies to; empty means all.""", json_schema_extra = { "linkml_meta": {'domain_of': ['Requirement', 'EvaluationSpecification'],
         'mixins': ['hashable']} })
    deterministic: Optional[bool] = Field(default=None, description="""Default true. A non-deterministic evaluator is never reused from a previous run.""", json_schema_extra = { "linkml_meta": {'domain_of': ['EvaluationSpecification'], 'mixins': ['hashable']} })
    cacheable: Optional[bool] = Field(default=None, description="""Default true.""", json_schema_extra = { "linkml_meta": {'domain_of': ['EvaluationSpecification'], 'mixins': ['hashable']} })
    cost_units: Optional[float] = Field(default=None, description="""Relative cost of one run (default 1), used to weight recomputation savings.""", json_schema_extra = { "linkml_meta": {'domain_of': ['EvaluationSpecification'], 'mixins': ['unhashable']} })
    environment: Optional[list[str]] = Field(default=None, description="""Declared runtime requirements (e.g. \"numpy>=2\"); part of the signature.""", json_schema_extra = { "linkml_meta": {'domain_of': ['EvaluationSpecification'], 'mixins': ['hashable']} })
    owner_role: Optional[str] = Field(default=None, description="""The role that must act when this object needs a human (review, acceptance, data).""", json_schema_extra = { "linkml_meta": {'domain_of': ['Requirement',
                       'Evidence',
                       'Observation',
                       'Assumption',
                       'EvaluationSpecification',
                       'ReadinessCondition',
                       'HumanReview',
                       'Risk'],
         'mixins': ['unhashable']} })
    id: str = Field(default=..., description="""Logical identifier, authored with a type prefix (alt:, req:, meas:, obs:, ev:, crit:, rc:, ...).""", json_schema_extra = { "linkml_meta": {'domain_of': ['NamedThing',
                       'ChangeSet',
                       'PendingAction',
                       'Proposition',
                       'Claim'],
         'slot_uri': 'dcterms:identifier'} })
    name: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['NamedThing'],
         'mixins': ['unhashable'],
         'slot_uri': 'rdfs:label'} })
    description: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['NamedThing'],
         'mixins': ['unhashable'],
         'slot_uri': 'dcterms:description'} })
    comments: Optional[list[str]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['NamedThing'], 'mixins': ['unhashable']} })


class InputBinding(ConfiguredBaseModel):
    """
    One input of a spec: either a measure (the same alternative's value of it) or a reference to a specific object such as an Assumption. The unit must match the bound source's unit exactly.
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/pdo/schema/evaluation',
         'slot_usage': {'arg': {'name': 'arg', 'required': True},
                        'unit': {'name': 'unit', 'required': True}}})

    arg: str = Field(default=..., description="""Argument name: the keyword an evaluator receives for this input, output or parameter. Unlike a display name it is part of the computation, so it is hashable.""", json_schema_extra = { "linkml_meta": {'domain_of': ['Parameter', 'InputBinding', 'OutputDeclaration'],
         'mixins': ['hashable']} })
    measure: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Criterion',
                       'Requirement',
                       'Observation',
                       'InputBinding',
                       'OutputDeclaration'],
         'mixins': ['hashable']} })
    ref: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['InputBinding'], 'mixins': ['hashable']} })
    unit: str = Field(default=..., description="""Unit string, matched exactly (no conversion). \"1\" means dimensionless.""", json_schema_extra = { "linkml_meta": {'domain_of': ['Parameter',
                       'Measure',
                       'Requirement',
                       'Observation',
                       'Assumption',
                       'InputBinding',
                       'OutputDeclaration',
                       'CheckOutcome'],
         'mixins': ['hashable']} })


class OutputDeclaration(ConfiguredBaseModel):
    """
    A measure the spec produces; the unit must match the measure's unit.
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/pdo/schema/evaluation',
         'slot_usage': {'arg': {'name': 'arg', 'required': True},
                        'measure': {'name': 'measure', 'required': True},
                        'unit': {'name': 'unit', 'required': True}}})

    arg: str = Field(default=..., description="""Argument name: the keyword an evaluator receives for this input, output or parameter. Unlike a display name it is part of the computation, so it is hashable.""", json_schema_extra = { "linkml_meta": {'domain_of': ['Parameter', 'InputBinding', 'OutputDeclaration'],
         'mixins': ['hashable']} })
    measure: str = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['Criterion',
                       'Requirement',
                       'Observation',
                       'InputBinding',
                       'OutputDeclaration'],
         'mixins': ['hashable']} })
    unit: str = Field(default=..., description="""Unit string, matched exactly (no conversion). \"1\" means dimensionless.""", json_schema_extra = { "linkml_meta": {'domain_of': ['Parameter',
                       'Measure',
                       'Requirement',
                       'Observation',
                       'Assumption',
                       'InputBinding',
                       'OutputDeclaration',
                       'CheckOutcome'],
         'mixins': ['hashable']} })


class ReadinessPolicy(NamedThing):
    """
    Who may approve and accept risk, and the risk score at which treatment is required.
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/pdo/schema/governance'})

    approval_roles: Optional[list[str]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['ReadinessPolicy'], 'mixins': ['hashable']} })
    risk_acceptance_roles: Optional[list[str]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['ReadinessPolicy'], 'mixins': ['hashable']} })
    risk_score_threshold: Optional[int] = Field(default=None, description="""Risks with likelihood x consequence at or above this need treatment (default 12).""", json_schema_extra = { "linkml_meta": {'domain_of': ['ReadinessPolicy'], 'mixins': ['hashable']} })
    approval_validity_days: Optional[int] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['ReadinessPolicy'], 'mixins': ['hashable']} })
    id: str = Field(default=..., description="""Logical identifier, authored with a type prefix (alt:, req:, meas:, obs:, ev:, crit:, rc:, ...).""", json_schema_extra = { "linkml_meta": {'domain_of': ['NamedThing',
                       'ChangeSet',
                       'PendingAction',
                       'Proposition',
                       'Claim'],
         'slot_uri': 'dcterms:identifier'} })
    name: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['NamedThing'],
         'mixins': ['unhashable'],
         'slot_uri': 'rdfs:label'} })
    description: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['NamedThing'],
         'mixins': ['unhashable'],
         'slot_uri': 'dcterms:description'} })
    comments: Optional[list[str]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['NamedThing'], 'mixins': ['unhashable']} })


class ReadinessCondition(NamedThing):
    """
    A condition required before authorization, evaluated by a built-in predicate against the current decision state. review_status conditions consume a recorded HumanReview.
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/pdo/schema/governance',
         'slot_usage': {'predicate': {'name': 'predicate', 'required': True}}})

    predicate: ReadinessPredicate = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['ReadinessCondition'], 'mixins': ['hashable']} })
    required: Optional[bool] = Field(default=None, description="""Default true. Conditions that are not required are reported but do not gate readiness.""", json_schema_extra = { "linkml_meta": {'domain_of': ['ReadinessCondition', 'ConditionResult'], 'mixins': ['hashable']} })
    review: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['ReadinessCondition'], 'mixins': ['hashable']} })
    parameters: Optional[list[Parameter]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['EvaluationSpecification', 'ReadinessCondition'],
         'mixins': ['hashable']} })
    owner_role: Optional[str] = Field(default=None, description="""The role that must act when this object needs a human (review, acceptance, data).""", json_schema_extra = { "linkml_meta": {'domain_of': ['Requirement',
                       'Evidence',
                       'Observation',
                       'Assumption',
                       'EvaluationSpecification',
                       'ReadinessCondition',
                       'HumanReview',
                       'Risk'],
         'mixins': ['unhashable']} })
    id: str = Field(default=..., description="""Logical identifier, authored with a type prefix (alt:, req:, meas:, obs:, ev:, crit:, rc:, ...).""", json_schema_extra = { "linkml_meta": {'domain_of': ['NamedThing',
                       'ChangeSet',
                       'PendingAction',
                       'Proposition',
                       'Claim'],
         'slot_uri': 'dcterms:identifier'} })
    name: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['NamedThing'],
         'mixins': ['unhashable'],
         'slot_uri': 'rdfs:label'} })
    description: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['NamedThing'],
         'mixins': ['unhashable'],
         'slot_uri': 'dcterms:description'} })
    comments: Optional[list[str]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['NamedThing'], 'mixins': ['unhashable']} })


class BiasCheck(ReadinessCondition):
    """
    A readiness condition that tests for a bias: in decision framing (an omitted alternative, a criterion counted twice) or in model output (a score resting on an unstated source).
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/pdo/schema/governance'})

    bias_kind: Optional[BiasKind] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['BiasCheck'], 'mixins': ['hashable']} })
    bias_category: Optional[BiasCategory] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['BiasCheck'], 'mixins': ['hashable']} })
    predicate: ReadinessPredicate = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['ReadinessCondition'], 'mixins': ['hashable']} })
    required: Optional[bool] = Field(default=None, description="""Default true. Conditions that are not required are reported but do not gate readiness.""", json_schema_extra = { "linkml_meta": {'domain_of': ['ReadinessCondition', 'ConditionResult'], 'mixins': ['hashable']} })
    review: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['ReadinessCondition'], 'mixins': ['hashable']} })
    parameters: Optional[list[Parameter]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['EvaluationSpecification', 'ReadinessCondition'],
         'mixins': ['hashable']} })
    owner_role: Optional[str] = Field(default=None, description="""The role that must act when this object needs a human (review, acceptance, data).""", json_schema_extra = { "linkml_meta": {'domain_of': ['Requirement',
                       'Evidence',
                       'Observation',
                       'Assumption',
                       'EvaluationSpecification',
                       'ReadinessCondition',
                       'HumanReview',
                       'Risk'],
         'mixins': ['unhashable']} })
    id: str = Field(default=..., description="""Logical identifier, authored with a type prefix (alt:, req:, meas:, obs:, ev:, crit:, rc:, ...).""", json_schema_extra = { "linkml_meta": {'domain_of': ['NamedThing',
                       'ChangeSet',
                       'PendingAction',
                       'Proposition',
                       'Claim'],
         'slot_uri': 'dcterms:identifier'} })
    name: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['NamedThing'],
         'mixins': ['unhashable'],
         'slot_uri': 'rdfs:label'} })
    description: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['NamedThing'],
         'mixins': ['unhashable'],
         'slot_uri': 'dcterms:description'} })
    comments: Optional[list[str]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['NamedThing'], 'mixins': ['unhashable']} })


class HumanReview(NamedThing):
    """
    A recorded review of a set of objects. The kernel stamps the digest of what it covered when the review is recorded; if any covered object changes later, the review is stale and counts as pending.
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/pdo/schema/governance',
         'slot_usage': {'review_status': {'name': 'review_status', 'required': True}}})

    review_kind: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['HumanReview'], 'mixins': ['unhashable']} })
    covers: Optional[list[str]] = Field(default=None, description="""Ids of reviewed objects, or selectors \"all:<collection>\" (e.g. all:criteria, all:alternatives).""", json_schema_extra = { "linkml_meta": {'domain_of': ['HumanReview'], 'mixins': ['hashable']} })
    review_status: ReviewStatus = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['HumanReview'], 'mixins': ['hashable']} })
    reviewer: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['HumanReview'], 'mixins': ['unhashable']} })
    rationale: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['HumanReview', 'HumanApproval'], 'mixins': ['unhashable']} })
    recorded_in_episode: Optional[str] = Field(default=None, description="""Episode in which the action was recorded (stamped by the kernel).""", json_schema_extra = { "linkml_meta": {'domain_of': ['HumanReview', 'HumanApproval'], 'mixins': ['unhashable']} })
    covered_digest: Optional[str] = Field(default=None, description="""Digest of the covered objects when the review was recorded (stamped by the kernel).""", json_schema_extra = { "linkml_meta": {'domain_of': ['HumanReview'], 'mixins': ['hashable']} })
    owner_role: Optional[str] = Field(default=None, description="""The role that must act when this object needs a human (review, acceptance, data).""", json_schema_extra = { "linkml_meta": {'domain_of': ['Requirement',
                       'Evidence',
                       'Observation',
                       'Assumption',
                       'EvaluationSpecification',
                       'ReadinessCondition',
                       'HumanReview',
                       'Risk'],
         'mixins': ['unhashable']} })
    id: str = Field(default=..., description="""Logical identifier, authored with a type prefix (alt:, req:, meas:, obs:, ev:, crit:, rc:, ...).""", json_schema_extra = { "linkml_meta": {'domain_of': ['NamedThing',
                       'ChangeSet',
                       'PendingAction',
                       'Proposition',
                       'Claim'],
         'slot_uri': 'dcterms:identifier'} })
    name: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['NamedThing'],
         'mixins': ['unhashable'],
         'slot_uri': 'rdfs:label'} })
    description: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['NamedThing'],
         'mixins': ['unhashable'],
         'slot_uri': 'dcterms:description'} })
    comments: Optional[list[str]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['NamedThing'], 'mixins': ['unhashable']} })


class Risk(NamedThing):
    """
    A risk to the decision. Risks scoring at or above the policy threshold (likelihood x consequence) must be mitigated, or accepted by a role the policy allows.
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/pdo/schema/governance',
         'slot_usage': {'consequence': {'name': 'consequence', 'required': True},
                        'likelihood': {'name': 'likelihood', 'required': True}}})

    affects: Optional[list[str]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Risk'], 'mixins': ['hashable']} })
    likelihood: int = Field(default=..., ge=1, le=5, json_schema_extra = { "linkml_meta": {'domain_of': ['Risk'], 'mixins': ['hashable']} })
    consequence: int = Field(default=..., ge=1, le=5, json_schema_extra = { "linkml_meta": {'domain_of': ['Risk'], 'mixins': ['hashable']} })
    treatment_status: Optional[TreatmentStatus] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Risk'], 'mixins': ['hashable']} })
    accepted_by_role: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Risk'], 'mixins': ['hashable']} })
    owner_role: Optional[str] = Field(default=None, description="""The role that must act when this object needs a human (review, acceptance, data).""", json_schema_extra = { "linkml_meta": {'domain_of': ['Requirement',
                       'Evidence',
                       'Observation',
                       'Assumption',
                       'EvaluationSpecification',
                       'ReadinessCondition',
                       'HumanReview',
                       'Risk'],
         'mixins': ['unhashable']} })
    id: str = Field(default=..., description="""Logical identifier, authored with a type prefix (alt:, req:, meas:, obs:, ev:, crit:, rc:, ...).""", json_schema_extra = { "linkml_meta": {'domain_of': ['NamedThing',
                       'ChangeSet',
                       'PendingAction',
                       'Proposition',
                       'Claim'],
         'slot_uri': 'dcterms:identifier'} })
    name: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['NamedThing'],
         'mixins': ['unhashable'],
         'slot_uri': 'rdfs:label'} })
    description: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['NamedThing'],
         'mixins': ['unhashable'],
         'slot_uri': 'dcterms:description'} })
    comments: Optional[list[str]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['NamedThing'], 'mixins': ['unhashable']} })


class HumanApproval(NamedThing):
    """
    A recorded authorization of one alternative. The kernel stamps the state digest it covered; the decision is actionable only while the recommendation, readiness and that digest still match.
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/pdo/schema/governance',
         'slot_usage': {'approved_alternative': {'name': 'approved_alternative',
                                                 'required': True},
                        'approver_role': {'name': 'approver_role', 'required': True}}})

    approved_alternative: str = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['HumanApproval'], 'mixins': ['hashable']} })
    approver_role: str = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['HumanApproval'], 'mixins': ['hashable']} })
    approver: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['HumanApproval'], 'mixins': ['unhashable']} })
    rationale: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['HumanReview', 'HumanApproval'], 'mixins': ['unhashable']} })
    valid_until: Optional[date] = Field(default=None, description="""After this date the evidence is stale and readiness becomes pending until it is refreshed.""", json_schema_extra = { "linkml_meta": {'domain_of': ['Evidence', 'HumanApproval'], 'mixins': ['hashable']} })
    recorded_in_episode: Optional[str] = Field(default=None, description="""Episode in which the action was recorded (stamped by the kernel).""", json_schema_extra = { "linkml_meta": {'domain_of': ['HumanReview', 'HumanApproval'], 'mixins': ['unhashable']} })
    state_digest: Optional[str] = Field(default=None, description="""Digest of the decision state the approval covered (stamped by the kernel).""", json_schema_extra = { "linkml_meta": {'domain_of': ['HumanApproval', 'DecisionEpisodeRecord'],
         'mixins': ['hashable']} })
    id: str = Field(default=..., description="""Logical identifier, authored with a type prefix (alt:, req:, meas:, obs:, ev:, crit:, rc:, ...).""", json_schema_extra = { "linkml_meta": {'domain_of': ['NamedThing',
                       'ChangeSet',
                       'PendingAction',
                       'Proposition',
                       'Claim'],
         'slot_uri': 'dcterms:identifier'} })
    name: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['NamedThing'],
         'mixins': ['unhashable'],
         'slot_uri': 'rdfs:label'} })
    description: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['NamedThing'],
         'mixins': ['unhashable'],
         'slot_uri': 'dcterms:description'} })
    comments: Optional[list[str]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['NamedThing'], 'mixins': ['unhashable']} })


class DecisionState(ConfiguredBaseModel):
    """
    One decision state (or a part of one, while it is split across files).
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/pdo/schema/state'})

    as_of: Optional[date] = Field(default=None, description="""The state's clock; evidence expiry and approval validity are judged against it.""", json_schema_extra = { "linkml_meta": {'domain_of': ['DecisionState', 'ChangeSet', 'DecisionEpisodeRecord'],
         'mixins': ['hashable']} })
    question: Optional[str] = Field(default=None, description="""The decision question in plain words.""", json_schema_extra = { "linkml_meta": {'domain_of': ['DecisionState', 'DecisionProgram', 'DecisionEpisodeRecord'],
         'mixins': ['unhashable']} })
    roles: Optional[list[Role]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['DecisionState'], 'mixins': ['hashable']} })
    objectives: Optional[list[Objective]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['DecisionState'], 'mixins': ['hashable']} })
    alternatives: Optional[list[Alternative]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['DecisionState'], 'mixins': ['hashable']} })
    measures: Optional[list[Measure]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['DecisionState'], 'mixins': ['hashable']} })
    criteria: Optional[list[Criterion]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['DecisionState'], 'mixins': ['hashable']} })
    requirements: Optional[list[Requirement]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['DecisionState'], 'mixins': ['hashable']} })
    decision_rule: Optional[DecisionRule] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['DecisionState'], 'mixins': ['hashable']} })
    scenarios: Optional[list[Scenario]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['DecisionState'], 'mixins': ['hashable']} })
    evidence_items: Optional[list[Evidence]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['DecisionState'], 'mixins': ['hashable']} })
    observations: Optional[list[Observation]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['DecisionState'], 'mixins': ['hashable']} })
    assumptions: Optional[list[Assumption]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['DecisionState'], 'mixins': ['hashable']} })
    evaluation_specifications: Optional[list[EvaluationSpecification]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['DecisionState'], 'mixins': ['hashable']} })
    readiness_policy: Optional[ReadinessPolicy] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['DecisionState'], 'mixins': ['hashable']} })
    readiness_conditions: Optional[list[ReadinessCondition]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['DecisionState'], 'mixins': ['hashable']} })
    bias_checks: Optional[list[BiasCheck]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['DecisionState'], 'mixins': ['hashable']} })
    reviews: Optional[list[HumanReview]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['DecisionState'], 'mixins': ['hashable']} })
    risks: Optional[list[Risk]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['DecisionState'], 'mixins': ['hashable']} })
    approvals: Optional[list[HumanApproval]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['DecisionState'], 'mixins': ['hashable']} })


class ExecutableDecisionState(DecisionState):
    """
    A decision state complete enough to run.
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/pdo/schema/state',
         'slot_usage': {'alternatives': {'name': 'alternatives', 'required': True},
                        'as_of': {'name': 'as_of', 'required': True},
                        'criteria': {'name': 'criteria', 'required': True},
                        'decision_rule': {'name': 'decision_rule', 'required': True},
                        'measures': {'name': 'measures', 'required': True},
                        'readiness_policy': {'name': 'readiness_policy',
                                             'required': True}}})

    as_of: date = Field(default=..., description="""The state's clock; evidence expiry and approval validity are judged against it.""", json_schema_extra = { "linkml_meta": {'domain_of': ['DecisionState', 'ChangeSet', 'DecisionEpisodeRecord'],
         'mixins': ['hashable']} })
    question: Optional[str] = Field(default=None, description="""The decision question in plain words.""", json_schema_extra = { "linkml_meta": {'domain_of': ['DecisionState', 'DecisionProgram', 'DecisionEpisodeRecord'],
         'mixins': ['unhashable']} })
    roles: Optional[list[Role]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['DecisionState'], 'mixins': ['hashable']} })
    objectives: Optional[list[Objective]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['DecisionState'], 'mixins': ['hashable']} })
    alternatives: list[Alternative] = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['DecisionState'], 'mixins': ['hashable']} })
    measures: list[Measure] = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['DecisionState'], 'mixins': ['hashable']} })
    criteria: list[Criterion] = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['DecisionState'], 'mixins': ['hashable']} })
    requirements: Optional[list[Requirement]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['DecisionState'], 'mixins': ['hashable']} })
    decision_rule: DecisionRule = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['DecisionState'], 'mixins': ['hashable']} })
    scenarios: Optional[list[Scenario]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['DecisionState'], 'mixins': ['hashable']} })
    evidence_items: Optional[list[Evidence]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['DecisionState'], 'mixins': ['hashable']} })
    observations: Optional[list[Observation]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['DecisionState'], 'mixins': ['hashable']} })
    assumptions: Optional[list[Assumption]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['DecisionState'], 'mixins': ['hashable']} })
    evaluation_specifications: Optional[list[EvaluationSpecification]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['DecisionState'], 'mixins': ['hashable']} })
    readiness_policy: ReadinessPolicy = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['DecisionState'], 'mixins': ['hashable']} })
    readiness_conditions: Optional[list[ReadinessCondition]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['DecisionState'], 'mixins': ['hashable']} })
    bias_checks: Optional[list[BiasCheck]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['DecisionState'], 'mixins': ['hashable']} })
    reviews: Optional[list[HumanReview]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['DecisionState'], 'mixins': ['hashable']} })
    risks: Optional[list[Risk]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['DecisionState'], 'mixins': ['hashable']} })
    approvals: Optional[list[HumanApproval]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['DecisionState'], 'mixins': ['hashable']} })


class DecisionProgram(NamedThing):
    """
    A decision maintained over time, from a baseline state through successive change sets.
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/pdo/schema/program',
         'slot_usage': {'baseline_episode': {'name': 'baseline_episode',
                                             'required': True}}})

    question: Optional[str] = Field(default=None, description="""The decision question in plain words.""", json_schema_extra = { "linkml_meta": {'domain_of': ['DecisionState', 'DecisionProgram', 'DecisionEpisodeRecord'],
         'mixins': ['unhashable']} })
    decision_owner_role: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['DecisionProgram'], 'mixins': ['unhashable']} })
    baseline_episode: str = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['DecisionProgram'], 'mixins': ['unhashable']} })
    baseline_dir: Optional[str] = Field(default=None, description="""Directory of the baseline state's files, relative to program.yaml (default baseline/).""", json_schema_extra = { "linkml_meta": {'domain_of': ['DecisionProgram'], 'mixins': ['unhashable']} })
    changes_dir: Optional[str] = Field(default=None, description="""Directory of change-set files, relative to program.yaml (default changes/).""", json_schema_extra = { "linkml_meta": {'domain_of': ['DecisionProgram'], 'mixins': ['unhashable']} })
    id: str = Field(default=..., description="""Logical identifier, authored with a type prefix (alt:, req:, meas:, obs:, ev:, crit:, rc:, ...).""", json_schema_extra = { "linkml_meta": {'domain_of': ['NamedThing',
                       'ChangeSet',
                       'PendingAction',
                       'Proposition',
                       'Claim'],
         'slot_uri': 'dcterms:identifier'} })
    name: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['NamedThing'],
         'mixins': ['unhashable'],
         'slot_uri': 'rdfs:label'} })
    description: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['NamedThing'],
         'mixins': ['unhashable'],
         'slot_uri': 'dcterms:description'} })
    comments: Optional[list[str]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['NamedThing'], 'mixins': ['unhashable']} })


class ChangeSet(ConfiguredBaseModel):
    """
    One episode's change from its parent: assignments to existing objects (assign), new objects (add, a partial decision state), and removals by id. A change set is kept only if the resulting state validates. Human actions (reviews, approvals) are recorded by adding or assigning them here.
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/pdo/schema/program',
         'slot_usage': {'parent': {'name': 'parent', 'required': True}}})

    id: str = Field(default=..., description="""Logical identifier, authored with a type prefix (alt:, req:, meas:, obs:, ev:, crit:, rc:, ...).""", json_schema_extra = { "linkml_meta": {'domain_of': ['NamedThing',
                       'ChangeSet',
                       'PendingAction',
                       'Proposition',
                       'Claim'],
         'slot_uri': 'dcterms:identifier'} })
    parent: str = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['ChangeSet', 'DecisionEpisodeRecord'], 'mixins': ['hashable']} })
    as_of: Optional[date] = Field(default=None, description="""The state's clock; evidence expiry and approval validity are judged against it.""", json_schema_extra = { "linkml_meta": {'domain_of': ['DecisionState', 'ChangeSet', 'DecisionEpisodeRecord'],
         'mixins': ['hashable']} })
    trigger: Optional[TriggerKind] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['ChangeSet'], 'mixins': ['unhashable']} })
    change_description: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['ChangeSet'], 'mixins': ['unhashable']} })
    author_role: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['ChangeSet'], 'mixins': ['unhashable']} })
    assign: Optional[list[SlotAssignment]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['ChangeSet'], 'mixins': ['hashable']} })
    add: Optional[DecisionState] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['ChangeSet'], 'mixins': ['hashable']} })
    remove: Optional[list[str]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['ChangeSet'], 'mixins': ['hashable']} })


class DecisionEpisodeRecord(ConfiguredBaseModel):
    """
    One evaluated decision episode.
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/pdo/schema/record'})

    episode: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['DecisionEpisodeRecord'], 'mixins': ['hashable']} })
    parent: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['ChangeSet', 'DecisionEpisodeRecord'], 'mixins': ['hashable']} })
    source_path: Optional[str] = Field(default=None, description="""Where the evaluated state or program was read from (as given), so the record can be replayed.""", json_schema_extra = { "linkml_meta": {'domain_of': ['DecisionEpisodeRecord'], 'mixins': ['unhashable']} })
    question: Optional[str] = Field(default=None, description="""The decision question in plain words.""", json_schema_extra = { "linkml_meta": {'domain_of': ['DecisionState', 'DecisionProgram', 'DecisionEpisodeRecord'],
         'mixins': ['unhashable']} })
    as_of: Optional[date] = Field(default=None, description="""The state's clock; evidence expiry and approval validity are judged against it.""", json_schema_extra = { "linkml_meta": {'domain_of': ['DecisionState', 'ChangeSet', 'DecisionEpisodeRecord'],
         'mixins': ['hashable']} })
    state_digest: Optional[str] = Field(default=None, description="""Digest of the decision state the approval covered (stamped by the kernel).""", json_schema_extra = { "linkml_meta": {'domain_of': ['HumanApproval', 'DecisionEpisodeRecord'],
         'mixins': ['hashable']} })
    kernel_version: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['DecisionEpisodeRecord'], 'mixins': ['unhashable']} })
    recommended: Optional[list[str]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['DecisionEpisodeRecord', 'ScenarioOutcome'],
         'mixins': ['hashable']} })
    feasible: Optional[list[str]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['DecisionEpisodeRecord', 'ScenarioOutcome'],
         'mixins': ['hashable']} })
    infeasible: Optional[list[str]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['DecisionEpisodeRecord', 'ScenarioOutcome'],
         'mixins': ['hashable']} })
    unresolved: Optional[list[str]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['DecisionEpisodeRecord', 'ScenarioOutcome'],
         'mixins': ['hashable']} })
    outcomes: Optional[list[AlternativeOutcome]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['DecisionEpisodeRecord', 'ScenarioOutcome'],
         'mixins': ['hashable']} })
    readiness: Optional[ReadinessAssessment] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['DecisionEpisodeRecord'], 'mixins': ['hashable']} })
    actionable: Optional[bool] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['DecisionEpisodeRecord'], 'mixins': ['hashable']} })
    actionable_reason: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['DecisionEpisodeRecord'], 'mixins': ['unhashable']} })
    matching_approval: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['DecisionEpisodeRecord'], 'mixins': ['hashable']} })
    flips: Optional[list[FlipPoint]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['DecisionEpisodeRecord'], 'mixins': ['hashable']} })
    parameter_scans: Optional[list[ParameterScan]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['DecisionEpisodeRecord'], 'mixins': ['hashable']} })
    scenario_outcomes: Optional[list[ScenarioOutcome]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['DecisionEpisodeRecord'], 'mixins': ['hashable']} })
    robust: Optional[bool] = Field(default=None, description="""On a scenario outcome, the baseline recommendation is feasible and uniquely best in that scenario; on a record, in every scenario of the declared set.""", json_schema_extra = { "linkml_meta": {'domain_of': ['DecisionEpisodeRecord', 'ScenarioOutcome'],
         'mixins': ['hashable']} })
    pending_actions: Optional[list[PendingAction]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['DecisionEpisodeRecord'], 'mixins': ['hashable']} })
    runs: Optional[list[EvaluationRun]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['DecisionEpisodeRecord'], 'mixins': ['hashable']} })
    propositions: Optional[list[Proposition]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['DecisionEpisodeRecord'], 'mixins': ['hashable']} })
    claims: Optional[list[Claim]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['DecisionEpisodeRecord'], 'mixins': ['hashable']} })
    impact: Optional[ImpactSummary] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['DecisionEpisodeRecord'], 'mixins': ['hashable']} })


class AlternativeOutcome(ConfiguredBaseModel):
    """
    How one alternative fared.
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/pdo/schema/record'})

    alternative: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Observation', 'AlternativeOutcome'], 'mixins': ['hashable']} })
    utility: Optional[float] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['AlternativeOutcome'], 'mixins': ['hashable']} })
    utility_lower: Optional[float] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['AlternativeOutcome'], 'mixins': ['hashable']} })
    utility_upper: Optional[float] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['AlternativeOutcome'], 'mixins': ['hashable']} })
    feasibility: Optional[AssessmentStatus] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['AlternativeOutcome'], 'mixins': ['hashable']} })
    checks: Optional[list[CheckOutcome]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['AlternativeOutcome'], 'mixins': ['hashable']} })
    criterion_values: Optional[list[CriterionValue]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['AlternativeOutcome'], 'mixins': ['hashable']} })


class CheckOutcome(ConfiguredBaseModel):
    """
    One requirement check, in the common g <= 0 convention.
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/pdo/schema/record'})

    requirement: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['CheckOutcome'], 'mixins': ['hashable']} })
    status: Optional[AssessmentStatus] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['CheckOutcome', 'ConditionResult', 'Claim'],
         'mixins': ['hashable']} })
    g: Optional[float] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['CheckOutcome'], 'mixins': ['hashable']} })
    measured: Optional[float] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['CheckOutcome', 'CriterionValue'], 'mixins': ['hashable']} })
    comparator: Optional[Comparator] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Requirement', 'CheckOutcome'], 'mixins': ['hashable']} })
    threshold_value: Optional[float] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Requirement', 'CheckOutcome'], 'mixins': ['hashable']} })
    unit: Optional[str] = Field(default=None, description="""Unit string, matched exactly (no conversion). \"1\" means dimensionless.""", json_schema_extra = { "linkml_meta": {'domain_of': ['Parameter',
                       'Measure',
                       'Requirement',
                       'Observation',
                       'Assumption',
                       'InputBinding',
                       'OutputDeclaration',
                       'CheckOutcome'],
         'mixins': ['hashable']} })


class CriterionValue(ConfiguredBaseModel):
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/pdo/schema/record'})

    criterion: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['CriterionValue'], 'mixins': ['hashable']} })
    measured: Optional[float] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['CheckOutcome', 'CriterionValue'], 'mixins': ['hashable']} })
    value_score: Optional[float] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['CriterionValue'], 'mixins': ['hashable']} })
    weight: Optional[float] = Field(default=None, ge=0, le=1, json_schema_extra = { "linkml_meta": {'domain_of': ['Criterion', 'CriterionValue'], 'mixins': ['hashable']} })


class ReadinessAssessment(ConfiguredBaseModel):
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/pdo/schema/record'})

    readiness_state: Optional[ReadinessState] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['ReadinessAssessment'], 'mixins': ['hashable']} })
    conditions: Optional[list[ConditionResult]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['ReadinessAssessment'], 'mixins': ['hashable']} })


class ConditionResult(ConfiguredBaseModel):
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/pdo/schema/record'})

    condition: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['ConditionResult'], 'mixins': ['hashable']} })
    status: Optional[AssessmentStatus] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['CheckOutcome', 'ConditionResult', 'Claim'],
         'mixins': ['hashable']} })
    required: Optional[bool] = Field(default=None, description="""Default true. Conditions that are not required are reported but do not gate readiness.""", json_schema_extra = { "linkml_meta": {'domain_of': ['ReadinessCondition', 'ConditionResult'], 'mixins': ['hashable']} })
    reason: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['ConditionResult', 'PendingAction'], 'mixins': ['unhashable']} })


class PendingAction(ConfiguredBaseModel):
    """
    Something a person must do before the decision is actionable, routed to the owning role. The id is a content digest, so the same open action keeps its id across runs and can be deduplicated.
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/pdo/schema/record'})

    id: str = Field(default=..., description="""Logical identifier, authored with a type prefix (alt:, req:, meas:, obs:, ev:, crit:, rc:, ...).""", json_schema_extra = { "linkml_meta": {'domain_of': ['NamedThing',
                       'ChangeSet',
                       'PendingAction',
                       'Proposition',
                       'Claim'],
         'slot_uri': 'dcterms:identifier'} })
    action_kind: Optional[ActionKind] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['PendingAction'], 'mixins': ['hashable']} })
    target: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['SlotAssignment', 'PendingAction', 'FlipPoint', 'ParameterScan'],
         'mixins': ['hashable']} })
    role: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['PendingAction'], 'mixins': ['hashable']} })
    reason: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['ConditionResult', 'PendingAction'], 'mixins': ['unhashable']} })
    produced_by: Optional[str] = Field(default=None, description="""The node (and so the evaluation run) or recorded action that produced the claim.""", json_schema_extra = { "linkml_meta": {'domain_of': ['PendingAction', 'Claim'], 'mixins': ['hashable']} })


class FlipPoint(ConfiguredBaseModel):
    """
    A value of one input at which the recommendation changes, all other inputs held fixed.
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/pdo/schema/record'})

    parameter: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['FlipPoint', 'ParameterScan'], 'mixins': ['hashable']} })
    target: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['SlotAssignment', 'PendingAction', 'FlipPoint', 'ParameterScan'],
         'mixins': ['hashable']} })
    slot: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['SlotAssignment', 'FlipPoint', 'ParameterScan'],
         'mixins': ['hashable']} })
    baseline_number: Optional[float] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['FlipPoint', 'ParameterScan'], 'mixins': ['hashable']} })
    flip_number: Optional[float] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['FlipPoint'], 'mixins': ['hashable']} })
    method: Optional[FlipMethod] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['FlipPoint'], 'mixins': ['hashable']} })
    boundary_kind: Optional[BoundaryKind] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['FlipPoint'], 'mixins': ['hashable']} })
    new_recommendation: Optional[list[str]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['FlipPoint'], 'mixins': ['hashable']} })
    previous_recommendation: Optional[list[str]] = Field(default=None, description="""The recommendation on the baseline side of the flip point.""", json_schema_extra = { "linkml_meta": {'domain_of': ['FlipPoint'], 'mixins': ['hashable']} })
    direction: Optional[FlipDirection] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['FlipPoint'], 'mixins': ['hashable']} })
    crossing: Optional[str] = Field(default=None, description="""The quantity whose zero is the boundary, e.g. \"U/alt:B - U/alt:A\" (a utility margin) or \"g/req:max-cost/alt:B\" (a requirement check).""", json_schema_extra = { "linkml_meta": {'domain_of': ['FlipPoint'], 'mixins': ['hashable']} })
    counterpart: Optional[str] = Field(default=None, description="""For a weight flip, the criterion whose weight absorbs the shift (the other weights held fixed).""", json_schema_extra = { "linkml_meta": {'domain_of': ['FlipPoint'], 'mixins': ['hashable']} })
    verified: Optional[bool] = Field(default=None, description="""Re-running just below and just above the flip point gives the two recommendations reported.""", json_schema_extra = { "linkml_meta": {'domain_of': ['FlipPoint'], 'mixins': ['hashable']} })


class ParameterScan(ConfiguredBaseModel):
    """
    One numeric input scanned for flip points over [scan_low, scan_high], all other inputs held fixed. A scan with no flip supports a claim about that range only.
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/pdo/schema/record'})

    parameter: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['FlipPoint', 'ParameterScan'], 'mixins': ['hashable']} })
    target: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['SlotAssignment', 'PendingAction', 'FlipPoint', 'ParameterScan'],
         'mixins': ['hashable']} })
    slot: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['SlotAssignment', 'FlipPoint', 'ParameterScan'],
         'mixins': ['hashable']} })
    baseline_number: Optional[float] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['FlipPoint', 'ParameterScan'], 'mixins': ['hashable']} })
    scan_low: Optional[float] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['ParameterScan'], 'mixins': ['hashable']} })
    scan_high: Optional[float] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['ParameterScan'], 'mixins': ['hashable']} })
    grid_points: Optional[int] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['ParameterScan'], 'mixins': ['hashable']} })
    flip_count: Optional[int] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['ParameterScan'], 'mixins': ['hashable']} })


class ScenarioOutcome(ConfiguredBaseModel):
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/pdo/schema/record'})

    scenario: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['ScenarioOutcome'], 'mixins': ['hashable']} })
    recommended: Optional[list[str]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['DecisionEpisodeRecord', 'ScenarioOutcome'],
         'mixins': ['hashable']} })
    feasible: Optional[list[str]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['DecisionEpisodeRecord', 'ScenarioOutcome'],
         'mixins': ['hashable']} })
    robust: Optional[bool] = Field(default=None, description="""On a scenario outcome, the baseline recommendation is feasible and uniquely best in that scenario; on a record, in every scenario of the declared set.""", json_schema_extra = { "linkml_meta": {'domain_of': ['DecisionEpisodeRecord', 'ScenarioOutcome'],
         'mixins': ['hashable']} })
    infeasible: Optional[list[str]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['DecisionEpisodeRecord', 'ScenarioOutcome'],
         'mixins': ['hashable']} })
    unresolved: Optional[list[str]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['DecisionEpisodeRecord', 'ScenarioOutcome'],
         'mixins': ['hashable']} })
    outcomes: Optional[list[AlternativeOutcome]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['DecisionEpisodeRecord', 'ScenarioOutcome'],
         'mixins': ['hashable']} })


class EvaluationRun(ConfiguredBaseModel):
    """
    One node evaluation; the signature identifies its exact inputs and implementation.
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/pdo/schema/record'})

    node: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['EvaluationRun'], 'mixins': ['hashable']} })
    node_kind: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['EvaluationRun'], 'mixins': ['hashable']} })
    implementation: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['EvaluationSpecification', 'EvaluationRun'],
         'mixins': ['hashable']} })
    signature: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['EvaluationRun'], 'mixins': ['hashable']} })
    run_key: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['EvaluationRun'], 'mixins': ['hashable']} })
    value_digest: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['EvaluationRun'], 'mixins': ['hashable']} })
    execution_status: Optional[ExecutionStatus] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['EvaluationRun'], 'mixins': ['unhashable']} })
    input_nodes: Optional[list[str]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['EvaluationRun'], 'mixins': ['hashable']} })
    duration_ms: Optional[float] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['EvaluationRun'], 'mixins': ['unhashable']} })
    error: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['EvaluationRun'], 'mixins': ['unhashable']} })


class ImpactSummary(ConfiguredBaseModel):
    """
    What a change touched, and what was rerun versus reused.
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/pdo/schema/record'})

    changed_nodes: Optional[list[str]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['ImpactSummary'], 'mixins': ['hashable']} })
    impact_size: Optional[int] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['ImpactSummary'], 'mixins': ['hashable']} })
    executed_count: Optional[int] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['ImpactSummary'], 'mixins': ['unhashable']} })
    reused_count: Optional[int] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['ImpactSummary'], 'mixins': ['unhashable']} })
    total_nodes: Optional[int] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['ImpactSummary'], 'mixins': ['hashable']} })


class Proposition(ConfiguredBaseModel):
    """
    Content that can be true or false, e.g. \"alt:B satisfies req:min-range\".
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'exact_mappings': ['dapper:Proposition'],
         'from_schema': 'https://w3id.org/pdo/schema/record'})

    id: str = Field(default=..., description="""Logical identifier, authored with a type prefix (alt:, req:, meas:, obs:, ev:, crit:, rc:, ...).""", json_schema_extra = { "linkml_meta": {'domain_of': ['NamedThing',
                       'ChangeSet',
                       'PendingAction',
                       'Proposition',
                       'Claim'],
         'slot_uri': 'dcterms:identifier'} })
    statement: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Proposition'], 'mixins': ['hashable']} })
    about: Optional[list[str]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Proposition'], 'mixins': ['hashable']} })


class Claim(ConfiguredBaseModel):
    """
    An assessment of a proposition, with the reasoning basis that produced it: computed (an evaluator or check), semantic (an agent's interpretation), or human (a recorded judgement).
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'exact_mappings': ['dapper:Claim'],
         'from_schema': 'https://w3id.org/pdo/schema/record'})

    id: str = Field(default=..., description="""Logical identifier, authored with a type prefix (alt:, req:, meas:, obs:, ev:, crit:, rc:, ...).""", json_schema_extra = { "linkml_meta": {'domain_of': ['NamedThing',
                       'ChangeSet',
                       'PendingAction',
                       'Proposition',
                       'Claim'],
         'slot_uri': 'dcterms:identifier'} })
    proposition: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Claim'], 'mixins': ['hashable']} })
    status: Optional[AssessmentStatus] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['CheckOutcome', 'ConditionResult', 'Claim'],
         'mixins': ['hashable']} })
    basis: Optional[ClaimBasis] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Claim'], 'mixins': ['hashable']} })
    produced_by: Optional[str] = Field(default=None, description="""The node (and so the evaluation run) or recorded action that produced the claim.""", json_schema_extra = { "linkml_meta": {'domain_of': ['PendingAction', 'Claim'], 'mixins': ['hashable']} })
    supported_by: Optional[list[str]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Claim'], 'mixins': ['hashable']} })


# Model rebuild
# see https://pydantic-docs.helpmanual.io/usage/models/#rebuilding-a-model
NamedThing.model_rebuild()
Role.model_rebuild()
SourceRef.model_rebuild()
Parameter.model_rebuild()
Objective.model_rebuild()
Alternative.model_rebuild()
Measure.model_rebuild()
Criterion.model_rebuild()
PiecewisePoint.model_rebuild()
Requirement.model_rebuild()
DecisionRule.model_rebuild()
Scenario.model_rebuild()
SlotAssignment.model_rebuild()
Evidence.model_rebuild()
Observation.model_rebuild()
Assumption.model_rebuild()
UncertaintySpec.model_rebuild()
EvaluationSpecification.model_rebuild()
InputBinding.model_rebuild()
OutputDeclaration.model_rebuild()
ReadinessPolicy.model_rebuild()
ReadinessCondition.model_rebuild()
BiasCheck.model_rebuild()
HumanReview.model_rebuild()
Risk.model_rebuild()
HumanApproval.model_rebuild()
DecisionState.model_rebuild()
ExecutableDecisionState.model_rebuild()
DecisionProgram.model_rebuild()
ChangeSet.model_rebuild()
DecisionEpisodeRecord.model_rebuild()
AlternativeOutcome.model_rebuild()
CheckOutcome.model_rebuild()
CriterionValue.model_rebuild()
ReadinessAssessment.model_rebuild()
ConditionResult.model_rebuild()
PendingAction.model_rebuild()
FlipPoint.model_rebuild()
ParameterScan.model_rebuild()
ScenarioOutcome.model_rebuild()
EvaluationRun.model_rebuild()
ImpactSummary.model_rebuild()
Proposition.model_rebuild()
Claim.model_rebuild()
