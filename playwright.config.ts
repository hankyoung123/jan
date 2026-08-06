import { defineConfig, devices } from '@playwright/test'

export const minimumDesktopViewport = {
  width: 1024,
  height: 740,
} as const

export default defineConfig({
  testDir: './tests/visual',
  fullyParallel: false,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? 'github' : 'list',
  use: {
    baseURL: 'http://127.0.0.1:4174',
    viewport: minimumDesktopViewport,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [
    {
      name: 'minimum-desktop-chromium',
      use: {
        ...devices['Desktop Chrome'],
        viewport: minimumDesktopViewport,
      },
    },
  ],
  webServer: {
    command:
      'corepack yarn workspace @janhq/web-app dev --host 127.0.0.1 --port 4174 --strictPort',
    url: 'http://127.0.0.1:4174/submission',
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
})
