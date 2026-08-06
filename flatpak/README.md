# Linux packaging

The product-owned build entrypoint is
`.github/workflows/story-engine-build-linux-flatpak.yml`. It is deliberately
manual/reusable and produces an unsigned artifact for inspection; publishing,
signing, and installation smoke tests remain release-environment gates.

The former `ai.jan.Jan` manifest downloaded and installed a Jan `.deb`; that
would ship the wrong product and is therefore kept only as an upstream
reference under `docs/upstream/jan/flatpak/`. The Story Engine manifest
consumes a reproducible Story Engine Tauri bundle and uses the
`com.storyengine.desktop` application ID, and includes the Python Story Engine
sidecar. Do not restore the archived Jan manifest as a release entrypoint.
