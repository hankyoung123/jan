import { describe, expect, it } from 'vitest'

import { primaryRoutes, storyRoutes } from './navigation'

describe('Story navigation', () => {
  it('exposes the story workspace and cloud-provider entry points', () => {
    expect(storyRoutes.map(({ title, url }) => [title, url])).toEqual([
      ['进入世界', '/session'],
      ['工作台', '/'],
      ['推进故事', '/evolve'],
      ['角色', '/characters'],
      ['世界设定', '/world'],
      ['事件历史', '/events'],
      ['章节正文', '/manuscript'],
    ])
    expect(primaryRoutes.map(({ title }) => title)).toEqual([
      '投稿',
      '全局设置',
    ])
  })

  it('does not expose Jan chat or chat-project routes', () => {
    const urls = [...primaryRoutes, ...storyRoutes].map(({ url }) => url)

    expect(urls).not.toContain('/threads/$threadId')
    expect(urls).not.toContain('/project')
  })
})
