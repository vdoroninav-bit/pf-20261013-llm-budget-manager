// Права интерфейса по операциям контракта — копия x-roles из 12_openapi.yaml для операций, которые вызывает админка.
// Меню, кнопки и маршруты решают видимость только через can(); тест access.test.tsx сверяет таблицу с контрактом.
import type { Role } from '../api/types';

const ALL: readonly Role[] = ['admin', 'budget_owner', 'finance_viewer'];
const ADMIN: readonly Role[] = ['admin'];
const ADMIN_OWNER: readonly Role[] = ['admin', 'budget_owner'];

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
  listProviderSources: ADMIN,
  listCallers: ADMIN,
  createCaller: ADMIN,
  updateCaller: ADMIN,
  revokeCaller: ADMIN,
  listAdminUsers: ADMIN,
  createAdminUser: ADMIN,
  updateAdminUser: ADMIN,
} as const satisfies Record<string, readonly Role[]>;

export type OperationId = keyof typeof OPERATION_ROLES;

export function can(role: Role | undefined | null, operation: OperationId): boolean {
  return !!role && (OPERATION_ROLES[operation] as readonly Role[]).includes(role);
}
