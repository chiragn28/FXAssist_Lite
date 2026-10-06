#!/usr/bin/env bash
# Checks that this machine can build and run FXAssist Lite, and prints the exact fix
# for anything missing. It never installs or changes anything.
#
# Usage: scripts/bootstrap.sh [--phase N]
#   --phase N   treat tools needed up to phase N as required (default: 0).
#               Tools for later phases are still reported, as warnings.
#
# Exit code: 0 if every required check passed, 1 otherwise.
#
# Test hooks (tests/test_bootstrap.py):
#   FXA_BOOTSTRAP_HIDE="docker,uv"   makes those commands look missing
#   FXA_BOOTSTRAP_DOCKER_MEM_BYTES=n pretends Docker reports n bytes of memory

set -uo pipefail

PHASE=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --phase) PHASE="${2:?--phase needs a number}"; shift 2 ;;
        -h|--help) sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "unknown argument: $1 (try --help)" >&2; exit 2 ;;
    esac
done
if ! [[ "$PHASE" =~ ^[0-9]+$ ]]; then
    echo "--phase must be a number, got: $PHASE" >&2
    exit 2
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MISSING=0
WARNINGS=0

ok()   { printf '  [ OK ] %s\n' "$1"; }
warn() { printf '  [WARN] %s\n' "$1"; [[ -n "${2:-}" ]] && printf '         fix: %s\n' "$2"; WARNINGS=$((WARNINGS + 1)); }
miss() { printf '  [MISS] %s\n' "$1"; [[ -n "${2:-}" ]] && printf '         fix: %s\n' "$2"; MISSING=$((MISSING + 1)); }

# Report a problem as MISS if the tool is needed by the requested phase, else as WARN.
need() { # need <phase-needed> <message> <fix>
    if (( PHASE >= $1 )); then miss "$2" "$3"; else warn "$2 (needed from Phase $1)" "$3"; fi
}

has() {
    local hidden=",${FXA_BOOTSTRAP_HIDE:-},"
    [[ "$hidden" == *",$1,"* ]] && return 1
    command -v "$1" >/dev/null 2>&1
}

# Compare dotted versions: version_ge 0.9.1 0.8.4 -> true
version_ge() {
    [[ "$(printf '%s\n%s\n' "$2" "$1" | sort -V | head -n1)" == "$2" ]]
}

section() { printf '\n%s\n' "$1"; }

# ENV-02: classify the memory Docker can use. The VM reports a little less than its
# configured size (an 8 GB WSL2 setting shows as about 7.7 GiB), so each tier accepts
# 90% of its nominal size. Budgets are estimates until measured in Phases 3 and 4.
check_docker_memory() { # check_docker_memory <bytes>
    local mem_mib=$(( $1 / 1024 / 1024 ))
    local shown fix
    shown="$(awk -v m="$mem_mib" 'BEGIN { printf "%.1f GiB", m / 1024 }')"
    fix="raise memory in %UserProfile%\\.wslconfig ([wsl2] memory=8GB), then: wsl --shutdown"
    if (( mem_mib >= 8 * 1024 * 9 / 10 )); then
        ok "Docker memory $shown (full stack and kind)"
    elif (( mem_mib >= 6 * 1024 * 9 / 10 )); then
        warn "Docker memory $shown: enough for the full compose stack, tight for kind (8 GB)" "$fix"
    elif (( mem_mib >= 4 * 1024 * 9 / 10 )); then
        warn "Docker memory $shown: use the lite profile (make up), not make up-full" "$fix"
    else
        miss "Docker memory $shown is below the 4 GB minimum" "$fix"
    fi
}

printf 'FXAssist Lite bootstrap check (required up to Phase %s)\n' "$PHASE"

