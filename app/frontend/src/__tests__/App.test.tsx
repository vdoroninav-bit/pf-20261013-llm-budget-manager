// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { App } from '../App';
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
});
