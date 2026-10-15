// @vitest-environment jsdom
// Экран 10 «Пользователи админки» (design/screens.md §10): listAdminUsers, createAdminUser, updateAdminUser.
import { afterEach, describe, expect, it } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { DEP_ID, fail, list, ok } from './support/contractApi';
import { adminUser, department } from './support/fixtures';
import { renderApp, resetApp } from './support/renderApp';

afterEach(resetApp);

const OWNER_ID = '88888888-8888-4888-8888-888888888888';
const ADMIN2_ID = '99999999-9999-4999-8999-999999999999';
const owner = adminUser({ id: OWNER_ID, login: 'ivanova', role: 'budget_owner', department_id: DEP_ID, must_change_password: true });

describe('10 Пользователи админки', () => {
  it('список: русские подписи и listAdminUsers по контракту', async () => {
    const { api } = renderApp('#/admin-users', {
      role: 'admin',
      handlers: { listAdminUsers: list([adminUser(), owner]), listDepartments: list([department()]) },
    });
    expect(await screen.findByText('ivanova')).toBeTruthy();
    const heads = screen.getAllByRole('columnheader').map((h) => h.textContent?.trim());
    expect(heads).toEqual(['Логин', 'Роль', 'Подразделение', 'Активен', 'Должен сменить пароль']);
    const row = screen.getByText('ivanova').closest('tr')!;
    expect(within(row).getByText('Владелец бюджета')).toBeTruthy();
    expect(await within(row).findByText('Маркетинг')).toBeTruthy();
    expect(api.violations).toEqual([]);
  });

  it('создание: подразделение видно только для владельца бюджета; createAdminUser по контракту', async () => {
    const { api } = renderApp('#/admin-users/create', {
      role: 'admin',
      handlers: {
        listDepartments: list([department()]),
        createAdminUser: ({ body }: { body: { login: string } }) => ok({ ...owner, login: body.login }, 201),
        listAdminUsers: list([adminUser(), owner]),
      },
    });
    fireEvent.change(await screen.findByLabelText(/Логин/), { target: { value: 'ivanova' } });
    expect(screen.queryByLabelText(/Подразделение/)).toBeNull();
    fireEvent.click(screen.getByLabelText(/^Владелец бюджета/));
    fireEvent.change(screen.getByLabelText(/Временный пароль/), { target: { value: 'temporary-pass-1' } });
    expect(screen.getByText('Пользователь сменит его при первом входе')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Создать пользователя' }));
    expect(await screen.findByText('Выберите подразделение владельца бюджета.')).toBeTruthy();
    expect(api.callsOf('createAdminUser')).toHaveLength(0);

    fireEvent.mouseDown(screen.getByLabelText(/Подразделение/));
    fireEvent.click(await screen.findByRole('option', { name: 'Маркетинг' }));
    await waitFor(() => expect(screen.queryByText('Выберите подразделение владельца бюджета.')).toBeNull());
    fireEvent.click(screen.getByRole('button', { name: 'Создать пользователя' }));
    await waitFor(() => expect(api.callsOf('createAdminUser')).toHaveLength(1));
    expect(api.callsOf('createAdminUser')[0]!.body).toEqual({
      login: 'ivanova',
      role: 'budget_owner',
      department_id: DEP_ID,
      password: 'temporary-pass-1',
    });
    expect(api.violations).toEqual([]);
  });

  it('409 conflict — «Пользователь с таким логином уже есть.»', async () => {
    renderApp('#/admin-users/create', {
      role: 'admin',
      handlers: { listDepartments: list([]), createAdminUser: fail(409, 'conflict') },
    });
    fireEvent.change(await screen.findByLabelText(/Логин/), { target: { value: 'admin' } });
    fireEvent.change(screen.getByLabelText(/Временный пароль/), { target: { value: 'temporary-pass-1' } });
    fireEvent.click(screen.getByRole('button', { name: 'Создать пользователя' }));
    expect(await screen.findByText('Пользователь с таким логином уже есть.')).toBeTruthy();
  });

  it('единственный активный администратор: отключение и смена роли выключены с подсказкой', async () => {
    renderApp(`#/admin-users/${adminUser().id}`, {
      role: 'admin',
      handlers: { listAdminUsers: list([adminUser(), owner]), listDepartments: list([department()]) },
    });
    const active = (await screen.findByLabelText('Вход разрешён')) as HTMLInputElement;
    expect(active.disabled).toBe(true);
    expect((screen.getByLabelText(/^Владелец бюджета/) as HTMLInputElement).disabled).toBe(true);
    expect(screen.getAllByText('Это единственный активный администратор. Сначала назначьте другого.').length).toBeGreaterThan(0);
  });

  it('изменение: отключение входа и новый временный пароль — updateAdminUser по контракту; 409 last_admin', async () => {
    const admin2 = adminUser({ id: ADMIN2_ID, login: 'sidorov' });
    let n = 0;
    const { api } = renderApp(`#/admin-users/${ADMIN2_ID}`, {
      role: 'admin',
      handlers: {
        listAdminUsers: list([adminUser(), admin2]),
        listDepartments: list([department()]),
        updateAdminUser: ({ body }: { body: object }) =>
          ++n === 1 ? fail(409, 'last_admin') : ok({ ...admin2, ...body }),
      },
    });
    const active = (await screen.findByLabelText('Вход разрешён')) as HTMLInputElement;
    // Пока список пользователей не загружен, поля заблокированы (вдруг это последний администратор).
    await waitFor(() => expect(active.disabled).toBe(false));
    fireEvent.click(active);
    fireEvent.change(screen.getByLabelText(/Задать новый временный пароль/), { target: { value: 'reset-password-12' } });
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить' }));
    await waitFor(() => expect(api.callsOf('updateAdminUser')).toHaveLength(1));
    expect(api.callsOf('updateAdminUser')[0]!.body).toEqual({ active: false, reset_password: 'reset-password-12' });
    expect(await screen.findByText('Нельзя оставить систему без активного администратора.')).toBeTruthy();
    expect(api.violations).toEqual([]);
  });
});
