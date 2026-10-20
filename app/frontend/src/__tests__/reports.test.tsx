// @vitest-environment jsdom
// Экран 8 «Отчёты, CSV и сверка» (design/screens.md §8, T072): getSpendReport, getSpendCsv, getReconciliation, listDepartments.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { fail, list, ok, type Handler } from './support/contractApi';
import { department, department2, reconRow, spendRow } from './support/fixtures';
import { renderApp, resetApp } from './support/renderApp';

const NN = ' ';

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(new Date('2026-10-14T16:20:00+03:00'));
});
afterEach(() => {
  vi.useRealTimers();
  resetApp();
});

const rows = [
  spendRow(),
  spendRow({
    department_id: null,
    department_name: null,
    model_id: 'claude-sonnet',
    source: 'import',
    amount_rub: '1200.00',
    original_currency: 'USD',
    original_amount: '13.00',
    late: true,
  }),
];
const base = (over: Record<string, Handler> = {}): Record<string, Handler> => ({
  getSpendReport: ok({ items: rows, total_rub: '25500.10' }),
  listDepartments: list([department(), department2()]),
  ...over,
});
const lastQuery = (api: { callsOf: (id: string) => Array<{ query: Record<string, string> }> }) =>
  api.callsOf('getSpendReport').at(-1)!.query;

