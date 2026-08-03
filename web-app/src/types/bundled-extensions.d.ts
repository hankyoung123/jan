type BundledExtensionCtor = new (
  url: string,
  name: string,
  productName?: string,
  active?: boolean,
  description?: string,
  version?: string
) => import('@janhq/core').BaseExtension

declare module '@janhq/assistant-extension' {
  const ext: BundledExtensionCtor
  export default ext
}
declare module '@janhq/conversational-extension' {
  const ext: BundledExtensionCtor
  export default ext
}
