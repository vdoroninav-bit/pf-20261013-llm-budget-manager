// @vitest-environment jsdom
// Экран 3.5 «Сотрудники» (design/screens.md §3.5): listEmployees, getEmployee, putEmployeeLimits, revealPseudonym.
import { afterEach, describe, expect, it } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { DEP_ID, fail, list, ok } from './support/contractApi';
import { department, employee } from './support/fixtures';
import { renderApp, resetApp } from './support/renderApp';

afterEach(resetApp);

// testing-library сравнивает строку с текстом узла после нормализации пробелов: U+202F там уже обычный пробел.
const NN = ' ';
const PSEUDO = 'emp-7f3a9c01b2';

describe('вкладка «Сотрудники» в карточке подразделения', () => {
  it('рендерит русские подписи и отправляет listEmployees с department_id по контракту', async () => {
    const { api } = renderApp(`#/departments/${DEP_ID}/1`, {
      role: 'admin',
      handlers: {
        getDepartment: ok(department()),
        listModels: list([]),
        listEmployees: list([employee(), employee({ pseudonym: 'emp-00000000aa', active: false })]),
      },
    });
    expect(await screen.findByText(PSEUDO)).toBeTruthy();
    expect(
      screen.getByText('Сотрудники показаны псевдонимами. Сопоставление с учётной записью видит только администратор.'),
    ).toBeTruthy();
    const heads = screen.getAllByRole('columnheader').map((h) => h.textContent?.trim());
    expect(heads).toEqual(['Сотрудник', 'Активен', 'Личный лимит в сутки', 'В неделю', 'В месяц']);
    expect(screen.getByText('Выведен из подразделения')).toBeTruthy();
    expect(screen.getAllByText(`500,00${NN}₽`)).toHaveLength(2);
    const call = api.callsOf('listEmployees')[0]!;
    expect(call.query).toEqual({ department_id: DEP_ID, page: '1', page_size: '50' });
    expect(api.violations).toEqual([]);
  });

  it('нет сотрудников — подсказка про выгрузку AD (у администратора — ссылка)', async () => {
    renderApp(`#/departments/${DEP_ID}/1`, {
      role: 'admin',
      handlers: { getDepartment: ok(department()), listModels: list([]), listEmployees: list([]) },
    });
    expect(
      await screen.findByText('В подразделении нет сотрудников. Они появятся после загрузки выгрузки групп AD.'),
    ).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Загрузить файл AD' })).toBeTruthy();
  });
});

describe('карточка сотрудника', () => {
  it('администратор: русские подписи, putEmployeeLimits по контракту', async () => {
    const { api } = renderApp(`#/employees/${PSEUDO}`, {
      role: 'admin',
      handlers: {
        getEmployee: ok(employee()),
        getDepartment: ok(department()),
        putEmployeeLimits: ({ body }: { body: unknown }) => ok(body),
      },
    });
    expect(await screen.findByText('Маркетинг')).toBeTruthy();
    expect(screen.getAllByText(PSEUDO).length).toBeGreaterThan(0);
    expect(
      screen.getByText('Личный лимит действует вместе с лимитом подразделения: решение принимается по самому строгому.'),
    ).toBeTruthy();
    expect(screen.getByText(/Остаток по личному лимиту в админке не показывается/)).toBeTruthy();
    fireEvent.change(screen.getByLabelText('В неделю'), { target: { value: '2000' } });
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить лимиты' }));
    await waitFor(() => expect(api.callsOf('putEmployeeLimits')).toHaveLength(1));
    const call = api.callsOf('putEmployeeLimits')[0]!;
    expect(call.path).toBe(`/api/v1/employees/${PSEUDO}/limits`);
    expect(call.body).toEqual({ day: '500.00', week: '2000.00', month: null });
    expect(api.violations).toEqual([]);
  });

  it('«Кто это?»: подтверждение, revealPseudonym, логин и имя под псевдонимом', async () => {
    const { api } = renderApp(`#/employees/${PSEUDO}`, {
      role: 'admin',
      handlers: {
        getEmployee: ok(employee()),
        getDepartment: ok(department()),
        revealPseudonym: ok({ pseudonym: PSEUDO, login: 'ivanova', display_name: 'Иванова А. С.' }),
      },
    });
    fireEvent.click(await screen.findByRole('button', { name: 'Кто это?' }));
    const dialog = await screen.findByRole('dialog');
    expect(
      within(dialog).getByText(
        `Показать учётную запись сотрудника ${PSEUDO}? Просмотр будет записан в журнал аудита.`,
      ),
    ).toBeTruthy();
    expect(api.callsOf('revealPseudonym')).toHaveLength(0);
    fireEvent.click(within(dialog).getByRole('button', { name: 'Показать' }));
    expect(await screen.findByText('ivanova')).toBeTruthy();
    expect(screen.getByText('Иванова А. С.')).toBeTruthy();
    expect(api.callsOf('revealPseudonym')[0]!.path).toBe(`/api/v1/pseudonyms/${PSEUDO}`);
    expect(api.violations).toEqual([]);
  });

  it('владелец бюджета: лимиты только для чтения, без «Кто это?»', async () => {
    const { api } = renderApp(`#/employees/${PSEUDO}`, {
      role: 'budget_owner',
      handlers: { getEmployee: ok(employee()), getDepartment: ok(department()) },
    });
    expect(await screen.findByText(`500,00${NN}₽`)).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Кто это?' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Сохранить лимиты' })).toBeNull();
    expect(api.violations).toEqual([]);
  });

  it('404 — «Запись не найдена…»', async () => {
    renderApp(`#/employees/${PSEUDO}`, { role: 'admin', handlers: { getEmployee: fail(404, 'not_found') } });
    expect(await screen.findByText(/Запись не найдена/)).toBeTruthy();
  });
});
