// Карта ресурсов react-admin → операции 12_openapi.yaml (v1.0.2).
// Каждая строка сверяется с контрактом тестом resources.contract.test.ts: флаги и фильтры должны
// совпадать с объявленными операциями и параметрами ровно, без «лишних» и «забытых».
// Операции вне CRUD (лимиты PUT, revoke, preview, runImport, курсы PUT, отчёты) — в задачах экранов.

export interface ListConfig {
  /** Операция объявляет page и page_size. Иначе сервер отдаёт весь список, листаем на клиенте. */
  paginated: boolean;
  /** Операция объявляет sort и order. */
  sortable: boolean;
  /** Query-параметры фильтра, кроме page, page_size, sort, order. */
  filters: readonly string[];
}

export interface ResourceConfig {
  /** Путь коллекции относительно /api/v1/. */
  path: string;
  /** Поле-идентификатор записи; react-admin получает его копию в `id`. */
  key: string;
  list: ListConfig;
  /** GET /<path>/{key}. Если нет — getOne ищет запись в полном списке (только для списков без пагинации). */
  getOne: boolean;
  /** POST /<path>. */
  create: boolean;
  /** PATCH /<path>/{key}. */
  update: boolean;
  /** DELETE /<path>/{key}. */
  delete: boolean;
}

const r = (cfg: Partial<ResourceConfig> & Pick<ResourceConfig, 'path'> & { list: ListConfig }): ResourceConfig => ({
  key: 'id',
  getOne: false,
  create: false,
  update: false,
  delete: false,
  ...cfg,
});

export const RESOURCES = {
  departments: r({
    path: 'departments',
    list: { paginated: true, sortable: true, filters: ['q'] },
    getOne: true,
    create: true,
    update: true,
  }),
  employees: r({
    path: 'employees',
    key: 'pseudonym',
    list: { paginated: true, sortable: false, filters: ['department_id'] },
    getOne: true,
  }),
  models: r({
    path: 'models',
    list: { paginated: true, sortable: true, filters: [] },
    getOne: true,
    create: true,
    update: true,
  }),
  // Имя ресурса = маршрут экрана (design/screens.md §5: /admin/#/fx-rates), как у admin-users.
  'fx-rates': r({
    path: 'fx-rates',
    key: 'month',
    list: { paginated: false, sortable: false, filters: [] },
  }),
  subscriptions: r({
    path: 'subscriptions',
    list: { paginated: true, sortable: false, filters: ['department_id', 'active_on'] },
    getOne: true,
    create: true,
    update: true,
    delete: true,
  }),
  providerSources: r({
    path: 'provider-sources',
    list: { paginated: false, sortable: false, filters: [] },
    create: true,
    update: true,
    delete: true,
  }),
  callers: r({
    path: 'callers',
    list: { paginated: false, sortable: false, filters: [] },
    create: true,
    update: true,
  }),
  // Имя ресурса = маршрут экрана (design/screens.md §10: /admin/#/admin-users).
  'admin-users': r({
    path: 'admin-users',
    list: { paginated: false, sortable: false, filters: [] },
    create: true,
    update: true,
  }),
  importRuns: r({
    path: 'imports/runs',
    list: { paginated: true, sortable: false, filters: ['provider'] },
  }),
  notifications: r({
    path: 'notifications',
    list: { paginated: true, sortable: false, filters: ['unread'] },
  }),
  events: r({
    path: 'events',
    list: { paginated: true, sortable: false, filters: ['from', 'to', 'department_id', 'outcome', 'code', 'flag'] },
  }),
  audit: r({
    path: 'audit',
    list: { paginated: true, sortable: false, filters: ['from', 'to', 'object_type'] },
  }),
} as const satisfies Record<string, ResourceConfig>;

export type ResourceName = keyof typeof RESOURCES;

/** Ограничение контракта для page_size (components.parameters.PageSize). */
export const MAX_PAGE_SIZE = 200;
