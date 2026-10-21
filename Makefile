SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
PYTHON ?= python3.12
VENV := .venv
BIN := $(VENV)/bin

.PHONY: help install lint format typecheck test check demo craft record live calibrate calibrate-live secret-scan clean

help: ## List the commands
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-15s %s\n", $$1, $$2}'

install: ## Create .venv and install the package with dev tools
	$(PYTHON) -m venv $(VENV)
	$(BIN)/pip install -q --upgrade pip
	$(BIN)/pip install -q -e '.[dev]'

lint: ## Ruff lint and format check
	$(BIN)/ruff check .
	$(BIN)/ruff format --check .

format: ## Apply ruff formatting
	$(BIN)/ruff format .
	$(BIN)/ruff check --fix .

typecheck: ## mypy in strict mode
	$(BIN)/mypy

test: ## Unit and integration tests with coverage (no network)
	$(BIN)/pytest

check: lint typecheck test ## Everything CI runs

demo: ## Replay v1 and v2 on cassettes, compare, gate. Saves docs/demo-output.txt
	@mkdir -p docs
	$(BIN)/vae demo | tee docs/demo-output.txt

craft: ## Rebuild hand-crafted cassettes from cassettes/handcrafted-src
	$(BIN)/vae craft

record: ## Re-record v1 and v2 cassettes from the live API (needs ANTHROPIC_API_KEY, costs money)
	$(BIN)/vae run --agent v1 --mode record
	$(BIN)/vae run --agent v2 --mode record

live: ## One live run of v2, no cassettes written (needs ANTHROPIC_API_KEY)
	$(BIN)/vae run --agent v2 --mode live --baseline baselines/v2.json

calibrate: ## Agreement of the blind grading pass with the labels
	$(BIN)/vae calibrate --verdicts calibration/blind-pass-1/verdicts.json

calibrate-live: ## Agreement of the live judge model with the labels (needs ANTHROPIC_API_KEY)
	$(BIN)/vae calibrate --live

secret-scan: ## Regex scan of the full git history for key patterns
	$(BIN)/python scripts/secret_scan.py

clean: ## Remove generated reports and caches
	rm -rf reports .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov
