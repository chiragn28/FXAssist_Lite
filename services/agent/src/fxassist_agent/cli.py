"""`fxassist` command line, used by the Makefile targets fetch, ingest, ask, eval, experiment."""

from __future__ import annotations

import argparse
import json
import logging
import sys
import textwrap

from .config import Settings
from .embeddings import FastEmbedder
from .fetch import fetch_all, fetched_dates
from .graph import Agent, AgentDeps, AgentResult
from .ingest import ingest_all
from .llm import CircuitBreaker, LLMError, OpenAICompatLLM
from .sources import load_excluded, load_sources, render_sources_md
from .store import StoreError, VectorStore


class UserFacingError(RuntimeError):
    """An error with a message that already says how to fix it."""


def build_embedder(settings: Settings) -> FastEmbedder:
    return FastEmbedder(
        settings.embed_model,
        settings.embed_dim,
        cache_dir=settings.embed_cache_dir or settings.data_dir / "cache" / "fastembed",
        batch_size=settings.embed_batch_size,
    )


def build_store(settings: Settings, embedder) -> VectorStore:
    from qdrant_client import QdrantClient

    url = settings.resolved_qdrant_url
    client = QdrantClient(url=url, timeout=10)
    try:
        client.get_collections()
    except Exception as exc:  # qdrant-client raises several transport exception types
        raise UserFacingError(
            f"Cannot reach Qdrant at {url} ({type(exc).__name__}). Start it with: make up"
        ) from exc
    return VectorStore(client, settings.collection, embedder)


def build_llm(settings: Settings) -> OpenAICompatLLM:
    return OpenAICompatLLM(
        settings.resolved_llm_base_url,
        settings.llm_model,
        api_key=settings.llm_api_key.get_secret_value(),
        timeout_s=settings.llm_timeout_s,
        connect_timeout_s=settings.llm_connect_timeout_s,
        read_timeout_s=settings.llm_read_timeout_s,
        max_retries=settings.llm_max_retries,
        backoff_base_s=settings.llm_backoff_base_s,
        backoff_max_s=settings.llm_backoff_max_s,
        max_context_tokens=settings.llm_max_context_tokens,
        breaker=CircuitBreaker(settings.llm_breaker_failures, settings.llm_breaker_reset_s),
        trace_content=settings.trace_content,
    )


def build_agent(settings: Settings) -> Agent:
    embedder = build_embedder(settings)
    store = build_store(settings, embedder)
    store.check_compatible()  # DAT-10: refuse to answer from vectors of another model
    llm = build_llm(settings)
    llm.check_model()  # ENV-03: clear error with the exact fix command
    return Agent(AgentDeps(settings=settings, store=store, embedder=embedder, llm=llm))


# --- Commands ------------------------------------------------------------------------


def cmd_fetch(settings: Settings, args: argparse.Namespace) -> int:
    sources = load_sources(settings.sources_file)
    report = fetch_all(sources, settings.raw_dir, force=args.force)
    settings.reports_dir.mkdir(parents=True, exist_ok=True)
    (settings.reports_dir / "fetch-report.json").write_text(report.to_json())
    counts: dict[str, int] = {}
    for r in report.results:
        counts[r.status] = counts.get(r.status, 0) + 1
    print(
        f"fetch: {len(sources)} sources ({', '.join(f'{k} {v}' for k, v in sorted(counts.items()))})"
    )
    for r in report.failed:
        print(f"  FAILED {r.source_id}: {r.error}")
    _write_sources_md(settings, sources)
    return 0  # DAT-07: failures are reported, the rest of the corpus is still usable


def _write_sources_md(settings: Settings, sources) -> None:
    md = render_sources_md(
        sources, load_excluded(settings.sources_file), fetched_dates(settings.raw_dir, sources)
    )
    (settings.data_dir / "SOURCES.md").write_text(md, encoding="utf-8")


def cmd_sources_md(settings: Settings, args: argparse.Namespace) -> int:
    _write_sources_md(settings, load_sources(settings.sources_file))
    print(f"wrote {settings.data_dir / 'SOURCES.md'}")
    return 0


def cmd_ingest(settings: Settings, args: argparse.Namespace) -> int:
    if not args.no_fetch:
        cmd_fetch(settings, argparse.Namespace(force=False))
    sources = load_sources(settings.sources_file)
    embedder = build_embedder(settings)
    store = build_store(settings, embedder)
    if args.rebuild:
        print(f"rebuild: dropping collection '{settings.collection}'")
        store.drop()
    report = ingest_all(sources, settings.raw_dir, store, embedder, settings)
    settings.reports_dir.mkdir(parents=True, exist_ok=True)
    (settings.reports_dir / "ingest-report.json").write_text(report.to_json())
    print(report.summary())
    print(f"report: {settings.reports_dir / 'ingest-report.json'}")
    return 0


def format_result(result: AgentResult) -> str:
    lines = [f"[{result.outcome}, {result.seconds:.1f}s]", ""]
    lines += textwrap.wrap(result.answer, 100, replace_whitespace=False) or [""]
    if result.citations:
        lines += ["", "Sources:"]
        for c in result.citations:
            page = f", p. {c.page}" if c.page else ""
            lines.append(f"  [{c.label}] {c.publisher}: {c.title}{page}\n       {c.url}")
    lines += ["", "Informational only, not financial advice."]
    return "\n".join(lines)


def cmd_ask(settings: Settings, args: argparse.Namespace) -> int:
    agent = build_agent(settings)
    result = agent.ask(args.question)
    if args.json:
        print(json.dumps(result.to_dict(), indent=2, default=str))
    else:
        print(format_result(result))
        if args.verbose:
            print(f"\nreason: {result.reason or '-'}")
            print("steps: " + " -> ".join(f"{s['node']} {s['seconds']}s" for s in result.steps))
            print(f"retrieved: {result.retrieved}")
    return 0


def cmd_eval(settings: Settings, args: argparse.Namespace) -> int:
    from .evaluate import run_eval

    agent = build_agent(settings)
    return run_eval(agent, settings, limit=args.limit, scenarios=not args.no_scenarios)


def cmd_experiment(settings: Settings, args: argparse.Namespace) -> int:
    from .evaluate import run_experiment

    return run_experiment(settings, build_embedder(settings))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fxassist", description=__doc__)
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("fetch", help="download the corpus into data/raw")
    p.add_argument("--force", action="store_true", help="download again even if present")
    p.set_defaults(func=cmd_fetch)

    p = sub.add_parser("ingest", help="fetch, chunk, embed and store the corpus")
    p.add_argument("--no-fetch", action="store_true", help="use files already in data/raw")
    p.add_argument("--rebuild", action="store_true", help="drop and rebuild the collection")
    p.set_defaults(func=cmd_ingest)

    p = sub.add_parser("ask", help="ask one question")
    p.add_argument("question")
    p.add_argument("--json", action="store_true", help="print the full result as JSON")
    p.set_defaults(func=cmd_ask)

    p = sub.add_parser("eval", help="run the evaluation set against the configured model")
    p.add_argument("--limit", type=int, help="only the first N questions")
    p.add_argument("--no-scenarios", action="store_true", help="skip planted-excerpt scenarios")
    p.set_defaults(func=cmd_eval)

    p = sub.add_parser("experiment", help="retrieval-only chunk size x top-k experiment")
    p.set_defaults(func=cmd_experiment)

    p = sub.add_parser("sources-md", help="regenerate data/SOURCES.md")
    p.set_defaults(func=cmd_sources_md)

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    settings = Settings()
    try:
        return args.func(settings, args)
    except (UserFacingError, StoreError, LLMError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
