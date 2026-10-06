# FXAssist Lite. Run `make help` for the target list.
# Designed for GNU make inside WSL2 (ADR-019) and in CI on Linux.

SHELL := /usr/bin/env bash
.SHELLFLAGS := -euo pipefail -c
.DEFAULT_GOAL := help
MAKEFLAGS += --no-print-directory

# Raise this as phases are completed; `make bootstrap` then requires those tools.
PHASE ?= 0

# Use .env when present, otherwise the committed defaults (ENV-04: ports come from here).
ENV_FILE ?= $(if $(wildcard .env),.env,.env.example)
COMPOSE := docker compose --project-directory . -f deploy/compose/compose.yaml --env-file $(ENV_FILE)

GITLEAKS_IMAGE := docker.io/zricethezav/gitleaks:v8.30.1

# Placeholder recipe for targets whose phase has not been built yet.
define not_yet
	@echo "make $@: not built yet, arrives in Phase $(1) (see CLAUDE_CODE_PROMPT.md)." >&2; exit 2
endef

.PHONY: help bootstrap install lint fmt test secrets-scan check \
        up up-full down down-volumes ps logs \
        ingest ask eval kind-up kind-deploy demo

##@ Setup
help: ## Show this help
	@awk 'BEGIN {FS = ":.*##"} \
		/^##@/ { printf "\n\033[1m%s\033[0m\n", substr($$0, 5); next } \
		/^[a-zA-Z_-]+:.*##/ { printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)
	@echo

bootstrap: ## Check prerequisites and print the exact fix for anything missing (PHASE=n)
	@bash scripts/bootstrap.sh --phase $(PHASE)

install: ## Create the virtualenv from uv.lock and install the git hooks
	uv sync --locked
	uv run pre-commit install

##@ Quality
lint: ## Lint and format-check Python code
	uv run ruff check .
	uv run ruff format --check .

fmt: ## Auto-format and auto-fix Python code
	uv run ruff format .
	uv run ruff check --fix .

test: ## Run the test suite
	uv run pytest

secrets-scan: ## Scan the full git history for secrets with gitleaks (SAF-07)
	docker run --rm -v "$(CURDIR):/repo" -w /repo --entrypoint sh $(GITLEAKS_IMAGE) \
		-c 'git config --global --add safe.directory /repo && gitleaks git /repo --redact --verbose'

check: lint test ## Everything CI runs that needs no Docker
	uv lock --check

##@ Local stack (Docker Compose)
up: ## Start the lite stack and wait until every service is healthy
	$(COMPOSE) up -d --wait

up-full: ## Start the full stack (adds observability from Phase 3)
	COMPOSE_PROFILES=full $(COMPOSE) up -d --wait

down: ## Stop the stack (keeps data volumes)
	COMPOSE_PROFILES=full $(COMPOSE) down

down-volumes: ## Stop the stack and delete its data volumes
	COMPOSE_PROFILES=full $(COMPOSE) down --volumes

ps: ## Show stack status
	$(COMPOSE) ps

logs: ## Follow stack logs
	$(COMPOSE) logs -f --tail=100

##@ RAG (Phase 1)
ingest: ## Fetch, chunk, embed and store the documents in Qdrant
	$(call not_yet,1)

ask: ## Ask one question: make ask Q="What is a margin call?"
	$(call not_yet,1)

eval: ## Run the evaluation set and print scores
	$(call not_yet,1)

##@ Kubernetes (Phase 4)
kind-up: ## Create the local kind cluster
	$(call not_yet,4)

kind-deploy: ## Build, load and deploy the Helm chart to kind
	$(call not_yet,4)

##@ Demo
demo: ## End-to-end demo of the whole system
	$(call not_yet,2)
