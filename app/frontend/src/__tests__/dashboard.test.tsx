// @vitest-environment jsdom
// Экран 2 «Панель подразделения» (design/screens.md §2, T072): listDepartments, getDepartment, getDepartmentBalance,
// listDepartmentBalanceItems, getSpendReport, getImportStatus (плашка, В1), listNotifications, markNotificationRead.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { DEP2_ID, DEP_ID, fail, list, ok, session, type Handler } from './support/contractApi';
import {
  balanceItem,
  balanceReport,
  department,
  department2,
  importStatus,
  limitBalance,
  notification,
  providerStatus,
  spendRow,
} from './support/fixtures';
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

const spend = (items = [spendRow()], total = '24300.10') => ok({ items, total_rub: total });

/** Ответы для панели подразделения DEP_ID. */
const base = (over: Record<string, Handler> = {}): Record<string, Handler> => ({
  getDepartment: ({ pathParams }) =>
    ok(pathParams.department_id === DEP2_ID ? department2() : department({ policy_mode: 'enforce', subs_warn_pct: 50 })),
  getDepartmentBalance: ok(balanceReport()),
  getSpendReport: spend(),
  getImportStatus: ok(importStatus()),
  listNotifications: list([notification()]),
  listDepartments: list([department(), department2()]),
  ...over,
});

describe('2 Панель: владелец бюджета (своё подразделение)', () => {
  it('шапка, шкалы день и месяц (неделя без лимита не рисуется), остаток и перерасход, сброс по МСК', async () => {
    const { api } = renderApp('#/', { role: 'budget_owner', handlers: base() });
    expect(await screen.findByRole('heading', { name: 'Маркетинг' })).toBeTruthy();
    expect(await screen.findByText('Режим: Ограничивать')).toBeTruthy();
    expect(screen.getByText('Если утилита недоступна: Пропускать вызовы')).toBeTruthy();
    expect(screen.getByText('Расчёт на 14.10.2026, 16:20 МСК')).toBeTruthy();

    const meters = screen.getByRole('region', { name: 'Лимиты и остатки' });
    const imgs = within(meters).getAllByRole('img');
    expect(imgs.map((i) => i.getAttribute('aria-label')!.split(':')[0])).toEqual(['Сутки', 'Месяц']);
    expect(imgs[1]!.getAttribute('aria-label')).toContain('перерасход 1');
    const day = within(meters).getByRole('group', { name: 'Сутки' });
    expect(within(day).getByText('Остаток')).toBeTruthy();
    expect(within(day).getByText(`1${NN}240,00${NN}₽`)).toBeTruthy();
    expect(within(day).getByText(`лимит 5${NN}000,00${NN}₽`)).toBeTruthy();
    expect(within(day).getByText('сброс 15.10, 00:00 МСК')).toBeTruthy();
    const month = within(meters).getByRole('group', { name: 'Месяц' });
    expect(within(month).getByText('Перерасход')).toBeTruthy();
    expect(within(month).getByText(`−1${NN}120,40${NN}₽`)).toBeTruthy();
    expect(within(month).getByText('сброс 01.11, 00:00 МСК')).toBeTruthy();
    // Легенда источников — текстом, не только цветом.
    expect(within(meters).getByText('Токены (Decision API)')).toBeTruthy();

    // Владелец не выбирает подразделение и не ходит в listDepartments за выбором.
    expect(screen.queryByLabelText('Подразделение')).toBeNull();
    expect(api.callsOf('getDepartmentBalance')[0]!.pathParams.department_id).toBe(DEP_ID);
    expect(api.violations).toEqual([]);
  });

  it('плашка импорта без ссылки на экран 7; при ≥ 2 неудачах — «Администратор получил уведомление» (В1)', async () => {
    renderApp('#/', {
      role: 'budget_owner',
      handlers: base({
        getImportStatus: ok(
          importStatus({
            providers: [providerStatus({ consecutive_failures: 2, last_success_day: '2026-10-11' }), providerStatus({ provider: 'openai', configured: false })],
          }),
        ),
      }),
    });
    const banner = await screen.findByRole('region', { name: 'Импорт расхода провайдеров' });
    expect(
      await within(banner).findByText(
        'Anthropic: 2 загрузки подряд не удались. Последняя успешная — 11.10. Администратор получил уведомление.',
      ),
    ).toBeTruthy();
    expect(within(banner).getByText('OpenAI: ключ администратора не задан.')).toBeTruthy();
    expect(within(banner).queryByRole('link')).toBeNull();
  });

  it('лента уведомлений: непрочитанные, «Прочитано» → markNotificationRead и строка уходит', async () => {
    let read = false;
    const { api } = renderApp('#/', {
      role: 'budget_owner',
      handlers: base({
        listNotifications: () => list(read ? [] : [notification()]),
        markNotificationRead: () => {
          read = true;
          return { status: 204 };
        },
      }),
    });
    const feed = await screen.findByRole('region', { name: 'Уведомления' });
    expect(await within(feed).findByText('Маркетинг: израсходовано 80% месячного лимита')).toBeTruthy();
    expect(within(feed).getByText('14.10.2026, 11:02 МСК')).toBeTruthy();
    expect(api.callsOf('listNotifications')[0]!.query).toMatchObject({ unread: 'true' });
    fireEvent.click(within(feed).getByRole('button', { name: 'Прочитано' }));
    await waitFor(() => expect(api.callsOf('markNotificationRead')).toHaveLength(1));
    expect(api.callsOf('markNotificationRead')[0]!.pathParams.notification_id).toBe(notification().id);
    await waitFor(() => expect(within(feed).queryByText('Маркетинг: израсходовано 80% месячного лимита')).toBeNull());
    expect(api.violations).toEqual([]);
  });

  it('владелец без подразделения (department_id = null): «Ваша учётная запись не привязана…»', async () => {
    const { api } = renderApp('#/', {
      role: 'budget_owner',
      session: session('budget_owner', { department_id: null }),
      handlers: base(),
    });
    expect(
      await screen.findByText('Ваша учётная запись не привязана к подразделению. Обратитесь к администратору.'),
    ).toBeTruthy();
    expect(api.callsOf('getDepartmentBalance')).toHaveLength(0);
  });

  it('403 по чужому подразделению в ссылке — «Нет доступа»', async () => {
    renderApp(`#/dashboard/${DEP2_ID}`, {
      role: 'budget_owner',
      handlers: base({
        getDepartment: fail(403, 'forbidden'),
        getDepartmentBalance: fail(403, 'forbidden'),
        getSpendReport: fail(403, 'forbidden'),
      }),
    });
    expect((await screen.findAllByText(/Этот раздел недоступен для вашей роли/)).length).toBeGreaterThan(0);
  });
});

