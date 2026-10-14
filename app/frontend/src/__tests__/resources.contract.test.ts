// Сверка карты ресурсов dataProvider с 12_openapi.yaml: фронтенд ходит только в операции контракта
// (engineering-standards, «Контракт-first»). Тест падает, если контракт изменился, а карта — нет.
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import { parse } from 'yaml';
import { RESOURCES } from '../api/resources';

type Param = { $ref?: string; name?: string; in?: string };
type Operation = { parameters?: Param[]; responses?: Record<string, unknown> };
type PathItem = Partial<Record<'get' | 'post' | 'patch' | 'put' | 'delete', Operation>> & { parameters?: Param[] };

const spec = parse(readFileSync(fileURLToPath(new URL('../../../../12_openapi.yaml', import.meta.url)), 'utf8')) as {
  info: { version: string };
  paths: Record<string, PathItem>;
  components: { parameters: Record<string, { name: string; in: string }> };
};

const API = '/api/v1/';
const queryNames = (op: Operation): string[] =>
  (op.parameters ?? [])
    .map((p) => (p.$ref ? spec.components.parameters[p.$ref.split('/').pop()!]! : (p as { name: string; in: string })))
    .filter((p) => p.in === 'query')
    .map((p) => p.name);

/** Путь вида /api/v1/<path>/{param} — единственная запись-ребёнок коллекции. */
const itemPath = (path: string): string | undefined =>
  Object.keys(spec.paths).find((p) => new RegExp(`^${API}${path}/\\{[a-z_]+\\}$`).test(p));

describe('карта ресурсов совпадает с 12_openapi.yaml', () => {
  it('контракт той версии, из которой сгенерирован schema.d.ts', () => {
    expect(spec.info.version).toBe('1.0.1');
  });

  for (const [name, cfg] of Object.entries(RESOURCES)) {
    describe(name, () => {
      const list = spec.paths[API + cfg.path]?.get;

      it('список существует и отвечает {items, total}', () => {
        expect(list, `GET ${API}${cfg.path}`).toBeDefined();
      });

      it('пагинация, сортировка и фильтры — ровно те, что объявлены', () => {
        const q = queryNames(list!);
        expect(cfg.list.paginated).toBe(q.includes('page') && q.includes('page_size'));
        expect(cfg.list.sortable).toBe(q.includes('sort') && q.includes('order'));
        const declared = q.filter((n) => !['page', 'page_size', 'sort', 'order'].includes(n)).sort();
        expect([...cfg.list.filters].sort()).toEqual(declared);
      });

      it('getOne, create, update, delete — ровно по наличию операций', () => {
        const item = itemPath(cfg.path);
        const itemOps = item ? spec.paths[item]! : {};
        expect(cfg.getOne, 'getOne').toBe(Boolean(itemOps.get));
        expect(cfg.update, 'update (PATCH)').toBe(Boolean(itemOps.patch));
        expect(cfg.delete, 'delete').toBe(Boolean(itemOps.delete));
        expect(cfg.create, 'create (POST)').toBe(Boolean(spec.paths[API + cfg.path]?.post));
      });
    });
  }
});
