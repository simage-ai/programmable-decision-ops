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
  value count. If they all agree (within a relative 1e-12), that is *y*. If accepted values
  conflict, *y* is unknown. If the only
  remaining observations are `provisional`, *y* is unknown (pending review). `rejected` observations
  are ignored. No usable observation means unknown.
- **An evaluation specification** that outputs *m* for *a*. It runs on its bound inputs: a measure
  binding reads *y(a, measure)*, and a ref binding reads an assumption's `value` (a ref to anything
  but an assumption is an error). If any input is unknown, or the evaluator raises or returns a
  non-finite number, *y* is unknown. See "Evaluators" below for what an evaluator may depend on. The vehicle example
  computes range = tank_gal × mpg × terrain_factor (A 40 × 8 = 320, B 45 × 8 = 360, C 47.5 × 8 = 380 at
  terrain 1.0).
- **Nothing:** *y* is unknown.

Each *(a, m)* has at most one producer. Observations and a specification, or two specifications,
producing the same pair is an error, not a precedence rule.

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

The declared `rule_kind` matters for authorization. Under `additive_mavt` the recommendation above
is the decision. Under `expected_mavt` it is the decision only after `pdo uncertainty` evaluates the
distributions, so the point-value ranking is reported but provisional: `recommendation_exists` is
unknown (with a `run_uncertainty_analysis` action), which keeps readiness pending.

The vehicle baseline gives U_A = 0.466667, U_B = 0.533333 and U_C = 0.62. C fails min reliability
(g = +0.01) and is infeasible, so the recommendation is {B}.

## Readiness conditions

Each condition evaluates to pass, fail or unknown. "Supporting evidence" means the evidence behind the
accepted, valued observations of the *recommended* alternatives (all of their measures).

| predicate | pass | fail | unknown |
|---|---|---|---|
| recommendation_exists | recommendation non-empty (additive_mavt) | empty and nothing unresolved | empty, something unresolved; or the rule is expected_mavt |
| unique_recommendation | exactly one recommended | none recommended and nothing unresolved | tie (two or more), or none recommended but something unresolved |
| coverage | nothing unresolved, or every unresolved alternative has U_upper < max U − tol | — | an unresolved alternative could still win (or nothing is ranked) |
| evidence_current | no supporting evidence past `valid_until` (judged against `as_of`) | — | some supporting evidence expired |
| evidence_traceable | all supporting evidence has a non-empty `source_ref` | some has none | — |
| assumptions_validated | every critical assumption is validated (the default) | a critical assumption is invalid | a critical assumption is unvalidated |
| risks_treated | every risk with likelihood × consequence ≥ threshold (default 12) is mitigated, or accepted by a role in `risk_acceptance_roles` | a risk at threshold is `unacceptable`, or accepted by a role not allowed | a risk at threshold is untreated (the default) |
| review_status | the review is `pass` and not stale | the review is `fail`, stale or not | the review is pending, or it passed and is stale |
| omitted_options | at least `min_alternatives` (default 2) alternatives, and at least one not incumbent | otherwise | — |
| distinct_measures | no two criteria score the same measure | two do | — |
| locators_current | every observation (not rejected) whose evidence is an indexed document is located in that document's current version | — | some cite an earlier version (their section changed in a revision), or have no locator |

Supporting evidence is limited to the recommended alternatives: the evidence behind a runner-up's
utility, or behind the check that excludes an infeasible alternative, is not tested. That is a
deliberate scope for this version, not a guarantee about the rest of the evidence.

A review is **stale** when any object it `covers` has changed hashable content since the review was
recorded. Covers can be ids or `all:<collection>` selectors, and the selector is resolved in the
current state, so adding an alternative changes what `all:alternatives` covers and makes the review
stale. A review cannot cover reviews or approvals.

**Readiness** looks only at conditions with `required` true (the default):

- **blocked** if any of them fails;
- otherwise **pending** if any is unknown;
- otherwise **ready**.

## Approval and actionable

A decision is **actionable** when all of these hold:

1. readiness is ready;
2. there is exactly one recommended alternative;
3. some approval approves that alternative, was given by a role in `approval_roles`, has not passed
   its `valid_until`, and its stamped state digest equals the current one. An approval recorded
   without `valid_until` expires `approval_validity_days` (from the policy) after the `as_of` of the
   episode that recorded it.

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

Operations apply in the order remove, add, assign. Removing and adding the same id in one change
replaces that object. `assign` cannot change an id or a kernel stamp, and `add` cannot carry `as_of`
(set the change set's own `as_of` to move the clock). The result must validate (schema and referential
checks), or the change set is rejected.

