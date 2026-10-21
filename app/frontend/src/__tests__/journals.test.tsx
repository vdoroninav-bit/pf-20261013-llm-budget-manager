// @vitest-environment jsdom
// Экран 11 «Журналы» (design/screens.md §11, T072): listEvents, listAudit, listNotifications, markNotificationRead;
// колокольчик в шапке (А, В).
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { DEP_ID, list, type Handler } from './support/contractApi';
import { auditEntry, callEvent, department, department2, notification, NOTE_ID } from './support/fixtures';
import { renderApp, resetApp } from './support/renderApp';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { AUDIT_DEFAULT_DAYS } from '../config/ui';

const NN = ' ';

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(new Date('2026-10-14T16:20:00+03:00'));
});
afterEach(() => {
  vi.useRealTimers();
  resetApp();
});

const deps = list([department(), department2()]);
const tabsOf = () =>
  within(screen.getByRole('navigation', { name: 'Журналы' }))
    .getAllByRole('link')
    .map((l) => l.textContent);

describe('11 События', () => {
  it('А: период по умолчанию сегодня; колонки и словари; вкладки «События», «Аудит», «Уведомления»', async () => {
    const { api } = renderApp('#/events', {
      role: 'admin',
      handlers: { listEvents: list([callEvent()]), listDepartments: deps, listNotifications: list([]) },
    });
    expect(await screen.findByText('Шлюз платформы')).toBeTruthy();
    expect(api.callsOf('listEvents')[0]!.query).toEqual({ from: '2026-10-14', to: '2026-10-14', page: '1', page_size: '50' });
    const row = screen.getAllByRole('row')[1]!;
    expect(within(row).getByText('14.10.2026, 10:15 МСК')).toBeTruthy();
    expect(within(row).getByText('emp-7f3a9c01b2')).toBeTruthy();
    expect(within(row).getByText('claude-sonnet').getAttribute('lang')).toBe('en');
    expect(within(row).getByText('Перенаправлено')).toBeTruthy();
    expect(within(row).getByText('Бюджет исчерпан')).toBeTruthy();
    expect(within(row).getByText(`12,00${NN}₽`)).toBeTruthy();
    expect(within(row).getByText(`1${NN}200 / 350 / 0`)).toBeTruthy();
    expect(within(row).getByText('Списано')).toBeTruthy();
    expect(within(row).getByText('Решение не выполнено')).toBeTruthy();
    expect(tabsOf()).toEqual(['События', 'Аудит', 'Уведомления']);
    expect(api.violations).toEqual([]);
  });

  it('фильтры: решение, код, пометка, подразделение → query; «Отказ» с кодом', async () => {
    const { api } = renderApp('#/events', {
      role: 'admin',
      handlers: { listEvents: list([callEvent()]), listDepartments: deps, listNotifications: list([]) },
    });
    await screen.findByText('Шлюз платформы');
    fireEvent.change(screen.getByLabelText('Решение'), { target: { value: 'deny' } });
    fireEvent.change(screen.getByLabelText('Код отказа'), { target: { value: 'budget_exceeded' } });
    fireEvent.change(screen.getByLabelText('Пометка'), { target: { value: 'decision_not_followed' } });
    await screen.findByRole('option', { name: 'Разработка' });
    fireEvent.change(screen.getByLabelText('Подразделение'), { target: { value: DEP_ID } });
    await waitFor(() =>
      expect(api.callsOf('listEvents').at(-1)!.query).toMatchObject({
        outcome: 'deny',
        code: 'budget_exceeded',
        flag: 'decision_not_followed',
        department_id: DEP_ID,
      }),
    );
    expect(api.violations).toEqual([]);
  });

  it('В: без фильтра подразделения; вкладки «События» и «Уведомления»', async () => {
    const { api } = renderApp('#/events', {
      role: 'budget_owner',
      handlers: { listEvents: list([]), listNotifications: list([]) },
    });
    expect(
      await screen.findByText(
        'За выбранный период решений не было. Если системы уже подключены, проверьте, что они отправляют запросы с действующим ключом.',
      ),
    ).toBeTruthy();
    expect(screen.queryByLabelText('Подразделение')).toBeNull();
    expect(tabsOf()).toEqual(['События', 'Уведомления']);
    expect(api.violations).toEqual([]);
  });

  it('Ф: журналов нет — «Нет доступа» без запроса', async () => {
    const { api } = renderApp('#/events', { role: 'finance_viewer', handlers: {} });
    expect(await screen.findByText(/Этот раздел недоступен для вашей роли/)).toBeTruthy();
    expect(api.callsOf('listEvents')).toHaveLength(0);
  });
});

