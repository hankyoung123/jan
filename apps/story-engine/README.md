# Story Engine Sidecar

FastAPI sidecar for the Living Story World product (code name:
AI Story Evolution Engine). It owns the Concordia-backed simulation,
Resolution boundary, durable checkpoints and branches, Actor memories, and
restricted player Perception.

Validated ResolvedEvents, checkpoints, Actor state, and Memory are the runtime
authority. Markdown seeds and Wiki/manuscript output are inputs or rebuildable
projections; they do not directly commit world consequences.

## Long-project benchmark

The opt-in legacy authoring benchmark is separate from the unit suite. It
exercises retained workspace, event, manuscript, RAG, and model-gateway code;
passing it does not establish World Session MVP completion.

    uv run python benchmarks/long_project.py

Use smaller arguments for a local smoke run:

    uv run python benchmarks/long_project.py --events 100 --scenes 10 --characters 5 --model-delay-ms 5
