# FXAssist Lite. Run `make help` for the target list.
# Designed for GNU make inside WSL2 (ADR-019) and in CI on Linux.

SHELL := /usr/bin/env bash
.SHELLFLAGS := -euo pipefail -c
.DEFAULT_GOAL := help
MAKEFLAGS += --no-print-directory

# Raise this as phases are completed; `make bootstrap` then requires those tools.
PHASE ?= 1

# Use .env when present, otherwise the committed defaults (ENV-04: ports come from here).
ENV_FILE ?= $(if $(wildcard .env),.env,.env.example)
-include $(ENV_FILE)
FXA_LLM_MODEL ?= qwen2.5:3b-instruct

# ADR-022: give Ollama the GPU when nvidia-smi works. Override with FXA_GPU=0 or FXA_GPU=1.
FXA_GPU ?= $(if $(shell nvidia-smi -L 2>/dev/null),1,0)
COMPOSE_FILES := -f deploy/compose/compose.yaml $(if $(filter 1,$(FXA_GPU)),-f deploy/compose/compose.gpu.yaml)
COMPOSE := docker compose --project-directory . $(COMPOSE_FILES) --env-file $(ENV_FILE)
FXA := uv run fxassist

GITLEAKS_IMAGE := docker.io/zricethezav/gitleaks:v8.30.1

# Placeholder recipe for targets whose phase has not been built yet.
define not_yet
	@echo "make $@: not built yet, arrives in Phase $(1) (see CLAUDE_CODE_PROMPT.md)." >&2; exit 2
endef

.PHONY: help bootstrap install lint fmt test secrets-scan check \
        up up-lite up-full pull-model down down-volumes ps logs \
        fetch ingest ask eval experiment sources-md kind-up kind-deploy demo

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

test: ## Run the test suite (no Docker, no network, no model needed)
	uv run pytest

secrets-scan: ## Scan the full git history for secrets with gitleaks (SAF-07)
	docker run --rm -v "$(CURDIR):/repo" -w /repo --entrypoint sh $(GITLEAKS_IMAGE) \
		-c 'git config --global --add safe.directory /repo && gitleaks git /repo --redact --verbose'

check: lint test ## Everything CI runs that needs no Docker
	uv lock --check

##@ Local stack (Docker Compose)
up: ## Start data stores and Ollama (GPU if available), wait until healthy
	@echo "Ollama GPU: $(if $(filter 1,$(FXA_GPU)),on,off) (set FXA_GPU=0 or 1 to override)"
	COMPOSE_PROFILES=llm $(COMPOSE) up -d --wait --build

up-lite: ## Start the data stores only, for low-RAM machines (ENV-02)
	$(COMPOSE) up -d --wait

up-full: ## Start everything, including observability from Phase 3
	COMPOSE_PROFILES=llm,full $(COMPOSE) up -d --wait --build

pull-model: ## Download the local dev model into Ollama (FXA_LLM_MODEL)
	COMPOSE_PROFILES=llm $(COMPOSE) exec ollama ollama pull $(FXA_LLM_MODEL)

down: ## Stop the stack (keeps data volumes)
	COMPOSE_PROFILES=llm,full $(COMPOSE) down

down-volumes: ## Stop the stack and delete its data volumes (including the model)
	COMPOSE_PROFILES=llm,full $(COMPOSE) down --volumes

ps: ## Show stack status
	COMPOSE_PROFILES=llm,full $(COMPOSE) ps

logs: ## Follow stack logs
	COMPOSE_PROFILES=llm,full $(COMPOSE) logs -f --tail=100

##@ RAG (Phase 1)
fetch: ## Download the documents in data/sources.yaml into data/raw
	$(FXA) fetch

ingest: ## Fetch, extract, chunk, embed and store the documents in Qdrant
	$(FXA) ingest

ask: export FXA_Q = $(Q)
ask: ## Ask one question: make ask Q="What is a margin call?"
	@test -n "$$FXA_Q" || { echo 'usage: make ask Q="your question"' >&2; exit 2; }
	@$(FXA) ask "$$FXA_Q"

eval: ## Run the evaluation set against the local model and print scores
	$(FXA) eval

experiment: ## Retrieval-only chunk size x top-k experiment (DAT-09)
	$(FXA) experiment

sources-md: ## Regenerate data/SOURCES.md from data/sources.yaml
	$(FXA) sources-md

##@ Kubernetes (Phase 4)
kind-up: ## Create the local kind cluster
	$(call not_yet,4)

kind-deploy: ## Build, load and deploy the Helm chart to kind
	$(call not_yet,4)

##@ Demo
demo: ## End-to-end demo of the whole system
	$(call not_yet,2)
