# notebooks

`fxassist_gpu_lab.ipynb`: the GPU lab (ADR-004). It runs only on Kaggle (or Colab) in your browser: environment checks, vLLM 0.31.0 in its own virtualenv, a 10-minute smoke test, the benchmark matrix, the evaluation with the real model, an induced out-of-memory drill and a tensor-parallel run if two GPUs are assigned. Everything it measures is written to `fxassist_results/` as it happens and packed into one zip at the end.

- Edit `build_notebook.py`, then `make notebook`; a test fails if the `.ipynb` and its builder differ.
- The cells only call `bench/lab.py`. `make lab-dry-run` runs that same code against the mock LLM, so mistakes show up on the laptop, not in GPU hours.
- How to run it, the hour budget and what was verified about the free tiers: `docs/KAGGLE_PLAYBOOK.md`.
