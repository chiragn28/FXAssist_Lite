"""LoRA fine-tuning of the student model on bench/ft_data.py output, then a merged copy (ADR-026).

Runs in the GPU lab's vLLM virtualenv (torch and transformers come with vLLM; peft and accelerate
are added). Built for a T4 (FT-04):
  - the base model is loaded in float16 (a T4 has no bfloat16, GPU-02); the LoRA weights are
    float32 and the loss is scaled, so small gradients do not underflow
  - gradient checkpointing; batch size 1 with gradient accumulation
  - a time limit: training stops cleanly when it runs out, and says how far it got (GPU-06)
  - the loss counts only the assistant's tokens, never the prompt
Validation loss is measured before and after training, so "it learned something" is a number.
The adapter is merged into a full float16 copy that vLLM serves like any other model.

    python -m bench.ft_train --base DIR --data DIR --adapter DIR --merged DIR --log FILE
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import time
from pathlib import Path
from typing import Any

TARGET_MODULES = ["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"]


def encode(tokenizer, messages: list[dict[str, str]], max_len: int) -> dict[str, list[int]] | None:
    """Token ids and labels for one chat; labels are -100 everywhere except the reply.

    Returns None when the prompt alone is too long or the template does not split cleanly.
    """
    prompt = tokenizer.apply_chat_template(
        messages[:-1], tokenize=False, add_generation_prompt=True
    )
    full = tokenizer.apply_chat_template(messages, tokenize=False)
    if not full.startswith(prompt):
        return None
    prompt_ids = tokenizer(prompt, add_special_tokens=False)["input_ids"]
    reply_ids = tokenizer(full[len(prompt) :], add_special_tokens=False)["input_ids"]
    if len(prompt_ids) + len(reply_ids) > max_len or not reply_ids:
        return None
    return {
        "input_ids": prompt_ids + reply_ids,
        "labels": [-100] * len(prompt_ids) + reply_ids,
    }


def load_rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def log_line(path: Path, row: dict[str, Any]) -> None:
    row = {"t": round(time.time(), 1), **row}
    print(json.dumps(row), flush=True)
    with path.open("a") as f:
        f.write(json.dumps(row) + "\n")
        f.flush()
        os.fsync(f.fileno())


def main(argv: list[str] | None = None) -> int:
    import torch
    from peft import LoraConfig, get_peft_model
    from transformers import AutoModelForCausalLM, AutoTokenizer

    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--base", type=Path, required=True)
    p.add_argument("--data", type=Path, required=True)
    p.add_argument("--adapter", type=Path, required=True)
    p.add_argument("--merged", type=Path, required=True)
    p.add_argument("--log", type=Path, required=True)
    p.add_argument("--max-minutes", type=float, default=30.0)
    p.add_argument("--epochs", type=float, default=1.0)
    p.add_argument("--lr", type=float, default=1e-4)
    p.add_argument("--rank", type=int, default=16)
    p.add_argument("--alpha", type=int, default=32)
    p.add_argument("--accumulate", type=int, default=8)
    p.add_argument("--max-len", type=int, default=3072)
    p.add_argument("--seed", type=int, default=0)
    args = p.parse_args(argv)
    args.log.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    random.seed(args.seed)
    torch.manual_seed(args.seed)
    device = "cuda"

    tokenizer = AutoTokenizer.from_pretrained(args.base)
    data = {}
    for split in ("train", "val"):
        rows = load_rows(args.data / f"{split}.jsonl")
        encoded = [encode(tokenizer, r["messages"], args.max_len) for r in rows]
        data[split] = [e for e in encoded if e]
        log_line(
            args.log, {"event": "data", "split": split, "rows": len(rows), "kept": len(data[split])}
        )
    if not data["train"]:
        raise SystemExit("no training examples left after encoding")

    model = AutoModelForCausalLM.from_pretrained(args.base, dtype=torch.float16)
    model = model.to(torch.float16).to(device)
    model.config.use_cache = False
    model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
    model.enable_input_require_grads()
    model = get_peft_model(
        model,
        LoraConfig(
            r=args.rank,
            lora_alpha=args.alpha,
            lora_dropout=0.05,
            target_modules=TARGET_MODULES,
            task_type="CAUSAL_LM",
        ),
    )
    trainable = [p for p in model.parameters() if p.requires_grad]
    for param in trainable:  # FT-04: float32 master weights for the adapter only
        param.data = param.data.float()
    log_line(
        args.log,
        {
            "event": "model",
            "trainable_params": sum(p.numel() for p in trainable),
            "total_params": sum(p.numel() for p in model.parameters()),
            "torch": torch.__version__,
            "gpu": torch.cuda.get_device_name(0),
        },
    )

    def loss_of(example: dict[str, list[int]]) -> torch.Tensor:
        ids = torch.tensor([example["input_ids"]], device=device)
        labels = torch.tensor([example["labels"]], device=device)
        with torch.autocast("cuda", dtype=torch.float16):
            out = model(input_ids=ids, labels=labels)
        return out.loss.float()

    @torch.no_grad()
    def val_loss() -> float:
        model.eval()
        losses = [loss_of(e).item() for e in data["val"]]
        model.train()
        return round(sum(losses) / len(losses), 4) if losses else float("nan")

    before = val_loss()
    log_line(args.log, {"event": "val", "when": "before", "loss": before})

    total_steps = max(1, math.ceil(len(data["train"]) * args.epochs / args.accumulate))
    optimizer = torch.optim.AdamW(trainable, lr=args.lr, weight_decay=0.0)
    warmup = max(1, total_steps // 20)

    def lr_at(step: int) -> float:  # linear warm-up, then cosine to 10%
        if step < warmup:
            return args.lr * (step + 1) / warmup
        progress = (step - warmup) / max(1, total_steps - warmup)
        return args.lr * (0.1 + 0.9 * 0.5 * (1 + math.cos(math.pi * progress)))

    scaler = torch.amp.GradScaler("cuda")
    model.train()
    order = list(range(len(data["train"])))
    step, seen, running, stopped_early = 0, 0, [], False
    deadline = started + args.max_minutes * 60
    while step < total_steps:
        random.shuffle(order)
        for i in order:
            loss = loss_of(data["train"][i]) / args.accumulate
            scaler.scale(loss).backward()
            running.append(loss.item() * args.accumulate)
            seen += 1
            if seen % args.accumulate:
                continue
            for group in optimizer.param_groups:
                group["lr"] = lr_at(step)
            scaler.unscale_(optimizer)
            torch.nn.utils.clip_grad_norm_(trainable, 1.0)
            scaler.step(optimizer)
            scaler.update()
            optimizer.zero_grad(set_to_none=True)
            step += 1
            if step % 10 == 0 or step == total_steps:
                log_line(
                    args.log,
                    {
                        "event": "train",
                        "step": step,
                        "of": total_steps,
                        "loss": round(sum(running) / len(running), 4),
                        "lr": lr_at(step - 1),
                        "minutes": round((time.monotonic() - started) / 60, 1),
                    },
                )
                running = []
            if step >= total_steps:
                break
            if time.monotonic() > deadline:
                stopped_early = True
                break
        if stopped_early:
            break

    after = val_loss()
    log_line(
        args.log,
        {
            "event": "val",
            "when": "after",
            "loss": after,
            "steps": step,
            "planned_steps": total_steps,
            "examples_seen": seen,
            "stopped_by_time_limit": stopped_early,
        },
    )

    model.save_pretrained(args.adapter)
    merged = model.merge_and_unload()
    merged = merged.to(torch.float16)
    merged.config.use_cache = True
    merged.save_pretrained(args.merged, safe_serialization=True)
    tokenizer.save_pretrained(args.merged)
    gen_config = args.base / "generation_config.json"
    if gen_config.exists():
        (args.merged / "generation_config.json").write_text(gen_config.read_text())
    log_line(
        args.log,
        {"event": "saved", "minutes": round((time.monotonic() - started) / 60, 1)},
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