describe('2 Панель: «Из чего сложился расход» и «По моделям»', () => {
  it('суммы источников выбранного периода; «Показать строки» → listDepartmentBalanceItems; у А «Кто это?»', async () => {
    const { api } = renderApp(`#/dashboard/${DEP_ID}`, {
      role: 'admin',
      handlers: base({
        listDepartmentBalanceItems: ({ query }) =>
          list(query.source === 'import' ? [balanceItem({ source: 'import', subject_pseudonym: null, flags: [] })] : [balanceItem()], 120),
      }),
    });
    const block = await screen.findByRole('region', { name: 'Месяц: из чего сложился расход' });
    const tokenRow = within(block).getByRole('row', { name: /Токены \(Decision API\)/ });
    expect(within(tokenRow).getByText(`29${NN}950,10${NN}₽`)).toBeTruthy();
    fireEvent.click(within(tokenRow).getByRole('button', { name: 'Показать строки' }));
    const rows = await within(block).findByRole('table', { name: 'Строки: Токены (Decision API)' });
    expect(api.callsOf('listDepartmentBalanceItems')[0]!.query).toEqual({
      period: 'month',
      source: 'token',
      page: '1',
      page_size: '50',
    });
    expect(within(rows).getByText('14.10.2026')).toBeTruthy();
    expect(within(rows).getByText('emp-7f3a9c01b2')).toBeTruthy();
    expect(within(rows).getByRole('button', { name: 'Кто это?' })).toBeTruthy();
    expect(within(rows).getByText('Нет отчёта')).toBeTruthy();
    expect(within(block).getByText('Показаны первые 1 из 120.')).toBeTruthy();

    const importRow = within(block).getByRole('row', { name: /Импорт провайдера/ });
    fireEvent.click(within(importRow).getByRole('button', { name: 'Показать строки' }));
    const importRows = await within(block).findByRole('table', { name: 'Строки: Импорт провайдера' });
    expect(within(importRows).getByText('14.10.2026 (сутки UTC)')).toBeTruthy();
    expect(api.violations).toEqual([]);
  });

  it('Ф: колонки «Сотрудник» в строках нет', async () => {
    renderApp(`#/dashboard/${DEP_ID}`, {
      role: 'finance_viewer',
      handlers: base({ listDepartmentBalanceItems: list([balanceItem({ subject_pseudonym: null })]) }),
    });
    const block = await screen.findByRole('region', { name: 'Месяц: из чего сложился расход' });
    fireEvent.click(within(within(block).getByRole('row', { name: /Токены/ })).getByRole('button', { name: 'Показать строки' }));
    const rows = await within(block).findByRole('table', { name: /^Строки:/ });
    const heads = within(rows).getAllByRole('columnheader').map((h) => h.textContent);
    expect(heads).toEqual(['Дата', 'Источник', 'Модель', 'Сумма', 'Пометки']);
  });

  it('«По моделям»: getSpendReport с начала периода по сегодня, group_by=model,source; USD, поздний импорт, итог', async () => {
    const { api } = renderApp(`#/dashboard/${DEP_ID}`, {
      role: 'admin',
      handlers: base({
        getSpendReport: spend(
          [
            spendRow(),
            spendRow({
              model_id: 'claude-sonnet',
              source: 'import',
              amount_rub: '12007.30',
              original_currency: 'USD',
              original_amount: '128.40',
              late: true,
            }),
          ],
          '36307.40',
        ),
      }),
    });
    const block = await screen.findByRole('region', { name: 'По моделям, месяц' });
    expect(await within(block).findByText('claude-sonnet')).toBeTruthy();
    expect(api.callsOf('getSpendReport')[0]!.query).toEqual({
      from: '2026-10-01',
      to: '2026-10-14',
      department_id: DEP_ID,
      group_by: 'model,source',
    });
    expect(within(block).getByText(`128,40${NN}$`)).toBeTruthy();
    expect(within(block).getByText('Поздний импорт')).toBeTruthy();
    expect(within(block).getByText(`36${NN}307,40${NN}₽`)).toBeTruthy();

    // Переключатель периода: сутки → отчёт за сегодня.
    fireEvent.click(screen.getByRole('button', { name: 'Сутки' }));
    await within(await screen.findByRole('region', { name: 'По моделям, сутки' })).findByText('claude-sonnet');
    expect(api.callsOf('getSpendReport').at(-1)!.query).toMatchObject({ from: '2026-10-14', to: '2026-10-14' });
    expect(api.violations).toEqual([]);
  });

  it('частичные данные: разбивка по моделям упала — шкалы есть, на месте блока ошибка и «Повторить»', async () => {
    let n = 0;
    renderApp(`#/dashboard/${DEP_ID}`, {
      role: 'admin',
      handlers: base({ getSpendReport: () => (++n === 1 ? fail(503, 'server_error') : spend([], '0.00')) }),
    });
    expect(await screen.findByText(/Не удалось загрузить разбивку по моделям/)).toBeTruthy();
    expect(screen.getByRole('region', { name: 'Лимиты и остатки' })).toBeTruthy();
    const block = screen.getByRole('region', { name: 'По моделям, месяц' });
    fireEvent.click(within(block).getByRole('button', { name: 'Повторить' }));
    expect(await within(block).findByText('За этот период расхода нет')).toBeTruthy();
  });
});

