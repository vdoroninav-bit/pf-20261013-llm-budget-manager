// @vitest-environment jsdom
// Экран 9 «Ключи вызывающих систем» (design/screens.md §9): listCallers, createCaller, updateCaller, revokeCaller,
// listDepartments, listProviderSources.
import { afterEach, describe, expect, it } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { DEP_ID, list, ok } from './support/contractApi';
import { caller, department } from './support/fixtures';
import { renderApp, resetApp } from './support/renderApp';

afterEach(resetApp);

const SECRET = 'bm_ab12_s3cr3t-value';

describe('9 Ключи систем', () => {
  it('список: русские подписи, listCallers по контракту, названия подразделений', async () => {
    const { api } = renderApp('#/callers', {
      role: 'admin',
      handlers: {
        listCallers: list([
          caller(),
          caller({
            id: '77777777-7777-4777-8777-777777777777',
            name: 'Бот поддержки',
            subject_scope: { type: 'any_employee' },
            status: 'revoked',
            revoked_at: '2026-10-10T12:00:00+03:00',
          }),
        ]),
        listDepartments: list([department()]),
      },
    });
    expect(await screen.findByText('Шлюз платформы')).toBeTruthy();
    const heads = screen.getAllByRole('columnheader').map((h) => h.textContent?.trim());
    expect(heads).toEqual(['Название', 'Префикс ключа', 'От чьего имени', 'Источник для сверки', 'Статус', 'Создан', '']);
    expect(await screen.findByText('Сотрудники подразделения «Маркетинг»')).toBeTruthy();
    expect(screen.getByText('Любой сотрудник')).toBeTruthy();
    expect(screen.getByText('Действует')).toBeTruthy();
    expect(screen.getByText('Отозван 10.10.2026, 12:00 МСК')).toBeTruthy();
    expect(screen.getAllByText('bm_ab12')).toHaveLength(2);
    expect(api.callsOf('listCallers')[0]!.query).toEqual({});
    expect(api.violations).toEqual([]);
  });

  it('пусто — подсказка', async () => {
    renderApp('#/callers', { role: 'admin', handlers: { listCallers: list([]) } });
    expect(
      await screen.findByText(
        'Ключей нет. Выдайте ключ каждой системе, которая будет спрашивать разрешение перед вызовом модели.',
      ),
    ).toBeTruthy();
  });

  it('выдача ключа: createCaller по контракту, ключ показан один раз, «Готово» только после флажка', async () => {
    const { api } = renderApp('#/callers/create', {
      role: 'admin',
      handlers: {
        listDepartments: list([department()]),
        listProviderSources: list([]),
        createCaller: ({ body }: { body: { name: string } }) => ok({ ...caller({ name: body.name }), api_key: SECRET }, 201),
        listCallers: list([caller()]),
      },
    });
    fireEvent.change(await screen.findByLabelText(/Название/), { target: { value: 'Шлюз платформы' } });
    fireEvent.click(screen.getByLabelText('Служебная система подразделения'));
    fireEvent.mouseDown(await screen.findByLabelText(/Подразделение/));
    fireEvent.click(await screen.findByRole('option', { name: 'Маркетинг' }));
    expect(screen.getByText(/Укажите, если вызовы этой системы идут через workspace или проект/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Выдать ключ' }));

    await waitFor(() => expect(api.callsOf('createCaller')).toHaveLength(1));
    expect(api.callsOf('createCaller')[0]!.body).toEqual({
      name: 'Шлюз платформы',
      subject_scope: { type: 'service', department_id: DEP_ID },
    });
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText('Ключ создан. Сохраните его сейчас — повторно он не будет показан.')).toBeTruthy();
    expect(within(dialog).getByText(SECRET)).toBeTruthy();
    const done = within(dialog).getByRole('button', { name: 'Готово' }) as HTMLButtonElement;
    expect(done.disabled).toBe(true);
    fireEvent.keyDown(dialog, { key: 'Escape' });
    expect(screen.getByRole('dialog')).toBeTruthy();
    fireEvent.click(within(dialog).getByLabelText('Я сохранил ключ'));
    expect(done.disabled).toBe(false);
    fireEvent.click(done);
    await waitFor(() => expect(screen.queryByText(SECRET)).toBeNull());
    expect(window.localStorage.length).toBe(0);
    expect(api.violations).toEqual([]);
  });

  it('для «Сотрудники подразделения» без подразделения — ошибка, запроса нет', async () => {
    const { api } = renderApp('#/callers/create', {
      role: 'admin',
      handlers: { listDepartments: list([department()]), listProviderSources: list([]) },
    });
    fireEvent.change(await screen.findByLabelText(/Название/), { target: { value: 'X' } });
    fireEvent.click(screen.getByLabelText('Сотрудники подразделения'));
    fireEvent.click(screen.getByRole('button', { name: 'Выдать ключ' }));
    expect(await screen.findByText('Выберите подразделение')).toBeTruthy();
    expect(api.callsOf('createCaller')).toHaveLength(0);
  });

  it('отзыв: диалог с текстом, revokeCaller по контракту', async () => {
    const { api } = renderApp('#/callers', {
      role: 'admin',
      handlers: {
        listCallers: list([caller()]),
        listDepartments: list([department()]),
        revokeCaller: ok(caller({ status: 'revoked', revoked_at: '2026-10-15T10:00:00+03:00' })),
      },
    });
    fireEvent.click(await screen.findByRole('button', { name: 'Отозвать ключ «Шлюз платформы»' }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText(/Он перестанет работать сразу: запросы с ним будут отклонены/)).toBeTruthy();
    expect(document.activeElement).toBe(within(dialog).getByRole('button', { name: 'Отмена' }));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Отозвать ключ' }));
    await waitFor(() => expect(api.callsOf('revokeCaller')).toHaveLength(1));
    expect(api.callsOf('revokeCaller')[0]!.path).toBe(`/api/v1/callers/${caller().id}/revoke`);
    expect(await screen.findByText('Ключ отозван')).toBeTruthy();
    expect(api.violations).toEqual([]);
  });

  it('изменение: только название и источник — updateCaller по контракту', async () => {
    const { api } = renderApp(`#/callers/${caller().id}`, {
      role: 'admin',
      handlers: {
        listCallers: list([caller()]),
        listDepartments: list([department()]),
        listProviderSources: list([]),
        updateCaller: ({ body }: { body: object }) => ok({ ...caller(), ...body }),
      },
    });
    const name = await screen.findByDisplayValue('Шлюз платформы');
    fireEvent.change(name, { target: { value: 'Шлюз 2' } });
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить' }));
    await waitFor(() => expect(api.callsOf('updateCaller')).toHaveLength(1));
    expect(api.callsOf('updateCaller')[0]!.body).toEqual({ name: 'Шлюз 2' });
    expect(api.violations).toEqual([]);
  });
});
