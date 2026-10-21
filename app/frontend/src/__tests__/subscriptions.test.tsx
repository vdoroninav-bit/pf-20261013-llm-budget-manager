// @vitest-environment jsdom
// Экран 5 «Подписки с предпросмотром доли» (design/screens.md §5.1–5.3): listSubscriptions, getSubscription,
// createSubscription, previewSubscription, endSubscription, deleteSubscription, listDepartments, listEmployees.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { DEP2_ID, DEP_ID, fail, list, ok, type Handler } from './support/contractApi';
import { SUB_ID, department, department2, employee, subscription, subscriptionPreview } from './support/fixtures';
import { renderApp, resetApp } from './support/renderApp';

// testing-library нормализует U+202F в обычный пробел.
const NN = ' ';

beforeEach(() => {
  // Только Date: «сегодня по МСК» = 14.10.2026. Таймеры настоящие (дебаунс предпросмотра, react-query).
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(new Date('2026-10-14T12:00:00+03:00'));
});
afterEach(() => {
  vi.useRealTimers();
  resetApp();
});

describe('5.1 Список подписок', () => {
  it('колонки, форматы, чипы курса, назначение; listSubscriptions с active_on = сегодня по МСК', async () => {
    const { api } = renderApp('#/subscriptions', {
      role: 'admin',
      handlers: {
        listSubscriptions: list([
          subscription(),
          subscription({
            id: '99999999-9999-4999-8999-999999999991',
            provider: 'OpenAI',
            plan: 'ChatGPT Business',
            seat_price: '30',
            currency: 'USD',
            seats: 2,
            end_date: '2026-12-31',
            assignee: { type: 'employee', ref: 'emp-7f3a9c01b2' },
            monthly_total_rub: '5550.00',
            fx_warning: 'last_known_rate_used',
          }),
          subscription({
            id: '99999999-9999-4999-8999-999999999992',
            provider: 'OpenAI',
            plan: 'Team',
            currency: 'USD',
            seat_price: '25',
            assignee: { type: 'unallocated', ref: null },
            monthly_total_rub: null,
            fx_warning: 'no_rate',
          }),
        ]),
        listDepartments: list([department(), department2()]),
      },
    });
    expect(await screen.findByText('Claude Team')).toBeTruthy();
    const heads = screen.getAllByRole('columnheader').map((h) => h.textContent?.trim());
    expect(heads).toEqual(['Провайдер', 'Тариф', 'Мест', 'Цена места', 'В месяц, ₽', 'Назначена', 'Период', 'Курс']);

    const row1 = screen.getByText('Claude Team').closest('tr')!;
    expect(within(row1).getByText(`2${NN}500,00${NN}₽`)).toBeTruthy();
    expect(within(row1).getByText(`7${NN}500,00${NN}₽`)).toBeTruthy();
    expect(await within(row1).findByText('Подразделение «Маркетинг»')).toBeTruthy();
    expect(within(row1).getByText('с 01.10.2026, бессрочно')).toBeTruthy();

    const row2 = screen.getByText('ChatGPT Business').closest('tr')!;
    expect(within(row2).getByText(`30${NN}$`)).toBeTruthy();
    expect(within(row2).getByText('emp-7f3a9c01b2')).toBeTruthy();
    expect(within(row2).getByText('01.10.2026 – 31.12.2026')).toBeTruthy();
    expect(within(row2).getByText('Курс прошлого месяца')).toBeTruthy();

    const row3 = screen.getByText('Team').closest('tr')!;
    expect(within(row3).getByText('Не распределено')).toBeTruthy();
    expect(within(row3).getAllByText('Нет курса')).toHaveLength(2);

    expect(api.callsOf('listSubscriptions')[0]!.query).toEqual({ page: '1', page_size: '50', active_on: '2026-10-14' });

    fireEvent.click(screen.getByLabelText('Все, включая закрытые'));
    await waitFor(() => expect(api.callsOf('listSubscriptions').at(-1)!.query).toEqual({ page: '1', page_size: '50' }));
    expect((screen.getByLabelText('Действует на дату') as HTMLInputElement).disabled).toBe(true);

    fireEvent.change(screen.getByLabelText('Подразделение'), { target: { value: DEP2_ID } });
    await waitFor(() =>
      expect(api.callsOf('listSubscriptions').at(-1)!.query).toEqual({ page: '1', page_size: '50', department_id: DEP2_ID }),
    );
    expect(api.violations).toEqual([]);
  });

  it('вкладки «Подписки» и «Курсы USD/₽» у администратора, «Ввести подписку»', async () => {
    renderApp('#/subscriptions', {
      role: 'admin',
      handlers: { listSubscriptions: list([subscription()]), listDepartments: list([department()]) },
    });
    const tabs = await screen.findByRole('navigation', { name: 'Подписки и курсы' });
    expect(within(tabs).getByRole('link', { name: 'Подписки' }).getAttribute('aria-current')).toBe('page');
    expect(within(tabs).getByRole('link', { name: 'Курсы USD/₽' })).toBeTruthy();
    expect(screen.getAllByRole('link', { name: 'Ввести подписку' }).length).toBeGreaterThan(0);
  });

  it('пусто у администратора — подсказка и «Ввести подписку»', async () => {
    renderApp('#/subscriptions', { role: 'admin', handlers: { listSubscriptions: list([]), listDepartments: list([]) } });
    expect(
      await screen.findByText(
        'Подписок нет. Введите подписки, которые не видны через API провайдеров, например Claude Team или ChatGPT Business.',
      ),
    ).toBeTruthy();
    expect(screen.getAllByRole('link', { name: 'Ввести подписку' }).length).toBeGreaterThan(0);
  });

  it('владелец бюджета: «Подписок нет.», без ввода и без вкладки курсов', async () => {
    const { api } = renderApp('#/subscriptions', {
      role: 'budget_owner',
      handlers: { listSubscriptions: list([]), listDepartments: list([department()]) },
    });
    expect(await screen.findByText('Подписок нет.')).toBeTruthy();
    expect(screen.queryByRole('link', { name: 'Ввести подписку' })).toBeNull();
    expect(screen.queryByRole('link', { name: 'Курсы USD/₽' })).toBeNull();
    expect(api.callsOf('listFxRates')).toHaveLength(0);
    expect(api.violations).toEqual([]);
  });
});