# --- Platform (ADR-019) ---------------------------------------------------------
section "Platform"
case "$(uname -s)" in
    Linux)
        if grep -qi microsoft /proc/version 2>/dev/null; then
            ok "running inside WSL2"
            if [[ "$REPO_ROOT" == /mnt/* ]]; then
                warn "repo is on the Windows filesystem ($REPO_ROOT): slow file I/O and permission problems" \
                     "clone into your Linux home instead: cd ~ && git clone <repo-url> fxassist_lite"
            else
                ok "repo is on the Linux filesystem"
            fi
        else
            ok "running on native Linux"
        fi
        ;;
    Darwin)
        warn "macOS is not the target platform; most targets should work but are untested" ;;
    MINGW*|MSYS*|CYGWIN*)
        miss "running in Git Bash/MSYS on Windows; the project targets WSL2 (ADR-019)" \
             "in PowerShell: wsl --install -d Ubuntu-24.04 ; then open Ubuntu and clone the repo into ~"
        ;;
    *)
        warn "unknown platform: $(uname -s)" ;;
esac
if [[ "$REPO_ROOT" == *OneDrive* ]]; then
    warn "repo is inside OneDrive: syncing .venv and .git causes lock and corruption problems" \
         "move the repo out of OneDrive (inside WSL2: ~/fxassist_lite)"
fi

# --- Core tools (Phase 0) --------------------------------------------------------
section "Core tools"
if has git; then
    ok "git $(git --version | awk '{print $3}')"
    autocrlf="$(git -C "$REPO_ROOT" config core.autocrlf 2>/dev/null || true)"
    if [[ "$autocrlf" == "true" ]]; then
        warn "git core.autocrlf=true (.gitattributes keeps this repo LF, but other repos may break)" \
             "git config --global core.autocrlf input"
    fi
else
    miss "git" "sudo apt-get update && sudo apt-get install -y git"
fi

if has make; then ok "make"; else miss "make" "sudo apt-get install -y make"; fi
if has curl; then ok "curl"; else miss "curl" "sudo apt-get install -y curl"; fi

# ENV-01: a CRLF Makefile or script fails in confusing ways, so check the checkout itself.
crlf_files=()
for f in Makefile scripts/*.sh; do
    [[ -f "$REPO_ROOT/$f" ]] && grep -q $'\r' "$REPO_ROOT/$f" && crlf_files+=("$f")
done
if (( ${#crlf_files[@]} == 0 )); then
    ok "Makefile and scripts use LF line endings"
else
    miss "CRLF line endings in: ${crlf_files[*]}" \
         "git config core.autocrlf false && git rm --cached -r -q . && git reset --hard"
fi

UV_MIN="0.8.4"
if has uv; then
    uv_version="$(uv --version | awk '{print $2}')"
    if version_ge "$uv_version" "$UV_MIN"; then ok "uv $uv_version"
    else miss "uv $uv_version is older than $UV_MIN" "uv self update"; fi
    if uv python find 3.11 >/dev/null 2>&1; then
        ok "Python 3.11 available to uv"
    else
        miss "Python 3.11 not found by uv" "uv python install 3.11"
    fi
else
    miss "uv (Python version and dependency manager, ADR-021)" \
         "curl -LsSf https://astral.sh/uv/install.sh | sh   # then open a new shell"
fi

# --- Docker ---------------------------------------------------------------------
section "Docker"
if [[ -n "${FXA_BOOTSTRAP_DOCKER_MEM_BYTES:-}" ]]; then
    check_docker_memory "$FXA_BOOTSTRAP_DOCKER_MEM_BYTES"
elif has docker; then
    ok "docker CLI"
    if docker info >/dev/null 2>&1; then
        ok "docker daemon reachable"
        check_docker_memory "$(docker info --format '{{.MemTotal}}' 2>/dev/null || echo 0)"
    else
        miss "docker daemon not reachable" \
             "start Docker Desktop; in WSL2 also enable Settings > Resources > WSL integration for your distro"
    fi
    if docker compose version >/dev/null 2>&1; then
        ok "docker compose $(docker compose version --short 2>/dev/null)"
    else
        miss "docker compose v2 plugin" "update Docker Desktop (compose v2 ships with it)"
    fi
else
    miss "docker CLI" \
         "install Docker Desktop for Windows, then enable Settings > Resources > WSL integration for your distro"
fi

# --- Disk -----------------------------------------------------------------------
section "Disk"
free_gib="$(df -Pk "$REPO_ROOT" 2>/dev/null | awk 'NR==2 {print int($4 / 1024 / 1024)}')"
if [[ -z "$free_gib" ]]; then
    warn "could not read free disk space"
elif (( free_gib >= 15 )); then
    ok "${free_gib} GiB free"
else
    warn "only ${free_gib} GiB free; images, the embedding model and kind need about 15 GiB" \
         "free space, or prune Docker: docker system prune"
fi

# --- Later phases ---------------------------------------------------------------
section "Later phases"
# ENV-03 (Phase 1): Ollama serves the local dev model.
OLLAMA_URL="${OLLAMA_URL:-http://localhost:11434}"
if has ollama || curl -fsS --max-time 2 "$OLLAMA_URL/api/tags" >/dev/null 2>&1; then
    if curl -fsS --max-time 2 "$OLLAMA_URL/api/tags" >/dev/null 2>&1; then
        ok "Ollama reachable at $OLLAMA_URL"
    else
        need 1 "Ollama installed but not reachable at $OLLAMA_URL" \
             "start it (ollama serve), or set OLLAMA_URL if it runs on the Windows host"
    fi
else
    need 1 "Ollama (local dev model server)" "curl -fsSL https://ollama.com/install.sh | sh"
fi

if has kind;    then ok "kind";    else need 4 "kind (local Kubernetes)" "see https://kind.sigs.k8s.io/docs/user/quick-start/#installation"; fi
if has kubectl; then ok "kubectl"; else need 4 "kubectl" "see https://kubernetes.io/docs/tasks/tools/install-kubectl-linux/"; fi
if has helm;    then ok "helm";    else need 4 "helm" "see https://helm.sh/docs/intro/install/"; fi

# --- Repo setup -----------------------------------------------------------------
section "Repo setup"
NEXT_STEP="make up"
if [[ -f "$REPO_ROOT/.git/hooks/pre-commit" ]]; then
    ok "pre-commit hooks installed (secret scan on commit, SAF-07)"
else
    warn "pre-commit hooks not installed" "make install"
    NEXT_STEP="make install"
fi
if [[ -f "$REPO_ROOT/.env" ]]; then
    ok ".env present"
else
    warn ".env not found; .env.example defaults will be used" "cp .env.example .env"
fi

# --- Summary --------------------------------------------------------------------
printf '\n'
if (( MISSING == 0 )); then
    printf 'Ready for Phase %s: %d warning(s), nothing required is missing.\n' "$PHASE" "$WARNINGS"
    printf 'Next: %s\n' "$NEXT_STEP"
    exit 0
fi
printf 'Not ready: %d required item(s) missing, %d warning(s). Apply the fixes above and re-run make bootstrap.\n' \
    "$MISSING" "$WARNINGS"
exit 1