describe('11 Аудит', () => {
  const entries = list([
    auditEntry(),
    auditEntry({
      id: 102,
      actor: 'caller:44444444-4444-4444-8444-444444444444',
      action: 'forbidden_access',
      object_type: 'department',
      object_id: null,
      before: null,
      after: null,
    }),
  ]);

  it('колонки, «Система …» для caller:<id>, словарь действий, чип у forbidden_access; фильтр типа объекта', async () => {
    const { api } = renderApp('#/audit', {
      role: 'admin',
      handlers: { listAudit: entries, listNotifications: list([]) },
    });
    expect(await screen.findByText('Изменение')).toBeTruthy();
    const q = api.callsOf('listAudit')[0]!.query;
    expect(q).toMatchObject({ from: '2026-10-08', to: '2026-10-14' });
    const rows = screen.getAllByRole('row').filter((r) => within(r).queryAllByRole('cell').length > 0);
    expect(within(rows[1]!).getByText('Система 44444444-4444-4444-8444-444444444444')).toBeTruthy();
    expect(within(rows[1]!).getByText('Попытка доступа без прав')).toBeTruthy();
    fireEvent.change(screen.getByLabelText('Тип объекта'), { target: { value: 'department' } });
    await waitFor(() => expect(api.callsOf('listAudit').at(-1)!.query.object_type).toBe('department'));
    expect(api.violations).toEqual([]);
  });

  it('3Ф5: период аудита по умолчанию — 7 суток МСК, константа в общем модуле config/ui', () => {
    expect(AUDIT_DEFAULT_DAYS).toBe(7);
    const src = readFileSync(resolve(process.cwd(), 'src/resources/audit/index.tsx'), 'utf8');
    expect(src).toMatch(/import \{[^}]*\bAUDIT_DEFAULT_DAYS\b[^}]*\} from '\.\.\/\.\.\/config\/ui'/);
    // Своё число в экране не заводится: «-6» = «7 суток включая сегодня» должно считаться от константы.
    expect(src).not.toMatch(/addDays\(today, -\d+\)/);
  });

  it('раскрытие строки — «Было / Стало», изменённый ключ подписан «изменено»', async () => {
    renderApp('#/audit', { role: 'admin', handlers: { listAudit: entries, listNotifications: list([]) } });
    await screen.findByText('Изменение');
    // Открывается кнопкой с клавиатуры (aria-expanded), а не скрытой стрелкой Datagrid.
    const toggle = screen.getAllByRole('button', { name: 'Было / Стало' })[0]!;
    expect(toggle.getAttribute('aria-expanded')).toBe('false');
    fireEvent.click(toggle);
    expect(toggle.getAttribute('aria-expanded')).toBe('true');
    const diff = await screen.findByRole('table', { name: 'Было / Стало' });
    const changed = within(diff).getByRole('row', { name: /policy_mode/ });
    expect(within(changed).getByText('изменено')).toBeTruthy();
    expect(within(changed).getByText('"observe"')).toBeTruthy();
    expect(within(changed).getByText('"enforce"')).toBeTruthy();
    const same = within(diff).getByRole('row', { name: /name/ });
    expect(within(same).queryByText('изменено')).toBeNull();
  });

  it('В: «Нет доступа», listAudit не вызывается', async () => {
    const { api } = renderApp('#/audit', { role: 'budget_owner', handlers: { listNotifications: list([]) } });
    expect(await screen.findByText(/Этот раздел недоступен для вашей роли/)).toBeTruthy();
    expect(api.callsOf('listAudit')).toHaveLength(0);
  });
});

