# Phase 10 Desktop Release Audit

This audit records the current desktop packaging evidence and the release
conditions that still require product-owned signing and update infrastructure.

## Local macOS Evidence

The standard macOS bundle command completed successfully on 2026-08-01:

```text
corepack yarn build:icon
corepack yarn tauri build --bundles app --ci
```

The resulting `Story Engine.app` contains:

- `CFBundleDisplayName=Story Engine`;
- `CFBundleIdentifier=com.storyengine.desktop`;
- the `story-engine` URL scheme;
- the arm64 Tauri executable;
- the arm64 PyInstaller `story-engine` sidecar;
- bundled model/runtime resources and the product license.

The build emits only the existing Vite large-chunk advisory and the retained
llama.cpp dead-code warning.

The generated `Story Engine_0.1.0_aarch64.dmg` was mounted on macOS using a
temporary mount point. The app was copied into a temporary staging directory,
its bundle identifier and executable were checked, and the staged app was then
removed. No `/Applications` or user data directory was touched.

## Local Reverification

The product build and artifact smoke check were rerun on 2026-08-01 after the
MLX server SwiftPM cache was restored:

```text
corepack yarn build:tauri                         # exit 0
```

The read-only DMG check confirmed `CFBundleIdentifier=com.storyengine.desktop`
and `CFBundleDisplayName=Story Engine`, then verified executable
`Contents/MacOS/story-engine-desktop`, Python sidecar
`Contents/Resources/story-engine/story-engine`, MLX server and CLI resources
under `Contents/Resources/resources/bin/`, `Contents/Resources/LICENSE`,
`Contents/Resources/THIRD_PARTY_NOTICES.md`,
`Contents/Resources/licenses/AGPL-3.0.txt`, and
`Contents/Resources/licenses/Concordia-2.4.0-APACHE-2.0.txt`.
The temporary disk image was detached successfully and no application or user
data directory was modified.

## Release Gates Still Open

These are release-environment requirements, not local implementation claims:

1. macOS Developer ID signing and notarization credentials are required.
   `codesign --verify --deep --strict` correctly rejects the locally built
   unsigned bundle.
2. Tauri updater artifacts require a product-owned signing key, public key,
   and endpoint serving `latest.json` plus platform signatures. The source
   config intentionally has no endpoint or public key, so development builds
   cannot contact a placeholder service.
3. Windows installer launch/uninstall evidence requires a Windows runner and
   an actual signed NSIS/MSI artifact.
4. Linux package install/uninstall evidence requires a Linux runner and a
   generated `.deb` or AppImage.

The release build path now leaves updater controls enabled in production while
keeping them disabled only for `tauri dev`; a signed release configuration can
therefore activate the existing Tauri updater without changing application
code. `scripts/prepare-release-config.mjs` now creates that release-only config
from `STORY_ENGINE_VERSION`, `STORY_ENGINE_UPDATER_ENDPOINT`, and
`TAURI_UPDATER_PUBLIC_KEY`; it never writes the private signing key to disk and
does not mutate `src-tauri/tauri.conf.json`. The product-owned
`build:tauri:release` script consumes that generated file with Tauri's
`--config` merge on each supported platform; the normal development
`build:tauri` path continues to use the updater-disabled source config.

The product-owned `.github/workflows/story-engine-build-linux-flatpak.yml`
now builds the Story Engine Tauri Debian bundle, stages the Python sidecar, and
creates an unsigned `com.storyengine.desktop` Flatpak artifact. The workflow
also installs the bundle into the runner's user Flatpak installation, verifies
the application ID, and uninstalls it again. This is an executable release
check, but it has not been run by the current macOS development environment and
does not claim signing or publishing coverage.

Product-owned manual/reusable workflows now also exist for
`.github/workflows/story-engine-build-macos.yml` and
`.github/workflows/story-engine-build-windows.yml`. The macOS workflow mounts
the generated DMG at a temporary path and checks the bundle identifier,
executable, Story Engine sidecar, notices, and exact license texts before
detaching it. The Windows workflow silently installs the NSIS artifact into the
runner's temporary user profile, checks the desktop binary, sidecar, notices,
and license texts, then invokes the uninstaller. The Linux Debian staging check
enforces the same license-resource boundary before building the Flatpak. These
are executable release checks on their respective GitHub runners; they have
not been run by the current macOS development environment.

The inherited Jan NSIS template is retained only in
`docs/upstream/jan/workflows-packaging/`. The active Windows configuration uses
Tauri's default NSIS template because no custom template is configured; this
avoids carrying the template's Jan placeholders and CI-specific absolute paths
into the Story Engine build.
