#!/usr/bin/env bash
# Run the GPU lab on Kaggle through the Kaggle API, without the browser.
#
#   scripts/kaggle_lab.sh push     upload the notebook and start a background run (GPU + Internet)
#   scripts/kaggle_lab.sh status   show the run's status (queued, running, complete, error)
#   scripts/kaggle_lab.sh pull     download the run's output into results/raw/<date>/
#
# Needs a phone-verified Kaggle account (GPU and Internet require it), a one-time sign-in
# (`uvx --from kaggle==2.2.4 kaggle auth login`, which opens your browser), and your Kaggle
# username in .env as FXA_KAGGLE_USERNAME. Credentials stay with the Kaggle CLI; this script
# never reads or prints them.
#
# Settings (environment variables):
#   REPO_URL               repo the notebook clones (default: the `github` git remote)
#   FXA_KAGGLE_MACHINE     NvidiaTeslaT4 = GPU T4 x2 (default). Use "" for Kaggle's default GPU.
#   FXA_KAGGLE_SLUG        kernel name (default fxassist-gpu-lab)
#   FXA_LAB_MAX_PRIORITY, FXA_LAB_RUN_EVAL, FXA_LAB_RUN_OOM_DRILL
#                          override the notebook parameters of the same name, e.g. an eval-only
#                          rerun: FXA_LAB_MAX_PRIORITY=0 FXA_LAB_RUN_OOM_DRILL=False make lab-push

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
KAGGLE_CLI_VERSION="2.2.4"  # verified on PyPI 2026-10-07 (docs/VERSIONS.md)
KAGGLE=(uvx --quiet --from "kaggle==${KAGGLE_CLI_VERSION}" kaggle)
SLUG="${FXA_KAGGLE_SLUG:-fxassist-gpu-lab}"
MACHINE="${FXA_KAGGLE_MACHINE-NvidiaTeslaT4}"
BUILD="$ROOT/.kaggle-kernel"

env_value() { grep -E "^$1=" "$ROOT/.env" 2>/dev/null | tail -n1 | cut -d= -f2-; }

# The kernel id is "<kaggle username>/<slug>". CLI 2.x sign-in (`kaggle auth login` or an
# access token) does not reveal the username, so it comes from configuration.
resolve_user() {
    local user="${KAGGLE_USERNAME:-$(env_value FXA_KAGGLE_USERNAME)}"
    local legacy="$HOME/.kaggle/kaggle.json"
    if [[ -z "$user" && -f "$legacy" ]]; then
        user="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("username",""))' "$legacy")"
    fi
    if [[ -z "$user" ]]; then
        echo "Kaggle username unknown. Add it to .env:  FXA_KAGGLE_USERNAME=<your kaggle username>" >&2
        return 1
    fi
    echo "$user"
}

check_auth() {
    # Any of: `kaggle auth login` (cached OAuth), KAGGLE_API_TOKEN, ~/.kaggle/access_token,
    # or a legacy ~/.kaggle/kaggle.json. A cheap authenticated call tells us which works.
    if ! "${KAGGLE[@]}" kernels list --mine --page-size 1 >/dev/null 2>&1; then
        echo "Not signed in to the Kaggle API. In your Ubuntu terminal run, once:" >&2
        echo "  uvx --from kaggle==${KAGGLE_CLI_VERSION} kaggle auth login" >&2
        return 1
    fi
}

USER_NAME=""
kernel_id() { echo "$USER_NAME/$SLUG"; }

