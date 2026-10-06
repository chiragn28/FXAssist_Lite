#!/usr/bin/env bash
# API-05 on kind: does the SSE stream survive a buffering reverse proxy?
# Puts nginx (default proxy_buffering on) in front of the gateway twice:
#   honour : nginx as shipped, which obeys the gateway's `X-Accel-Buffering: no`
#   ignore : nginx told to ignore that header
# and reports when the first `status` event and the `answer` event reach the client.
# Result on 2026-10-07: both streamed (first event ~0.1 s, answer ~0.7 s); nginx 1.30.5 with
# default buffer sizes forwards small chunks as they arrive, so the header was not what made it
# work here. It stays as defence in depth for proxies that do hold responses back.
# Usage: scripts/kind_proxy_check.sh <namespace> <api key>
set -euo pipefail
ns="${1:?namespace}"; key="${2:?api key}"
image="nginxinc/nginx-unprivileged:1.30.5-alpine"

run() { # run <name> <extra nginx directive>
    local name="$1" extra="$2" port="$3"
    kubectl -n "$ns" create configmap "proxy-$name" --dry-run=client -o yaml --from-literal=default.conf="
server {
  listen 8080;
  location / {
    proxy_pass http://fxassist-gateway:8000;
    proxy_http_version 1.1;
    $extra
  }
}" | kubectl apply -f - >/dev/null
    kubectl -n "$ns" delete pod "proxy-$name" --ignore-not-found >/dev/null
    kubectl -n "$ns" run "proxy-$name" --image="$image" --restart=Never \
        --overrides="{\"spec\":{\"volumes\":[{\"name\":\"c\",\"configMap\":{\"name\":\"proxy-$name\"}}],
          \"containers\":[{\"name\":\"proxy-$name\",\"image\":\"$image\",
          \"volumeMounts\":[{\"name\":\"c\",\"mountPath\":\"/etc/nginx/conf.d\"}]}]}}" >/dev/null
    kubectl -n "$ns" wait --for=condition=ready "pod/proxy-$name" --timeout=120s >/dev/null
    kubectl -n "$ns" port-forward "pod/proxy-$name" "$port:8080" >/dev/null 2>&1 &
    local pf=$!
    sleep 2
    local q="What is a CFD? ($name $RANDOM$RANDOM)"   # unique: no cache hit
    # Arrival time of the first `status` event vs the `answer` event, as the client sees them.
    uv run python - "$port" "$key" "$q" <<'PY'
import sys, time, httpx
port, key, q = sys.argv[1], sys.argv[2], sys.argv[3]
start, first, answer = time.monotonic(), None, None
with httpx.stream("POST", f"http://127.0.0.1:{port}/v1/ask", json={"question": q},
                  headers={"Authorization": f"Bearer {key}"}, timeout=60) as r:
    for line in r.iter_lines():
        now = time.monotonic() - start
        if line.startswith("event: status") and first is None:
            first = now
        if line.startswith("event: answer"):
            answer = now
print(f"  first status event {first:.2f}s, answer event {answer:.2f}s, "
      + ("streamed" if answer - first > 0.2 else "BUFFERED: everything arrived at once"))
PY
    kill $pf
    kubectl -n "$ns" delete pod "proxy-$name" >/dev/null
    kubectl -n "$ns" delete configmap "proxy-$name" >/dev/null
}

echo "SSE through nginx $image (default proxy_buffering on):"
echo "honour (nginx as shipped):"; run honour "" 18080
echo "ignore (nginx told to ignore X-Accel-Buffering):"; run ignore "proxy_ignore_headers X-Accel-Buffering;" 18081
