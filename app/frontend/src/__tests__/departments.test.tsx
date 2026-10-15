// @vitest-environment jsdom
// Экран 3 «Подразделения и лимиты» (design/screens.md §3): listDepartments, getDepartment, createDepartment,
// updateDepartment, putDepartmentLimits, listModels.
import { afterEach, describe, expect, it } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { DEP_ID, fail, list, ok } from './support/contractApi';
import { department, department2, model } from './support/fixtures';
import { renderApp, resetApp } from './support/renderApp';

afterEach(resetApp);

// testing-library сравнивает строку с текстом узла после нормализации пробелов: U+202F там уже обычный пробел.
const NN = ' ';
const headers = () => screen.getAllByRole('columnheader').map((h) => h.textContent?.trim());

describe('3.1 Список подразделений', () => {
  it('рендерит русские подписи и отправляет listDepartments по контракту', async () => {
    const { api } = renderApp('#/departments', {
      role: 'admin',
      handlers: { listDepartments: list([department({ policy_mode: 'enforce' }), department2()]) },
    });
    expect(await screen.findByText('Маркетинг')).toBeTruthy();
    expect(headers()).toEqual([
      'Название',
      'Группа AD',
      'Режим',
      'Если утилита недоступна',
      'Сутки',
      'Неделя',
      'Месяц',
      'Сотрудников',
    ]);
    const row = screen.getByText('Маркетинг').closest('tr')!;
    expect(within(row).getByText('Ограничивать')).toBeTruthy();
    expect(within(row).getByText('Пропускать вызовы')).toBeTruthy();
    expect(within(row).getByText(`5${NN}000,00${NN}₽`)).toBeTruthy();
    expect(within(row).getByText('нет лимита')).toBeTruthy();
    const row2 = screen.getByText('Разработка').closest('tr')!;
    expect(within(row2).getByText('—')).toBeTruthy();
    expect(screen.getByRole('link', { name: /Создать подразделение/ })).toBeTruthy();
    expect(screen.getByLabelText('Найти подразделение')).toBeTruthy();
    expect(api.callsOf('listDepartments')[0]!.query).toEqual({ page: '1', page_size: '50', sort: 'name', order: 'asc' });
    expect(api.violations).toEqual([]);
  });

  it('пусто у администратора — подсказка и действия «Загрузить файл AD», «Создать подразделение»', async () => {
    renderApp('#/departments', { role: 'admin', handlers: { listDepartments: list([]) } });
    expect(
      await screen.findByText('Подразделений пока нет. Загрузите выгрузку групп AD или создайте подразделение вручную.'),
    ).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Загрузить файл AD' })).toBeTruthy();
    expect(screen.getAllByRole('link', { name: 'Создать подразделение' }).length).toBeGreaterThan(0);
  });

  it('пусто у финансиста — без действий', async () => {
    renderApp('#/departments', { role: 'finance_viewer', handlers: { listDepartments: list([]) } });
    expect(await screen.findByText('Подразделений пока нет. Их создаёт администратор.')).toBeTruthy();
    expect(screen.queryByRole('link', { name: /Создать/ })).toBeNull();
  });

  it('403 — состояние «Нет доступа»', async () => {
    renderApp('#/departments', { role: 'admin', handlers: { listDepartments: fail(403, 'forbidden') } });
    expect(
      await screen.findByText('Этот раздел недоступен для вашей роли. Попытка записана в журнал аудита.'),
    ).toBeTruthy();
  });

  it('ошибка сети — текст и кнопка «Повторить»', async () => {
    renderApp('#/departments', { role: 'admin', handlers: { listDepartments: fail(500, 'internal') } });
    expect(await screen.findByText(/Сервер не смог выполнить запрос/)).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Повторить' })).toBeTruthy();
  });

  it('владелец бюджета из списка сразу попадает в карточку своего подразделения', async () => {
    const { api } = renderApp('#/departments', {
      role: 'budget_owner',
      handlers: {
        listDepartments: list([department()]),
        getDepartment: ok(department()),
        listModels: list([model()]),
        listEmployees: list([]),
      },
    });
    await waitFor(() => expect(window.location.hash).toBe(`#/departments/${DEP_ID}/show`));
    expect(await screen.findByRole('tab', { name: 'Сотрудники' })).toBeTruthy();
    expect(api.violations).toEqual([]);
  });
});

