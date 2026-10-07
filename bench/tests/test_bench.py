"""Benchmark harness and GPU-lab flow: BEN-01 to BEN-09, GPU-02, GPU-05 to GPU-07, CAC-05.

The lab's mock mode runs the same code the notebook runs, with the mock LLM instead of vLLM:
this is the dry run that catches mistakes before any GPU hours are spent (Phase 5).
"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from bench import lab
from bench.harness import Cell, RequestResult, ResultLog, percentile, run_cell, summarise
from bench.matrix import (
    BASE_SERVER,
    EXPERIMENTS,
    OOM_DRILL,
    Experiment,
    check_one_variable,
    scaled_down,
)
from bench.prompts import prompt_sets
from fxassist_mock_llm.app import Behaviour, create_app
from fxassist_mock_llm.server import serve


@pytest.fixture(scope="module")
def mock_url():
    behaviour = Behaviour(
        models=["m"], flavour="vllm", first_token_ms=20, token_ms=1, reply=" ".join(["tok"] * 64)
    )
    with serve(create_app(behaviour)) as server:
        yield f"{server.url}/v1"


# --- Measurement ---------------------------------------------------------------------------


def test_ben05_percentiles_are_nearest_rank_and_never_invented() -> None:
    values = [float(v) for v in range(1, 101)]
    assert percentile(values, 50) == 50 and percentile(values, 95) == 95
    assert percentile([3.0], 95) == 3.0
    assert percentile([], 95) is None  # no data is not zero


def test_ben06_errors_are_counted_and_kept_out_of_latency() -> None:
    results = [
        RequestResult(True, 0.1, 1.0, 10),
        RequestResult(True, 0.1, 2.0, 10),
        RequestResult(False, error="HTTP 503"),
        RequestResult(False, error="ReadTimeout"),
    ]
    s = summarise(results, wall_s=2.0)
    assert s["error_rate"] == 0.5 and s["errors"] == {"HTTP 503": 1, "ReadTimeout": 1}
    assert s["latency_p95_s"] == 2.0  # failures do not count as fast successes
    assert s["throughput_tokens_per_s"] == 10.0


def test_ben01_ben08_cell_row_records_fixed_output_and_discards_warmup(tmp_path, mock_url) -> None:
    cell = Cell(
        "baseline",
        "fp16",
        concurrency=4,
        prompt_set="short",
        max_tokens=16,
        requests=8,
        warmup=3,
        repetitions=1,
    )
    sets = prompt_sets()
    log = ResultLog(tmp_path / "b.jsonl")
    [row] = run_cell(
        cell, base_url=mock_url, model="m", prompts=sets["short"], server=BASE_SERVER, log=log
    )
    assert row["requests"] == 8  # the 3 warm-up requests are not in the measurement
    assert row["output_tokens_mean"] == 16  # max_tokens fixes the length (BEN-08)
    assert row["ttft_p50_s"] >= 0.02 and row["error_rate"] == 0
    assert row["prompt_set_sha256"] == sets["short"].sha256  # BEN-04
    assert row["server"]["enable_prefix_caching"] is False  # BEN-02: recorded in every row


def test_gpu05_rows_are_on_disk_at_once_and_runs_resume(tmp_path, mock_url) -> None:
    cell = Cell("baseline", "fp16", 2, "short", max_tokens=8, requests=2, warmup=1, repetitions=3)
    path = tmp_path / "b.jsonl"
    log = ResultLog(path)
    run_cell(
        replace(cell, repetitions=2),
        base_url=mock_url,
        model="m",
        prompts=prompt_sets()["short"],
        server={},
        log=log,
    )
    assert len(path.read_text().splitlines()) == 2  # written as each repetition ended
    path.write_text(path.read_text() + '{"cell_key": "cut off by a killed sess')  # partial row
    resumed = ResultLog(path)
    rows = run_cell(
        cell, base_url=mock_url, model="m", prompts=prompt_sets()["short"], server={}, log=resumed
    )
    assert [r["repetition"] for r in rows] == [3]  # only the missing repetition ran


def test_ben03_every_cell_has_at_least_three_repetitions() -> None:
    assert all(c.repetitions >= 3 for e in EXPERIMENTS for c in e.cells)


def test_ben04_prompt_sets_are_fixed_and_hashed() -> None:
    first, second = prompt_sets(), prompt_sets()
    assert {k: v.sha256 for k, v in first.items()} == {k: v.sha256 for k, v in second.items()}
    assert min(len(p) for p in first["long"].prompts) > 4000  # RAG-sized: ~1,500 tokens


# --- The matrix ------------------------------------------------------------------------------


def test_ben07_each_knob_experiment_changes_one_setting() -> None:
    check_one_variable(EXPERIMENTS)
    bad = EXPERIMENTS + [
        Experiment("two-knobs", "fp16", 5, {"max_num_seqs": 8, "max_model_len": 2048})
    ]
    with pytest.raises(ValueError, match="must change one"):
        check_one_variable(bad)


def test_ben09_fp16_and_awq_differ_only_in_the_model() -> None:
    fp16 = next(e for e in EXPERIMENTS if e.name == "baseline" and e.variant == "fp16")
    awq = next(e for e in EXPERIMENTS if e.name == "baseline" and e.variant == "awq")
    differ = {k for k in fp16.server_config if fp16.server_config[k] != awq.server_config[k]}
    assert differ == {"model", "quantization"}
    strip = lambda cells: [(c.concurrency, c.prompt_set, c.max_tokens, c.requests) for c in cells]  # noqa: E731
    assert strip(fp16.cells) == strip(awq.cells)


def test_gpu02_every_server_runs_float16_explicitly() -> None:
    for e in [*EXPERIMENTS, OOM_DRILL]:
        assert e.server_config["dtype"] == "float16"
    command = lab.Server(
        lab.LabConfig.for_mode("kaggle"), EXPERIMENTS[0].server_config, "x"
    ).command()
    assert command[command.index("--dtype") + 1] == "float16"


def test_matrix_covers_the_required_experiments() -> None:
    names = {(e.name, e.variant) for e in EXPERIMENTS}
    assert {("baseline", "fp16"), ("baseline", "awq")} <= names
    baseline = next(e for e in EXPERIMENTS if e.name == "baseline" and e.variant == "fp16")
    assert {c.concurrency for c in baseline.cells} == {1, 4, 16, 32}
    assert {c.prompt_set for c in baseline.cells} == {"short", "long"}
    knobs = {next(iter(e.server)) for e in EXPERIMENTS if e.server}
    assert knobs == {
        "gpu_memory_utilization",
        "max_num_seqs",
        "max_model_len",
        "enable_prefix_caching",
        "tensor_parallel_size",  # Phase 7
    }


# --- The whole lab flow, in mock mode (the dry run) -------------------------------------------


def test_lab_dry_run_in_mock_mode(tmp_path) -> None:
    cfg = lab.LabConfig.for_mode(
        "mock", results_dir=tmp_path / "results", scratch_dir=tmp_path / "s", port=lab_free_port()
    )
    env = lab.check_environment(cfg)
    assert env["problems"] == []  # a GPU is not required in mock mode
    smoke = lab.smoke_test(cfg)
    assert smoke["status"] == "ok" and smoke["ignore_eos_exact"]
    lab.run_experiments(cfg, scaled_down(EXPERIMENTS), smoke)
    rows = [
        json.loads(line) for line in (cfg.results_dir / "benchmark.jsonl").read_text().splitlines()
    ]
    assert len(rows) == sum(2 * 2 for _ in EXPERIMENTS)  # 2 cells x 2 repetitions each
    assert all(r["error_rate"] == 0 for r in rows)
    lab.run_experiments(cfg, scaled_down(EXPERIMENTS), smoke)  # resume: nothing re-run
    assert len((cfg.results_dir / "benchmark.jsonl").read_text().splitlines()) == len(rows)
    lab.oom_drill(cfg)
    zip_path = lab.package(cfg)
    stages = [
        json.loads(line)["stage"]
        for line in (cfg.results_dir / "stages.jsonl").read_text().splitlines()
    ]
    assert stages[0] == "environment" and "smoke" in stages and stages[-1] == "package"
    assert zip_path.exists() and zip_path.stat().st_size > 0


def test_gpu06_hour_budget_stops_new_experiments(tmp_path) -> None:
    cfg = lab.LabConfig.for_mode(
        "mock", results_dir=tmp_path / "r", scratch_dir=tmp_path / "s", hour_budget=0.1
    )
    lab.run_experiments(cfg, scaled_down(EXPERIMENTS[:1]))
    stage = json.loads((cfg.results_dir / "stages.jsonl").read_text().splitlines()[-1])
    assert stage["status"] == "skipped" and "hour budget" in stage["note"]


def test_gpu07_fewer_than_two_gpus_skips_the_multi_gpu_run(tmp_path) -> None:
    cfg = lab.LabConfig.for_mode("mock", results_dir=tmp_path / "r", scratch_dir=tmp_path / "s")
    lab.check_environment(cfg)
    stages = [
        json.loads(line) for line in (cfg.results_dir / "stages.jsonl").read_text().splitlines()
    ]
    multi = next(s for s in stages if s["stage"] == "multi-gpu")
    assert multi["status"] == "skipped" and "needs 2" in multi["note"]


def test_gpu09_gpu10_problems_are_reported_with_the_fix(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(lab, "internet_ok", lambda *a, **k: False)
    monkeypatch.setattr(
        lab, "gpus", lambda: [{"name": "Tesla T4", "memory_mib": 15360, "compute_capability": 7.5}]
    )
    cfg = lab.LabConfig.for_mode("kaggle", results_dir=tmp_path / "r", scratch_dir=tmp_path / "s")
    env = lab.check_environment(cfg)
    assert env["bf16_supported"] is False  # GPU-02: T4 is compute capability 7.5
    assert any("Internet on" in p and "phone" in p.lower() for p in env["problems"])


def lab_free_port() -> int:
    from fxassist_mock_llm.server import free_port

    return free_port()


def test_gpu07_tensor_parallel_is_skipped_with_one_gpu(tmp_path) -> None:
    from bench.matrix import TENSOR_PARALLEL

    cfg = lab.LabConfig.for_mode("kaggle", results_dir=tmp_path / "r", scratch_dir=tmp_path / "s")
    cfg.gpu_count = 1
    lab.run_experiments(cfg, TENSOR_PARALLEL)
    stage = json.loads((cfg.results_dir / "stages.jsonl").read_text().splitlines()[-1])
    assert stage["status"] == "skipped" and "needs 2 GPUs, 1 assigned" in stage["note"]


def test_gpu08_multi_gpu_start_retries_once_with_the_nccl_workaround(tmp_path, monkeypatch) -> None:
    attempts = []

    class FakeServer(lab.Server):
        def start(self):
            attempts.append(dict(self.extra_env))

        def wait_ready(self, timeout_s):
            if not self.extra_env:
                return False, "NCCL error: unhandled system error (peer access not supported)"
            return True, "ready"

        def stop(self):
            pass

    monkeypatch.setattr(lab, "Server", FakeServer)
    cfg = lab.LabConfig.for_mode("kaggle", results_dir=tmp_path / "r", scratch_dir=tmp_path / "s")
    server = lab.start_server(cfg, BASE_SERVER | {"tensor_parallel_size": 2}, "tp2")
    assert server is not None and attempts == [{}, {"NCCL_P2P_DISABLE": "1"}]
    stages = [
        json.loads(line) for line in (cfg.results_dir / "stages.jsonl").read_text().splitlines()
    ]
    assert stages[0]["status"] == "failed" and "NCCL error" in stages[0]["note"]  # kept verbatim
    assert stages[1]["status"] == "ok-with-workaround"


def test_report_marks_missing_numbers_pending_and_labels_mock_runs(tmp_path) -> None:
    from bench import report

    empty = report.render(tmp_path)
    assert "PENDING" in empty and "MOCK" not in empty
    cfg = lab.LabConfig.for_mode(
        "mock", results_dir=tmp_path / "r", scratch_dir=tmp_path / "s", port=lab_free_port()
    )
    lab.check_environment(cfg)
    lab.smoke_test(cfg)
    lab.run_experiments(cfg, scaled_down(EXPERIMENTS[:1]))
    text = report.render(cfg.results_dir)
    assert "MOCK DRY RUN" in text
    assert "| fp16, c=1, short | " in text and "(1 rep)" not in text  # median and range of 2 reps
    assert "| awq, c=1, short | PENDING" in text  # not run: never a made-up number


def test_notebook_is_generated_and_only_calls_tested_code() -> None:
    import ast
    import importlib.util
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "build_notebook", root / "notebooks" / "build_notebook.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    committed = json.loads((root / "notebooks" / "fxassist_gpu_lab.ipynb").read_text())
    assert committed == module.build(), "run `make notebook` after editing build_notebook.py"
    code = ["".join(c["source"]) for c in committed["cells"] if c["cell_type"] == "code"]
    for source in code:
        tree = ast.parse(source)  # every cell is valid Python
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == "lab"
            ):
                assert hasattr(lab, node.attr), (
                    f"notebook calls lab.{node.attr}, which does not exist"
                )
    joined = "\n".join(code)
    assert 'print(os.environ["HF_TOKEN"]' not in joined and "HF_TOKEN: set" in joined  # GPU-11
    assert all(not c.get("outputs") for c in committed["cells"] if c["cell_type"] == "code")


def test_gpu03_memory_numbers_are_read_from_vllm_log_lines() -> None:
    from bench.report import memory_from_log

    log = (
        "INFO 10-07 [model_runner.py:408] Model loading took 5.79 GiB memory and 41.523000 seconds\n"
        "INFO 10-07 [gpu_worker.py:693] Available KV cache memory: 6.12 GiB\n"
        "INFO 10-07 [kv_cache_utils.py:2465] GPU KV cache size: 178,256 tokens, "
        "Maximum concurrency for 4,096 tokens per request: 43.52x\n"
    )
    m = memory_from_log(log)
    assert (m["model_gib"], m["load_s"], m["kv_gib"], m["kv_tokens"]) == (
        "5.79",
        "41.5",
        "6.12",
        "178,256",
    )
    assert m["max_concurrency"] == "43.52x at 4,096 tokens"
    assert memory_from_log("nothing here")["kv_tokens"] == "PENDING"


def test_dep06_download_retries_resumes_and_fails_with_the_fix(tmp_path, monkeypatch) -> None:
    import subprocess

    calls = []

    def flaky_run(cmd, timeout=120, **kw):
        calls.append(cmd)
        ok = len(calls) >= 3  # two network failures, then success
        return subprocess.CompletedProcess(cmd, 0 if ok else 1, "", "ConnectionError: offline")

    monkeypatch.setattr(lab, "run", flaky_run)
    monkeypatch.setattr(lab.time, "sleep", lambda s: None)
    cfg = lab.LabConfig.for_mode("kaggle", results_dir=tmp_path / "r", scratch_dir=tmp_path / "s")
    lab.download_model(cfg, "Qwen/Qwen2.5-3B-Instruct")
    assert len(calls) == 3
    assert "allow_patterns" in calls[0][-1] and "*.safetensors" in calls[0][-1]  # GPU-10

    monkeypatch.setattr(
        lab,
        "run",
        lambda cmd, timeout=120, **kw: subprocess.CompletedProcess(cmd, 1, "", "offline"),
    )
    with pytest.raises(RuntimeError, match="Internet is on.*resumes them"):
        lab.download_model(cfg, "Qwen/Qwen2.5-3B-Instruct", attempts=2)


def test_run_sync_works_inside_a_running_event_loop() -> None:
    """Kaggle runs notebooks with an event loop already running (second Kaggle run, 2026-10-07)."""
    import asyncio

    from bench.harness import run_sync

    async def answer() -> int:
        await asyncio.sleep(0)
        return 42

    async def notebook_cell() -> int:  # like code in a Jupyter cell: a loop is running
        return run_sync(answer())

    assert run_sync(answer()) == 42  # plain script
    assert asyncio.run(notebook_cell()) == 42  # inside a running loop


def test_corpus_gaps_lists_failed_fetches_and_skipped_documents(tmp_path) -> None:
    """The first GPU run lost all Wikipedia documents silently; gaps must now be reported."""
    import json

    from bench.lab import corpus_gaps

    fetch = tmp_path / "fetch-report.json"
    fetch.write_text(
        json.dumps(
            {
                "results": [
                    {"source_id": "wiki-a", "status": "failed", "error": "HTTP 403"},
                    {"source_id": "esma-b", "status": "downloaded"},
                ]
            }
        )
    )
    ingest = tmp_path / "ingest-report.json"
    ingest.write_text(
        json.dumps(
            {
                "docs": [
                    {"source_id": "wiki-a", "status": "missing"},
                    {"source_id": "esma-b", "status": "ingested"},
                ]
            }
        )
    )
    assert corpus_gaps(fetch, ingest) == ["wiki-a: fetch HTTP 403", "wiki-a: missing"]
    assert corpus_gaps(tmp_path / "none.json", tmp_path / "none2.json") == []
