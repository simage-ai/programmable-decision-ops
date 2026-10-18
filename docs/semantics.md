# Kernel semantics

What the kernel computes, stated without reference to its code. Benchmark labels are computed by hand
from this document, so it is the specification the implementation is tested against.

## Decision state

A decision state holds typed objects with logical ids (`alt:B`, `req:min-range`, `obs:B.cost`, ...) and
a clock, `as_of`. Each object has **hashable** slots (its content: values, thresholds, statuses) and
**unhashable** slots (names, descriptions, rationale, owner roles, assignees, `cost_units`). Changing only
unhashable slots changes no computed value and invalidates nothing.

## Measures

For each alternative *a* and measure *m*, the value *y(a, m)* comes from exactly one producer:

- **Observations** of *(a, m)*. Only observations with `applicability` accepted (the default) and a
  value count. If they all agree, that is *y*. If accepted values conflict, *y* is unknown. If the only
  remaining observations are `provisional`, *y* is unknown (pending review). `rejected` observations
  are ignored. No usable observation means unknown.
- **An evaluation specification** that outputs *m* for *a*. It runs on its bound inputs: a measure
  binding reads *y(a, measure)*, and a ref binding reads an assumption's `value`. If any input is
  unknown, or the evaluator raises or returns a non-finite number, *y* is unknown. The vehicle example
  computes range = tank_gal × mpg × terrain_factor (A 40 × 8 = 320, B 45 × 8 = 360, C 47.5 × 8 = 380 at
  terrain 1.0).
- **Nothing:** *y* is unknown.

## Checks and feasibility

Each requirement *r* applies to `applies_to` (empty means all alternatives). In the common convention,
g ≤ 0 passes:

| comparator | g | passes when |
|---|---|---|
| ge | threshold − y | g ≤ tol |
| le | y − threshold | g ≤ tol |
| gt | threshold − y | g < −tol |
| lt | y − threshold | g < −tol |

`tol` is `check_tolerance` (default 1e-9). An unknown *y* gives status unknown.

An alternative's **feasibility** combines its *mandatory* checks:

- **fail** if any check is a known fail, even when another is unknown (a known violation dominates);
- otherwise **unknown** if any check is unknown;
- otherwise **pass**.

## Value and utility

A criterion scores a measure with v = (y − anchor_worst) / (anchor_best − anchor_worst), clipped to
[0, 1] unless `clip: false`, or with piecewise-linear `points`. An unknown *y* gives an unknown *v*.

Utility is U(a) = Σ_k w_k · v_k(a). The weights must sum to 1. If any *v* is unknown, U is unknown and
bounded by [Σ known w·v, Σ known w·v + Σ unknown w].

## Decision

- **feasible**: alternatives whose feasibility is pass.
- **infeasible**: alternatives whose feasibility is fail.
- **unresolved**: feasibility unknown, or feasible but with an unknown U.
- **recommended**: among feasible alternatives with a known U, every one with U ≥ max U − `tie_tolerance`
  (default 1e-9). It is empty when no feasible alternative has a known U.

The vehicle baseline gives U_A = 0.466667, U_B = 0.533333 and U_C = 0.62. C fails min reliability
(g = +0.01) and is infeasible, so the recommendation is {B}.

## Readiness conditions

Each condition evaluates to pass, fail or unknown. "Supporting evidence" means the evidence behind the
accepted, valued observations of the *recommended* alternatives (all of their measures).

| predicate | pass | fail | unknown |
|---|---|---|---|
| recommendation_exists | recommendation non-empty | empty and nothing unresolved | empty, something unresolved |
| unique_recommendation | exactly one recommended | none recommended and nothing unresolved | tie (two or more), or none recommended but something unresolved |
| coverage | nothing unresolved, or every unresolved alternative has U_upper < max U − tol | — | an unresolved alternative could still win (or nothing is ranked) |
| evidence_current | no supporting evidence past `valid_until` (judged against `as_of`) | — | some supporting evidence expired |
| evidence_traceable | all supporting evidence has a non-empty `source_ref` | some has none | — |
| assumptions_validated | every critical assumption is validated (the default) | a critical assumption is invalid | a critical assumption is unvalidated |
| risks_treated | every risk with likelihood × consequence ≥ threshold (default 12) is mitigated, or accepted by a role in `risk_acceptance_roles` | a risk at threshold is `unacceptable`, or accepted by a role not allowed | a risk at threshold is untreated (the default) |
| review_status | the review is `pass` and not stale | the review is `fail` | the review is pending, or stale |
| omitted_options | at least `min_alternatives` (default 2) alternatives, and at least one not incumbent | otherwise | — |
| distinct_measures | no two criteria score the same measure | two do | — |

A review is **stale** when any object it `covers` has changed hashable content since the review was
recorded. Covers can be ids or `all:<collection>` selectors, and the selector is resolved in the
current state, so adding an alternative changes what `all:alternatives` covers and makes the review
stale.

**Readiness** looks only at conditions with `required` true (the default):

- **blocked** if any of them fails;
- otherwise **pending** if any is unknown;
- otherwise **ready**.

## Approval and actionable

A decision is **actionable** when all of these hold:

1. readiness is ready;
2. there is exactly one recommended alternative;
3. some approval approves that alternative, was given by a role in `approval_roles`, has not passed
   its `valid_until`, and its stamped state digest equals the current one.

The **state digest** covers the hashable content of every object except approvals. It does not cover
the clock. So any change to hashable content anywhere invalidates every existing approval, even when
the recommendation is unchanged. This is the conservative policy. Moving only the clock leaves
approvals matching, although an expiry may then change readiness. Editing only prose or other
unhashable slots also leaves them matching.

## Programs, episodes and change sets

A program has a baseline episode (`baseline_episode`, built from `baseline/`) and change sets in
`changes/`, each producing an episode from its `parent`:

```yaml
id: ep:e05                  # the episode this produces
parent: ep:e04
as_of: 2027-06-01           # optional: moves the clock
trigger: evidence           # TriggerKind
change_description: Test engineer accepts the new reliability report.
author_role: role:test-engineer
assign:                     # set one slot of one existing object
  - {target: obs:B.reliability-2, slot: applicability, new_value: accepted}
add:                        # new objects: a partial decision state
  approvals:
    - {id: appr:e05-b, approved_alternative: alt:B, approver_role: role:mda}
remove: [obs:B.reliability]  # ids to delete
```

Operations apply in the order remove, add, assign. The result must validate (schema and referential
checks), or the change set is rejected.

Reviews and approvals that a change set adds, or whose slots it assigns, are stamped by the kernel
with the episode and the digest they cover:

- a review with the digest of its covered objects;
- an approval with the state digest of the resulting state.

Reviews and approvals already in the baseline are stamped at the baseline. Authors never write
digests.

## Benchmark outcome classes

Compare full evaluations of a parent episode and a child episode, and take the first class that
applies:

1. **empty_feasible_set**: the child's feasible set is empty.
2. **changed_winner**: the child's recommended set differs from the parent's.
3. **recompute_same_winner**: the recommendation is the same, but some decision number changed: a
   measure value, a check's g or status, a value score, a utility or bound, or a feasibility status.
4. **readiness_only**: none of the above, but the readiness state or actionable changed.
5. **no_material_change**: nothing above changed.

Note that, because of the conservative approval policy, a child of an approved (actionable) parent
whose change alters any hashable content outside approvals loses actionability. That is at least
`readiness_only`.