describe('3.2–3.3 Карточка подразделения (администратор)', () => {
  const handlers = (over = {}) => ({
    getDepartment: ok(department()),
    listModels: list([model(), model({ id: 'yandexgpt-lite', provider: 'yandexgpt' })]),
    ...over,
  });

  it('рендерит русские подписи; «Ограничивать» через диалог; updateDepartment только с изменённым полем', async () => {
    const { api } = renderApp(`#/departments/${DEP_ID}`, {
      role: 'admin',
      handlers: handlers({ updateDepartment: ({ body }: { body: object }) => ok({ ...department(), ...body }) }),
    });
    expect(await screen.findByDisplayValue('Маркетинг')).toBeTruthy();
    expect(await screen.findByLabelText('yandexgpt-lite')).toBeTruthy();
    for (const label of ['Название', 'Режим', 'Если утилита недоступна', 'Разрешённые модели']) {
      expect(screen.getAllByText(label).length, label).toBeGreaterThan(0);
    }
    expect(screen.getByText('CN=AI-Marketing')).toBeTruthy();
    expect(screen.getByLabelText(/Предупреждать, когда подписки занимают/)).toBeTruthy();

    fireEvent.click(screen.getByLabelText('Ограничивать'));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText('Включить ограничение расходов?')).toBeTruthy();
    expect(within(dialog).getByText(/локальный акт о мониторинге/)).toBeTruthy();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Включить ограничение' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());

    fireEvent.click(screen.getByRole('button', { name: 'Сохранить настройки' }));
    await waitFor(() => expect(api.callsOf('updateDepartment')).toHaveLength(1));
    const call = api.callsOf('updateDepartment')[0]!;
    expect(call.path).toBe(`/api/v1/departments/${DEP_ID}`);
    expect(call.body).toEqual({ policy_mode: 'enforce' });
    expect(await screen.findByText('Настройки сохранены. Изменение записано в журнал аудита.')).toBeTruthy();
    expect(api.violations).toEqual([]);
  });

  it('отмена диалога оставляет прежний режим', async () => {
    renderApp(`#/departments/${DEP_ID}`, { role: 'admin', handlers: handlers() });
    await screen.findByDisplayValue('Маркетинг');
    fireEvent.click(screen.getByLabelText('Блокировать вызовы'));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText(/вызовы моделей этого подразделения будут отклоняться/)).toBeTruthy();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Отмена' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect((screen.getByLabelText('Пропускать вызовы (рекомендуется)') as HTMLInputElement).checked).toBe(true);
  });

  it('ни одной разрешённой модели — предупреждение', async () => {
    renderApp(`#/departments/${DEP_ID}`, { role: 'admin', handlers: handlers() });
    fireEvent.click(await screen.findByLabelText('gigachat-pro'));
    expect(
      await screen.findByText('Ни одна модель не разрешена: все вызовы получат отказ «Модель не разрешена».'),
    ).toBeTruthy();
  });

  it('каталог пуст — ссылка на каталог моделей', async () => {
    renderApp(`#/departments/${DEP_ID}`, { role: 'admin', handlers: handlers({ listModels: list([]) }) });
    expect(await screen.findByText('Каталог моделей пуст. Сначала добавьте модели.')).toBeTruthy();
    expect(screen.getByRole('link', { name: 'К каталогу моделей' })).toBeTruthy();
  });

  it('лимиты: ввод с запятой, пусто — нет лимита; putDepartmentLimits со всеми тремя полями', async () => {
    const { api } = renderApp(`#/departments/${DEP_ID}`, {
      role: 'admin',
      handlers: handlers({ putDepartmentLimits: ({ body }: { body: unknown }) => ok(body) }),
    });
    await screen.findByDisplayValue('Маркетинг');
    expect(screen.getByText(/Периоды календарные по МСК/)).toBeTruthy();
    const day = screen.getByLabelText('В сутки') as HTMLInputElement;
    expect(day.value).toBe('5000,00');
    fireEvent.change(day, { target: { value: '30 000,5' } });
    fireEvent.change(screen.getByLabelText('В месяц'), { target: { value: '60000' } });
    expect(await screen.findByText('Суточный лимит больше недельного — недельный сработает раньше.')).toBeTruthy();
    fireEvent.change(screen.getByLabelText('В неделю'), { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить лимиты' }));
    await waitFor(() => expect(api.callsOf('putDepartmentLimits')).toHaveLength(1));
    const call = api.callsOf('putDepartmentLimits')[0]!;
    expect(call.path).toBe(`/api/v1/departments/${DEP_ID}/limits`);
    expect(call.body).toEqual({ day: '30000.50', week: null, month: '60000.00' });
    expect(await screen.findByText('Лимиты сохранены')).toBeTruthy();
    expect(api.violations).toEqual([]);
  });

  it('лимиты: неверный формат — ошибка под полем, запроса нет', async () => {
    const { api } = renderApp(`#/departments/${DEP_ID}`, { role: 'admin', handlers: handlers() });
    await screen.findByDisplayValue('Маркетинг');
    fireEvent.change(screen.getByLabelText('В сутки'), { target: { value: '12,345' } });
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить лимиты' }));
    expect(await screen.findByText('Введите сумму, например 5000 или 5000,50')).toBeTruthy();
    expect(api.callsOf('putDepartmentLimits')).toHaveLength(0);
  });

  it('422 с errors[] — текст под полем', async () => {
    renderApp(`#/departments/${DEP_ID}`, {
      role: 'admin',
      handlers: handlers({
        updateDepartment: fail(422, 'validation_error', undefined, {
          errors: [{ field: 'name', message: 'Название уже занято' }],
        }),
      }),
    });
    const name = await screen.findByDisplayValue('Маркетинг');
    fireEvent.change(name, { target: { value: 'Продажи' } });
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить настройки' }));
    expect(await screen.findByText('Название уже занято')).toBeTruthy();
  });
});

