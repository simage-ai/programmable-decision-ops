# programmable-decision-ops: a guide for agents

Decisions as programs: a LinkML decision schema, a deterministic kernel (package and CLI `pdo`), an MCP
server and agent skills. A decision is typed objects; the kernel compiles them into a dependency graph and
computes the recommendation, feasibility, utilities, readiness, authorization and pending human actions.
It is the open-source feasibility artifact for an Army SBIR proposal (topic ARM26BX06-NV012), which lives in
a separate repository, `../papers-and-proposals/src/army-nv012/`.

README.md is the user guide; this file is what you need to work on the code. Milestones M1 (kernel and
benchmark), M2 (sensitivity), M3 (dashboard), M4 (document provenance) and M5 (onboarding, paper scenarios)
are merged (PRs #1 to #6).

## Where things are

| Path | What |
|---|---|
| `schema/*.yaml` | The LinkML schema. Every slot carries a `hashable` or `unhashable` mixin |
| `src/pdo/model.py`, `identity_slots.py`, `project/` | Generated from the schema by `make gen-project`; committed, never edited by hand |
| `src/pdo/graph.py`, `run.py`, `builtins.py`, `episodes.py`, `actions.py` | The kernel: compile, evaluate, episodes as change sets, pending actions |
| `src/pdo/flip.py`, `sensitivity.py`, `uncertainty.py`, `package.py` | What flips the decision, sensitivity and value of information, expected utility, Decision Packages |
| `src/pdo/docs.py`, `provenance.py`, `sysml.py` | Reports indexed into sections with locators; the SysML v2 importer |
| `src/pdo/onboard.py`, `study.py` | `pdo init`, `pdo onboard` (three funnels, single next step), `pdo study` |
| `src/pdo/dashboard.py`, `dashboard.html` | The read-only dashboard: data export and the self-contained page (docs/dashboard.md) |
| `src/pdo/tools.py`, `mcp_server.py`, `cli*.py` | The same operations as Python functions, MCP tools (`pdo_<name>`) and CLI commands |
| `src/pdo/bench/` | The perturbation and provenance benchmarks, the paper scenarios and their figures |
| `examples/vehicle`, `examples/fires_sensor` | The two example decisions (vehicle: trade study and 21-episode program; Fires: uncertainty) |
| `bench/` | Benchmark suites with independent hand labels |
| `results/` | Generated tables, figures and proposal values, committed (deterministic, rewritten only when changed) |
| `skills/` | Agent skills: decision-onboarding, sysml-to-decision, document-provenance |
| `docs/semantics.md` | The specification the kernel and the benchmark labels are written against |

## Commands

```sh
make install            # uv sync --all-extras
make lint               # linkml-lint, identity markers, ruff (line length 120)
uv run pytest -q        # about 2.5 minutes
make gen-project        # after any schema change; CI fails if generated files drift
make bench              # perturbation and provenance benchmarks -> results/bench/
make scenarios          # the paper's scenarios: claims, data, figures, captions -> results/scenarios/
make dashboards         # dashboards of both examples in site/ (not committed)
make proposal-values    # proposal numbers -> results/proposal/pdo_values.tsv, copied into the proposal
make proposal-figures   # the proposal's three figures, copied into the proposal
```

CI (.github/workflows/test.yml) runs lint, the tests, the codegen freshness check, `pdo bench check
bench/vehicle` and `pdo bench provenance --check`. Run the same before you call work done.

## Rules

- **Every number is the kernel's.** Tools, the dashboard, figures and captions read kernel results; they
  never compute or adjust a utility, margin or status themselves.
- **Unknown stays unknown.** A missing value, an unaccepted observation or a failed evaluator is never an
  invented pass or fail.
- **Judgements belong to people.** Framing (criteria, weights, anchors, readiness), acceptances, reviews and
  approvals come from the person who holds the role; agents propose, and their values enter as provisional.
- **Episodes are never edited.** Every later change, including an approval, is a new change set.
- **Results are deterministic and committed.** Tables and figures are written only when their content
  changes, with no dates or machine-specific values (timing goes to `timing.tsv` only).
- **Work on a branch; merge by pull request with a merge commit.** Commit or push only when asked.
- **The proposal is a separate repository.** Do not commit proposal prose or proposal-specific content
  here. The proposal receives numbers and figures only through `make proposal-values` and
  `make proposal-figures`, which copy into `../papers-and-proposals/src/army-nv012/`; committing there is
  a separate step in that repository, with the user's go-ahead.

## The dashboard

`pdo dashboard PATH -o DIR [--fragment]` writes `index.html` (data embedded), `dashboard.json` and, with
`--fragment`, a page without its html/body wrapper for hosts that supply one. `src/pdo/dashboard.py` builds
the data (content-addressed: each distinct value and object once); `src/pdo/dashboard.html` is the page
template, plain JavaScript with no build step. Two views: Decision (default, for decision makers and
reviewers) and Engineering (the compiled graph). docs/dashboard.md describes both and dashboard.json.

- The Decision view speaks in the kernel's terms: ids, values and formulas. Do not add generated sentences
  ("Why we recommend A over B"); the user rejected them as canned. The only prose is what people wrote.
- Build the DOM with `textContent` (never `innerHTML`), and keep `[hidden]{display:none!important}`.
- Episode links: `#e06` (Decision) and `#e06.engineering`.
- tests/test_dashboard.py checks the data and anchors in the template; update it with the page.
- Check the page in a browser at desktop and phone width before calling a change done.

## Paper figures

`pdo bench scenarios` (src/pdo/bench/scenarios.py, scenario_figures.py) runs six scenarios; each checks
the claims it supports and writes its data, a figure (PNG and PDF) and `caption.md`. The proposal uses
three: trade-study, program-refresh and perturbations (scripts/proposal_figures.py, ids with underscores
because the proposal builder requires them). report-revisions and uncertainty stay generated, outside that
set; onboarding has data and claims but no figure.

Figures read like paper figures: panel letters, axis labels and legends only, with the explanation in the
caption, which is written from the same results. 6.5 in wide. The alternatives keep one color and marker
in every figure (a colorblind-validated palette), and figures are byte-identical across runs.
