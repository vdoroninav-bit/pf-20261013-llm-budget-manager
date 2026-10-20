// @vitest-environment jsdom
// Экран 1 «Вход» и 1.2 «Смена пароля» (design/screens.md §1), переход на смену пароля при 403 password_change_required.
import { afterEach, describe, expect, it } from 'vitest';
import { fireEvent, screen, waitFor } from '@testing-library/react';
import { fail, list, ok, session } from './support/contractApi';
import { renderApp, resetApp } from './support/renderApp';

afterEach(resetApp);

const type = (label: RegExp | string, value: string) =>
  fireEvent.change(screen.getByLabelText(label, { selector: 'input' }), { target: { value } });

describe('1.1 Вход', () => {
  it('рендерит русские подписи и отправляет login по контракту', async () => {
    let loggedIn = false;
    const { api } = renderApp('#/login', {
      role: 'admin',
      handlers: {
        me: () => (loggedIn ? ok(session('admin')) : fail(401, 'unauthorized')),
        login: () => {
          loggedIn = true;
          return ok(session('admin'));
        },
        // После входа — панель (T072): у администратора она начинается с выбора подразделения.
        listDepartments: list([]),
      },
    });
    expect(await screen.findByRole('heading', { name: 'Бюджеты LLM' })).toBeTruthy();
    expect(screen.getByText('Учёт и лимиты расходов на языковые модели')).toBeTruthy();
    type(/Логин/, 'admin');
    type(/Пароль/, 'secret-password');
    expect((screen.getByLabelText(/Логин/, { selector: 'input' }) as HTMLInputElement).autocomplete).toBe('username');
    expect((screen.getByLabelText(/Пароль/, { selector: 'input' }) as HTMLInputElement).autocomplete).toBe(
      'current-password',
    );
    fireEvent.click(screen.getByRole('button', { name: 'Войти' }));
    await waitFor(() => expect(api.callsOf('login')).toHaveLength(1));
    expect(api.callsOf('login')[0]!.body).toEqual({ login: 'admin', password: 'secret-password' });
    expect(
      await screen.findByText('Подразделений пока нет. Загрузите выгрузку групп AD или создайте подразделение вручную.'),
    ).toBeTruthy();
    expect(api.violations).toEqual([]);
  });

  it('401 invalid_credentials: текст под формой, пароль очищен, фокус в поле «Пароль»', async () => {
    const { api } = renderApp('#/login', {
      role: 'admin',
      handlers: { me: fail(401, 'unauthorized'), login: fail(401, 'invalid_credentials') },
    });
    await screen.findByRole('button', { name: 'Войти' });
    type(/Логин/, 'admin');
    type(/Пароль/, 'wrong');
    fireEvent.click(screen.getByRole('button', { name: 'Войти' }));
    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toContain('Неверный логин или пароль.');
    const password = screen.getByLabelText(/Пароль/, { selector: 'input' }) as HTMLInputElement;
    expect(password.value).toBe('');
    expect(document.activeElement).toBe(password);
    expect(api.violations).toEqual([]);
  });

  it('429 login_locked — текст о блокировке на 15 минут', async () => {
    renderApp('#/login', {
      role: 'admin',
      handlers: { me: fail(401, 'unauthorized'), login: fail(429, 'login_locked') },
    });
    await screen.findByRole('button', { name: 'Войти' });
    type(/Логин/, 'admin');
    type(/Пароль/, 'x');
    fireEvent.click(screen.getByRole('button', { name: 'Войти' }));
    expect((await screen.findByRole('alert')).textContent).toMatch(/закрыт на 15 минут/);
  });
});

describe('1.2 Смена пароля', () => {
  const forced = session('budget_owner', { must_change_password: true });

  it('принудительная смена: русские подписи, без «Отмена», есть «Выйти»; changePassword по контракту', async () => {
    const { api } = renderApp('#/password', {
      role: 'budget_owner',
      session: forced,
      handlers: { changePassword: { status: 204 } },
    });
    expect(await screen.findByRole('heading', { name: 'Смена пароля' })).toBeTruthy();
    expect(screen.getByText('Не короче 12 символов')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Отмена' })).toBeNull();
    expect(screen.getByRole('button', { name: 'Выйти' })).toBeTruthy();

    type(/Текущий пароль/, 'temporary-pass');
    type(/^Новый пароль/, 'new-password-123');
    type(/Повторите новый пароль/, 'new-password-124');
    fireEvent.click(screen.getByRole('button', { name: 'Сменить пароль' }));
    expect(await screen.findByText('Пароли не совпадают')).toBeTruthy();
    expect(api.callsOf('changePassword')).toHaveLength(0);

    type(/Повторите новый пароль/, 'new-password-123');
    fireEvent.click(screen.getByRole('button', { name: 'Сменить пароль' }));
    await waitFor(() => expect(api.callsOf('changePassword')).toHaveLength(1));
    expect(api.callsOf('changePassword')[0]!.body).toEqual({
      old_password: 'temporary-pass',
      new_password: 'new-password-123',
    });
    expect(await screen.findByText('Пароль изменён')).toBeTruthy();
    await waitFor(() => expect(window.location.hash).toBe('#/'));
    expect(api.violations).toEqual([]);
  });

  it('новый пароль короче 12 символов — ошибка под полем, запроса нет', async () => {
    const { api } = renderApp('#/password', { role: 'admin', handlers: {} });
    await screen.findByRole('heading', { name: 'Смена пароля' });
    type(/Текущий пароль/, 'old');
    type(/^Новый пароль/, 'short');
    type(/Повторите новый пароль/, 'short');
    fireEvent.click(screen.getByRole('button', { name: 'Сменить пароль' }));
    expect(await screen.findByText('Не короче 12 символов', { selector: '.Mui-error, .Mui-error *' })).toBeTruthy();
    expect(api.callsOf('changePassword')).toHaveLength(0);
  });

  // Сервер отвечает 422 invalid_credentials (отклонение backend 5, принято в 14_tech_rulings.md).
  it('неверный текущий пароль — «Текущий пароль указан неверно.», сессия не сбрасывается', async () => {
    renderApp('#/password', { role: 'admin', handlers: { changePassword: fail(422, 'invalid_credentials') } });
    await screen.findByRole('heading', { name: 'Смена пароля' });
    type(/Текущий пароль/, 'bad-old-password');
    type(/^Новый пароль/, 'new-password-123');
    type(/Повторите новый пароль/, 'new-password-123');
    fireEvent.click(screen.getByRole('button', { name: 'Сменить пароль' }));
    expect(await screen.findByText('Текущий пароль указан неверно.')).toBeTruthy();
    expect(screen.getByRole('heading', { name: 'Смена пароля' })).toBeTruthy();
  });

  it('403 password_change_required на любом экране — переход на смену пароля (14_tech_rulings.md, Б1)', async () => {
    const { api } = renderApp('#/departments', {
      role: 'admin',
      handlers: { listDepartments: fail(403, 'password_change_required'), listModels: list([]) },
    });
    expect(await screen.findByRole('heading', { name: 'Смена пароля' })).toBeTruthy();
    expect(window.location.hash).toBe('#/password');
    expect(api.violations).toEqual([]);
  });
});