describe('3.4 Создание подразделения', () => {
  it('рендерит русские подписи и отправляет createDepartment по контракту', async () => {
    const { api } = renderApp('#/departments/create', {
      role: 'admin',
      handlers: {
        createDepartment: ({ body }: { body: { name: string } }) =>
          ok(department2({ name: body.name, policy_mode: 'observe' }), 201),
        getDepartment: ok(department2({ name: 'Служебные системы' })),
        listModels: list([]),
      },
    });
    expect(await screen.findByText(/Новое подразделение создаётся в режиме «Наблюдать»/)).toBeTruthy();
    fireEvent.change(screen.getByLabelText(/Название/), { target: { value: 'Служебные системы' } });
    fireEvent.click(screen.getByRole('button', { name: 'Создать' }));
    await waitFor(() => expect(api.callsOf('createDepartment')).toHaveLength(1));
    expect(api.callsOf('createDepartment')[0]!.body).toEqual({ name: 'Служебные системы' });
    expect(api.violations).toEqual([]);
  });

  it('409 conflict — «Подразделение с таким названием уже есть.»', async () => {
    renderApp('#/departments/create', { role: 'admin', handlers: { createDepartment: fail(409, 'conflict') } });
    fireEvent.change(await screen.findByLabelText(/Название/), { target: { value: 'Маркетинг' } });
    fireEvent.click(screen.getByRole('button', { name: 'Создать' }));
    expect(await screen.findByText('Подразделение с таким названием уже есть.')).toBeTruthy();
  });
});

describe('Карточка подразделения: чтение (финансист)', () => {
  it('только чтение, без вкладки «Сотрудники» и без запросов сотрудников', async () => {
    const { api } = renderApp(`#/departments/${DEP_ID}/show`, {
      role: 'finance_viewer',
      handlers: { getDepartment: ok(department({ policy_mode: 'warn' })) },
    });
    expect(await screen.findByText('Предупреждать')).toBeTruthy();
    expect(screen.getAllByText('Маркетинг').length).toBeGreaterThan(0);
    expect(screen.getByText(`5${NN}000,00${NN}₽`)).toBeTruthy();
    expect(screen.getByText('gigachat-pro')).toBeTruthy();
    expect(screen.queryByRole('tab', { name: 'Сотрудники' })).toBeNull();
    expect(screen.queryByRole('button', { name: /Сохранить/ })).toBeNull();
    expect(api.violations).toEqual([]);
  });
});
