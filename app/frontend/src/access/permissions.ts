// Права интерфейса по операциям контракта — копия x-roles из 12_openapi.yaml для операций, которые вызывает админка.
// Меню, кнопки и маршруты решают видимость только через can(); тест access.test.tsx сверяет таблицу с контрактом.
import type { Role } from '../api/types';

const ALL: readonly Role[] = ['admin', 'budget_owner', 'finance_viewer'];
const ADMIN: readonly Role[] = ['admin'];
const ADMIN_OWNER: readonly Role[] = ['admin', 'budget_owner'];
const ADMIN_FINANCE: readonly Role[] = ['admin', 'finance_viewer'];

export const OPERATION_ROLES = {
  changePassword: ALL,
  importOrg: ADMIN,
  listDepartments: ALL,
  createDepartment: ADMIN,
  getDepartment: ALL,
  updateDepartment: ADMIN,
  putDepartmentLimits: ADMIN,
  listEmployees: ADMIN_OWNER,
  getEmployee: ADMIN_OWNER,
  putEmployeeLimits: ADMIN,
  revealPseudonym: ADMIN,
  listModels: ALL,
  createModel: ADMIN,
  getModel: ALL,
  updateModel: ADMIN,
  listModelPrices: ALL,
  createModelPrice: ADMIN,
  listFxRates: ADMIN_FINANCE,
  putFxRate: ADMIN,
  listSubscriptions: ALL,
  getSubscription: ALL,
  createSubscription: ADMIN,
  previewSubscription: ADMIN,
  endSubscription: ADMIN,
  deleteSubscription: ADMIN,
  listProviderSources: ADMIN,
  listCallers: ADMIN,
  createCaller: ADMIN,
  updateCaller: ADMIN,
  revokeCaller: ADMIN,
  listAdminUsers: ADMIN,
  createAdminUser: ADMIN,
  updateAdminUser: ADMIN,
  // T064: импорт провайдеров (экран 7) и плашка импорта на панели (В1: budget_owner — сокращённый ответ).
  getImportStatus: ALL,
  listImportRuns: ADMIN,
  runImport: ADMIN,
  createProviderSource: ADMIN,
  updateProviderSource: ADMIN,
  deleteProviderSource: ADMIN,
  // T072: панель, отчёты, журналы.
  getDepartmentBalance: ALL,
  listDepartmentBalanceItems: ALL,
  getSpendReport: ALL,
  getSpendCsv: ALL,
  getReconciliation: ADMIN_FINANCE,
  listNotifications: ADMIN_OWNER,
  markNotificationRead: ADMIN_OWNER,
  listEvents: ADMIN_OWNER,
  listAudit: ADMIN,
} as const satisfies Record<string, readonly Role[]>;

export type OperationId = keyof typeof OPERATION_ROLES;

/**
 * Экран 7 «Импорт провайдеров» (design/screens.md §7): администратор и финансист.
 * У владельца бюджета getImportStatus есть только для плашки на панели (13_design_rulings.md, В1) — пункта меню и экрана нет.
 */
export function canSeeImportScreen(role: Role | undefined | null): boolean {
  return can(role, 'getImportStatus') && role !== 'budget_owner';
}

export function can(role: Role | undefined | null, operation: OperationId): boolean {
  return !!role && (OPERATION_ROLES[operation] as readonly Role[]).includes(role);
}
