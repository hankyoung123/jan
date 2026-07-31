import { expect, test, type Page, type TestInfo } from '@playwright/test'

import { minimumDesktopViewport } from '../../playwright.config'

const profiles = [
  ['character', 'Character', 'character', 'llamacpp', 'qwen3-8b', 0.7],
  ['resolver', 'Resolver', 'resolver', 'openai', 'gpt-5-mini', 0.2],
  ['editor', 'Editor', 'editor', 'openai', 'gpt-5-mini', 0.1],
  ['writer', 'Writer', 'writer', 'openai', 'gpt-5-mini', 0.8],
  ['embedding', 'Embedding', 'embedding', 'llamacpp', 'bge-m3', null],
].map(([id, name, taskType, providerId, model, temperature]) => ({
  id,
  name,
  task_type: taskType,
  provider_id: providerId,
  model,
  max_output_tokens: 2048,
  timeout_seconds: 60,
  temperature,
  enabled: true,
}))

const primaryRoutes = [
  ['submission', '/submission'],
  ['workbench', '/'],
  ['evolve', '/evolve'],
  ['characters', '/characters'],
  ['world', '/world'],
  ['events', '/events'],
  ['manuscript', '/manuscript'],
  ['model-center', '/hub/'],
  ['settings', '/settings/general'],
] as const

async function mockOperationalData(page: Page) {
  await page.route('http://127.0.0.1:39281/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    if (path === '/models/profiles') {
      await route.fulfill({ json: profiles })
      return
    }
    if (path === '/models/usage') {
      await route.fulfill({
        json: {
          requests: 0,
          prompt_tokens: 0,
          completion_tokens: 0,
          total_tokens: 0,
        },
      })
      return
    }
    await route.fulfill({ status: 404, json: { detail: 'not required' } })
  })

  await page.route(
    'https://raw.githubusercontent.com/janhq/model-catalog/**',
    (route) => route.fulfill({ json: [] })
  )
}

test('all primary pages fit the Tauri minimum desktop window', async ({
  page,
}, testInfo: TestInfo) => {
  test.setTimeout(180_000)
  expect(testInfo.project.use.viewport).toEqual(minimumDesktopViewport)
  await mockOperationalData(page)

  for (const [name, path] of primaryRoutes) {
    await page.goto(path)
    await expect(page.locator('#initial-loader')).toHaveCount(0, {
      timeout: 45_000,
    })
    await expect(page.getByRole('link', { name: '投稿', exact: true })).toBeVisible()

    const geometry = await page.evaluate(() => {
      const documentElement = document.documentElement
      const body = document.body
      const main = document.querySelector('main')
      return {
        viewportWidth: window.innerWidth,
        viewportHeight: window.innerHeight,
        documentClientWidth: documentElement.clientWidth,
        documentScrollWidth: documentElement.scrollWidth,
        bodyClientWidth: body.clientWidth,
        bodyScrollWidth: body.scrollWidth,
        mainClientWidth: main?.clientWidth ?? 0,
        mainScrollWidth: main?.scrollWidth ?? 0,
      }
    })

    expect(geometry.viewportWidth, `${name}: viewport width`).toBe(
      minimumDesktopViewport.width
    )
    expect(geometry.viewportHeight, `${name}: viewport height`).toBe(
      minimumDesktopViewport.height
    )
    expect(geometry.documentScrollWidth, `${name}: document overflow`).toBeLessThanOrEqual(
      geometry.documentClientWidth
    )
    expect(geometry.bodyScrollWidth, `${name}: body overflow`).toBeLessThanOrEqual(
      geometry.bodyClientWidth
    )
    expect(geometry.mainScrollWidth, `${name}: main overflow`).toBeLessThanOrEqual(
      geometry.mainClientWidth
    )

    const screenshot = await page.screenshot()
    expect(screenshot.byteLength, `${name}: non-empty screenshot`).toBeGreaterThan(
      10_000
    )
    await testInfo.attach(`${name}-minimum-window`, {
      body: screenshot,
      contentType: 'image/png',
    })
  }
})
