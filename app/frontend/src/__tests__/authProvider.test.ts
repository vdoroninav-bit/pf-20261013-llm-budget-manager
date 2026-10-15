import { describe, expect, it } from 'vitest';
import { HttpError } from 'ra-core';
import { createAuthProvider, PASSWORD_ROUTE } from '../authProvider';
import { createHttpClient } from '../api/http';
import { createSessionStore } from '../api/session';
import { fakeFetch, json, noContent, problem } from './fakeFetch';

const session = (over: object = {}) => ({
  user: {
    id: 'u-1',
    login: 'owner',
    role: 'budget_owner',
    department_id: 'd-1',
    active: true,
    must_change_password: false,
    ...over,
  },
  csrf_token: 'csrf-from-server',
});

function setup(...replies: Parameters<typeof fakeFetch>) {
  const ff = fakeFetch(...replies);
  const store = createSessionStore();
  const http = createHttpClient({ fetch: ff.fetch, session: store });
  return { auth: createAuthProvider(http, store), http, store, calls: ff.calls };
}

describe('authProvider.login', () => {
  it('шлёт login и password, сохраняет CSRF из ответа и подставляет его в изменяющие запросы', async () => {
    const { auth, http, store, calls } = setup(json(200, session()), json(200, {}));
    await auth.login({ username: 'owner', password: 'secret-pass' });
    expect(calls[0]!.method).toBe('POST');
    expect(calls[0]!.url).toBe('/api/v1/auth/login');
    expect(calls[0]!.body).toEqual({ login: 'owner', password: 'secret-pass' });
    expect(calls[0]!.headers['x-csrf-token']).toBeUndefined();
    expect(store.csrfToken()).toBe('csrf-from-server');

    await http.request('PATCH', '/departments/d-1', { body: {} });
    expect(calls[1]!.headers['x-csrf-token']).toBe('csrf-from-server');
  });

  it('CSRF не попадает в хранилище браузера', async () => {
    const { auth } = setup(json(200, session()));
    await auth.login({ username: 'owner', password: 'x' });
    expect(globalThis.localStorage?.length ?? 0).toBe(0);
    expect(globalThis.sessionStorage?.length ?? 0).toBe(0);
  });

  it('при must_change_password ведёт на смену пароля', async () => {
    const { auth } = setup(json(200, session({ must_change_password: true })));
    await expect(auth.login({ username: 'owner', password: 'x' })).resolves.toEqual({
      redirectTo: '/password',
    });
  });

  it('401 invalid_credentials — русский текст', async () => {
    const { auth, store } = setup(problem(401, 'invalid_credentials'));
    await expect(auth.login({ username: 'owner', password: 'bad' })).rejects.toThrow('Неверный логин или пароль.');
    expect(store.get()).toBeNull();
  });

  it('429 login_locked — текст с сервера, если он есть', async () => {
    const { auth } = setup(problem(429, 'login_locked', 'Вход закрыт до 12:15 МСК.'));
    await expect(auth.login({ username: 'owner', password: 'bad' })).rejects.toThrow('Вход закрыт до 12:15 МСК.');
  });
});

describe('authProvider.checkAuth', () => {
  it('без сессии в памяти спрашивает me и берёт CSRF из ответа (перезагрузка страницы)', async () => {
    const { auth, store, calls } = setup(json(200, session()));
    await expect(auth.checkAuth({})).resolves.toBeUndefined();
    expect(calls[0]!.url).toBe('/api/v1/auth/me');
    expect(calls[0]!.method).toBe('GET');
    expect(store.csrfToken()).toBe('csrf-from-server');
  });

  it('с сессией в памяти запросов не делает', async () => {
    const { auth, store, calls } = setup();
    store.set(session() as never);
    await auth.checkAuth({});
    expect(calls).toHaveLength(0);
  });

  it('401 от me — отказ', async () => {
    const { auth } = setup(problem(401, 'unauthorized'));
    await expect(auth.checkAuth({})).rejects.toBeDefined();
  });
});

describe('authProvider.checkError', () => {
  it('401 сбрасывает сессию и выводит на вход', async () => {
    const { auth, store } = setup();
    store.set(session() as never);
    await expect(auth.checkError(new HttpError('x', 401))).rejects.toBeDefined();
    expect(store.get()).toBeNull();
  });

  it('403 не выкидывает из админки', async () => {
    const { auth, store } = setup();
    store.set(session() as never);
    await expect(auth.checkError(new HttpError('x', 403))).resolves.toBeUndefined();
    expect(store.get()).not.toBeNull();
  });

  it('403 password_change_required ведёт на смену пароля без выхода (контракт 1.0.2, 14_tech_rulings.md Б1)', async () => {
    const { auth, store } = setup();
    store.set(session() as never);
    await expect(
      auth.checkError(new HttpError('x', 403, { code: 'password_change_required' })),
    ).rejects.toMatchObject({ redirectTo: PASSWORD_ROUTE, logoutUser: false });
    expect(store.get()?.user.must_change_password).toBe(true);
  });
});

describe('authProvider.changePassword', () => {
  it('шлёт changePassword с CSRF; после 204 смена пароля больше не требуется', async () => {
    const { auth, store, calls } = setup(noContent());
    store.set(session({ must_change_password: true }) as never);
    await auth.changePassword({ old_password: 'temporary-pass', new_password: 'new-password-123' });
    expect(calls[0]!.method).toBe('POST');
    expect(calls[0]!.url).toBe('/api/v1/auth/password');
    expect(calls[0]!.headers['x-csrf-token']).toBe('csrf-from-server');
    expect(calls[0]!.body).toEqual({ old_password: 'temporary-pass', new_password: 'new-password-123' });
    expect(store.get()?.user.must_change_password).toBe(false);
  });
});

describe('authProvider.logout', () => {
  it('шлёт logout с CSRF и забывает сессию', async () => {
    const { auth, store, calls } = setup(noContent());
    store.set(session() as never);
    await auth.logout({});
    expect(calls[0]!.url).toBe('/api/v1/auth/logout');
    expect(calls[0]!.method).toBe('POST');
    expect(calls[0]!.headers['x-csrf-token']).toBe('csrf-from-server');
    expect(store.get()).toBeNull();
  });

  it('без сессии запрос не шлёт', async () => {
    const { auth, calls } = setup();
    await auth.logout({});
    expect(calls).toHaveLength(0);
  });

  it('ошибка сервера при выходе не мешает выйти', async () => {
    const { auth, store } = setup(problem(401, 'unauthorized'));
    store.set(session() as never);
    await auth.logout({});
    expect(store.get()).toBeNull();
  });
});

describe('authProvider: личность и права', () => {
  it('getIdentity и getPermissions берутся из сессии', async () => {
    const { auth, store } = setup();
    store.set(session() as never);
    await expect(auth.getIdentity!()).resolves.toEqual({
      id: 'u-1',
      fullName: 'owner',
      role: 'budget_owner',
      departmentId: 'd-1',
      mustChangePassword: false,
    });
    await expect(auth.getPermissions!({})).resolves.toBe('budget_owner');
  });
});
