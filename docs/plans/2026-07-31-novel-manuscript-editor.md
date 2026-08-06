# Novel Manuscript Editor Integration

## Decision

The manuscript workspace uses the published `novel@1.0.2` React editor as its
editor boundary. Novel's Tiptap 2 dependency line is kept intact; the product
does not introduce Tiptap 3, copy Novel into an internal fork, or retain a raw
`contentEditable` fallback and call it a production editor.

Jan remains the desktop shell and primary page template. The chapter toolbar,
navigation controls, save actions, and inspector controls use the repository's
shadcn/ui primitives.

## Component boundary

`web-app/src/editor/NovelManuscriptEditor.tsx` is a domain-neutral adapter. It
accepts Novel/Tiptap JSON content, emits the updated JSON plus plain text, and
contains only presentation-level formatting commands. It does not call a model,
write project files, or decide whether prose introduces canonical facts.

The manuscript feature owns scene title, dirty state, source-event context,
fact-difference state, and Story Engine requests. A page can edit a local draft,
but it may only report a formal save after the Python Story Engine accepts the
scene mutation.

## Data boundary

- Novel/Tiptap JSON is the in-memory editing representation.
- Scene Markdown below the project `scenes/` directory is canonical.
- Python converts accepted editor text into canonical Markdown and performs
  atomic writes.
- Writer generation receives confirmed Story Events only.
- A manuscript review that discovers a new fact returns an Event Amendment
  candidate. The editor never writes that fact into world or character Canon.

## Initial delivery

1. Replace the manuscript placeholder with the real Novel adapter.
2. Use shadcn/ui buttons for formatting, generation, and save controls.
3. Preserve the chapter/scene navigator and source-event inspector.
4. Track title and body changes as an unsaved draft; disable save when clean.
5. Connect scene loading and saving to explicit Story Engine contracts before
   presenting a successful canonical save.

## Verification

- Component tests prove the page mounts the Novel adapter and has no raw editor
  fallback.
- Interaction tests prove title/body edits set dirty state and clean drafts
  cannot be saved.
- Story Engine tests prove only approved events can be Writer sources and scene
  Markdown is written atomically.
- Contract generation, lint, Web tests, Python tests, production build, and
  wide/narrow visual checks must pass.
