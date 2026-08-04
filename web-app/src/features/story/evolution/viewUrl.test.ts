import { describe, expect, it } from 'vitest'

import { applyViewLocation, readViewLocation } from './viewUrl'

describe('viewUrl', () => {
  it('reads step, stage, actor and panel from the query string', () => {
    expect(
      readViewLocation(
        '?branch=main&session=session:abc&step=18&stage=resolution&actor=chen-mo&panel=trace'
      )
    ).toEqual({
      step: 18,
      stage: 'resolution',
      actor: 'chen-mo',
      panel: 'trace',
    })
  })

  it('ignores malformed step and unknown panel values', () => {
    expect(readViewLocation('?step=abc&panel=notes')).toEqual({
      step: undefined,
      stage: undefined,
      actor: undefined,
      panel: undefined,
    })
  })

  it('applies location while preserving branch and session', () => {
    const search = applyViewLocation(
      '?branch=main&session=session:abc',
      {
        step: 3,
        stage: 'action_spec',
        actor: 'lin-lan',
        panel: 'memory',
      }
    )
    const params = new URLSearchParams(search)

    expect(params.get('branch')).toBe('main')
    expect(params.get('session')).toBe('session:abc')
    expect(params.get('step')).toBe('3')
    expect(params.get('stage')).toBe('action_spec')
    expect(params.get('actor')).toBe('lin-lan')
    expect(params.get('panel')).toBe('memory')
  })

  it('removes view fields when omitted', () => {
    const search = applyViewLocation(
      '?step=3&stage=resolution&actor=chen-mo&panel=trace',
      {}
    )
    const params = new URLSearchParams(search)

    expect(params.has('step')).toBe(false)
    expect(params.has('stage')).toBe(false)
    expect(params.has('actor')).toBe(false)
    expect(params.has('panel')).toBe(false)
  })
})
