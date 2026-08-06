# Archived Jan packaging workflows

These reusable workflow templates and the NSIS template came from the locked Jan
`v0.8.4` upstream baseline. They reference Jan release services, artifact names,
branding, or CI-specific absolute paths, so they are retained only for
migration history and are not active Story Engine workflow entrypoints.

The active product packaging entrypoint is
`.github/workflows/story-engine-build-linux-flatpak.yml`. It builds the Story
Engine Tauri Debian bundle and produces an unsigned
`com.storyengine.desktop` Flatpak artifact for release-environment inspection.
