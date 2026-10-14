// schema.d.ts должен быть сгенерирован из текущего 12_openapi.yaml (`npm run gen:api`).
// Если контракт изменился, а типы нет, — тест падает с подсказкой.
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import openapiTS, { astToString } from 'openapi-typescript';

describe('src/api/schema.d.ts', () => {
  it('совпадает с выводом openapi-typescript для 12_openapi.yaml', async () => {
    const specUrl = new URL('../../../../12_openapi.yaml', import.meta.url);
    const generated = astToString(await openapiTS(specUrl, { silent: true }));
    const committed = readFileSync(fileURLToPath(new URL('../api/schema.d.ts', import.meta.url)), 'utf8');
    const strip = (s: string) => s.replace(/^\/\*\*[\s\S]*?\*\/\s*/, '').trim();
    expect(strip(committed), 'запустите npm run gen:api').toBe(strip(generated));
  }, 30_000);
});
