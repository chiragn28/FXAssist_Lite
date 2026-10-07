# FXAssist Lite. Run `make help` for the target list.
# Designed for GNU make inside WSL2 (ADR-019) and in CI on Linux.

SHELL := /usr/bin/env bash
.SHELLFLAGS := -euo pipefail -c
.DEFAULT_GOAL := help
MAKEFLAGS += --no-print-directory

# Raise this as phases are completed; `make bootstrap` then requires those tools.
PHASE ?= 5

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
        fetch ingest ask eval eval-record experiment sources-md \
        serve mock-llm api-key drill load dashboard demo \
        images image-budget kind-up kind-load kind-deploy kind-down kind-key kind-status \
        kind-rbac-check kind-watchdog-drill kind-rollout-test helm-check \
        notebook lab-dry-run report

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

eval-record: ## Store the latest eval run in PostgreSQL (or RUN=eval/runs/<ts>)
	uv run fxassist-gateway record-eval $(or $(RUN),$$(ls -d eval/runs/2*/ | tail -1))

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

##@ Containers and Kubernetes (Phase 4)
IMAGE_TAG := 0.4.0
APP_IMAGES := fxassist/gateway:$(IMAGE_TAG) fxassist/mock-llm:$(IMAGE_TAG) fxassist/watchdog:$(IMAGE_TAG)
STORE_IMAGES := qdrant/qdrant:v1.19.2-unprivileged redis:8.8.3 postgres:18.6
KIND_CLUSTER := fxassist
KIND_NS := fxassist
FXA_KIND_GATEWAY_PORT ?= 8080
# A separate kubeconfig: kind does not switch your current kubectl context (ADR-013).
export KUBECONFIG := $(HOME)/.kube/kind-$(KIND_CLUSTER)
HELM_CHART := deploy/helm/fxassist
METRICS_SERVER_URL := https://github.com/kubernetes-sigs/metrics-server/releases/download/v0.9.0/components.yaml

images: ## Build the gateway, mock LLM and watchdog images
	docker build -f services/gateway/Dockerfile -t fxassist/gateway:$(IMAGE_TAG) .
	docker build -f services/mock_llm/Dockerfile -t fxassist/mock-llm:$(IMAGE_TAG) .
	docker build -f services/watchdog/Dockerfile -t fxassist/watchdog:$(IMAGE_TAG) .

image-budget: ## Fail if an image is over its size budget (CI-04)
	uv run python scripts/image_budget.py

kind-up: ## Create the kind cluster (one node) with metrics-server for the HPA
	@if kind get clusters 2>/dev/null | grep -qx $(KIND_CLUSTER); then \
		echo "kind cluster $(KIND_CLUSTER) already exists"; \
	else \
		mkdir -p $(dir $(KUBECONFIG)) && \
		kind create cluster --name $(KIND_CLUSTER) --kubeconfig $(KUBECONFIG) --wait 120s \
			--config <(sed 's/$${FXA_KIND_GATEWAY_PORT}/$(FXA_KIND_GATEWAY_PORT)/' deploy/kind/cluster.yaml); \
	fi
	kubectl apply -f $(METRICS_SERVER_URL) >/dev/null
	@# kind's kubelets use self-signed certificates; metrics-server must accept them.
	kubectl -n kube-system patch deployment metrics-server --type=json \
		-p '[{"op":"add","path":"/spec/template/spec/containers/0/args/-","value":"--kubelet-insecure-tls"}]' \
		>/dev/null 2>&1 || true
	@echo "kubeconfig: $(KUBECONFIG)  (export KUBECONFIG=$(KUBECONFIG) to use kubectl directly)"

KIND_PLATFORM ?= linux/amd64

kind-load: images ## Build images and load them into the kind node (K8S-08)
	@# `kind load docker-image` fails with Docker's containerd image store ("content digest
	@# not found") because it exports every platform of an image. Export one platform instead.
	@tmp=$$(mktemp -d) && trap 'rm -rf "$$tmp"' EXIT && \
	for image in $(APP_IMAGES) $(STORE_IMAGES); do \
		docker image inspect $$image >/dev/null 2>&1 || docker pull -q --platform $(KIND_PLATFORM) $$image; \
		docker save --platform $(KIND_PLATFORM) -o "$$tmp/image.tar" $$image && \
		kind load image-archive "$$tmp/image.tar" --name $(KIND_CLUSTER) >/dev/null && \
		echo "loaded $$image"; \
	done

kind-deploy: kind-load ## Build, load and install the Helm chart on kind; waits for ingestion
	scripts/kind-secrets.sh $(KIND_NS) $(ENV_FILE)
	helm upgrade --install fxassist $(HELM_CHART) --namespace $(KIND_NS) \
		-f $(HELM_CHART)/values-kind.yaml $(HELM_ARGS) --wait --wait-for-jobs --timeout 20m
	@echo "gateway: http://127.0.0.1:$(FXA_KIND_GATEWAY_PORT)  (make kind-key for an API key)"

kind-key: ## Create an API key in the kind deployment
	@kubectl -n $(KIND_NS) exec deploy/fxassist-gateway -- fxassist-gateway create-key --name kind-$$(date +%s)

kind-status: ## Pods, services and the watchdog's last runs
	kubectl -n $(KIND_NS) get pods,svc,hpa,pdb,cronjob,jobs

kind-rbac-check: ## Prove the watchdog can restart one deployment and nothing else (K8S-05)
	scripts/kind_rbac_check.sh $(KIND_NS)

kind-watchdog-drill: ## Break the mock LLM and watch the watchdog restart it exactly once (K8S-03)
	uv run python scripts/kind_watchdog_drill.py --namespace $(KIND_NS)

kind-rollout-test: ## Roll the gateway while sending traffic; expect zero failed requests (K8S-07)
	uv run python scripts/kind_rollout_test.py --namespace $(KIND_NS) --gateway http://127.0.0.1:$(FXA_KIND_GATEWAY_PORT)

helm-check: ## helm lint, and show how each environment differs from the base (K8S-09)
	helm lint $(HELM_CHART) -f $(HELM_CHART)/values-kind.yaml
	uv run python scripts/helm_diff.py

kind-down: ## Delete the kind cluster
	kind delete cluster --name $(KIND_CLUSTER) --kubeconfig $(KUBECONFIG)

##@ GPU lab (Phase 5)
notebook: ## Regenerate notebooks/fxassist_gpu_lab.ipynb from its builder
	uv run python notebooks/build_notebook.py

lab-dry-run: ## Run the whole GPU-lab flow against the mock LLM (no GPU), then a report
	uv run python -m bench.dry_run
	@latest=$$(ls -td results/mock/*/ | head -1) && \
	uv run python -m bench.report "$$latest" --out "$$latest/BENCHMARKS.md" && \
	echo "mock report: $$latest/BENCHMARKS.md (a dry run, not results)"

report: ## Build results/BENCHMARKS.md from a downloaded GPU-lab run: make report RUN=results/raw/<date>/fxassist_results
	@test -n "$(RUN)" || { echo 'usage: make report RUN=results/raw/<date>/fxassist_results' >&2; exit 2; }
	uv run python -m bench.report "$(RUN)"

lab-push: ## Start the GPU lab on Kaggle via the API: GPU T4 x2, Internet on (needs ~/.kaggle/kaggle.json)
	@bash scripts/kaggle_lab.sh push

lab-status: ## Show the Kaggle GPU-lab run status
	@bash scripts/kaggle_lab.sh status

lab-pull: ## Download the Kaggle GPU-lab output into results/raw/<date>/
	@bash scripts/kaggle_lab.sh pull

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
