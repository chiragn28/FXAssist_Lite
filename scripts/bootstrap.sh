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
#   FXA_BOOTSTRAP_BUSY_PORTS="5432 6379" pretends those host ports are taken
#   FXA_BOOTSTRAP_ENV=path            reads ports from this file instead of .env

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
        warn "Docker memory $shown: use the lite profile (make up-lite), not make up" "$fix"
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

# --- Ports (ENV-04) -------------------------------------------------------------
# Ask Docker itself whether each configured host port can be published: on WSL2 a port held
# by a Windows program (for example a local PostgreSQL) is invisible to Linux tools, but makes
# `docker compose up` fail. Found by the Phase 8 fresh-clone test. Ports already published by
# FXAssist's own containers are fine. Uses an image that is already present; pulls nothing.
section "Ports"
# Effective ports: .env.example defaults, overridden by .env (or FXA_BOOTSTRAP_ENV, for tests).
port_env="${FXA_BOOTSTRAP_ENV:-$REPO_ROOT/.env}"
declare -A port_of=()
for f in "$REPO_ROOT/.env.example" "$port_env"; do
    [[ -f "$f" ]] || continue
    while IFS='=' read -r k v; do port_of[$k]="$v"; done < <(grep -oE '^FXA_[A-Z_]+_PORT=[0-9]+' "$f")
done
port_vars=()
for k in "${!port_of[@]}"; do port_vars+=("$k=${port_of[$k]}"); done
probe_image=""
if [[ -z "${FXA_BOOTSTRAP_BUSY_PORTS+x}" ]] && has docker && docker info >/dev/null 2>&1; then
    for image in redis:8.8.3 postgres:18.6 python:3.11.17-slim-trixie; do
        if docker image inspect "$image" >/dev/null 2>&1; then probe_image="$image"; break; fi
    done
fi
if [[ -z "${FXA_BOOTSTRAP_BUSY_PORTS+x}" && -z "$probe_image" ]]; then
    warn "ports not checked (needs Docker and a local image; nothing is pulled)"
else
    # Host ports published by FXAssist's own containers ("127.0.0.1:6333-6334->..." or ":8000->").
    ours=" "
    while IFS='|' read -r cname cports; do
        [[ "$cname" == fxassist* ]] || continue
        while [[ "$cports" =~ :([0-9]+)(-([0-9]+))?- ]]; do
            first="${BASH_REMATCH[1]}"; last="${BASH_REMATCH[3]:-$first}"
            for ((p = first; p <= last; p++)); do ours+="$p "; done
            cports="${cports#*"${BASH_REMATCH[0]}"}"
        done
    done < <(docker ps --format '{{.Names}}|{{.Ports}}' 2>/dev/null || true)
    busy=0
    for entry in "${port_vars[@]}"; do
        name="${entry%%=*}"; port="${entry#*=}"
        if [[ -n "${FXA_BOOTSTRAP_BUSY_PORTS+x}" ]]; then
            [[ " $FXA_BOOTSTRAP_BUSY_PORTS " == *" $port "* ]] && taken=1 || taken=0
        elif [[ "$ours" == *" $port "* ]]; then
            taken=0
        elif docker run --rm -p "127.0.0.1:$port:9" --entrypoint true "$probe_image" >/dev/null 2>&1; then
            taken=0
        else
            taken=1
        fi
        if (( taken )); then
            warn "port $port ($name) is taken on this machine" "set another free port in .env, e.g. $name=$((port + 50000 > 65535 ? port + 1000 : port + 50000))"
            busy=$((busy + 1))
        fi
    done
    (( busy == 0 )) && ok "${#port_vars[@]} host ports free"
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

# --- Local model (ADR-022, ENV-03) ----------------------------------------------
# Ollama runs as a compose container, so there is nothing to install: these are warnings
# about stack state, each with the command that fixes it.
section "Local model"
env_value() { grep -E "^$1=" "$REPO_ROOT/.env" 2>/dev/null | tail -n1 | cut -d= -f2-; }
MODEL="${FXA_LLM_MODEL:-$(env_value FXA_LLM_MODEL)}"; MODEL="${MODEL:-qwen2.5:3b-instruct}"
PORT="${FXA_OLLAMA_PORT:-$(env_value FXA_OLLAMA_PORT)}"; PORT="${PORT:-11434}"
OLLAMA_URL="http://localhost:$PORT"
if tags="$(curl -fsS --max-time 2 "$OLLAMA_URL/api/tags" 2>/dev/null)"; then
    ok "Ollama reachable at $OLLAMA_URL"
    if [[ "$tags" == *"\"$MODEL\""* ]]; then ok "model $MODEL pulled"
    else warn "model $MODEL not pulled yet" "make pull-model"; fi
else
    warn "Ollama not running at $OLLAMA_URL" "make up   # then, once: make pull-model"
fi
if gpu="$(nvidia-smi -L 2>/dev/null | head -n1)" && [[ -n "$gpu" ]]; then
    ok "NVIDIA GPU visible: ${gpu%% (UUID*} (Ollama will use it)"
else
    warn "no NVIDIA GPU visible: Ollama will run on CPU (slower; needs about 3 GB more RAM)"
fi

# --- Later phases ---------------------------------------------------------------
section "Later phases"
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
