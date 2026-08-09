# Living Story World

> 一个真的会回应你的故事世界。

Living Story World（产品代号：AI Story Evolution Engine）是一个本地桌面形态的
Persistent AI World Simulator。用户以一个 Actor 的身份进入持续世界，用自然语言
表达 Intent；Concordia Game Master 根据当前 Reality 裁定 Outcome，只有验证后的
ResolvedEvent 能进入历史并改变 World、Actor 和 Memory。

MVP 的核心是 World Session、自由 Intent、受限 Perception、持续时间与状态、NPC
自主响应、Checkpoint、Timeline 和 Branch。小说正文、Writer、Editor、投稿和传统
多 Agent 创作流程不是当前产品主流程；保留的相关代码只能作为遗留或未来投影能力。

[Living Story World PRD v1.0](docs/product-plan.md) 是产品最高层定义。
[Architecture](docs/architecture.md) 和 [ADR-0009](docs/adr/0009-living-story-world-product-boundary.md)
描述当前工程边界；较早文档与其冲突时，以 PRD 为准。

## Upstream foundation

This branch descends directly from Jan `v0.8.4` at
`5f30aee467f08941964a83f946e2663e7ae0e01f`. Jan supplies the Tauri shell,
React infrastructure, settings, Provider management, model acquisition, and
local llama.cpp/MLX runtimes. Product branding and the general chat domain are
being replaced while the reusable model infrastructure remains intact.

Concordia is locked to `v2.4.0` at
`702998f57da71f87bf4e607abc1325ee51cca21f` and will be consumed as an
unmodified Python dependency. It is the persistent simulation kernel behind
the project-owned `concordia_runtime`, recipe, persistence, and API layers.

## Workspace

```text
web-app/             Jan-based authoritative React application
src-tauri/            Jan-based authoritative Tauri runtime and plugins
core/                 Jan model and extension contracts
extensions/           Jan model download and local inference extensions
apps/story-engine/    Python story-domain Sidecar
packages/contracts/   Generated Story Engine OpenAPI/TypeScript contracts
```

## Prerequisites

- Node.js 20 or newer
- Yarn 4.5.3 through Corepack
- Rust 1.80 or newer
- Make 3.81 or newer
- uv and Python 3.12
- macOS Apple Silicon builds: Metal Toolchain

## Development

Install the Jan workspace and Python environment:

```bash
corepack enable
yarn install
yarn bootstrap:jan
uv sync --project apps/story-engine --extra dev
```

Start the Story Engine and Jan-based web application in separate terminals:

```bash
yarn story-engine:dev
yarn dev:web
```

For the native application, use `yarn dev:tauri` after the platform-specific
Jan prerequisites and local inference binaries are available.

To prepare a release-only updater configuration, provide the product endpoint
and Tauri public key through the release environment. The command writes an
ignored `.build/tauri.release.conf.json` and leaves the development config
unchanged. Then use the release build entrypoint, which consumes that generated
configuration and enables updater artifacts:

```bash
STORY_ENGINE_VERSION=0.2.0 \
STORY_ENGINE_UPDATER_ENDPOINT=https://updates.example.com/latest.json \
TAURI_UPDATER_PUBLIC_KEY='...' \
yarn build:tauri:release
```

The private signing key is consumed by the Tauri build environment and is not
written by this command.

The optional custom HMAC update check reads
`STORY_ENGINE_UPDATE_SIGNING_KEY` at compile time. Do not commit this value or
use a development fallback. When it is absent, the custom signed check fails
closed and the standard Tauri updater path remains available for a configured
release endpoint.

## Quality gates

```bash
yarn lint
yarn typecheck
yarn test
yarn contracts:check
yarn build
```

## Attribution

Jan-derived source remains under the Apache License 2.0 with upstream notices
preserved. Jan names, logos, screenshots, and other trademark assets are not
part of the product identity. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)
and [the migration map](docs/upstream/jan-v0.8.4-migration-map.md).
