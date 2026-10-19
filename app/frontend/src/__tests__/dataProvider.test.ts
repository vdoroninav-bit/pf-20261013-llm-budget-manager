import { describe, expect, it } from 'vitest';
import { HttpError } from 'ra-core';
import { createDataProvider } from '../dataProvider';
import { createHttpClient } from '../api/http';
import { createSessionStore } from '../api/session';
import { fakeFetch, json, noContent, problem } from './fakeFetch';

const ADMIN = {
  user: { id: 'u-1', login: 'admin', role: 'admin', active: true, must_change_password: false, department_id: null },
  csrf_token: 'csrf-123',
} as const;

function setup(...replies: Parameters<typeof fakeFetch>) {
  const ff = fakeFetch(...replies);
  const session = createSessionStore();
  session.set(structuredClone(ADMIN) as never);
  const dp = createDataProvider(createHttpClient({ fetch: ff.fetch, session }));
  return { dp, calls: ff.calls };
}

const listParams = (over: object = {}) => ({
  pagination: { page: 1, perPage: 25 },
  sort: { field: 'id', order: 'ASC' as const },
  filter: {},
  ...over,
});

describe('dataProvider.getList: запрос', () => {
  it('переводит пагинацию, сортировку и фильтр в page, page_size, sort, order и параметры контракта', async () => {
    const { dp, calls } = setup(json(200, { items: [], total: 0 }));
    await dp.getList('departments', {
      pagination: { page: 2, perPage: 25 },
      sort: { field: 'name', order: 'DESC' },
      filter: { q: 'ИТ' },
    });
    expect(calls).toHaveLength(1);
    const url = new URL(calls[0]!.url, 'http://x');
    expect(url.pathname).toBe('/api/v1/departments');
    expect(Object.fromEntries(url.searchParams)).toEqual({
      page: '2',
      page_size: '25',
      sort: 'name',
      order: 'desc',
      q: 'ИТ',
    });
    expect(calls[0]!.method).toBe('GET');
    expect(calls[0]!.credentials).toBe('same-origin');
    expect(calls[0]!.headers['x-csrf-token']).toBeUndefined();
  });

  it('не передаёт sort и order, если операция их не объявляет (listEmployees)', async () => {
    const { dp, calls } = setup(json(200, { items: [], total: 0 }));
    await dp.getList('employees', listParams({ filter: { department_id: 'd-1' } }));
    const url = new URL(calls[0]!.url, 'http://x');
    expect(url.pathname).toBe('/api/v1/employees');
    expect(Object.fromEntries(url.searchParams)).toEqual({ page: '1', page_size: '25', department_id: 'd-1' });
  });

  it('пропускает пустые значения фильтра и передаёт булевы как true/false', async () => {
    const { dp, calls } = setup(json(200, { items: [], total: 0 }));
    await dp.getList('notifications', listParams({ filter: { unread: true } }));
    expect(calls[0]!.url).toContain('unread=true');
    const { dp: dp2, calls: calls2 } = setup(json(200, { items: [], total: 0 }));
    await dp2.getList('notifications', listParams({ filter: { unread: undefined } }));
    expect(new URL(calls2[0]!.url, 'http://x').searchParams.has('unread')).toBe(false);
  });

  it('отказывает в фильтре, которого нет в контракте операции, не делая запроса', async () => {
    const { dp, calls } = setup();
    await expect(dp.getList('departments', listParams({ filter: { name: 'x' } }))).rejects.toThrow(/name/);
    expect(calls).toHaveLength(0);
  });

  it('отказывает в page_size больше 200 (ограничение контракта)', async () => {
    const { dp, calls } = setup();
    await expect(dp.getList('departments', listParams({ pagination: { page: 1, perPage: 500 } }))).rejects.toThrow(
      /200/,
    );
    expect(calls).toHaveLength(0);
  });

  it('отказывает в неизвестном ресурсе', async () => {
    const { dp } = setup();
    await expect(dp.getList('nope', listParams())).rejects.toThrow(/nope/);
  });
});

describe('dataProvider.getList: ответ', () => {
  it('превращает {items, total} в {data, total}', async () => {
    const items = [
      { id: 'd-1', name: 'ИТ' },
      { id: 'd-2', name: 'Финансы' },
    ];
    const { dp } = setup(json(200, { items, total: 31 }));
    const res = await dp.getList('departments', listParams());
    expect(res).toEqual({ data: items, total: 31 });
  });

  it('берёт id из ключа ресурса: псевдоним сотрудника', async () => {
    const { dp } = setup(json(200, { items: [{ pseudonym: 'emp-7f3a9c01b2', active: true, limits: {} }], total: 1 }));
    const res = await dp.getList('employees', listParams());
    expect(res.data[0]).toEqual({ id: 'emp-7f3a9c01b2', pseudonym: 'emp-7f3a9c01b2', active: true, limits: {} });
  });

  it('для операции без пагинации (listFxRates) не шлёт параметры и листает на клиенте', async () => {
    const items = ['2026-07', '2026-08', '2026-09'].map((month) => ({ month, usd_rub: '90.0000' }));
    const { dp, calls } = setup(json(200, { items, total: 3 }));
    const res = await dp.getList('fx-rates', {
      pagination: { page: 2, perPage: 2 },
      sort: { field: 'month', order: 'DESC' },
      filter: {},
    });
    expect(calls[0]!.url).toBe('/api/v1/fx-rates');
    expect(res.total).toBe(3);
    expect(res.data.map((r) => r.id)).toEqual(['2026-07']);
  });
});

