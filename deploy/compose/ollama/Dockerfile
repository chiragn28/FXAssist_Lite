# The official Ollama image runs as root. This wrapper changes only that (ADR-022, SAF-08):
# models live in /ollama, owned by the image's existing non-root user (ubuntu, uid 1000).
FROM ollama/ollama:0.35.1

RUN mkdir -p /ollama/models && chown -R 1000:1000 /ollama

ENV HOME=/ollama \
    OLLAMA_MODELS=/ollama/models

USER 1000:1000