cmd_push() {
    local repo="${REPO_URL:-$(git -C "$ROOT" remote get-url github 2>/dev/null || true)}"
    repo="${repo%.git}"
    if [[ ! "$repo" =~ ^https://github\.com/ ]]; then
        echo "Set REPO_URL to the public GitHub URL of this repo (got: '${repo:-nothing}')." >&2
        exit 1
    fi
    if [[ -n "$(git -C "$ROOT" status --porcelain -- bench notebooks eval services data/sources.yaml)" ]] \
       || [[ "$(git -C "$ROOT" rev-parse HEAD)" != "$(git -C "$ROOT" rev-parse github/main 2>/dev/null)" ]]; then
        echo "warning: the notebook clones $repo, which may not have your latest local commits." >&2
    fi
    rm -rf "$BUILD" && mkdir -p "$BUILD"
    python3 - "$ROOT/notebooks/fxassist_gpu_lab.ipynb" "$BUILD/fxassist_gpu_lab.ipynb" "$repo" <<'PY'
import json, os, re, sys
src, dst, repo = sys.argv[1:]
nb = json.load(open(src))
params = {"REPO_URL": f'"{repo}"'}
for name in ("MAX_PRIORITY", "RUN_EVAL", "RUN_OOM_DRILL"):
    value = os.environ.get(f"FXA_LAB_{name}")
    if value:
        if not re.fullmatch(r"\d+|True|False", value):
            sys.exit(f"FXA_LAB_{name} must be a number, True or False (got {value!r})")
        params[name] = value
hits = {name: 0 for name in params}
for cell in nb["cells"]:
    text = "".join(cell["source"])
    new = text
    for name, value in params.items():
        new, n = re.subn(
            rf"^({name} = )[^#\n]*?(\s*#.*)?$",
            lambda m: f"{m.group(1)}{value}{m.group(2) or ''}",  # keep the trailing comment
            new,
            flags=re.M,
        )
        hits[name] += n
    if new != text:
        cell["source"] = new.splitlines(keepends=True)
bad = {k: v for k, v in hits.items() if v != 1}
if bad:
    sys.exit(f"expected each parameter once in the parameters cell, found {bad}")
print("notebook parameters:", ", ".join(f"{k}={v}" for k, v in params.items()))
json.dump(nb, open(dst, "w"), indent=1)
PY
    python3 - "$BUILD/kernel-metadata.json" "$(kernel_id)" "$MACHINE" <<'PY'
import json, sys
path, kid, machine = sys.argv[1:]
meta = {
    "id": kid,
    "title": "fxassist-gpu-lab",
    "code_file": "fxassist_gpu_lab.ipynb",
    "language": "python",
    "kernel_type": "notebook",
    "is_private": True,
    "enable_gpu": True,
    "enable_internet": True,
    "dataset_sources": [],
    "competition_sources": [],
    "kernel_sources": [],
}
if machine:
    meta["machine_shape"] = machine
json.dump(meta, open(path, "w"), indent=2)
PY
    echo "pushing $(kernel_id) (GPU ${MACHINE:-default}, Internet on, private), cloning $repo"
    "${KAGGLE[@]}" kernels push -p "$BUILD"
    echo "Started. Check with: make lab-status   (the run takes about 4 to 5 hours)"
}

cmd_status() {
    "${KAGGLE[@]}" kernels status "$(kernel_id)"
}

cmd_pull() {
    # One folder per download, so a later (e.g. eval-only) run never overwrites an earlier one.
    local stamp; stamp="$(date +%Y%m%d-%H%M)"
    local dest="$ROOT/results/raw/$stamp"
    mkdir -p "$dest"
    "${KAGGLE[@]}" kernels output "$(kernel_id)" -p "$dest" --force
    if [[ -f "$dest/fxassist_results.zip" && ! -d "$dest/fxassist_results" ]]; then
        (cd "$dest" && python3 -m zipfile -e fxassist_results.zip .)
    fi
    echo "Downloaded to $dest. Next:"
    echo "  make report RUN=results/raw/$stamp/fxassist_results"
}

case "${1:-}" in
    push | status | pull)
        USER_NAME="$(resolve_user)" || exit 1
        check_auth || exit 1
        ;;
esac

case "${1:-}" in
    push) cmd_push ;;
    status) cmd_status ;;
    pull) cmd_pull ;;
    *) sed -n '2,17p' "$0" | sed 's/^# \{0,1\}//'; exit 2 ;;
esac