Reviews and approvals are stamped by the kernel with the episode and the digest they cover:

- a review with the digest of its covered objects;
- an approval with the state digest of the resulting state.

A change set stamps the reviews and approvals it adds, and those where it assigns a new judgement: a
review's `review_status`, or an approval's `approved_alternative`, `approver_role` or `valid_until`.
Assigning anything else (rationale, names, `covers`) leaves the stamp alone, so a stale review or
approval stays stale until someone records a new judgement. Reviews and approvals in the baseline are
stamped at the baseline.

Authors never write digests: a baseline, state file or change set that contains `covered_digest`,
`state_digest` or `recorded_in_episode` is rejected, so a digest copied from `pdo run` output cannot
make a decision actionable.

A plain state (a file or directory without `program.yaml`) is treated as a baseline: its reviews and
approvals are stamped against it when it is run. It has no history, so nothing in it can be stale;
staleness and approval matching across changes need a program.

## Document provenance

An evidence item may name an indexed document (`document_path`, a .docx, .pdf, .md or .txt file in the
program directory) and record its version (`document_digest`). A document is indexed into sections: the
body text between one heading and the next, with a heading path and an id. The id is the number printed in
the heading ("3.2 Vehicle B" has id "3.2") when the headings carry numbers (an unnumbered or repeated one
gets "s" and its outline position), else the outline position. Word headings come from heading styles,
PDF headings from the outline (bookmarks; without one, numbered heading lines whose numbers form a
continuing chain), Markdown headings from `#` or underlined titles. Text is normalized before digests are
taken: superscript digits kept as exponents (10⁵ is "10^5"), NFKC, typographic quotes and dashes folded,
whitespace collapsed, Markdown inline markup removed, PDF line-break hyphenation joined (a hyphen that
belongs to the text, as in "MIL-STD-810" or "2,000-mile", is kept), and PDF page furniture dropped: the
printed title, and a first or last line repeated on most pages (both of two) that is identical on each or
differs only in a standalone number tracking the page ("Page 3 of 9"), unless it prints an outline
heading.

- A **section digest** covers the section's body words, not its heading or paragraph breaks, and ignores a
  hyphen between two letters when the second is lowercase. So renaming, renumbering or moving a section,
  a different paragraph split by a PDF parser, or a word hyphenated across a PDF line leaves it unchanged,
  while any other edit to its words changes it (one exception: a hyphenated and a closed spelling of the
  same letters, such as "re-sign" and "resign", count as the same). A report with headings, paragraphs and simple tables gives
  the same section digests as Word, PDF and Markdown (the example's reports do); complex layouts (merged
  table headers, footnotes, multi-column pages) may not.
- The **document digest** covers every heading path and section digest, so any edit is a new version.

