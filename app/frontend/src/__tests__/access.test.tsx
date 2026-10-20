// @vitest-environment jsdom
// Права интерфейса — та же таблица, что x-roles контракта (13_design.md §4): меню и маршруты не расходятся с сервером.
import { afterEach, describe, expect, it } from 'vitest';
import { screen, within } from '@testing-library/react';
import { OPERATION_ROLES, can } from '../access/permissions';
import { spec } from './support/contractApi';
import { renderApp, resetApp } from './support/renderApp';

afterEach(resetApp);

const xRoles = (operationId: string): string[] | undefined => {
  for (const item of Object.values(spec.paths)) {
    for (const op of Object.values(item) as Array<{ operationId?: string; 'x-roles'?: string[] }>) {
      if (op && typeof op === 'object' && op.operationId === operationId) return op['x-roles'];
    }
  }
  return undefined;
};

describe('таблица прав интерфейса', () => {
  it('у каждой операции роли ровно как x-roles в 12_openapi.yaml', () => {
    for (const [operationId, roles] of Object.entries(OPERATION_ROLES)) {
      expect([...roles].sort(), operationId).toEqual([...(xRoles(operationId) ?? ['<нет операции>'])].sort());
    }
  });

  it('can() проверяет роль по операции', () => {
    expect(can('finance_viewer', 'listEmployees')).toBe(false);
    expect(can('budget_owner', 'listEmployees')).toBe(true);
    expect(can(undefined, 'listDepartments')).toBe(false);
  });
});

const menuItems = async () => {
  const nav = await screen.findByRole('navigation', { name: 'Разделы' });
  await within(nav).findByRole('menuitem', { name: /Панель/ });
  return within(nav)
    .getAllByRole('menuitem')
    .map((el) => el.textContent?.trim());
};

describe('меню по ролям (13_design.md §4, пункты T043, T052, T064, T072)', () => {
  it('администратор видит все экраны настройки в фиксированном порядке', async () => {
    renderApp('#/', { role: 'admin', handlers: {} });
    expect(await menuItems()).toEqual([
      'Панель',
      'Подразделения',
      'Модели',
      'Подписки и курсы',
      'Загрузка файла AD',
      'Импорт провайдеров',
      'Отчёты',
      'Ключи систем',
      'Пользователи',
      'Журналы',
    ]);
  });

  it('владелец бюджета видит «Моё подразделение» и модели, без загрузки AD, ключей и пользователей', async () => {
    renderApp('#/', { role: 'budget_owner', handlers: {} });
    // Импорта провайдеров у В нет (В1: getImportStatus — только для плашки на панели).
    expect(await menuItems()).toEqual(['Панель', 'Моё подразделение', 'Модели', 'Подписки и курсы', 'Отчёты', 'Журналы']);
  });

  it('наблюдатель-финансист видит подразделения и модели', async () => {
    renderApp('#/', { role: 'finance_viewer', handlers: {} });
    expect(await menuItems()).toEqual([
      'Панель',
      'Подразделения',
      'Модели',
      'Подписки и курсы',
      'Импорт провайдеров',
      'Отчёты',
    ]);
  });
});
