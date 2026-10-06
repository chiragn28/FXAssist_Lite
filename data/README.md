# data

| Path | Committed? | Contents |
|---|---|---|
| `sources.yaml` | yes | The corpus registry: URL, licence, redistributable flag per document. The single source of truth |
| `SOURCES.md` | yes | Generated from `sources.yaml` by `make sources-md` (and after every `make fetch`) |
| `raw/` | **no** | Downloaded documents plus a `.meta.json` sidecar each (URL, SHA-256, fetch time, Wikipedia revision) |
| `reports/` | no | `fetch-report.json` and `ingest-report.json` from the last run |
| `cache/` | no | The embedding model files |

Raw documents are never committed, because some licences forbid redistribution (DAT-08). Anyone can rebuild the corpus with `make fetch`.