An observation taken from such a document carries a **locator**: the file and document digest it was
made against, the section id, heading path and digest, the page (PDF) and paragraph, and the exact quote
with up to 32 characters either side and its character span in the section text (a W3C Web Annotation
TextQuoteSelector and TextPositionSelector). The tools compute locators; `pdo docs locate` and `pdo docs
extract` refuse a quote that is not in the document, that occurs more than once in the searched scope, or
that cuts a word or number ("0.9" is not a quote of "0.98", nor "10" of "10^5" or "1.2" of "1.2×10^-3").
`pdo docs extract` also refuses a quote that does not state the observation's value, and locating a value
whose evidence is already indexed but whose locator is missing or on an earlier version, unless a person
accepts it: `accept`, with a role that exists and owns the value (the observation's owner, else its
evidence's owner), recorded in the observation's applicability rationale. Values it adds or changes
without acceptance are provisional. Other change sets (`pdo_apply_change`) may not set locators or
document digests; only the docs tools compute them.

**Locators are unhashable.** A locator locates a value without being part of it (as DAPPER treats
representation locators), so it is not in the observation's content digest, the state digest or any
review's covered digest, and changing it re-runs no measure, check, value, utility or decision. The
`locators_current` condition reads the locators and document digests through its own local data,
so a locator change re-evaluates that condition, readiness and actionable, and nothing else.
`document_path` and `document_digest` on evidence are unhashable for the same reason; whole-file
identity stays with the hashable `content_sha256`, which should be left empty for a document whose
values are located, so that a revision is judged section by section.

**Revisions.** `pdo docs revise` absorbs a new version of a document, placed beside the old file (a file
copied over the old one is refused), as a change set. It sets `document_path` and `document_digest` on
every evidence item naming the old file. A locator moves to the new version only when its section's text
is unchanged there: a section still at its heading path is the same section (unchanged, or changed, and
then it is never matched to another section that now holds its old text); a section whose heading path is
gone moved to the one section with its digest that does not continue another old section (none: removed;
several: ambiguous). Every other locator stays on the old version, and `locators_current` is unknown
until a person reviews that value and re-anchors it (`pdo docs extract` with the observation's id,
accepted with their role): readiness is pending and the pending action `review_locator` goes to the
observation's owner, else its evidence's owner. A value from the document without a locator is flagged
the same way. A revision changes no value, so the state digest is unchanged and every approval still
matches; when the person corrects a value, the value change is material as usual.

`pdo docs verify` (also run by `pdo validate`; `pdo package` refuses to package when a readable document
contradicts a locator) checks the files, and fails when it could not read them. Errors: a
missing or unreadable document; an evidence item whose file is not the version it indexed (a file replaced
in place); a current locator whose file is another version, whose quote is not at its recorded span, or
whose recorded heading path, page, paragraph, prefix or suffix the document does not give. Warnings: a
current quote that does not state the observation's value; a value from an indexed document without a
locator; problems with locators on earlier versions, which are checked against their own (earlier) files;
and Word or PDF files when the docs extra is not installed.

## Benchmark outcome classes

Compare full evaluations of a parent episode and a child episode, and take the first class that
applies:

1. **empty_feasible_set**: the child's feasible set is empty.
2. **changed_winner**: the child's recommended set differs from the parent's.
3. **recompute_same_winner**: the recommendation is the same, but some decision number changed: a
   measure value, a check's g or status, a value score, a utility or bound, or a feasibility status.
   A decision number that exists in only one of the two evaluations (a new requirement's checks, a new
   alternative's values, a removed observation's measure) counts as changed.
4. **readiness_only**: none of the above, but the readiness state or actionable changed.
5. **no_material_change**: nothing above changed.

Note that, because of the conservative approval policy, a child of an approved (actionable) parent
whose change alters any hashable content outside approvals loses actionability. That is at least
`readiness_only`.

## Evaluators

An evaluator receives exactly its declared inputs and parameters as keyword arguments, nothing else.
Every parameter of the function must be declared, and every declared input must be a parameter (unless
the function takes `**kwargs`); otherwise the run fails and its measure is unknown. A default value can
therefore never stand in for an input the graph does not track. The evaluator's code identity is the
digest of its module's source content: editing the module, including a helper in it, is a changed input,
and a long-running process reloads it. Evaluators must not read files, globals, the clock or the
network. That is not enforced, and an evaluator that does so can break selective re-evaluation.

## Flip points and robustness

`pdo flip` reports, for every numeric source upstream of the decision (observation and assumption
values, requirement thresholds), the value at which the outcome changes with everything else held
fixed. The outcome is the recommended set, plus whether the feasible set is empty.

- **Range.** The scan covers an assumption's plausible range when it has one, otherwise ± `span` of
  the value.
- **Locating the boundary.** A grid is scanned, and each bracket where the outcome changes is
  bisected.
- **Exact refinement.** When the crossing quantity is affine in the parameter (the pairwise utility
  margin, or the g that changes status, checked at two interior points to within a relative 1e-10),
  the boundary is solved exactly (`affine_exact`).
- **Verification.** Every boundary is re-run at p* ± 1e-6 of the range.
- **Boundary kinds:** preference (another feasible alternative becomes best), feasibility, empty_set
  and unresolved.
- **Weights.** Weight trade-offs are closed form: weight shifts from criterion l to k, with the others
  fixed, over [0, w_k + w_l].

A grid can miss a flip that happens and reverses within one cell.

`pdo scenarios` evaluates each declared scenario. The recommendation is **robust** when it is a single
alternative that remains the only one recommended in every scenario. That is a claim about the tested
scenarios only.

## Decisions under uncertainty

`pdo uncertainty` samples every observation and assumption that carries an `UncertaintySpec`. Each
uncertain object has its own stream of uniforms, seeded from digest(seed, object id), and draws are
taken by inverse CDF, never `random.gauss`. These are common random numbers: alternatives and scenarios
share draws, and editing one object leaves every other object's draws unchanged.

Each draw is the point state with the drawn values assigned, evaluated by the same kernel. Over N draws:

- E[U](a) is the mean utility, with standard error sd/√N.
- P_F(a) is the share of draws in which every mandatory check of *a* passes.
- *a* is chance-feasible when P_F(a) ≥ 1 − `chance_alpha` (default 0). A draw with an unknown value
  widens P_F instead of counting as a pass or a fail.
- The expected_mavt recommendation is the argmax of E[U] over chance-feasible alternatives.

The same seed reproduces the same result. The point-value decision is reported alongside for
contrast.

## Sensitivity analysis

`pdo sensitivity` describes how the decision responds to its inputs, beyond the single value where it
flips.

**Inputs** are written `<object id>.<slot>`:

- an observation's or assumption's `value`;
- a requirement's `threshold_value`;
- a criterion's `anchor_worst` or `anchor_best`;
- an evaluator parameter, as `<spec id>.<arg>` (evaluator parameters never collide with slot names).

An input's **range** is the first of these that applies:

1. the 5th to 95th percentile of its declared distribution;
2. its plausible range;
3. its value ± `span` (default 10%).

A criterion anchor's default range stops halfway to the other anchor, and an explicit range that reaches
the other anchor is refused. An input whose value is 0, with no distribution or plausible range, has no
range: give `low` and `high`, otherwise the tornado lists it as skipped.

**Value of a choice and the decision margin.** Each alternative in each draw gets a value: its utility
when feasible, and `infeasible_value` (default 0, never positive) when a mandatory check fails. An
unknown value is left out and counted.

The **decision margin** is the baseline recommendation's value minus the best competitor's optimistic
value. For a tie, the recommendation's value is the best value in the tied set. The optimistic value is:

- `infeasible_value` for a competitor that fails a check;
- its utility, when only its feasibility is unknown;
- its utility upper bound, when its utility is unknown.

This is the same rule as the coverage condition, so an unresolved competitor can only shrink the margin.
A negative margin means another alternative could win.

- **Response curve.** One input is swept over its range with everything else fixed. It reports utility
  and feasibility per alternative, the points where the recommendation changes (found by bisection), the
  slope and elasticity at the input's point value, and how far each utility curve departs from a
  straight line.
  - Change points are reported in increasing order of the input.
  - A change point is where the recommendation leaves its current set. At a preference crossing that is
    τ/|slope| before the exact crossing, τ being the tie tolerance.
  - At a feasibility boundary it is offset by the requirement's `check_tolerance`, on either side.
- **Distribution.** One input is drawn from its distribution (or uniformly over its range), with
  everything else fixed, using the same per-object streams as `pdo uncertainty`. It reports:
  - each alternative's utility mean, sd, and 5th/50th/95th percentiles;
  - P(feasible) and P(win), with ties split evenly;
  - P(no recommendation) and P(the recommendation differs from the baseline);
  - expected regret (the best value minus its own);
  - the number of draws with an unknown value.
- **Tornado.** Each input is set to the low and the high end of its range, one at a time, and inputs are
  ranked by the largest change in the decision margin. It also scans 9 points across each range, so a
  recommendation change inside the range is flagged even when both ends agree.
- **Global indices.** All inputs that declare distributions are drawn jointly in a Saltelli design:
  matrices A and B, plus A with column i taken from B, for N(k + 2) probes (default N = 512).
  - First-order index: the Saltelli (2010) estimator on centered outputs. It is exactly 0 for an input
    with no effect.
  - Total index: Jansen (1999).
  - Spearman rank correlations come from the same sample.
  - The decision margin's indices carry 90% bootstrap intervals. Estimates can leave [0, 1] by sampling
    noise; the interval shows by how much.
- **Value of information.** Computed for the expected-value choice under the infeasible-value penalty,
  without a chance constraint, from the 2N draws of A and B.
  - EVPI = E[max_a V(a)] − max_a E[V(a)].
  - EVPPI per input uses **cross-fitted quantile binning**. Plain binning (the best mean in each bin of
    the input) is biased upward, because the maximum of noisy means exceeds the maximum of true
    conditional means.
  - Cross-fitting splits the draws in two folds by draw index, chooses in each bin on one fold, scores
    on the other, and averages both directions. The estimate can be slightly negative for an input that
    does not matter.
  - Each EVPPI carries a 90% Poisson-bootstrap interval. An input is **worth resolving** when its
    interval excludes zero, and only those name the evidence to collect next.
- **Replay.** A Decision Package records the analysis settings (seed, samples, span, infeasible value)
  with its sensitivity section, and `pdo replay` re-runs the analysis and checks it reproduces.

Every probe is the point graph with the varied source nodes rebuilt, run in cutoff mode, so it equals
compiling the assigned state but re-runs only what the varied inputs reach. Results report the probes
and the share of computed nodes each one re-ran.
