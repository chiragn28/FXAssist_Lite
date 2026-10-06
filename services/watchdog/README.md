# watchdog

Kubernetes CronJob that sends a fixed canary prompt to the LLM deployment and triggers a rollout restart after N consecutive failures, with a cooldown and an hourly restart cap. Its RBAC allows restarting one named deployment only (ADR-014).

Built in **Phase 4**. Edge cases: K8S-03, K8S-04, K8S-05.