describe('11 Уведомления и колокольчик', () => {
  it('колонки; внешний канал скрыт, если у всех disabled; «Прочитано» → markNotificationRead', async () => {
    let read = false;
    const { api } = renderApp('#/notifications', {
      role: 'admin',
      handlers: {
        listDepartments: deps,
        listNotifications: () => list([notification({ read })]),
        markNotificationRead: () => {
          read = true;
          return { status: 204 };
        },
      },
    });
    const table = await screen.findByRole('table', { name: 'Уведомления' });
    expect(within(table).getAllByRole('columnheader').map((h) => h.textContent)).toEqual([
      'Когда',
      'Что',
      'Подразделение',
      'Период',
      'Текст',
      '',
    ]);
    const row = within(table).getAllByRole('row')[1]!;
    expect(within(row).getByText('Израсходовано 80% лимита')).toBeTruthy();
    expect(within(row).getByText('Месяц с 01.10.2026')).toBeTruthy();
    expect(await within(row).findByText('Маркетинг')).toBeTruthy();
    fireEvent.click(within(row).getByRole('button', { name: 'Прочитано' }));
    await waitFor(() => expect(api.callsOf('markNotificationRead')).toHaveLength(1));
    expect(api.callsOf('markNotificationRead')[0]!.pathParams.notification_id).toBe(NOTE_ID);
    await waitFor(() => expect(within(row).queryByRole('button', { name: 'Прочитано' })).toBeNull());
    expect(api.violations).toEqual([]);
  });

  it('внешний канал показывается, если есть не disabled; фильтр «Только непрочитанные» → unread=true', async () => {
    const { api } = renderApp('#/notifications', {
      role: 'budget_owner',
      handlers: {
        listDepartments: deps,
        listNotifications: list([notification({ webhook_status: 'delivered' })]),
      },
    });
    const table = await screen.findByRole('table', { name: 'Уведомления' });
    expect(within(table).getByText('Внешний канал')).toBeTruthy();
    expect(within(table).getByText('Доставлено')).toBeTruthy();
    fireEvent.click(screen.getByRole('checkbox', { name: 'Только непрочитанные' }));
    await waitFor(() =>
      expect(api.callsOf('listNotifications').some((c) => c.query.unread === 'true' && c.query.page_size === '50')).toBe(true),
    );
    expect(api.violations).toEqual([]);
  });

  it('пусто — текст', async () => {
    renderApp('#/notifications', { role: 'admin', handlers: { listDepartments: deps, listNotifications: list([]) } });
    expect(await screen.findByText(/Уведомлений нет\. Сюда приходят пороги 80% и 100% лимита/)).toBeTruthy();
  });

  it('колокольчик (А, В): счётчик непрочитанных из total, ссылка на уведомления; у Ф колокольчика нет', async () => {
    const handlers: Record<string, Handler> = {
      listNotifications: list([notification()], 3),
    };
    const { api } = renderApp('#/models', { role: 'budget_owner', handlers: { ...handlers, listModels: list([]) } });
    const bell = await screen.findByRole('link', { name: 'Уведомления: 3 непрочитанных' });
    expect(bell.getAttribute('href')).toBe('#/notifications');
    expect(api.callsOf('listNotifications')[0]!.query).toMatchObject({ unread: 'true', page_size: '1' });
    resetApp();
    renderApp('#/models', { role: 'finance_viewer', handlers: { listModels: list([]) } });
    await screen.findByRole('navigation', { name: 'Разделы' });
    expect(screen.queryByRole('link', { name: /^Уведомления:/ })).toBeNull();
  });
});
