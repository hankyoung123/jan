# Story Engine

FastAPI sidecar that owns AI Story Evolution Engine Next domain logic and
canonical Markdown state.

## Long-project benchmark

The opt-in benchmark is separate from the unit suite. Its defaults exercise
10,000 Events, 500 Scenes, 50 active characters, the live workspace watcher,
incremental RAG, a delayed mock model transport, one turn/commit, and an
external Markdown refresh:

```bash
uv run python benchmarks/long_project.py
```

Use smaller arguments for a local smoke run:

```bash
uv run python benchmarks/long_project.py --events 100 --scenes 10 --characters 5 --model-delay-ms 5
```
