# eval

Hand-written evaluation data (ADR-016), checked against the extracted text of the corpus.

| File | Contents |
|---|---|
| `questions.yaml` | 39 questions: 21 answerable, 1 ambiguous (answer differs by regulator), 5 unanswerable, 3 vague, 3 off-domain, 4 personal-advice requests, 2 requests to reveal the prompt or keys |
| `attacks.yaml` | 10 prompt-injection attacks (SAF-04); the last ones are written to get past the rule-based guard |
| `scenarios.yaml` | 3 planted-excerpt scenarios run with the real model: conflicting excerpts (RET-02), an instruction hidden in an excerpt (RET-03), a missing number (SAF-03) |

Commands:

- `make eval` runs everything through the agent with the configured model and prints retrieval hit rate, citation correctness, abstention accuracy, safety pass rate and latency. Results go to `eval/runs/` (git-ignored).
- `make experiment` runs the retrieval-only chunk size x top-k grid (DAT-09). No model needed.

Local runs use Ollama's 4-bit `qwen2.5:3b-instruct` and are development numbers, not reported results. Reported numbers come only from the GPU lab (Phase 6) with sample sizes stated. With tens of questions, one question moves a rate by 3 to 5 points, and the local model is not fully deterministic even at temperature 0.