describe('8.1 Расход', () => {
  it('по умолчанию этот месяц, группировка подразделение + модель + источник; колонки, «Не распределено», итог', async () => {
    const { api } = renderApp('#/reports', { role: 'admin', handlers: base() });
    expect(await screen.findByText('claude-sonnet')).toBeTruthy();
    expect(lastQuery(api)).toEqual({ from: '2026-10-01', to: '2026-10-14', group_by: 'department,model,source' });
    const table = screen.getByRole('table', { name: 'Расход' });
    const heads = within(table).getAllByRole('columnheader').map((h) => h.textContent);
    expect(heads).toEqual(['Подразделение', 'Модель', 'Источник', 'Сумма, ₽', 'Исходная валюта и сумма', 'Поздний импорт']);
    const body = within(table).getAllByRole('row').slice(1);
    expect(within(body[1]!).getByText('Не распределено')).toBeTruthy();
    expect(within(body[1]!).getByText(`13,00${NN}$`)).toBeTruthy();
    expect(within(body[1]!).getByText('Поздний импорт')).toBeTruthy();
    const total = within(table).getByRole('row', { name: /Всего/ });
    expect(within(total).getByText(`25${NN}500,10${NN}₽`)).toBeTruthy();
    // Колонок с сотрудниками нет ни у одной роли (FR-028).
    expect(heads.some((h) => /Сотрудник/.test(h ?? ''))).toBe(false);
    const tabs = screen.getByRole('navigation', { name: 'Отчёты' });
    expect(within(tabs).getAllByRole('link').map((l) => l.textContent)).toEqual(['Расход', 'Сверка']);
    expect(api.violations).toEqual([]);
  });

  it('быстрые периоды: прошлый месяц, эта неделя (с понедельника)', async () => {
    const { api } = renderApp('#/reports', { role: 'admin', handlers: base() });
    await screen.findByText('claude-sonnet');
    fireEvent.click(screen.getByRole('button', { name: 'Прошлый месяц' }));
    await waitFor(() => expect(lastQuery(api)).toMatchObject({ from: '2026-09-01', to: '2026-09-30' }));
    fireEvent.click(screen.getByRole('button', { name: 'Эта неделя' }));
    await waitFor(() => expect(lastQuery(api)).toMatchObject({ from: '2026-10-12', to: '2026-10-14' }));
    expect(api.violations).toEqual([]);
  });

  it('группировка по дням — колонка «Период»; «Не распределено» → department_id=unallocated', async () => {
    const { api } = renderApp('#/reports', {
      role: 'admin',
      handlers: base({
        getSpendReport: ({ query }) =>
          ok({
            items: query.group_by?.includes('day') ? [spendRow({ period_start: '2026-10-13' })] : rows,
            total_rub: '24300.10',
          }),
      }),
    });
    await screen.findByText('claude-sonnet');
    fireEvent.click(screen.getByRole('checkbox', { name: 'Подразделение' }));
    fireEvent.click(screen.getByRole('checkbox', { name: 'День' }));
    await waitFor(() => expect(lastQuery(api).group_by).toBe('model,source,day'));
    expect(await screen.findByText('13.10.2026')).toBeTruthy();
    expect(within(screen.getByRole('table', { name: 'Расход' })).getAllByRole('columnheader')[0]!.textContent).toBe('Период');
    await screen.findByRole('option', { name: 'Разработка' });
    fireEvent.change(screen.getByLabelText('Подразделение', { selector: 'select' }), { target: { value: 'unallocated' } });
    await waitFor(() => expect(lastQuery(api).department_id).toBe('unallocated'));
    expect(api.violations).toEqual([]);
  });

  it('начало позже окончания — ошибка в фильтре, запроса нет', async () => {
    const { api } = renderApp('#/reports', { role: 'admin', handlers: base() });
    await screen.findByText('claude-sonnet');
    const before = api.callsOf('getSpendReport').length;
    fireEvent.change(screen.getByLabelText('Период с'), { target: { value: '2026-10-20' } });
    expect(await screen.findByText('Дата начала позже даты окончания')).toBeTruthy();
    expect(api.callsOf('getSpendReport')).toHaveLength(before);
    expect((screen.getByRole('button', { name: 'Выгрузить CSV' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('пусто — «За выбранный период расхода нет.»', async () => {
    renderApp('#/reports', { role: 'admin', handlers: base({ getSpendReport: ok({ items: [], total_rub: '0.00' }) }) });
    expect(await screen.findByText('За выбранный период расхода нет.')).toBeTruthy();
  });

  it('«Выгрузить CSV» → getSpendCsv(from, to, department_id) → файл spend_<from>_<to>.csv с BOM', async () => {
    const created: Blob[] = [];
    const createObjectURL = vi.fn((b: Blob) => {
      created.push(b);
      return 'blob:csv';
    });
    Object.assign(URL, { createObjectURL, revokeObjectURL: vi.fn() });
    let downloaded = '';
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      downloaded = this.download;
    });
    const { api } = renderApp('#/reports', {
      role: 'admin',
      handlers: base({
        getSpendCsv: { status: 200, contentType: 'text/csv', body: 'period_start;department;model;source;amount_rub\n' },
      }),
    });
    await screen.findByText('claude-sonnet');
    fireEvent.click(screen.getByRole('button', { name: 'Выгрузить CSV' }));
    await waitFor(() => expect(click).toHaveBeenCalled());
    expect(api.callsOf('getSpendCsv')[0]!.query).toEqual({ from: '2026-10-01', to: '2026-10-14' });
    expect(downloaded).toBe('spend_2026-10-01_2026-10-14.csv');
    // BOM — первые байты EF BB BF (Blob.text() его снимает при декодировании, поэтому смотрим байты).
    const bytes = new Uint8Array(await created[0]!.arrayBuffer());
    expect([...bytes.slice(0, 3)]).toEqual([0xef, 0xbb, 0xbf]);
    expect(await created[0]!.text()).toContain('period_start;department');
    expect(screen.getByText(/Без данных сотрудников/)).toBeTruthy();
    expect(api.violations).toEqual([]);
  });

  it('ошибка CSV — «Не удалось выгрузить CSV.» и «Повторить»', async () => {
    renderApp('#/reports', { role: 'admin', handlers: base({ getSpendCsv: fail(503, 'server_error') }) });
    await screen.findByText('claude-sonnet');
    fireEvent.click(screen.getByRole('button', { name: 'Выгрузить CSV' }));
    const alert = await screen.findByText(/Не удалось выгрузить CSV\./);
    expect(within(alert.closest('[role="alert"]') as HTMLElement).getByRole('button', { name: 'Повторить' })).toBeTruthy();
  });

  it('В: поля «Подразделение» нет, вкладки «Сверка» нет; прямая ссылка на сверку — «Нет доступа»', async () => {
    const { api } = renderApp('#/reports', { role: 'budget_owner', handlers: base() });
    expect(await screen.findByText('claude-sonnet')).toBeTruthy();
    expect(screen.queryByLabelText('Подразделение', { selector: 'select' })).toBeNull();
    expect(screen.queryByRole('navigation', { name: 'Отчёты' })).toBeNull();
    expect(api.violations).toEqual([]);
    resetApp();
    const second = renderApp('#/reports/reconciliation', { role: 'budget_owner', handlers: {} });
    expect(await screen.findByText(/Этот раздел недоступен для вашей роли/)).toBeTruthy();
    expect(second.api.callsOf('getReconciliation')).toHaveLength(0);
  });
});

describe('8.2 Сверка', () => {
  it('по умолчанию прошлый месяц; колонки; «больше 2%» по модулю и «меньше 95%» словами', async () => {
    const { api } = renderApp('#/reports/reconciliation', {
      role: 'finance_viewer',
      handlers: {
        listDepartments: list([department(), department2()]),
        getReconciliation: ok({
          items: [
            reconRow(),
            reconRow({ source_ref: 'wrkspc_02', diff_rub: '-310.00', diff_pct: '-3.1', coverage_pct: '90.0' }),
          ],
        }),
      },
    });
    expect(await screen.findByText('wrkspc_02')).toBeTruthy();
    expect(api.callsOf('getReconciliation')[0]!.query).toEqual({ month: '2026-09' });
    expect(screen.getByText(/Допустимое расхождение — до 2% \(SC-005\)/)).toBeTruthy();
    const table = screen.getByRole('table', { name: 'Сверка' });
    expect(within(table).getAllByRole('columnheader').map((h) => h.textContent)).toEqual([
      'Провайдер',
      'Источник',
      'Подразделение',
      'Импорт, ₽',
      'Decision API, ₽',
      'Разница, ₽',
      'Разница, %',
      'Покрытие, %',
    ]);
    const body = within(table).getAllByRole('row').slice(1);
    expect(within(body[0]!).queryByText('больше 2%')).toBeNull();
    expect(within(body[0]!).queryByText('меньше 95%')).toBeNull();
    expect(within(body[1]!).getByText('больше 2%')).toBeTruthy();
    expect(within(body[1]!).getByText('меньше 95%')).toBeTruthy();
    expect(within(body[1]!).getByText(`−310,00${NN}₽`)).toBeTruthy();
    expect(await within(body[0]!).findByText('Маркетинг')).toBeTruthy();

    fireEvent.change(screen.getByLabelText('Месяц'), { target: { value: '2026-08' } });
    await waitFor(() => expect(api.callsOf('getReconciliation').at(-1)!.query).toEqual({ month: '2026-08' }));
    expect(api.violations).toEqual([]);
  });

  it('пусто: текст; ссылка на «Импорт провайдеров» только у А', async () => {
    renderApp('#/reports/reconciliation', {
      role: 'admin',
      handlers: { listDepartments: list([]), getReconciliation: ok({ items: [] }) },
    });
    expect(await screen.findByText(/Нет источников со способом «Только сверка»/)).toBeTruthy();
    expect(within(screen.getByRole('main')).getByRole('link', { name: 'Импорт провайдеров' }).getAttribute('href')).toBe('#/imports');
    resetApp();
    renderApp('#/reports/reconciliation', {
      role: 'finance_viewer',
      handlers: { listDepartments: list([]), getReconciliation: ok({ items: [] }) },
    });
    const empty = await screen.findByText(/Нет источников со способом «Только сверка»/);
    expect(within(empty.parentElement as HTMLElement).queryByRole('link')).toBeNull();
  });
});

