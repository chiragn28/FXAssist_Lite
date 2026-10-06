#!/usr/bin/env bash
# K8S-05: prove the watchdog's ServiceAccount can restart the mock LLM Deployment and do
# nothing else, using `kubectl auth can-i` while impersonating it.
# Usage: scripts/kind_rbac_check.sh <namespace>
set -uo pipefail
ns="${1:?namespace}"
sa="system:serviceaccount:${ns}:fxassist-watchdog"
failures=0

check() { # check <expected yes|no> <verb> <resource> [extra args...]
    local expected="$1"; shift
    local got
    got="$(kubectl auth can-i -n "$ns" --as="$sa" "$@" 2>/dev/null || true)"
    if [[ "$got" == "$expected" ]]; then
        printf '  [PASS] %-3s  can-i %s\n' "$got" "$*"
    else
        printf '  [FAIL] got %-3s expected %-3s  can-i %s\n' "$got" "$expected" "$*"
        failures=$((failures + 1))
    fi
}

echo "Watchdog RBAC ($sa):"
check yes get   deployments/fxassist-mock-llm
check yes patch deployments/fxassist-mock-llm
check no  update deployments/fxassist-mock-llm
check no  delete deployments/fxassist-mock-llm
check no  patch deployments/fxassist-gateway
check no  get   deployments/fxassist-gateway
check no  list  deployments
check no  patch deployments
check no  create deployments
check no  get   pods
check no  delete pods
check no  create pods/exec
check no  get   secrets
check no  get   configmaps
check no  patch statefulsets/fxassist-qdrant
check no  get   deployments/fxassist-mock-llm -n kube-system
check no  patch deployments/fxassist-mock-llm -n default
(( failures == 0 )) && echo "RBAC limited to one deployment: OK" || { echo "$failures check(s) failed"; exit 1; }
