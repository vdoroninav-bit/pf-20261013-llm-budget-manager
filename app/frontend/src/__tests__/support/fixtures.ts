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

export const SUB_ID = '88888888-8888-4888-8888-888888888888';

export const subscription = (over: Partial<Schemas['Subscription']> = {}): Schemas['Subscription'] => ({
  id: SUB_ID,
  provider: 'Anthropic',
  plan: 'Claude Team',
  seat_price: '2500.00',
  currency: 'RUB',
  seats: 3,
  start_date: '2026-10-01',
  end_date: null,
  assignee: { type: 'department', ref: DEP_ID },
  monthly_total_rub: '7500.00',
  fx_warning: null,
  created_at: '2026-10-01T09:00:00+03:00',
  ...over,
});

export const fxRate = (over: Partial<Schemas['FxRate']> = {}): Schemas['FxRate'] => ({
  month: '2026-10',
  usd_rub: '92.5000',
  entered_by: 'admin',
  entered_at: '2026-10-01T10:00:00+03:00',
  ...over,
});

export const subscriptionPreview = (
  over: Partial<Schemas['SubscriptionPreview']> = {},
): Schemas['SubscriptionPreview'] => ({
  department_id: DEP_ID,
  periods: [
    { period: 'day', limit_rub: '5000.00', amortization_rub: '241.94', share_pct: '4.8' },
    { period: 'week', limit_rub: '20000.00', amortization_rub: '1693.55', share_pct: '8.5' },
    { period: 'month', limit_rub: null, amortization_rub: '7500.00', share_pct: null },
  ],
  warn_threshold_pct: 50,
  warn: false,
  fx_warning: null,
  ...over,
});

// ---- T064: импорт провайдеров
export const SRC_ID = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1';
export const RUN_ID = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb1';

type ProviderStatus = Schemas['ImportStatus']['providers'][number];
export const providerStatus = (over: Partial<ProviderStatus> = {}): ProviderStatus => ({
  provider: 'anthropic',
  configured: true,
  last_success_day: '2026-10-13',
  last_run_at: '2026-10-14T09:00:00+03:00',
  lag_hours: 16,
  consecutive_failures: 0,
  last_error_class: null,
  unallocated_sources: [],
  ...over,
});

export const importStatus = (over: Partial<Schemas['ImportStatus']> = {}): Schemas['ImportStatus'] => ({
  enabled: true,
  providers: [providerStatus(), providerStatus({ provider: 'openai', configured: false, last_success_day: null, last_run_at: null, lag_hours: null })],
  ...over,
});

export const providerSource = (over: Partial<Schemas['ProviderSource']> = {}): Schemas['ProviderSource'] => ({
  id: SRC_ID,
  provider: 'anthropic',
  kind: 'workspace',
  ref: 'wrkspc_01AbC',
  display_name: 'Маркетинг — Claude',
  department_id: DEP_ID,
  mode: 'import',
  ...over,
});

export const importRun = (over: Partial<Schemas['ImportRun']> = {}): Schemas['ImportRun'] => ({
  id: RUN_ID,
  provider: 'anthropic',
  status: 'ok',
  started_at: '2026-10-14T09:00:00+03:00',
  finished_at: '2026-10-14T09:01:30+03:00',
  day_from: '2026-10-11',
  day_to: '2026-10-13',
  lines_charged: 42,
  lines_reconcile: 5,
  lines_unallocated: 1,
  lines_changed: 0,
  error_class: null,
  ...over,
});

// ---- T072: панель, отчёты, журналы
type LimitBalance = Schemas['LimitBalance'];
export const limitBalance = (over: Partial<LimitBalance> = {}): LimitBalance => ({
  owner_type: 'department',
  owner_ref: DEP_ID,
  period: 'month',
  period_start: '2026-10-01T00:00:00+03:00',
  resets_at: '2026-11-01T00:00:00+03:00',
  limit_rub: '60000.00',
  spent: { subscriptions_rub: '18540.00', import_rub: '12007.30', token_rub: '29950.10' },
  reserved_rub: '623.00',
  remaining_rub: '-1120.40',
  used_pct: '100.8',
  over_limit: true,
  subscriptions_share_pct: '30.9',
  ...over,
});

