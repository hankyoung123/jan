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

const simulationSession = {
  session_id: 'session:visual',
  project_id: 'fog-harbor',
  branch_id: 'main',
  status: 'created',
  pending_control: 'none',
  content_locale: 'zh-CN',
  request: {
    project_id: 'fog-harbor',
    branch_id: 'main',
    premise_text: '灯塔突然熄灭',
    actor_ids: ['chen-mo', 'lin-lan'],
    content_locale: 'zh-CN',
    control: {
      mode: 'scene',
      pause_after_scene: true,
      max_steps: 100,
      max_scenes: 12,
      max_total_tokens: 500000,
      max_runtime_seconds: 3600,
      max_consecutive_model_failures: 3,
      allow_dynamic_entities: true,
      allow_user_override: true,
      checkpoint_every_steps: 5,
    },
    seed: null,
  },
  active_entity_ids: ['chen-mo', 'lin-lan'],
  dynamic_entities: [],
  current_step: 0,
  completed_scenes: 0,
  actor_states: {},
  game_master_states: {},
  memory_snapshots: {},
  raw_log_offset: 0,
  total_model_tokens: 0,
  consecutive_model_failures: 0,
  checkpoint_id: `checkpoint-${'a'.repeat(64)}`,
  started_at: '2026-08-02T00:00:00Z',
  updated_at: '2026-08-02T00:00:00Z',
  termination_reason_text: null,
  state_hash: 'a'.repeat(64),
  active_actor_id: null,
  current_action_spec: null,
}

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
    if (path === '/projects/fog-harbor') {
      await route.fulfill({
        json: {
          project: {
            schema: 'project/v1',
            id: 'fog-harbor',
            title: '雾港',
            genre: '悬疑',
            theme: '真相与信任',
            tone: '冷峻、克制',
            version: 0,
          },
          world: {
            current_time: '午夜',
            current_location: '雾港',
            rules: ['浓雾阻断远距离视线'],
            active_pressures: ['客船即将进港'],
            public_fact_ids: [],
            world_variables: {},
            version: 0,
          },
          facts: [],
          characters: [
            {
              id: 'chen-mo',
              display_name: '陈默',
              type: 'active',
              location: '灯塔入口',
              current_goal: '查明灯塔熄灭原因',
              core_desire: '找到真相',
              identity: '灯塔守望员',
              known_fact_ids: [],
              relationships: [],
              emotional_state: '警觉',
              resources: ['手电筒'],
              last_event_id: null,
              version: 0,
            },
            {
              id: 'lin-lan',
              display_name: '林岚',
              type: 'active',
              location: '港务所',
              current_goal: '让客船安全进港',
              core_desire: '保护船只',
              identity: '港务员',
              known_fact_ids: [],
              relationships: [],
              emotional_state: '焦急',
              resources: ['无线电'],
              last_event_id: null,
              version: 0,
            },
          ],
        },
      })
      return
    }
    if (path === '/projects/fog-harbor/simulations') {
      await route.fulfill({ json: [simulationSession] })
      return
    }
    if (path === '/projects/fog-harbor/simulations/session:visual') {
      await route.fulfill({ json: simulationSession })
      return
    }
    if (path === '/projects/fog-harbor/branches') {
      await route.fulfill({
        json: [
          {
            branch_id: 'main',
            project_id: 'fog-harbor',
            head_checkpoint_id: simulationSession.checkpoint_id,
            head_step: 0,
            parent_branch_id: null,
            fork_checkpoint_id: null,
            content_locale: 'zh-CN',
            created_at: '2026-08-02T00:00:00Z',
            updated_at: '2026-08-02T00:00:00Z',
          },
        ],
      })
      return
    }
    if (
      path === '/projects/fog-harbor/branches/main/simulation-trace' ||
      path === '/projects/fog-harbor/simulation-events'
    ) {
      await route.fulfill({ json: [] })
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
    if (name === 'evolve') {
      await page.addInitScript(() => {
        localStorage.setItem('story-engine.active-project-id', 'fog-harbor')
      })
    }
    await page.goto(path)
    await expect(page.locator('#initial-loader')).toHaveCount(0, {
      timeout: 45_000,
    })
    await expect(page.getByRole('link', { name: '投稿', exact: true })).toBeVisible()
    if (name === 'evolve') {
      await expect(page.getByText('Participant state')).toBeVisible()
      await expect(page.getByText('Current step pipeline')).toBeVisible()
    }

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