describe('2 Панель: администратор и финансист', () => {
  it('А: выбор подразделения из listDepartments; смена → /dashboard/:id; ссылка плашки на экран 7', async () => {
    const { api } = renderApp('#/', { role: 'admin', handlers: base() });
    const select = (await screen.findByLabelText('Подразделение')) as HTMLSelectElement;
    await waitFor(() => expect(select.value).toBe(DEP_ID));
    await screen.findByText('Режим: Ограничивать');
    const banner = screen.getByRole('region', { name: 'Импорт расхода провайдеров' });
    expect(within(banner).getByRole('link', { name: 'Импорт провайдеров' }).getAttribute('href')).toBe('#/imports');

    fireEvent.change(select, { target: { value: DEP2_ID } });
    await waitFor(() => expect(window.location.hash).toBe(`#/dashboard/${DEP2_ID}`));
    await waitFor(() =>
      expect(api.callsOf('getDepartmentBalance').some((c) => c.pathParams.department_id === DEP2_ID)).toBe(true),
    );
    expect(api.violations).toEqual([]);
  });

  it('Ф: ленты уведомлений нет и listNotifications не вызывается; ссылки на экран 7 нет', async () => {
    const { api } = renderApp('#/', { role: 'finance_viewer', handlers: base() });
    await screen.findByText('Режим: Ограничивать');
    const banner = await screen.findByRole('region', { name: 'Импорт расхода провайдеров' });
    expect(within(banner).queryByRole('link')).toBeNull();
    expect(screen.queryByRole('region', { name: 'Уведомления' })).toBeNull();
    expect(api.callsOf('listNotifications')).toHaveLength(0);
    expect(api.violations).toEqual([]);
  });

  it('лимиты не заданы: текст; у А — «Задать лимиты»; разбивки всё равно показываются', async () => {
    renderApp(`#/dashboard/${DEP_ID}`, {
      role: 'admin',
      handlers: base({ getDepartmentBalance: ok(balanceReport({ limits: [] })) }),
    });
    expect(await screen.findByText('Для подразделения не заданы лимиты — расход не ограничивается.')).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Задать лимиты' }).getAttribute('href')).toBe(`#/departments/${DEP_ID}`);
    expect(screen.queryAllByRole('img')).toHaveLength(0);
    expect(await screen.findByRole('region', { name: 'По моделям, месяц' })).toBeTruthy();
  });

  it('подразделений нет: А — с действиями, Ф — «Их создаёт администратор»', async () => {
    renderApp('#/', { role: 'admin', handlers: base({ listDepartments: list([]) }) });
    expect(
      await screen.findByText('Подразделений пока нет. Загрузите выгрузку групп AD или создайте подразделение вручную.'),
    ).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Загрузить файл AD' })).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Создать подразделение' })).toBeTruthy();
    resetApp();
    renderApp('#/', { role: 'finance_viewer', handlers: base({ listDepartments: list([]) }) });
    expect(await screen.findByText('Подразделений пока нет. Их создаёт администратор.')).toBeTruthy();
  });

  it('ошибка шкал — «Не удалось посчитать остатки.» и «Повторить»', async () => {
    let n = 0;
    renderApp(`#/dashboard/${DEP_ID}`, {
      role: 'admin',
      handlers: base({ getDepartmentBalance: () => (++n === 1 ? fail(503, 'server_error') : ok(balanceReport())) }),
    });
    expect(await screen.findByText(/Не удалось посчитать остатки\./)).toBeTruthy();
    fireEvent.click(screen.getAllByRole('button', { name: 'Повторить' })[0]!);
    expect(await screen.findByRole('region', { name: 'Лимиты и остатки' })).toBeTruthy();
  });

  it('пометки под шкалой: занят подписками (В3) и режим «Наблюдать» при перерасходе', async () => {
    renderApp(`#/dashboard/${DEP_ID}`, {
      role: 'admin',
      handlers: base({
        getDepartment: ok(department({ policy_mode: 'observe' })),
        getDepartmentBalance: ok(
          balanceReport({
            policy_mode: 'observe',
            limits: [
              limitBalance({
                period: 'day',
                limit_rub: '500.00',
                spent: { subscriptions_rub: '500.00', import_rub: '0.00', token_rub: '10.00' },
                reserved_rub: '0.00',
                remaining_rub: '-10.00',
                used_pct: '102.0',
                over_limit: true,
                subscriptions_share_pct: '100.0',
              }),
            ],
          }),
        ),
      }),
    });
    expect(await screen.findByText('Бюджет занят подписками: платные вызовы получают отказ')).toBeTruthy();
    expect(screen.getByText('Режим «Наблюдать»: вызовы не блокируются, превышения записываются в журнал.')).toBeTruthy();
  });

  it('«Обновить» и автообновление раз в 60 с перечитывают остатки', async () => {
    vi.useRealTimers();
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.setSystemTime(new Date('2026-10-14T16:20:00+03:00'));
    const { api } = renderApp(`#/dashboard/${DEP_ID}`, { role: 'admin', handlers: base() });
    await screen.findByRole('region', { name: 'Лимиты и остатки' });
    const first = api.callsOf('getDepartmentBalance').length;
    await act(() => vi.advanceTimersByTimeAsync(60_000));
    await waitFor(() => expect(api.callsOf('getDepartmentBalance').length).toBeGreaterThan(first));
    const second = api.callsOf('getDepartmentBalance').length;
    // «Обновить» панели — в рабочей области (у шапки react-admin своя кнопка с тем же текстом).
    fireEvent.click(within(screen.getByRole('main')).getByRole('button', { name: 'Обновить' }));
    await waitFor(() => expect(api.callsOf('getDepartmentBalance').length).toBeGreaterThan(second));
  });
});
