// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { App } from '../App';
import { PASSWORD_ROUTE } from '../authProvider';
import { fakeFetch, json, problem } from './fakeFetch';

afterEach(() => {
  cleanup();
  window.location.hash = '';
  document.documentElement.removeAttribute('data-theme');
});

describe('каркас админки', () => {
  it('без сессии показывает русскую форму входа', async () => {
    const ff = fakeFetch(problem(401, 'unauthorized'), problem(401, 'unauthorized'), problem(401, 'unauthorized'));
    render(<App fetch={ff.fetch} />);
    expect(await screen.findByRole('button', { name: 'Войти' })).toBeTruthy();
    expect(screen.getByLabelText(/Логин/)).toBeTruthy();
    expect(screen.getByLabelText(/Пароль/)).toBeTruthy();
    expect(screen.getByRole('heading', { name: 'Бюджеты LLM' })).toBeTruthy();
    expect(ff.calls[0]!.url).toBe('/api/v1/auth/me');
  });

  it('с сессией открывает панель и выставляет data-theme на <html>', async () => {
    const me = {
      user: { id: 'u-1', login: 'admin', role: 'admin', department_id: null, active: true, must_change_password: false },
      csrf_token: 'c',
    };
    const ff = fakeFetch(...Array.from({ length: 6 }, () => json(200, me)));
    render(<App fetch={ff.fetch} />);
    expect(await screen.findByText('Вы вошли как admin (Администратор).')).toBeTruthy();
    expect(['light', 'dark']).toContain(document.documentElement.dataset.theme);
    expect(window.localStorage.length).toBe(0);
  });

  it('не отправляет телеметрию react-admin (единственный внешний адрес в сборке)', async () => {
    const loaded: string[] = [];
    // Фиксируем адреса, которые кто-либо попытается загрузить через new Image() (так шлёт телеметрию ra-core).
    class SpyImage {
      set src(value: string) {
        loaded.push(value);
      }
    }
    vi.stubGlobal('Image', SpyImage);
    // ra-core шлёт телеметрию только в production-сборке — иначе тест прошёл бы и без disableTelemetry.
    vi.stubEnv('NODE_ENV', 'production');
    try {
      const ff = fakeFetch(problem(401, 'unauthorized'), problem(401, 'unauthorized'), problem(401, 'unauthorized'));
      render(<App fetch={ff.fetch} />);
      await screen.findByRole('button', { name: 'Войти' });
      expect(loaded).toEqual([]);
    } finally {
      vi.unstubAllEnvs();
      vi.unstubAllGlobals();
    }
  });

  it('маршрут смены пароля, куда ведёт login при must_change_password, зарегистрирован', async () => {
    const me = {
      user: { id: 'u-2', login: 'ivanova', role: 'budget_owner', department_id: 'd-1', active: true, must_change_password: true },
      csrf_token: 'c',
    };
    window.location.hash = `#${PASSWORD_ROUTE}`;
    const ff = fakeFetch(...Array.from({ length: 6 }, () => json(200, me)));
    render(<App fetch={ff.fetch} />);
    expect(await screen.findByRole('heading', { name: 'Смена пароля' })).toBeTruthy();
  });
});
