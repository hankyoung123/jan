# License Inventory

| Component | Version | License | Included code |
|---|---:|---|---|
| Jan repository root | v0.8.4 | Apache-2.0 | Fork baseline; root `LICENSE` |
| Jan assistant/download/llama.cpp/MLX extensions | v0.8.4 workspace | AGPL-3.0 in package manifests | Present; closed-release license review required |
| Jan conversational extension | v0.8.4 workspace | MIT | Present temporarily with the general chat domain |
| Jan RAG and vector DB extensions | v0.8.4 workspace | AGPL-3.0 | Excluded from source and distribution |
| Concordia | 2.4.0 | Apache-2.0 | Unmodified Python dependency; exact text in `Concordia-2.4.0-APACHE-2.0.txt` |
| Tauri | 2.x | Apache-2.0 / MIT | Linked dependency |
| React / React DOM | 19.x | MIT | Bundled dependency |
| React Router | 7.x | MIT | Bundled dependency |
| Lucide React | 0.468.x | ISC | Bundled dependency |
| Source Sans 3 | 5.2.x package | OFL-1.1 | Bundled font files |
| Newsreader | 5.2.x package | OFL-1.1 | Bundled font files |

## Exact license texts

- `AGPL-3.0.txt` is the GNU Affero General Public License version 3 text for
  the retained Jan packages that declare `AGPL-3.0`.
- The presence of this text records the applicable license; it does not make
  the current combination safe to distribute as closed source. Before a
  release, choose a compliant AGPL distribution model, replace the affected
  packages, or obtain another license from the relevant copyright holders.

The root `LICENSE` is Jan's exact Apache-2.0 license from the locked baseline;
it does not override more specific package manifests. Before another upstream
source or binary is distributed, add its exact license text and update
`THIRD_PARTY_NOTICES.md` with paths and modification details.

Every desktop Tauri configuration bundles `THIRD_PARTY_NOTICES.md` and this
directory beside the application resources. The macOS, Windows, and Linux
packaging smoke checks fail when either the notices, the AGPL text, or the
Concordia license text is missing from the produced artifact.
