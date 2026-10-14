// dataProvider react-admin поверх 12_openapi.yaml: списки {items,total}, page (с 1), page_size (≤ 200),
// sort, order (asc|desc). Ходит только в операции из карты RESOURCES, которая сверена с контрактом.
import { HttpError, type DataProvider, type GetListParams, type Identifier, type RaRecord } from 'ra-core';
import { MAX_PAGE_SIZE, RESOURCES, type ResourceConfig } from './api/resources';
import type { HttpClient, QueryValue } from './api/http';
import { ru } from './i18n/ru';

type Row = Record<string, unknown>;
type ListBody = { items: Row[]; total: number };

const fmt = (template: string, vars: Record<string, string>) =>
  template.replace(/%\{(\w+)\}/g, (_, k: string) => vars[k] ?? '');

function config(resource: string): ResourceConfig {
  const cfg = (RESOURCES as Record<string, ResourceConfig>)[resource];
  if (!cfg) throw new Error(fmt(ru.bm.error.unknown_resource, { resource }));
  return cfg;
}

function ensure(cfg: ResourceConfig, resource: string, operation: 'getOne' | 'create' | 'update' | 'delete') {
  if (!cfg[operation]) throw new Error(fmt(ru.bm.error.unsupported_operation, { operation, resource }));
}

const itemPath = (cfg: ResourceConfig, id: Identifier) => `/${cfg.path}/${encodeURIComponent(String(id))}`;

const withId = (cfg: ResourceConfig, row: Row): RaRecord => ({ ...row, id: row[cfg.key] as Identifier });

/** Изменённые поля для PATCH: схемы запросов закрыты (additionalProperties: false), лишнее даёт 422. */
export function changedFields(data: Row, previous: Row | undefined): Row {
  if (!previous) return data;
  const diff: Row = {};
  for (const [k, v] of Object.entries(data)) {
    if (JSON.stringify(v) !== JSON.stringify(previous[k])) diff[k] = v;
  }
  return diff;
}

function listQuery(cfg: ResourceConfig, resource: string, params: GetListParams): Record<string, QueryValue> {
  const query: Record<string, QueryValue> = {};
  const { page, perPage } = params.pagination ?? { page: 1, perPage: 25 };
  if (cfg.list.paginated) {
    if (perPage > MAX_PAGE_SIZE) throw new Error(ru.bm.error.page_size);
    query.page = page;
    query.page_size = perPage;
  }
  if (cfg.list.sortable && params.sort?.field) {
    query.sort = params.sort.field === 'id' ? cfg.key : params.sort.field;
    query.order = params.sort.order === 'DESC' ? 'desc' : 'asc';
  }
  for (const [name, value] of Object.entries(params.filter ?? {})) {
    if (value === undefined || value === null || value === '') continue;
    if (!cfg.list.filters.includes(name)) throw new Error(fmt(ru.bm.error.unknown_filter, { filter: name, resource }));
    query[name] = value as QueryValue;
  }
  return query;
}

const collator = new Intl.Collator('ru', { numeric: true, sensitivity: 'base' });

/** Для списков без пагинации в контракте: сортировка и страницы на клиенте. */
function pageLocally(rows: RaRecord[], params: GetListParams): RaRecord[] {
  const { field, order } = params.sort ?? { field: '', order: 'ASC' };
  const sorted = field
    ? [...rows].sort((a, b) => {
        const c = collator.compare(String(a[field] ?? ''), String(b[field] ?? ''));
        return order === 'DESC' ? -c : c;
      })
    : rows;
  const { page, perPage } = params.pagination ?? { page: 1, perPage: sorted.length };
  return sorted.slice((page - 1) * perPage, page * perPage);
}

export function createDataProvider(http: HttpClient): DataProvider {
  async function fetchList(resource: string, params: GetListParams) {
    const cfg = config(resource);
    const query = listQuery(cfg, resource, params);
    const { body } = await http.request<ListBody>('GET', `/${cfg.path}`, { query });
    const items = (body?.items ?? []).map((row) => withId(cfg, row));
    if (cfg.list.paginated) return { data: items, total: body?.total ?? items.length };
    return { data: pageLocally(items, params), total: items.length };
  }

  async function fetchOne(resource: string, id: Identifier): Promise<RaRecord> {
    const cfg = config(resource);
    if (cfg.getOne) {
      const { body } = await http.request<Row>('GET', itemPath(cfg, id));
      return withId(cfg, body ?? {});
    }
    // Отдельной операции нет, но список отдаётся целиком — запись берётся из него.
    if (!cfg.list.paginated) {
      const { body } = await http.request<ListBody>('GET', `/${cfg.path}`);
      const row = body?.items.find((it) => String(it[cfg.key]) === String(id));
      if (row) return withId(cfg, row);
      throw new HttpError(ru.bm.problem.not_found, 404, { code: 'not_found' });
    }
    throw new Error(fmt(ru.bm.error.unsupported_operation, { operation: 'getOne', resource }));
  }

  return {
    getList: (resource, params) => fetchList(resource, params),

    getOne: async (resource, { id }) => ({ data: await fetchOne(resource, id) }),

    getMany: async (resource, { ids }) => ({ data: await Promise.all(ids.map((id) => fetchOne(resource, id))) }),

    getManyReference: (resource, { target, id, ...rest }) =>
      fetchList(resource, { ...rest, filter: { ...rest.filter, [target]: id } }),

    create: async (resource, { data }) => {
      const cfg = config(resource);
      ensure(cfg, resource, 'create');
      const { body } = await http.request<Row>('POST', `/${cfg.path}`, { body: data });
      return { data: withId(cfg, body ?? {}) as never };
    },

    update: async (resource, { id, data, previousData }) => {
      const cfg = config(resource);
      ensure(cfg, resource, 'update');
      const { body } = await http.request<Row>('PATCH', itemPath(cfg, id), {
        body: changedFields(data as Row, previousData as Row | undefined),
      });
      return { data: withId(cfg, body ?? {}) as never };
    },

    updateMany: async (resource, { ids, data }) => {
      const cfg = config(resource);
      ensure(cfg, resource, 'update');
      for (const id of ids) await http.request('PATCH', itemPath(cfg, id), { body: data });
      return { data: ids };
    },

    delete: async (resource, { id, previousData }) => {
      const cfg = config(resource);
      ensure(cfg, resource, 'delete');
      await http.request('DELETE', itemPath(cfg, id));
      return { data: (previousData ?? { id }) as never };
    },

    deleteMany: async (resource, { ids }) => {
      const cfg = config(resource);
      ensure(cfg, resource, 'delete');
      for (const id of ids) await http.request('DELETE', itemPath(cfg, id));
      return { data: ids };
    },
  };
}
