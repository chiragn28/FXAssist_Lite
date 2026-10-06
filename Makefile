# FXAssist Lite. Run `make help` for the target list.
# Designed for GNU make inside WSL2 (ADR-019) and in CI on Linux.

SHELL := /usr/bin/env bash
.SHELLFLAGS := -euo pipefail -c
.DEFAULT_GOAL := help
MAKEFLAGS += --no-print-directory

# Raise this as phases are completed; `make bootstrap` then requires those tools.
PHASE ?= 3

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
        up up-lite up-mock pull-model down down-volumes ps logs \
        fetch ingest ask eval experiment sources-md \
        serve mock-llm api-key drill load dashboard kind-up kind-deploy demo

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
# Which model server the gateway container talks to: Ollama with `make up`, the mock otherwise.
WITH_OLLAMA := FXA_GATEWAY_LLM_BASE_URL=http://ollama:11434/v1 FXA_GATEWAY_LLM_MODEL=$(FXA_LLM_MODEL)
WITH_MOCK := FXA_GATEWAY_LLM_BASE_URL=http://mock-llm:8080/v1 FXA_GATEWAY_LLM_MODEL=mock-llm
FXA_GATEWAY_PORT ?= 8000
FXA_GRAFANA_PORT ?= 3000
FXA_PROMETHEUS_PORT ?= 9090
GATEWAY_URL := http://127.0.0.1:$(FXA_GATEWAY_PORT)

up: ## Start everything: data stores, gateway, mock LLM, Ollama (GPU if available), Prometheus, Grafana
	@echo "Ollama GPU: $(if $(filter 1,$(FXA_GPU)),on,off) (set FXA_GPU=0 or 1 to override)"
	$(WITH_OLLAMA) COMPOSE_PROFILES=llm,full $(COMPOSE) up -d --wait --build
	@echo "Grafana: http://127.0.0.1:$(FXA_GRAFANA_PORT)  Prometheus: http://127.0.0.1:$(FXA_PROMETHEUS_PORT)"

up-lite: ## Start without Ollama or observability: the gateway answers with the mock LLM (ENV-02)
	$(WITH_MOCK) $(COMPOSE) up -d --wait --build

up-mock: ## Everything except Ollama: observability with the mock LLM (no GPU or model download)
	$(WITH_MOCK) COMPOSE_PROFILES=full $(COMPOSE) up -d --wait --build

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

##@ API (Phase 2)
serve: ## Run the gateway on the host instead of in compose (port FXA_GATEWAY_PORT)
	uv run fxassist-gateway serve

mock-llm: ## Run the mock LLM on the host (port 8080)
	uv run fxassist-mock-llm

api-key: NAME ?= local-dev
api-key: ## Create an API key (printed once): make api-key NAME=alice
	@uv run fxassist-gateway create-key --name "$(NAME)"

drill: ## Stop Redis, PostgreSQL and Qdrant one by one and check the gateway (needs make up)
	@key=$$(uv run fxassist-gateway create-key --name drill 2>/dev/null) && \
	uv run python scripts/drill.py --gateway $(GATEWAY_URL) --key "$$key" --compose "$(COMPOSE)"

##@ Observability (Phase 3)
load: ## Send a small mixed load to fill the dashboard (not a benchmark): make load SECONDS=60
	@keys=""; for i in 1 2 3; do \
		keys="$$keys --key $$(uv run fxassist-gateway create-key --name load-$$i 2>/dev/null)"; done; \
	uv run python scripts/load.py --gateway $(GATEWAY_URL) $$keys --seconds $(or $(SECONDS),60)

dashboard: ## Regenerate the Grafana dashboard JSON from its spec
	uv run python observability/grafana/build_dashboard.py

##@ Kubernetes (Phase 4)
kind-up: ## Create the local kind cluster
	$(call not_yet,4)

kind-deploy: ## Build, load and deploy the Helm chart to kind
	$(call not_yet,4)

##@ Demo
demo: LLM ?= mock
demo: ## End-to-end demo: stack, ingest, key, questions (LLM=ollama for the real model)
	@if [ "$(LLM)" = "ollama" ]; then \
		$(WITH_OLLAMA) COMPOSE_PROFILES=llm $(COMPOSE) up -d --wait --build && \
		COMPOSE_PROFILES=llm $(COMPOSE) exec ollama ollama pull $(FXA_LLM_MODEL); \
	else \
		$(WITH_MOCK) $(COMPOSE) up -d --wait --build; \
	fi
	$(FXA) ingest
	@key=$$(uv run fxassist-gateway create-key --name demo 2>/dev/null) && \
	uv run python scripts/demo.py --gateway $(GATEWAY_URL) --key "$$key"