const fillDepartmentSubscription = async () => {
  fireEvent.change(await screen.findByLabelText(/^Провайдер/), { target: { value: 'Anthropic' } });
  fireEvent.change(screen.getByLabelText(/^Тариф/), { target: { value: 'Claude Team' } });
  fireEvent.change(screen.getByLabelText(/^Цена за место в месяц/), { target: { value: '2 500' } });
  fireEvent.change(screen.getByLabelText(/^Мест/), { target: { value: '3' } });
  fireEvent.change(screen.getByLabelText(/^Действует с/), { target: { value: '2026-10-01' } });
  fireEvent.change(await screen.findByRole('combobox', { name: /^Подразделение/ }), { target: { value: DEP_ID } });
};

const createHandlers = (extra: Record<string, Handler> = {}): Record<string, Handler> => ({
  listDepartments: list([department(), department2()]),
  getSubscription: ok(subscription()),
  ...extra,
});

describe('5.2 Ввод подписки с предпросмотром доли', () => {
  it('подписи, подсказки; предпросмотр через 400 мс по контракту; сохранение createSubscription', async () => {
    const { api } = renderApp('#/subscriptions/create', {
      role: 'admin',
      handlers: createHandlers({
        previewSubscription: ok(
          subscriptionPreview({
            warn: true,
            periods: [
              { period: 'day', limit_rub: '5000.00', amortization_rub: '241.94', share_pct: '4.8' },
              { period: 'month', limit_rub: '12000.00', amortization_rub: '7500.00', share_pct: '62.5' },
            ],
          }),
        ),
        createSubscription: ({ body }) => ok({ ...subscription(), ...(body as object), id: SUB_ID }, 201),
      }),
    });
    expect(
      await screen.findByText(
        'Стоимость списывается равномерно по дням месяца. После сохранения цену, число мест и назначение изменить нельзя — только закрыть подписку и ввести новую.',
      ),
    ).toBeTruthy();
    const panel = screen.getByRole('region', { name: 'Доля бюджета' });
    expect(panel.getAttribute('aria-live')).toBe('polite');
    expect(within(panel).getByText(/Заполните обязательные поля/)).toBeTruthy();

    await fillDepartmentSubscription();
    // Дебаунс 400 мс: через 200 мс после последнего изменения запроса ещё нет.
    await new Promise((r) => setTimeout(r, 200));
    expect(api.callsOf('previewSubscription')).toHaveLength(0);
    await waitFor(() => expect(api.callsOf('previewSubscription')).toHaveLength(1), { timeout: 2000 });
    expect(api.callsOf('previewSubscription')[0]!.body).toEqual({
      provider: 'Anthropic',
      plan: 'Claude Team',
      seat_price: '2500',
      currency: 'RUB',
      seats: 3,
      start_date: '2026-10-01',
      end_date: null,
      assignee: { type: 'department', ref: DEP_ID },
    });
    expect(await within(panel).findByText(`7${NN}500,00${NN}₽`)).toBeTruthy();
    expect(within(panel).getByText('Месяц')).toBeTruthy();
    expect(within(panel).getByText('62,5%')).toBeTruthy();
    expect(within(panel).getByRole('img', { name: `Месяц: лимит 12\u202f000,00\u202f₽, подписки 7\u202f500,00\u202f₽` })).toBeTruthy();
    expect(
      within(panel).getByText(
        'Подписки займут 62,5% месячного бюджета — больше порога 50%. Владелец бюджета получит уведомление.',
      ),
    ).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'Сохранить подписку' }));
    await waitFor(() => expect(api.callsOf('createSubscription')).toHaveLength(1));
    expect(api.callsOf('createSubscription')[0]!.body).toEqual(api.callsOf('previewSubscription')[0]!.body);
    expect(await screen.findByText('Подписка сохранена')).toBeTruthy();
    expect(api.violations).toEqual([]);
  });

  it('быстрые правки дают один пересчёт; «Пересчитываем…» во время запроса', async () => {
    let release: () => void = () => undefined;
    const { api } = renderApp('#/subscriptions/create', {
      role: 'admin',
      handlers: createHandlers({
        previewSubscription: () =>
          new Promise((resolve) => {
            release = () => resolve(ok(subscriptionPreview()));
          }),
      }),
    });
    await fillDepartmentSubscription();
    fireEvent.change(screen.getByLabelText(/^Мест/), { target: { value: '4' } });
    fireEvent.change(screen.getByLabelText(/^Мест/), { target: { value: '5' } });
    await waitFor(() => expect(api.callsOf('previewSubscription')).toHaveLength(1), { timeout: 2000 });
    expect(api.callsOf('previewSubscription')[0]!.body.seats).toBe(5);
    expect(await screen.findByText('Пересчитываем…')).toBeTruthy();
    release();
    await waitFor(() => expect(screen.queryByText('Пересчитываем…')).toBeNull());
    expect(api.violations).toEqual([]);
  });

  it('опоздавший ответ на старое состояние формы не затирает новый', async () => {
    const reply = (share: string) =>
      subscriptionPreview({ periods: [{ period: 'month', limit_rub: '1000.00', amortization_rub: '100.00', share_pct: share }] });
    let n = 0;
    renderApp('#/subscriptions/create', {
      role: 'admin',
      handlers: createHandlers({
        previewSubscription: () =>
          ++n === 1
            ? new Promise((resolve) => setTimeout(() => resolve(ok(reply('10.0'))), 900))
            : ok(reply('20.0')),
      }),
    });
    await fillDepartmentSubscription();
    await waitFor(() => expect(n).toBe(1), { timeout: 2000 });
    fireEvent.change(screen.getByLabelText(/^Мест/), { target: { value: '4' } });
    expect(await screen.findByText('20,0%', {}, { timeout: 2000 })).toBeTruthy();
    await new Promise((r) => setTimeout(r, 900));
    expect(screen.queryByText('10,0%')).toBeNull();
    expect(screen.getByText('20,0%')).toBeTruthy();
  });

  it('В3: «займут весь бюджет» — по суммам; 99,99% (share_pct 100.0) не даёт пометки', async () => {
    let reply = subscriptionPreview({
      periods: [{ period: 'month', limit_rub: '12000.00', amortization_rub: '11999.00', share_pct: '100.0' }],
    });
    const { api } = renderApp('#/subscriptions/create', {
      role: 'admin',
      handlers: createHandlers({ previewSubscription: () => ok(reply) }),
    });
    await fillDepartmentSubscription();
    expect(await screen.findByText('100,0%', {}, { timeout: 2000 })).toBeTruthy();
    expect(screen.queryByText(/займут весь бюджет периода/)).toBeNull();

    reply = subscriptionPreview({
      periods: [{ period: 'month', limit_rub: '12000.00', amortization_rub: '12000.00', share_pct: '100.0' }],
    });
    fireEvent.change(screen.getByLabelText(/^Мест/), { target: { value: '4' } });
    expect(
      await screen.findByText(
        'Подписки займут весь бюджет периода: платные вызовы подразделения будут получать отказ.',
        {},
        { timeout: 2000 },
      ),
    ).toBeTruthy();
    expect(api.violations).toEqual([]);
  });

  it('курс: last_known_rate_used и no_rate — тексты и «Ввести курс»', async () => {
    let fx: 'last_known_rate_used' | 'no_rate' = 'last_known_rate_used';
    renderApp('#/subscriptions/create', {
      role: 'admin',
      handlers: createHandlers({ previewSubscription: () => ok(subscriptionPreview({ fx_warning: fx })) }),
    });
    await fillDepartmentSubscription();
    fireEvent.click(screen.getByLabelText('USD'));
    expect(
      await screen.findByText('Курса USD/₽ на этот месяц нет, расчёт по последнему введённому курсу.', {}, { timeout: 2000 }),
    ).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Ввести курс' }).getAttribute('href')).toBe('#/fx-rates');
    fx = 'no_rate';
    fireEvent.change(screen.getByLabelText(/^Мест/), { target: { value: '4' } });
    expect(
      await screen.findByText(
        'Курс USD/₽ не вводился ни разу — подписка не будет списываться, пока курса нет.',
        {},
        { timeout: 2000 },
      ),
    ).toBeTruthy();
  });

  it('«Не распределять»: подсказка, assignee без ref, «не списывается ни из одного бюджета»', async () => {
    const { api } = renderApp('#/subscriptions/create', {
      role: 'admin',
      handlers: createHandlers({
        previewSubscription: ok(subscriptionPreview({ department_id: null, periods: [] })),
      }),
    });
    fireEvent.change(await screen.findByLabelText(/^Провайдер/), { target: { value: 'OpenAI' } });
    fireEvent.change(screen.getByLabelText(/^Тариф/), { target: { value: 'ChatGPT Business' } });
    fireEvent.change(screen.getByLabelText(/^Цена за место в месяц/), { target: { value: '30' } });
    fireEvent.change(screen.getByLabelText(/^Мест/), { target: { value: '1' } });
    fireEvent.change(screen.getByLabelText(/^Действует с/), { target: { value: '2026-10-01' } });
    fireEvent.click(screen.getByLabelText('Не распределять'));
    expect(
      screen.getByText('Подписка попадёт в строку «Не распределено» отчётов и не спишется ни из одного бюджета.'),
    ).toBeTruthy();
    expect(screen.queryByRole('combobox', { name: /^Подразделение/ })).toBeNull();
    expect(
      await screen.findByText('Подписка не списывается ни из одного бюджета.', {}, { timeout: 2000 }),
    ).toBeTruthy();
    expect(api.callsOf('previewSubscription')[0]!.body.assignee).toEqual({ type: 'unallocated', ref: null });
    expect(api.violations).toEqual([]);
  });

  it('«Сотрудник»: подразделение → сотрудник из listEmployees, подсказка о двойном списании', async () => {
    const { api } = renderApp('#/subscriptions/create', {
      role: 'admin',
      handlers: createHandlers({
        listEmployees: list([employee(), employee({ pseudonym: 'emp-0000000001' })]),
        previewSubscription: ok(subscriptionPreview()),
      }),
    });
    await fillDepartmentSubscription();
    fireEvent.click(screen.getByLabelText('Сотрудник'));
    expect(
      screen.getByText('Стоимость спишется и из личного лимита сотрудника, и из бюджета его подразделения.'),
    ).toBeTruthy();
    fireEvent.change(screen.getByRole('combobox', { name: /^Подразделение сотрудника/ }), { target: { value: DEP_ID } });
    await waitFor(() => expect(api.callsOf('listEmployees')).toHaveLength(1));
    expect(api.callsOf('listEmployees')[0]!.query).toEqual({ department_id: DEP_ID, page: '1', page_size: '200' });
    const select = await screen.findByRole('combobox', { name: /^Сотрудник \(псевдоним\)/ });
    await within(select).findByRole('option', { name: 'emp-0000000001' });
    fireEvent.change(select, { target: { value: 'emp-0000000001' } });
    await waitFor(
      () =>
        expect(api.callsOf('previewSubscription').at(-1)?.body.assignee).toEqual({
          type: 'employee',
          ref: 'emp-0000000001',
        }),
      { timeout: 2000 },
    );
    expect(api.violations).toEqual([]);
  });

  it('3Ф2: сотрудники подразделения загружаются всеми страницами (450 → 3 запроса по 200, 450 в списке)', async () => {
    const all = Array.from({ length: 450 }, (_, i) =>
      employee({ pseudonym: `emp-${(i + 1).toString(16).padStart(10, '0')}` }),
    );
    const { api } = renderApp('#/subscriptions/create', {
      role: 'admin',
      handlers: createHandlers({
        listEmployees: ({ query }) => {
          const page = Number(query.page);
          const size = Number(query.page_size);
          return list(all.slice((page - 1) * size, page * size), all.length);
        },
        previewSubscription: ok(subscriptionPreview()),
      }),
    });
    await fillDepartmentSubscription();
    fireEvent.click(screen.getByLabelText('Сотрудник'));
    fireEvent.change(screen.getByRole('combobox', { name: /^Подразделение сотрудника/ }), { target: { value: DEP_ID } });
    const select = await screen.findByRole('combobox', { name: /^Сотрудник \(псевдоним\)/ });
    await within(select).findByRole('option', { name: 'emp-00000001c2' }, { timeout: 2000 });
    // 450 сотрудников + пункт «Выберите…».
    expect(within(select).getAllByRole('option')).toHaveLength(451);
    expect(api.callsOf('listEmployees').map((c) => c.query)).toEqual([
      { department_id: DEP_ID, page: '1', page_size: '200' },
      { department_id: DEP_ID, page: '2', page_size: '200' },
      { department_id: DEP_ID, page: '3', page_size: '200' },
    ]);
    // Сотрудник № 201 и последний доступны для назначения.
    fireEvent.change(select, { target: { value: 'emp-00000000c9' } });
    await waitFor(
      () =>
        expect(api.callsOf('previewSubscription').at(-1)?.body.assignee).toEqual({
          type: 'employee',
          ref: 'emp-00000000c9',
        }),
      { timeout: 2000 },
    );
    expect(api.violations).toEqual([]);
  });

  it('3Ф2: пустая страница раньше total — загрузка останавливается, без бесконечного цикла', async () => {
    const { api } = renderApp('#/subscriptions/create', {
      role: 'admin',
      handlers: createHandlers({
        // total завышен (сотрудников удалили между запросами): вторая страница пустая.
        listEmployees: ({ query }) => list(query.page === '1' ? [employee()] : [], 500),
        previewSubscription: ok(subscriptionPreview()),
      }),
    });
    await fillDepartmentSubscription();
    fireEvent.click(screen.getByLabelText('Сотрудник'));
    fireEvent.change(screen.getByRole('combobox', { name: /^Подразделение сотрудника/ }), { target: { value: DEP_ID } });
    const select = await screen.findByRole('combobox', { name: /^Сотрудник \(псевдоним\)/ });
    await within(select).findByRole('option', { name: 'emp-7f3a9c01b2' });
    await waitFor(() => expect(api.callsOf('listEmployees')).toHaveLength(2));
    expect(api.violations).toEqual([]);
  });

  it('ошибка предпросмотра не блокирует сохранение', async () => {
    const { api } = renderApp('#/subscriptions/create', {
      role: 'admin',
      handlers: createHandlers({
        previewSubscription: fail(422, 'validation_error'),
        createSubscription: ok(subscription(), 201),
      }),
    });
    await fillDepartmentSubscription();
    expect(
      await screen.findByText('Не удалось рассчитать долю. Сохранить подписку всё равно можно.', {}, { timeout: 2000 }),
    ).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить подписку' }));
    await waitFor(() => expect(api.callsOf('createSubscription')).toHaveLength(1));
    expect(api.violations).toEqual([]);
  });

  it('422 fx_rate_missing при сохранении — текст и «Ввести курс»', async () => {
    renderApp('#/subscriptions/create', {
      role: 'admin',
      handlers: createHandlers({
        previewSubscription: ok(subscriptionPreview({ fx_warning: 'no_rate' })),
        createSubscription: fail(422, 'fx_rate_missing'),
      }),
    });
    await fillDepartmentSubscription();
    fireEvent.click(screen.getByLabelText('USD'));
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить подписку' }));
    expect(
      await screen.findByText('Для подписки в USD нужен курс USD/₽ хотя бы за один месяц.'),
    ).toBeTruthy();
    expect(screen.getAllByRole('link', { name: 'Ввести курс' }).length).toBeGreaterThan(0);
  });

  it('проверка формы: без обязательных полей запрос не уходит; «по» раньше «с» — ошибка; фокус на первом поле', async () => {
    const { api } = renderApp('#/subscriptions/create', { role: 'admin', handlers: createHandlers() });
    fireEvent.click(await screen.findByRole('button', { name: 'Сохранить подписку' }));
    expect(await screen.findByText('Проверьте выделенные поля.')).toBeTruthy();
    expect(document.activeElement).toBe(screen.getByLabelText(/^Провайдер/));
    expect(screen.getByLabelText(/^Провайдер/).getAttribute('aria-invalid')).toBe('true');

    await fillDepartmentSubscription();
    fireEvent.change(screen.getByLabelText(/^Действует по/), { target: { value: '2026-09-30' } });
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить подписку' }));
    expect(await screen.findByText('Дата окончания не может быть раньше даты начала.')).toBeTruthy();
    expect(api.callsOf('createSubscription')).toHaveLength(0);
    expect(api.callsOf('previewSubscription')).toHaveLength(0);
  });

  it('у владельца бюджета маршрута ввода нет', async () => {
    renderApp('#/subscriptions/create', {
      role: 'budget_owner',
      handlers: { listSubscriptions: list([]), listDepartments: list([department()]), getSubscription: fail(404, 'not_found') },
    });
    await screen.findByRole('navigation', { name: 'Разделы' });
    expect(screen.queryByRole('button', { name: 'Сохранить подписку' })).toBeNull();
  });
});

