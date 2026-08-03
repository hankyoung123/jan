import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import path from 'path'

export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    globals: true,
    css: true,
    testTimeout: 30000,
    hookTimeout: 30000,
    server: {
      deps: {
        // Novel's ESM bundle statically exposes its optional tweet node. Keep
        // that dependency chain inside Vite so CSS imports are transformed in
        // jsdom instead of being handed directly to Node.
        inline: ['novel', 'react-tweet'],
      },
    },
    coverage: {
      reporter: ['text', 'json', 'html', 'lcov'],
      include: ['src/**/*.{ts,tsx}'],
      exclude: [
        'node_modules/',
        'dist/',
        'coverage/',
        'src/**/*.test.ts',
        'src/**/*.test.tsx',
        'src/test/**/*',
      ],
    },
  },
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
      '@janhq/assistant-extension': path.resolve(__dirname, '../extensions/assistant-extension/dist/index.js'),
      '@janhq/conversational-extension': path.resolve(__dirname, '../extensions/conversational-extension/dist/index.js'),
    },
  },
  define: {
    IS_TAURI: JSON.stringify(false),
    IS_WEB_APP: JSON.stringify(false),
    IS_MACOS: JSON.stringify(false),
    IS_WINDOWS: JSON.stringify(false),
    IS_LINUX: JSON.stringify(false),
    IS_IOS: JSON.stringify(false),
    IS_ANDROID: JSON.stringify(false),
    PLATFORM: JSON.stringify('web'),
    VERSION: JSON.stringify('test'),
    POSTHOG_KEY: JSON.stringify(''),
    POSTHOG_HOST: JSON.stringify(''),
    AUTO_UPDATER_DISABLED: JSON.stringify('false'),
  },
})
