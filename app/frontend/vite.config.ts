/// <reference types="vitest/config" />
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { fileURLToPath } from 'node:url';
import { bmTokensCss } from './build/tokensCssPlugin.ts';

const repoRoot = fileURLToPath(new URL('../..', import.meta.url));

// Админка отдаётся бэкендом со статикой по /admin/ (12_plan.md §5), маршруты — в hash (/admin/#/...).
export default defineConfig({
  base: '/admin/',
  plugins: [react(), bmTokensCss()],
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    sourcemap: false,
    // Без modulepreload-полифилла: он не нужен целевым браузерам и не тянет лишний код.
    modulePreload: { polyfill: false },
    chunkSizeWarningLimit: 2000,
  },
  server: {
    // design/tokens.json лежит вне app/frontend — разрешаем dev-серверу читать корень проекта.
    fs: { allow: [repoRoot] },
    // Только для локальной разработки (npm run dev): API поднят отдельно. В сборку не попадает.
    proxy: process.env.BM_DEV_API ? { '/api': { target: process.env.BM_DEV_API, changeOrigin: false } } : undefined,
  },
  test: {
    include: ['src/**/*.test.{ts,tsx}'],
    environment: 'node',
    restoreMocks: true,
    setupFiles: ['src/__tests__/support/setup.ts'],
    // Предел на тест с запасом под общую нагрузку среды (см. setup.ts).
    testTimeout: 30_000,
  },
});