describe('5.3 Карточка подписки', () => {
  it('администратор: «Закрыть с даты» → endSubscription', async () => {
    const { api } = renderApp(`#/subscriptions/${SUB_ID}/show`, {
      role: 'admin',
      handlers: {
        getSubscription: ok(subscription()),
        listDepartments: list([department()]),
        endSubscription: ({ body }) => ok(subscription({ end_date: (body as { end_date: string }).end_date })),
      },
    });
    expect(await screen.findByText('Claude Team')).toBeTruthy();
    expect(screen.queryByText(/[Уу]далить/)).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Закрыть с даты' }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.change(within(dialog).getByLabelText('Последний день списания'), { target: { value: '2026-10-20' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Закрыть подписку' }));
    await waitFor(() => expect(api.callsOf('endSubscription')).toHaveLength(1));
    expect(api.callsOf('endSubscription')[0]!.body).toEqual({ end_date: '2026-10-20' });
    expect(api.callsOf('endSubscription')[0]!.path).toBe(`/api/v1/subscriptions/${SUB_ID}`);
    expect(await screen.findByText('Подписка закрыта. Списание за прошедшие дни сохранено.')).toBeTruthy();
    expect(api.violations).toEqual([]);
  });

  it('администратор: «Закрыть сегодня» → подтверждение с датой → deleteSubscription', async () => {
    const { api } = renderApp(`#/subscriptions/${SUB_ID}/show`, {
      role: 'admin',
      handlers: {
        getSubscription: ok(subscription()),
        listDepartments: list([department()]),
        deleteSubscription: { status: 204 },
      },
    });
    fireEvent.click(await screen.findByRole('button', { name: 'Закрыть сегодня' }));
    const dialog = await screen.findByRole('dialog');
    expect(
      within(dialog).getByText(
        'Последний день списания — сегодня, 14.10.2026. Списание за прошедшие дни сохранится, с завтрашнего дня списаний не будет.',
      ),
    ).toBeTruthy();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Закрыть подписку' }));
    await waitFor(() => expect(api.callsOf('deleteSubscription')).toHaveLength(1));
    expect(await screen.findByText('Подписка закрыта')).toBeTruthy();
    expect(api.violations).toEqual([]);
  });

  it('наблюдатель-финансист: только чтение', async () => {
    const { api } = renderApp(`#/subscriptions/${SUB_ID}/show`, {
      role: 'finance_viewer',
      handlers: { getSubscription: ok(subscription()), listDepartments: list([department()]) },
    });
    expect(await screen.findByText('Claude Team')).toBeTruthy();
    expect(await screen.findByText('Подразделение «Маркетинг»')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Закрыть с даты' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Закрыть сегодня' })).toBeNull();
    expect(api.violations).toEqual([]);
  });
});