export const balanceReport = (over: Partial<Schemas['BalanceReport']> = {}): Schemas['BalanceReport'] => ({
  as_of: '2026-10-14T16:20:00+03:00',
  owner_type: 'department',
  owner_ref: DEP_ID,
  department_id: DEP_ID,
  policy_mode: 'enforce',
  limits: [
    limitBalance({
      period: 'day',
      period_start: '2026-10-14T00:00:00+03:00',
      resets_at: '2026-10-15T00:00:00+03:00',
      limit_rub: '5000.00',
      spent: { subscriptions_rub: '598.06', import_rub: '0.00', token_rub: '3161.94' },
      reserved_rub: '0.00',
      remaining_rub: '1240.00',
      used_pct: '75.2',
      over_limit: false,
      subscriptions_share_pct: '12.0',
    }),
    limitBalance(),
  ],
  ...over,
});

export const balanceItem = (over: Partial<Schemas['BalanceItem']> = {}): Schemas['BalanceItem'] => ({
  source: 'token',
  ref_id: 'cccccccc-cccc-4ccc-8ccc-ccccccccccc1',
  date: '2026-10-14',
  model_id: 'gigachat-pro',
  subject_pseudonym: 'emp-7f3a9c01b2',
  amount_rub: '125.40',
  flags: ['no_report'],
  ...over,
});

export const spendRow = (over: Partial<Schemas['SpendRow']> = {}): Schemas['SpendRow'] => ({
  period_start: null,
  department_id: DEP_ID,
  department_name: 'Маркетинг',
  model_id: 'gigachat-pro',
  source: 'token',
  amount_rub: '24300.10',
  original_currency: null,
  original_amount: null,
  late: false,
  ...over,
});

export const NOTE_ID = 'dddddddd-dddd-4ddd-8ddd-ddddddddddd1';
export const notification = (over: Partial<Schemas['Notification']> = {}): Schemas['Notification'] => ({
  id: NOTE_ID,
  kind: 'threshold_80',
  department_id: DEP_ID,
  period: 'month',
  period_start: '2026-10-01',
  message: 'Маркетинг: израсходовано 80% месячного лимита',
  created_at: '2026-10-14T11:02:00+03:00',
  read: false,
  webhook_status: 'disabled',
  ...over,
});

export const callEvent = (over: Partial<Schemas['CallEvent']> = {}): Schemas['CallEvent'] => ({
  id: 'eeeeeeee-eeee-4eee-8eee-eeeeeeeeeee1',
  created_at: '2026-10-14T10:15:00+03:00',
  caller_name: 'Шлюз платформы',
  subject_pseudonym: 'emp-7f3a9c01b2',
  department_id: DEP_ID,
  requested_model: 'claude-sonnet',
  final_model: 'gigachat-pro',
  outcome: 'redirect',
  code: 'budget_exceeded',
  reserve_rub: '12.00',
  fact_rub: '9.75',
  tokens: { input: 1200, output: 350, cached: 0 },
  status: 'settled',
  flags: ['decision_not_followed'],
  expense_date: '2026-10-14',
  ...over,
});

export const auditEntry = (over: Partial<Schemas['AuditEntry']> = {}): Schemas['AuditEntry'] => ({
  id: 101,
  at: '2026-10-14T10:00:00+03:00',
  actor: 'admin',
  action: 'update',
  object_type: 'department',
  object_id: DEP_ID,
  // В schema.d.ts before/after — объект без описанных свойств (Record<string, never>): в контракте это свободный JSON.
  before: { policy_mode: 'observe', name: 'Маркетинг' } as never,
  after: { policy_mode: 'enforce', name: 'Маркетинг' } as never,
  ...over,
});

export const reconRow = (over: Partial<Schemas['ReconciliationRow']> = {}): Schemas['ReconciliationRow'] => ({
  month: '2026-09',
  provider: 'anthropic',
  source_ref: 'wrkspc_01AbC',
  department_id: DEP_ID,
  imported_rub: '10000.00',
  decision_api_rub: '9850.00',
  diff_rub: '150.00',
  diff_pct: '1.5',
  coverage_pct: '98.5',
  ...over,
});
