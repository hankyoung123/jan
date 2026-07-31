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
code.
