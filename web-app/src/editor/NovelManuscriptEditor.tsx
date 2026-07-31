import type { Editor as TiptapEditor } from '@tiptap/core'
import {
  EditorContent,
  EditorRoot,
  Placeholder,
  StarterKit,
  useEditor,
  type JSONContent,
} from 'novel'
import {
  Bold,
  Heading2,
  Italic,
  List,
  ListOrdered,
  Quote,
  Redo2,
  Undo2,
} from 'lucide-react'
import { useMemo } from 'react'

import { Button } from '@/components/ui/button'
import { Separator } from '@/components/ui/separator'
import { cn } from '@/lib/utils'

export interface NovelManuscriptValue {
  content: JSONContent
  text: string
}

interface NovelManuscriptEditorProps {
  initialContent: JSONContent
  onChange: (value: NovelManuscriptValue) => void
  readOnly?: boolean
}

const formattingActions = [
  {
    label: '粗体',
    icon: Bold,
    active: (editor: TiptapEditor) => editor.isActive('bold'),
    run: (editor: TiptapEditor) => editor.chain().focus().toggleBold().run(),
  },
  {
    label: '斜体',
    icon: Italic,
    active: (editor: TiptapEditor) => editor.isActive('italic'),
    run: (editor: TiptapEditor) => editor.chain().focus().toggleItalic().run(),
  },
  {
    label: '小标题',
    icon: Heading2,
    active: (editor: TiptapEditor) => editor.isActive('heading', { level: 2 }),
    run: (editor: TiptapEditor) =>
      editor.chain().focus().toggleHeading({ level: 2 }).run(),
  },
  {
    label: '项目列表',
    icon: List,
    active: (editor: TiptapEditor) => editor.isActive('bulletList'),
    run: (editor: TiptapEditor) =>
      editor.chain().focus().toggleBulletList().run(),
  },
  {
    label: '编号列表',
    icon: ListOrdered,
    active: (editor: TiptapEditor) => editor.isActive('orderedList'),
    run: (editor: TiptapEditor) =>
      editor.chain().focus().toggleOrderedList().run(),
  },
  {
    label: '引用',
    icon: Quote,
    active: (editor: TiptapEditor) => editor.isActive('blockquote'),
    run: (editor: TiptapEditor) =>
      editor.chain().focus().toggleBlockquote().run(),
  },
] as const

function ManuscriptToolbar() {
  const { editor } = useEditor()
  if (!editor) return null

  return (
    <div
      aria-label="正文格式工具栏"
      className="sticky top-0 z-10 flex min-h-11 flex-wrap items-center gap-0.5 border-b bg-background/95 px-2 backdrop-blur"
      role="toolbar"
    >
      <Button
        aria-label="撤销"
        disabled={!editor.can().undo()}
        onClick={() => editor.chain().focus().undo().run()}
        size="icon-sm"
        type="button"
        variant="ghost"
      >
        <Undo2 />
      </Button>
      <Button
        aria-label="重做"
        disabled={!editor.can().redo()}
        onClick={() => editor.chain().focus().redo().run()}
        size="icon-sm"
        type="button"
        variant="ghost"
      >
        <Redo2 />
      </Button>
      <Separator className="mx-1 h-5" orientation="vertical" />
      {formattingActions.map((action) => {
        const Icon = action.icon
        return (
          <Button
            aria-label={action.label}
            aria-pressed={action.active(editor)}
            className={cn(action.active(editor) && 'bg-accent')}
            key={action.label}
            onClick={() => action.run(editor)}
            size="icon-sm"
            type="button"
            variant="ghost"
          >
            <Icon />
          </Button>
        )
      })}
    </div>
  )
}

export function NovelManuscriptEditor({
  initialContent,
  onChange,
  readOnly = false,
}: NovelManuscriptEditorProps) {
  const extensions = useMemo(
    () => [
      StarterKit.configure({
        heading: { levels: [2, 3] },
      }),
      Placeholder.configure({
        placeholder: '开始写这个场景…',
      }),
    ],
    []
  )

  return (
    <div
      className="min-h-[500px] bg-background"
      data-testid="novel-manuscript-editor"
    >
      <EditorRoot>
        <EditorContent
          editable={!readOnly}
          editorProps={{
            attributes: {
              'aria-label': '场景正文',
              class:
                'prose prose-neutral dark:prose-invert min-h-[456px] max-w-none px-8 py-8 text-[17px] leading-8 outline-none md:px-12 [&_blockquote]:border-l-2 [&_blockquote]:pl-4 [&_h2]:mt-8 [&_h2]:font-studio [&_h2]:text-xl [&_ol]:list-decimal [&_ol]:pl-6 [&_p]:my-5 [&_ul]:list-disc [&_ul]:pl-6',
            },
          }}
          extensions={extensions}
          initialContent={initialContent}
          onUpdate={({ editor }) =>
            onChange({
              content: editor.getJSON(),
              text: editor.getText({ blockSeparator: '\n\n' }),
            })
          }
          slotBefore={readOnly ? null : <ManuscriptToolbar />}
        />
      </EditorRoot>
    </div>
  )
}

export type { JSONContent }
