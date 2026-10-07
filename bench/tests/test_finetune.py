"""Fine-tuning data rules (ADR-026), offline: no model, no GPU."""

from __future__ import annotations

import pytest

from bench import ft_data
from bench.finetune import FINETUNED_MODEL, finetuned_config
from bench.lab import LOCAL_MODEL_PREFIX


def test_ft01_questions_close_to_eval_questions_are_dropped() -> None:
    questions = ["What leverage does ESMA allow?", "What is a pip?", "What is a pip exactly?", "x"]
    vectors = [[1.0, 0.0], [0.0, 1.0], [0.01, 1.0], [0.5, 0.5]]
    eval_vectors = [[0.99, 0.05]]  # an evaluation question about ESMA leverage
    kept, why = ft_data.filter_questions(questions, vectors, eval_vectors)
    assert kept == [1]
    assert why == {"too close to an eval question": 1, "duplicate": 1, "empty": 1}


def test_ft02_teacher_question_parsing_tolerates_fences_and_rejects_nulls() -> None:
    assert ft_data.parse_question('```json\n{"question": " What is a lot? "}\n```') == (
        "What is a lot?"
    )
    assert ft_data.parse_question('{"question": null}') is None
    assert ft_data.parse_question("no json here") is None


def test_ft03_negative_example_keeps_the_excerpts_and_swaps_the_question() -> None:
    messages = [
        {"role": "system", "content": "SYSTEM"},
        {
            "role": "user",
            "content": "<excerpts>\nE\n</excerpts>\n\n<question>\nWhat is a pip?\n</question>",
        },
    ]
    neg = ft_data.negative_example(messages, "Who regulates CFDs in Japan?", "INSUFFICIENT_CONTEXT")
    assert neg[0] == messages[0]
    assert "<excerpts>\nE\n</excerpts>" in neg[1]["content"]
    assert "Who regulates CFDs in Japan?" in neg[1]["content"]
    assert "What is a pip?" not in neg[1]["content"]
    assert neg[2] == {"role": "assistant", "content": "INSUFFICIENT_CONTEXT"}
    with pytest.raises(ValueError):
        ft_data.negative_example([messages[0], {"role": "user", "content": "no tags"}], "q", "I")


def test_ft04_finetuned_model_is_served_like_the_fp16_baseline() -> None:
    config = finetuned_config()
    assert config["model"] == FINETUNED_MODEL and FINETUNED_MODEL.startswith(LOCAL_MODEL_PREFIX)
    assert config["dtype"] == "float16" and config["quantization"] is None  # GPU-02
    assert config["max_model_len"] == 4096
