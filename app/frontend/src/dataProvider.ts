// dataProvider react-admin поверх 12_openapi.yaml: списки {items,total}, page (с 1), page_size (≤ 200),
// sort, order (asc|desc). Ходит только в операции из карты RESOURCES, которая сверена с контрактом.
import {
  HttpError,
  type DataProvider,
  type GetListParams,
  type GetListResult,
  type GetManyResult,
  type Identifier,
  type RaRecord,
} from 'ra-core';
import { MAX_PAGE_SIZE, RESOURCES, type ResourceConfig } from './api/resources';
import type { HttpClient, QueryValue } from './api/http';
import { ru } from './i18n/ru';
import type { Schemas } from './api/types';

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

/** Операции контракта вне CRUD react-admin; экраны вызывают их через useDataProvider<BmDataProvider>(). */
export interface BmOperations {
  putDepartmentLimits(departmentId: Identifier, limits: Schemas['LimitSet']): Promise<Schemas['LimitSet']>;
  putEmployeeLimits(pseudonym: string, limits: Schemas['LimitSet']): Promise<Schemas['LimitSet']>;
  revealPseudonym(pseudonym: string): Promise<Schemas['EmployeeIdentity']>;
  listModelPrices(modelId: Identifier): Promise<Schemas['ModelPrice'][]>;
  createModelPrice(modelId: Identifier, body: Schemas['ModelPriceCreate']): Promise<Schemas['ModelPrice']>;
  revokeCaller(callerId: Identifier): Promise<Schemas['Caller']>;
  importOrg(file: File, dryRun: boolean): Promise<Schemas['OrgImportSummary']>;
  previewSubscription(body: Schemas['SubscriptionCreate']): Promise<Schemas['SubscriptionPreview']>;
  endSubscription(subscriptionId: Identifier, endDate: string): Promise<Schemas['Subscription']>;
  putFxRate(month: string, usdRub: string): Promise<Schemas['FxRate']>;
}

export type BmDataProvider = DataProvider & BmOperations;

const seg = (id: Identifier) => encodeURIComponent(String(id));

function required<T>(body: T | undefined, what: string): T {
  if (body === undefined) throw new Error(`${what}: пустой ответ`);
  return body;
}

export function createDataProvider(http: HttpClient): BmDataProvider {
  const operations: BmOperations = {
    async putDepartmentLimits(departmentId, limits) {
      const { body } = await http.request<Schemas['LimitSet']>('PUT', `/departments/${seg(departmentId)}/limits`, {
        body: limits,
      });
      return required(body, 'putDepartmentLimits');
    },
    async putEmployeeLimits(pseudonym, limits) {
      const { body } = await http.request<Schemas['LimitSet']>('PUT', `/employees/${seg(pseudonym)}/limits`, {
        body: limits,
      });
      return required(body, 'putEmployeeLimits');
    },
    async revealPseudonym(pseudonym) {
      const { body } = await http.request<Schemas['EmployeeIdentity']>('GET', `/pseudonyms/${seg(pseudonym)}`);
      return required(body, 'revealPseudonym');
    },
    async listModelPrices(modelId) {
      const { body } = await http.request<{ items: Schemas['ModelPrice'][] }>('GET', `/models/${seg(modelId)}/prices`);
      return required(body, 'listModelPrices').items;
    },
    async createModelPrice(modelId, price) {
      const { body } = await http.request<Schemas['ModelPrice']>('POST', `/models/${seg(modelId)}/prices`, {
        body: price,
      });
      return required(body, 'createModelPrice');
    },
    async revokeCaller(callerId) {
      const { body } = await http.request<Schemas['Caller']>('POST', `/callers/${seg(callerId)}/revoke`);
      return required(body, 'revokeCaller');
    },
    async importOrg(file, dryRun) {
      const form = new FormData();
      form.append('file', file);
      form.append('dry_run', String(dryRun));
      const { body } = await http.request<Schemas['OrgImportSummary']>('POST', '/org/import', { body: form });
      return required(body, 'importOrg');
    },
    async previewSubscription(subscription) {
      const { body } = await http.request<Schemas['SubscriptionPreview']>('POST', '/subscriptions/preview', {
        body: subscription,
      });
      return required(body, 'previewSubscription');
    },
    async endSubscription(subscriptionId, endDate) {
      const patch: Schemas['SubscriptionPatch'] = { end_date: endDate };
      const { body } = await http.request<Schemas['Subscription']>('PATCH', `/subscriptions/${seg(subscriptionId)}`, {
        body: patch,
      });
      return required(body, 'endSubscription');
    },
    async putFxRate(month, usdRub) {
      const put: Schemas['FxRatePut'] = { usd_rub: usdRub };
      const { body } = await http.request<Schemas['FxRate']>('PUT', `/fx-rates/${seg(month)}`, { body: put });
      return required(body, 'putFxRate');
    },
  };

  // Методы DataProvider обобщены по RecordType, который задаёт вызывающий экран. Транспорт видит только JSON,
  // поэтому тип записи — утверждение вызывающего; приведение сделано в одном месте, здесь.
  async function fetchList<R extends RaRecord>(resource: string, params: GetListParams): Promise<GetListResult<R>> {
    const cfg = config(resource);
    const query = listQuery(cfg, resource, params);
    const { body } = await http.request<ListBody>('GET', `/${cfg.path}`, { query });
    const items = (body?.items ?? []).map((row) => withId(cfg, row));
    const data = cfg.list.paginated ? items : pageLocally(items, params);
    return { data: data as R[], total: cfg.list.paginated ? (body?.total ?? items.length) : items.length };
  }

  async function fetchOne<R extends RaRecord>(resource: string, id: Identifier): Promise<R> {
    const cfg = config(resource);
    if (cfg.getOne) {
      const { body } = await http.request<Row>('GET', itemPath(cfg, id));
      return withId(cfg, body ?? {}) as R;
    }
    // Отдельной операции нет, но список отдаётся целиком — запись берётся из него.
    if (!cfg.list.paginated) {
      const { body } = await http.request<ListBody>('GET', `/${cfg.path}`);
      const row = body?.items.find((it) => String(it[cfg.key]) === String(id));
      if (row) return withId(cfg, row) as R;
      throw new HttpError(ru.bm.problem.not_found, 404, { code: 'not_found' });
    }
    throw new Error(fmt(ru.bm.error.unsupported_operation, { operation: 'getOne', resource }));
  }

  async function fetchMany<R extends RaRecord>(resource: string, ids: Identifier[]): Promise<GetManyResult<R>> {
    return { data: await Promise.all(ids.map((id) => fetchOne<R>(resource, id))) };
  }

  return {
    ...operations,
    getList: (resource, params) => fetchList(resource, params),

    getOne: async (resource, { id }) => ({ data: await fetchOne(resource, id) }),

    getMany: (resource, { ids }) => fetchMany(resource, ids),

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
