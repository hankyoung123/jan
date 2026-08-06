import type { BaseExtension } from '@janhq/core'
import type { ExtensionManifest } from '@/lib/extension'

type ExtensionCtor = new (
  url: string,
  name: string,
  productName?: string,
  active?: boolean,
  description?: string,
  version?: string
) => BaseExtension

type BundledEntry = {
  load: () => Promise<{ default: ExtensionCtor }>
  name: string
  productName: string
  version: string
  description: string
  // Runs on mobile (no native plugin dependencies). Desktop-only otherwise.
  mobile?: boolean
}

// Lazily imported so the extension bundles are NOT part of the service-hub
// bootstrap graph; they load only when extensions are enumerated (after the
// hub is ready), matching the pre-bundling load order.
const ENTRIES: BundledEntry[] = [
  {
    load: () => import('@janhq/assistant-extension'),
    name: '@janhq/assistant-extension',
    productName: 'Story Assistant',
    version: '1.0.2',
    description:
      'Powers the default AI assistant that works with all your installed models.',
  },
  {
    load: () => import('@janhq/conversational-extension'),
    name: '@janhq/conversational-extension',
    productName: 'Conversational',
    version: '1.0.0',
    description: 'Enables conversations and state persistence via your file system.',
    mobile: true,
  },
]

export async function getBundledExtensions(
  opts: { mobile?: boolean } = {}
): Promise<ExtensionManifest[]> {
  const active = ENTRIES.filter((e) => (opts.mobile ? e.mobile : true))
  return Promise.all(
    active.map(async ({ load, name, productName, description, version }) => {
      const { default: Ctor } = await load()
      return {
        name,
        productName,
        url: 'built-in',
        active: true,
        description,
        version,
        extensionInstance: new Ctor(
          'built-in',
          name,
          productName,
          true,
          description,
          version
        ),
      }
    })
  )
}
