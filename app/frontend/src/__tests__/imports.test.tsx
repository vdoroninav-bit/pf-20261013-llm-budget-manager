// @vitest-environment jsdom
// Экран 7 «Импорт провайдеров» (design/screens.md §7, T064): getImportStatus, runImport, listImportRuns,
// listProviderSources, createProviderSource, updateProviderSource, deleteProviderSource, listDepartments.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { DEP2_ID, DEP_ID, fail, list, ok, type Handler } from './support/contractApi';
import {
  SRC_ID,
  department,
  department2,
  importRun,
  importStatus,
  providerSource,
  providerStatus,
} from './support/fixtures';
import { renderApp, resetApp } from './support/renderApp';

beforeEach(() => {
  // Только Date: «сейчас» = 14.10.2026 16:20 МСК (после полудня — правило SC-008 действует).
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(new Date('2026-10-14T16:20:00+03:00'));
});
afterEach(() => {
  vi.useRealTimers();
  resetApp();
});

const departments = list([department(), department2()]);
const panel = (name: string) => screen.getByRole('region', { name });

describe('7.1 Состояние', () => {
  it('А: импорт включён; по провайдеру ключ, загружено по (сутки UTC), последний запуск, отставание; вкладки', async () => {
    const { api } = renderApp('#/imports', {
      role: 'admin',
      handlers: { getImportStatus: ok(importStatus()), listProviderSources: list([providerSource()]) },
    });
    expect(await screen.findByText('Импорт включён')).toBeTruthy();
    const a = panel('Anthropic');
    expect(within(a).getByText('Задан')).toBeTruthy();
    expect(within(a).getByText('13.10.2026 (сутки UTC)')).toBeTruthy();
    expect(within(a).getByText('14.10.2026, 09:00 МСК')).toBeTruthy();
    expect(within(a).getByText('16 ч')).toBeTruthy();
    expect(within(a).queryByText('Данные за вчера ещё не загружены')).toBeNull();
    // Неудач 0 — строка не показывается, ошибки нет — тоже.
    expect(within(a).queryByText('Неудач подряд')).toBeNull();
    expect(within(a).queryByText('Ошибка')).toBeNull();
    const o = panel('OpenAI');
    expect(within(o).getByText('Не задан — задайте OPENAI_ADMIN_KEY на сервере')).toBeTruthy();
    // Кнопка выключена заранее, с той же подсказкой, что у 409 provider_key_missing.
    const run = within(o).getByRole('button', { name: 'Загрузить сейчас' }) as HTMLButtonElement;
    expect(run.disabled).toBe(true);
    expect(within(o).getByText('Ключ администратора OpenAI не задан на сервере.')).toBeTruthy();
    expect((within(a).getByRole('button', { name: 'Загрузить сейчас' }) as HTMLButtonElement).disabled).toBe(false);
    const tabs = screen.getByRole('navigation', { name: 'Импорт провайдеров' });
    expect(within(tabs).getAllByRole('link').map((l) => l.textContent)).toEqual(['Состояние', 'Источники', 'Запуски']);
    expect(within(tabs).getByRole('link', { name: 'Состояние' }).getAttribute('aria-current')).toBe('page');
    expect(api.violations).toEqual([]);
  });

  it('отставание по SC-008, неудачи подряд, класс ошибки текстом и моноширинным', async () => {
    renderApp('#/imports', {
      role: 'admin',
      handlers: {
        getImportStatus: ok(
          importStatus({
            providers: [
              providerStatus({
                last_success_day: '2026-10-11',
                lag_hours: 64,
                consecutive_failures: 3,
                last_error_class: 'auth_rejected',
              }),
            ],
          }),
        ),
        listProviderSources: list([]),
      },
    });
    const a = await screen.findByRole('region', { name: 'Anthropic' });
    expect(within(a).getByText('64 ч')).toBeTruthy();
    expect(within(a).getByText('Данные за вчера ещё не загружены')).toBeTruthy();
    expect(within(a).getByText('3')).toBeTruthy();
    expect(within(a).getByText('Администратор получил уведомление')).toBeTruthy();
    expect(within(a).getByText('Провайдер отклонил ключ администратора. Проверьте ключ на сервере.')).toBeTruthy();
    const cls = within(a).getByText('auth_rejected');
    expect(cls.getAttribute('lang')).toBe('en');
  });

  it('импорт выключен и источников нет: пояснение про IMPORT_ENABLED, «не используется», кнопки выключены', async () => {
    const { api } = renderApp('#/imports', {
      role: 'admin',
      handlers: {
        getImportStatus: ok(importStatus({ enabled: false, providers: [providerStatus()] })),
        listProviderSources: list([]),
      },
    });
    expect(await screen.findByText('Импорт выключен')).toBeTruthy();
    expect(
      screen.getByText(
        'Импорт включается переменной окружения IMPORT_ENABLED на сервере. Пока он выключен, утилита не обращается в интернет.',
      ),
    ).toBeTruthy();
    expect(
      await screen.findByText(
        'Импорт расхода провайдеров не используется. Это нормально, если все вызовы идут через Decision API.',
      ),
    ).toBeTruthy();
    const a = panel('Anthropic');
    expect((within(a).getByRole('button', { name: 'Загрузить сейчас' }) as HTMLButtonElement).disabled).toBe(true);
    expect(
      within(a).getByText('Импорт выключен на сервере (IMPORT_ENABLED=false). Включите его в окружении и перезапустите утилиту.'),
    ).toBeTruthy();
    expect(api.violations).toEqual([]);
  });

  it('«Загрузить сейчас» → runImport(provider, сутки) → «поставлена в очередь» и переход на «Запуски»', async () => {
    const { api } = renderApp('#/imports', {
      role: 'admin',
      handlers: {
        getImportStatus: ok(importStatus()),
        listProviderSources: list([]),
        runImport: ({ body }) => ok(importRun({ status: 'queued', started_at: null, finished_at: null, ...body }), 202),
        listImportRuns: list([importRun({ status: 'queued', started_at: null, finished_at: null })]),
      },
    });
    const a = await screen.findByRole('region', { name: 'Anthropic' });
    fireEvent.click(within(a).getByRole('button', { name: 'Загрузить сейчас' }));
    const dialog = await screen.findByRole('dialog', { name: 'Загрузить расход Anthropic' });
    expect(within(dialog).getByText('Пусто — окно по умолчанию')).toBeTruthy();
    fireEvent.change(within(dialog).getByLabelText('С (сутки UTC)'), { target: { value: '2026-10-10' } });
    fireEvent.change(within(dialog).getByLabelText('По (сутки UTC)'), { target: { value: '2026-10-12' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Поставить в очередь' }));
    await waitFor(() => expect(api.callsOf('runImport')).toHaveLength(1));
    expect(api.callsOf('runImport')[0]!.body).toEqual({
      provider: 'anthropic',
      day_from: '2026-10-10',
      day_to: '2026-10-12',
    });
    expect(await screen.findByText('Загрузка поставлена в очередь')).toBeTruthy();
    expect(await screen.findByText('В очереди')).toBeTruthy();
    expect(window.location.hash).toBe('#/imports/runs');
    expect(api.violations).toEqual([]);
  });

  it('«Загрузить сейчас» без дат — только provider; «С» позже «По» — ошибка без запроса', async () => {
    const { api } = renderApp('#/imports', {
      role: 'admin',
      handlers: {
        getImportStatus: ok(importStatus()),
        listProviderSources: list([]),
        runImport: ok(importRun({ status: 'queued' }), 202),
        listImportRuns: list([]),
      },
    });
    const a = await screen.findByRole('region', { name: 'Anthropic' });
    fireEvent.click(within(a).getByRole('button', { name: 'Загрузить сейчас' }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.change(within(dialog).getByLabelText('С (сутки UTC)'), { target: { value: '2026-10-12' } });
    fireEvent.change(within(dialog).getByLabelText('По (сутки UTC)'), { target: { value: '2026-10-10' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Поставить в очередь' }));
    expect(await within(dialog).findByText('Дата начала позже даты окончания')).toBeTruthy();
    expect(api.callsOf('runImport')).toHaveLength(0);
    fireEvent.change(within(dialog).getByLabelText('С (сутки UTC)'), { target: { value: '' } });
    fireEvent.change(within(dialog).getByLabelText('По (сутки UTC)'), { target: { value: '' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Поставить в очередь' }));
    await waitFor(() => expect(api.callsOf('runImport')).toHaveLength(1));
    expect(api.callsOf('runImport')[0]!.body).toEqual({ provider: 'anthropic' });
    expect(api.violations).toEqual([]);
  });

  it('409 provider_key_missing — текст с названием провайдера, диалог остаётся', async () => {
    renderApp('#/imports', {
      role: 'admin',
      handlers: {
        getImportStatus: ok(importStatus()),
        listProviderSources: list([]),
        runImport: fail(409, 'provider_key_missing'),
      },
    });
    const a = await screen.findByRole('region', { name: 'Anthropic' });
    fireEvent.click(within(a).getByRole('button', { name: 'Загрузить сейчас' }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Поставить в очередь' }));
    expect(await within(dialog).findByText('Ключ администратора Anthropic не задан на сервере.')).toBeTruthy();
  });

  it('несопоставленные источники: ref моноширинным и «Сопоставить» → форма с ref и провайдером → createProviderSource', async () => {
    const { api } = renderApp('#/imports', {
      role: 'admin',
      handlers: {
        getImportStatus: ok(importStatus({ providers: [providerStatus({ unallocated_sources: ['wrkspc_NEW1'] })] })),
        listProviderSources: list([providerSource()]),
        listDepartments: departments,
        createProviderSource: ({ body }) => ok(providerSource({ id: '12121212-1212-4212-8212-121212121212', ...body }), 201),
      },
    });
    const a = await screen.findByRole('region', { name: 'Anthropic' });
    expect(within(a).getByText('wrkspc_NEW1').getAttribute('translate')).toBe('no');
    fireEvent.click(within(a).getByRole('button', { name: 'Сопоставить wrkspc_NEW1' }));
    const dialog = await screen.findByRole('dialog', { name: 'Добавить источник' });
    expect((within(dialog).getByLabelText(/^Идентификатор у провайдера/) as HTMLInputElement).value).toBe('wrkspc_NEW1');
    await within(dialog).findByRole('option', { name: 'Разработка' });
    fireEvent.change(within(dialog).getByLabelText(/^Подразделение/), { target: { value: DEP2_ID } });
    fireEvent.click(within(dialog).getByRole('radio', { name: 'Только сверка (Decision API)' }));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Сохранить источник' }));
    await waitFor(() => expect(api.callsOf('createProviderSource')).toHaveLength(1));
    expect(api.callsOf('createProviderSource')[0]!.body).toEqual({
      provider: 'anthropic',
      kind: 'workspace',
      ref: 'wrkspc_NEW1',
      display_name: null,
      department_id: DEP2_ID,
      mode: 'decision_api',
    });
    expect(await screen.findByText('Источник добавлен')).toBeTruthy();
    expect(api.violations).toEqual([]);
  });

  it('загрузка статуса упала — текст ошибки и «Повторить»', async () => {
    let n = 0;
    renderApp('#/imports', {
      role: 'admin',
      handlers: {
        getImportStatus: () => (++n === 1 ? fail(503, 'server_error') : ok(importStatus())),
        listProviderSources: list([]),
      },
    });
    expect(await screen.findByText(/Сервер не смог выполнить запрос/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Повторить' }));
    expect(await screen.findByText('Импорт включён')).toBeTruthy();
  });
});

describe('7 Видимость по ролям', () => {
  it('Ф: только «Состояние», без вкладок, «Загрузить сейчас» и «Сопоставить»; источники не запрашиваются', async () => {
    const { api } = renderApp('#/imports', {
      role: 'finance_viewer',
      handlers: {
        getImportStatus: ok(importStatus({ providers: [providerStatus({ unallocated_sources: ['wrkspc_NEW1'] })] })),
      },
    });
    const a = await screen.findByRole('region', { name: 'Anthropic' });
    expect(within(a).getByText('Задан')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Загрузить сейчас' })).toBeNull();
    expect(screen.queryByRole('button', { name: /Сопоставить/ })).toBeNull();
    expect(screen.queryByRole('navigation', { name: 'Импорт провайдеров' })).toBeNull();
    expect(api.callsOf('listProviderSources')).toHaveLength(0);
    expect(api.violations).toEqual([]);
  });

  it('Ф по прямой ссылке на «Источники» — «Нет доступа» без запроса', async () => {
    const { api } = renderApp('#/imports/sources', { role: 'finance_viewer', handlers: {} });
    expect(await screen.findByText(/Этот раздел недоступен для вашей роли/)).toBeTruthy();
    expect(api.callsOf('listProviderSources')).toHaveLength(0);
  });

  it('В: экрана 7 нет (В1) — «Нет доступа», getImportStatus не вызывается', async () => {
    const { api } = renderApp('#/imports', { role: 'budget_owner', handlers: {} });
    expect(await screen.findByText(/Этот раздел недоступен для вашей роли/)).toBeTruthy();
    expect(api.callsOf('getImportStatus')).toHaveLength(0);
  });
});

describe('7.2 Источники', () => {
  const sources = list([
    providerSource(),
    providerSource({
      id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa2',
      provider: 'openai',
      kind: 'project',
      ref: 'proj_abc123',
      display_name: null,
      department_id: DEP2_ID,
      mode: 'decision_api',
    }),
  ]);

  it('подпись, колонки, ref моноширинным, подразделение по названию, способ учёта словами', async () => {
    const { api } = renderApp('#/imports/sources', {
      role: 'admin',
      handlers: { listProviderSources: sources, listDepartments: departments },
    });
    expect(await screen.findByText('wrkspc_01AbC')).toBeTruthy();
    expect(screen.getByText(/Каждому источнику провайдера — ровно один способ учёта/)).toBeTruthy();
    const heads = screen.getAllByRole('columnheader').map((h) => h.textContent?.trim());
    expect(heads).toEqual([
      'Провайдер',
      'Тип',
      'Идентификатор у провайдера',
      'Название',
      'Подразделение',
      'Способ учёта',
      'Действия',
    ]);
    const rows = screen.getAllByRole('row').slice(1);
    expect(within(rows[0]!).getByText('Anthropic')).toBeTruthy();
    expect(within(rows[0]!).getByText('Workspace')).toBeTruthy();
    expect(within(rows[0]!).getByText('wrkspc_01AbC').getAttribute('lang')).toBe('en');
    expect(await within(rows[0]!).findByText('Маркетинг')).toBeTruthy();
    expect(within(rows[0]!).getByText('Списывать из бюджета (импорт)')).toBeTruthy();
    expect(within(rows[1]!).getByText('Проект')).toBeTruthy();
    expect(await within(rows[1]!).findByText('Разработка')).toBeTruthy();
    expect(within(rows[1]!).getByText('Только сверка (Decision API)')).toBeTruthy();
    expect(api.violations).toEqual([]);
  });

  it('добавление: для Anthropic только Workspace; OpenAI — проект; обязательные поля; тело по контракту', async () => {
    const { api } = renderApp('#/imports/sources', {
      role: 'admin',
      handlers: {
        listProviderSources: sources,
        listDepartments: departments,
        createProviderSource: ({ body }) => ok(providerSource({ id: '12121212-1212-4212-8212-121212121212', ...body }), 201),
      },
    });
    fireEvent.click(await screen.findByRole('button', { name: 'Добавить источник' }));
    const dialog = await screen.findByRole('dialog', { name: 'Добавить источник' });
    // Anthropic по умолчанию: «Проект» и «Ключ API» выключены с подсказкой.
    expect((within(dialog).getByRole('radio', { name: 'Проект' }) as HTMLInputElement).disabled).toBe(true);
    expect((within(dialog).getByRole('radio', { name: 'Ключ API' }) as HTMLInputElement).disabled).toBe(true);
    expect(within(dialog).getByText('Anthropic отдаёт расход только по workspace')).toBeTruthy();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Сохранить источник' }));
    expect(await within(dialog).findByText('Укажите идентификатор, до 200 символов')).toBeTruthy();
    expect(within(dialog).getByText('Выберите подразделение')).toBeTruthy();
    expect(api.callsOf('createProviderSource')).toHaveLength(0);

    fireEvent.click(within(dialog).getByRole('radio', { name: 'OpenAI' }));
    fireEvent.click(within(dialog).getByRole('radio', { name: 'Проект' }));
    fireEvent.change(within(dialog).getByLabelText(/^Идентификатор у провайдера/), { target: { value: ' proj_x9 ' } });
    fireEvent.change(within(dialog).getByLabelText(/^Название/), { target: { value: 'Аналитика' } });
    await within(dialog).findByRole('option', { name: 'Маркетинг' });
    fireEvent.change(within(dialog).getByLabelText(/^Подразделение/), { target: { value: DEP_ID } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Сохранить источник' }));
    await waitFor(() => expect(api.callsOf('createProviderSource')).toHaveLength(1));
    expect(api.callsOf('createProviderSource')[0]!.body).toEqual({
      provider: 'openai',
      kind: 'project',
      ref: 'proj_x9',
      display_name: 'Аналитика',
      department_id: DEP_ID,
      mode: 'import',
    });
    expect(api.violations).toEqual([]);
  });

  it.each([
    [fail(409, 'source_exists'), 'Этот источник уже есть в справочнике. У источника может быть только один способ учёта — измените существующую запись.'],
    [fail(422, 'unsupported_source_kind'), 'Для Anthropic можно добавить только workspace.'],
  ])('ошибка сохранения %# — текст в диалоге', async (reply, text) => {
    renderApp('#/imports/sources', {
      role: 'admin',
      handlers: { listProviderSources: sources, listDepartments: departments, createProviderSource: reply },
    });
    fireEvent.click(await screen.findByRole('button', { name: 'Добавить источник' }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.change(within(dialog).getByLabelText(/^Идентификатор у провайдера/), { target: { value: 'wrkspc_01AbC' } });
    await within(dialog).findByRole('option', { name: 'Маркетинг' });
    fireEvent.change(within(dialog).getByLabelText(/^Подразделение/), { target: { value: DEP_ID } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Сохранить источник' }));
    expect(await within(dialog).findByText(text)).toBeTruthy();
  });

  it('изменение: провайдер, тип и ref не меняются; PATCH только изменённых полей; текст про пересчёт', async () => {
    const { api } = renderApp('#/imports/sources', {
      role: 'admin',
      handlers: {
        listProviderSources: sources,
        listDepartments: departments,
        updateProviderSource: ({ body }) => ok(providerSource({ ...body })),
      },
    });
    const row = (await screen.findByText('wrkspc_01AbC')).closest('tr')!;
    fireEvent.click(within(row as HTMLElement).getByRole('button', { name: 'Изменить' }));
    const dialog = await screen.findByRole('dialog', { name: 'Источник wrkspc_01AbC' });
    expect(within(dialog).queryByLabelText(/^Идентификатор у провайдера/)).toBeNull();
    expect(within(dialog).queryByRole('radio', { name: 'OpenAI' })).toBeNull();
    fireEvent.click(within(dialog).getByRole('radio', { name: 'Только сверка (Decision API)' }));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Сохранить источник' }));
    await waitFor(() => expect(api.callsOf('updateProviderSource')).toHaveLength(1));
    const call = api.callsOf('updateProviderSource')[0]!;
    expect(call.pathParams.source_id).toBe(SRC_ID);
    expect(call.body).toEqual({ mode: 'decision_api' });
    expect(
      await screen.findByText('Сохранено. Статусы загруженных строк пересчитаются при следующей загрузке.'),
    ).toBeTruthy();
    expect(api.violations).toEqual([]);
  });

  it('удаление — подтверждение, фокус на «Отмена», затем deleteProviderSource', async () => {
    const { api } = renderApp('#/imports/sources', {
      role: 'admin',
      handlers: { listProviderSources: sources, listDepartments: departments, deleteProviderSource: { status: 204 } },
    });
    const row = (await screen.findByText('wrkspc_01AbC')).closest('tr')!;
    fireEvent.click(within(row as HTMLElement).getByRole('button', { name: 'Удалить' }));
    const dialog = await screen.findByRole('dialog', { name: 'Удалить источник?' });
    expect(within(dialog).getByText(/Его расход станет «Не распределено» при следующей загрузке/)).toBeTruthy();
    expect(document.activeElement?.textContent).toBe('Отмена');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Удалить источник' }));
    await waitFor(() => expect(api.callsOf('deleteProviderSource')).toHaveLength(1));
    expect(api.callsOf('deleteProviderSource')[0]!.pathParams.source_id).toBe(SRC_ID);
    expect(await screen.findByText('Источник удалён')).toBeTruthy();
    expect(api.violations).toEqual([]);
  });

  it('пусто — текст и «Добавить источник»', async () => {
    renderApp('#/imports/sources', { role: 'admin', handlers: { listProviderSources: list([]), listDepartments: departments } });
    expect(
      await screen.findByText('Справочник пуст. Добавьте workspace Anthropic или проекты OpenAI и выберите способ учёта.'),
    ).toBeTruthy();
    expect(screen.getAllByRole('button', { name: 'Добавить источник' }).length).toBeGreaterThan(0);
  });
});

describe('7.3 Запуски', () => {
  it('колонки, статусы словами, сутки UTC, класс ошибки; фильтр провайдера → query provider', async () => {
    const { api } = renderApp('#/imports/runs', {
      role: 'admin',
      handlers: {
        listImportRuns: ({ query }) =>
          query.provider === 'openai'
            ? list([importRun({ provider: 'openai', status: 'failed', error_class: 'provider_unavailable' })])
            : list([importRun(), importRun({ id: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb2', status: 'failed', error_class: 'bad_response' })]),
      },
    });
    expect(await screen.findByText('Успешно')).toBeTruthy();
    const heads = screen.getAllByRole('columnheader').map((h) => h.textContent?.trim());
    expect(heads).toEqual([
      'Провайдер',
      'Статус',
      'Начат',
      'Завершён',
      'Сутки (UTC)',
      'Списано строк',
      'Для сверки',
      'Не распределено',
      'Изменено провайдером',
      'Ошибка',
    ]);
    const rows = screen.getAllByRole('row').slice(1);
    expect(within(rows[0]!).getByText('11.10.2026 – 13.10.2026')).toBeTruthy();
    expect(within(rows[0]!).getByText('42')).toBeTruthy();
    expect(within(rows[1]!).getByText('Ошибка')).toBeTruthy();
    expect(within(rows[1]!).getByText(/Провайдер вернул данные в неожиданном формате/)).toBeTruthy();
    expect(within(rows[1]!).getByText('bad_response')).toBeTruthy();
    expect(api.callsOf('listImportRuns')[0]!.query).toEqual({ page: '1', page_size: '50' });

    fireEvent.change(screen.getByLabelText('Провайдер'), { target: { value: 'openai' } });
    await waitFor(() => expect(api.callsOf('listImportRuns').some((c) => c.query.provider === 'openai')).toBe(true));
    expect(await screen.findByText(/Провайдер не ответил/)).toBeTruthy();
    expect(api.violations).toEqual([]);
  });

  it('пусто — «Загрузок ещё не было…»', async () => {
    renderApp('#/imports/runs', { role: 'admin', handlers: { listImportRuns: list([]) } });
    expect(
      await screen.findByText(
        'Загрузок ещё не было. Первая выполнится автоматически в час, заданный на сервере (IMPORT_HOUR_MSK).',
      ),
    ).toBeTruthy();
  });

  it('опрос раз в 10 с, пока есть queued/running; затем останавливается (В7)', async () => {
    vi.useRealTimers();
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.setSystemTime(new Date('2026-10-14T16:20:00+03:00'));
    const replies: Handler[] = [
      list([importRun({ status: 'queued', started_at: null, finished_at: null })]),
      list([importRun({ status: 'running', finished_at: null })]),
      list([importRun({ status: 'ok' })]),
    ];
    const { api } = renderApp('#/imports/runs', {
      role: 'admin',
      handlers: { listImportRuns: () => (replies.shift() ?? list([importRun()])) as ReturnType<typeof list> },
    });
    expect(await screen.findByText('В очереди')).toBeTruthy();
    await act(() => vi.advanceTimersByTimeAsync(10_000));
    expect(await screen.findByText('Идёт')).toBeTruthy();
    await act(() => vi.advanceTimersByTimeAsync(10_000));
    expect(await screen.findByText('Успешно')).toBeTruthy();
    const after = api.callsOf('listImportRuns').length;
    expect(after).toBe(3);
    await act(() => vi.advanceTimersByTimeAsync(30_000));
    expect(api.callsOf('listImportRuns')).toHaveLength(after);
  });
});
