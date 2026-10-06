#!/usr/bin/env bash
# K8S-06: create the chart's Secret from local configuration, outside Helm and outside git.
# Usage: scripts/kind-secrets.sh <namespace> <env file>
set -euo pipefail
ns="${1:?namespace}"
env_file="${2:?env file}"
password="$(grep -E '^FXA_POSTGRES_PASSWORD=' "$env_file" | tail -1 | cut -d= -f2-)"
if [[ -z "$password" ]]; then
    echo "FXA_POSTGRES_PASSWORD is not set in $env_file" >&2
    exit 1
fi
kubectl get namespace "$ns" >/dev/null 2>&1 || kubectl create namespace "$ns" >/dev/null
# create --dry-run | apply: idempotent, and the value never appears on the command line of
# `kubectl apply` (it is read from stdin).
kubectl create secret generic fxassist-secrets --namespace "$ns" \
    --from-literal=postgres-password="$password" --dry-run=client -o yaml \
    | kubectl apply -f - >/dev/null
echo "secret fxassist-secrets is in namespace $ns"
