// Записи по схемам 12_openapi.yaml для тестов экранов. contractApi сверяет их с контрактом в каждом ответе.
import type { Schemas } from '../../api/types';
import { DEP2_ID, DEP_ID } from './contractApi';

export const department = (over: Partial<Schemas['Department']> = {}): Schemas['Department'] => ({
  id: DEP_ID,
  name: 'Маркетинг',
  ad_group: 'CN=AI-Marketing',
  policy_mode: 'observe',
  fail_mode: 'open',
  allowed_models: ['gigachat-pro'],
  subs_warn_pct: 50,
  limits: { day: '5000.00', week: '20000.00', month: null },
  employees_count: 12,
  ...over,
});

export const department2 = (over: Partial<Schemas['Department']> = {}) =>
  department({ id: DEP2_ID, name: 'Разработка', ad_group: null, limits: { day: null, week: null, month: null }, ...over });

export const employee = (over: Partial<Schemas['Employee']> = {}): Schemas['Employee'] => ({
  pseudonym: 'emp-7f3a9c01b2',
  department_id: DEP_ID,
  active: true,
  limits: { day: '500.00', week: null, month: null },
  ...over,
});

export const price = (over: Partial<Schemas['ModelPrice']> = {}): Schemas['ModelPrice'] => ({
  id: '33333333-3333-4333-8333-333333333333',
  model_id: 'gigachat-pro',
  valid_from: '2026-10-01T00:00:00+03:00',
  input_per_1k: '0.75',
  output_per_1k: '1.50',
  cached_input_per_1k: null,
  currency: 'RUB',
  ...over,
});

export const model = (over: Partial<Schemas['Model']> = {}): Schemas['Model'] => ({
  id: 'gigachat-pro',
  provider: 'gigachat',
  kind: 'chat',
  supports_tools: true,
  supports_json: true,
  max_context: 128000,
  default_input_tokens: 1000,
  default_output_tokens: 500,
  tie_order: 100,
  active: true,
  current_price: price(),
  ...over,
});

export const caller = (over: Partial<Schemas['Caller']> = {}): Schemas['Caller'] => ({
  id: '44444444-4444-4444-8444-444444444444',
  name: 'Шлюз платформы',
  key_prefix: 'bm_ab12',
  subject_scope: { type: 'department_employees', department_id: DEP_ID },
  provider_source_id: null,
  status: 'active',
  created_at: '2026-10-01T09:00:00+03:00',
  revoked_at: null,
  ...over,
});

export const adminUser = (over: Partial<Schemas['AdminUser']> = {}): Schemas['AdminUser'] => ({
  id: '00000000-0000-4000-8000-000000000001',
  login: 'admin',
  role: 'admin',
  department_id: null,
  active: true,
  must_change_password: false,
  ...over,
});

export const orgSummary = (over: Partial<Schemas['OrgImportSummary']> = {}): Schemas['OrgImportSummary'] => ({
  dry_run: true,
  departments_added: 2,
  employees_added: 30,
  employees_updated: 1,
  employees_moved: 3,
  employees_removed: 0,
  rows_total: 40,
  rows_applied: 38,
  errors: [{ line: 7, message: 'Пустой логин' }],
  warnings: [{ line: 12, message: 'Нет отметки основной группы — выбрана первая по порядку' }],
  ...over,
});
