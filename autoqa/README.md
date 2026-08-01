# Story Engine QA

The active quality gates for Story Engine are defined in
`.github/workflows/story-engine-ci.yml`, the Python tests under
`apps/story-engine/tests`, the Web tests under `web-app/src`, and the visual
minimum-window test under `tests/visual`.

The former Jan AutoQA runner downloaded Jan installers and assumed Jan's
application paths, process names, and ReportPortal project. It is archived at
`docs/upstream/jan/autoqa/` and is not an active Story Engine workflow. A
future packaged install test must consume Story Engine artifacts, verify
`com.storyengine.desktop`, wait for the bundled `story-engine` sidecar health
endpoint, and uninstall only from an isolated temporary environment.
