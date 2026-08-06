/** Roles and wire messages shared by cloud chat requests. */
export enum ChatCompletionRole {
  System = 'system',
  Assistant = 'assistant',
  User = 'user',
  Tool = 'tool',
}

export type ChatCompletionMessageContent =
  | string
  | Array<
      | { type: 'text'; text: string }
      | { type: 'image_url'; image_url: { url: string } }
      | { type: 'doc_url'; doc_url: { url: string } }
    >

export type ChatCompletionMessage = {
  content?: ChatCompletionMessageContent
  role: ChatCompletionRole
  type?: string
  output?: string
  tool_call_id?: string
  reasoning?: string | null
  reasoning_content?: string | null
}

export type chatCompletionRequestMessage = ChatCompletionMessage
