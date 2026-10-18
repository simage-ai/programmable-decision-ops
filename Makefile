SCHEMA := schema/pdo.yaml
RUN := uv run
PROPOSAL_DIR ?= ../papers-and-proposals/src/army-nv012

.PHONY: install lint gen-project test bench proposal-values sensitivity-figures all

install:
	uv sync --all-extras

lint:
	$(RUN) linkml-lint --ignore-warnings $(SCHEMA)
	$(RUN) python scripts/gen_identity.py --check
	$(RUN) ruff check src tests scripts

# Generated artifacts are committed; CI regenerates them and fails on a diff.
gen-project:
	mkdir -p project/jsonschema project/pydantic
	$(RUN) gen-json-schema --top-class DecisionState $(SCHEMA) > project/jsonschema/pdo_state.schema.json
	$(RUN) gen-json-schema --top-class DecisionEpisodeRecord $(SCHEMA) > project/jsonschema/pdo_record.schema.json
	$(RUN) gen-pydantic $(SCHEMA) > project/pydantic/pdo_model.py
	cp project/pydantic/pdo_model.py src/pdo/model.py
	$(RUN) python scripts/gen_identity.py

test:
	$(RUN) pytest -q

bench:
	$(RUN) pdo bench run bench/vehicle

# Proposal numbers: results/proposal/pdo_values.tsv, also copied into the proposal version directory.
proposal-values: bench
	$(RUN) python -m scripts.proposal_values --proposal-dir $(PROPOSAL_DIR)

# Sensitivity figures for the proposal (seeded, so reproducible on one machine).
sensitivity-figures:
	$(RUN) pdo sensitivity examples/fires_sensor --samples 256 --figures results/figures/fires > /dev/null
	$(RUN) pdo sensitivity examples/fires_sensor -p asm:threat-rcs-factor.value --figures results/figures/fires > /dev/null
	$(RUN) pdo sensitivity examples/vehicle -p obs:B.cost.value --figures results/figures/vehicle > /dev/null

all: lint test