describe('dataProvider: остальные методы', () => {
  it('getOne кодирует идентификатор модели в пути', async () => {
    const { dp, calls } = setup(json(200, { id: 'openai/gpt-x', provider: 'openai' }));
    const res = await dp.getOne('models', { id: 'openai/gpt-x' });
    expect(calls[0]!.url).toBe('/api/v1/models/openai%2Fgpt-x');
    expect(res.data.id).toBe('openai/gpt-x');
  });

  it('getOne без отдельной операции в контракте ищет запись в полном списке (provider-sources)', async () => {
    const { dp, calls } = setup(
      json(200, {
        items: [
          { id: 's-1', provider: 'anthropic' },
          { id: 's-2', provider: 'openai' },
        ],
        total: 2,
      }),
    );
    const res = await dp.getOne('providerSources', { id: 's-2' });
    expect(calls[0]!.url).toBe('/api/v1/provider-sources');
    expect(res.data).toEqual({ id: 's-2', provider: 'openai' });
  });

  it('update шлёт PATCH только с изменёнными полями и заголовком X-CSRF-Token', async () => {
    const prev = { id: 'd-1', name: 'ИТ', policy_mode: 'observe', allowed_models: ['a'], employees_count: 3 };
    const { dp, calls } = setup(json(200, { ...prev, policy_mode: 'enforce' }));
    const res = await dp.update('departments', {
      id: 'd-1',
      data: { ...prev, policy_mode: 'enforce' },
      previousData: prev,
    });
    expect(calls[0]!.method).toBe('PATCH');
    expect(calls[0]!.url).toBe('/api/v1/departments/d-1');
    expect(calls[0]!.body).toEqual({ policy_mode: 'enforce' });
    expect(calls[0]!.headers['x-csrf-token']).toBe('csrf-123');
    expect(calls[0]!.headers['content-type']).toBe('application/json');
    expect(res.data.policy_mode).toBe('enforce');
  });

  it('create шлёт POST и возвращает запись с id', async () => {
    const { dp, calls } = setup(json(201, { id: 'd-9', name: 'Сервисы' }));
    const res = await dp.create('departments', { data: { name: 'Сервисы' } });
    expect(calls[0]!.method).toBe('POST');
    expect(calls[0]!.body).toEqual({ name: 'Сервисы' });
    expect(calls[0]!.headers['x-csrf-token']).toBe('csrf-123');
    expect(res.data).toEqual({ id: 'd-9', name: 'Сервисы' });
  });

  it('delete при 204 возвращает previousData', async () => {
    const prev = { id: 'sub-1', provider: 'openai' };
    const { dp, calls } = setup(noContent());
    const res = await dp.delete('subscriptions', { id: 'sub-1', previousData: prev });
    expect(calls[0]!.method).toBe('DELETE');
    expect(calls[0]!.url).toBe('/api/v1/subscriptions/sub-1');
    expect(res.data).toEqual(prev);
  });

  it('отказывает в операции, которой нет в контракте (удаление подразделения)', async () => {
    const { dp, calls } = setup();
    await expect(dp.delete('departments', { id: 'd-1', previousData: { id: 'd-1' } })).rejects.toThrow();
    expect(calls).toHaveLength(0);
  });

  it('getManyReference — это getList с фильтром по связи', async () => {
    const { dp, calls } = setup(json(200, { items: [], total: 0 }));
    await dp.getManyReference('employees', { target: 'department_id', id: 'd-1', ...listParams() });
    expect(new URL(calls[0]!.url, 'http://x').searchParams.get('department_id')).toBe('d-1');
  });
});

describe('dataProvider: ошибки Problem', () => {
  it('403 даёт HttpError со статусом и detail с сервера', async () => {
    const { dp } = setup(problem(403, 'forbidden', 'Подразделение недоступно'));
    const err = await dp.getOne('departments', { id: 'd-2' }).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(HttpError);
    expect((err as HttpError).status).toBe(403);
    expect((err as HttpError).message).toBe('Подразделение недоступно');
  });

  it('без detail берёт русский текст по code', async () => {
    const { dp } = setup(problem(404, 'not_found'));
    const err = (await dp.getOne('departments', { id: 'd-2' }).catch((e: unknown) => e)) as HttpError;
    expect(err.message).toBe('Запись не найдена. Возможно, её удалили — вернитесь к списку.');
  });

  it('422 раскладывает errors[] по полям формы', async () => {
    const { dp } = setup(
      problem(422, 'validation_error', undefined, { errors: [{ field: 'name', message: 'Обязательное поле' }] }),
    );
    const err = (await dp.create('departments', { data: { name: '' } }).catch((e: unknown) => e)) as HttpError;
    expect(err.status).toBe(422);
    expect(err.body.errors).toEqual({ name: 'Обязательное поле', root: { serverError: 'Проверьте выделенные поля.' } });
  });

  it('сбой сети даёт понятный текст', async () => {
    const { dp } = setup(new TypeError('Failed to fetch'));
    const err = (await dp.getOne('departments', { id: 'd-2' }).catch((e: unknown) => e)) as HttpError;
    expect(err.message).toBe('Сервер не отвечает. Проверьте, что утилита запущена, и повторите.');
  });
});
