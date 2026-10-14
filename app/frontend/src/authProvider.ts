// authProvider: локальные учётные записи админки (FR-030), cookie bm_session + X-CSRF-Token.
// Операции: login, logout, me (12_openapi.yaml, tag auth). CSRF приходит в Session.csrf_token
// из login и из me (после перезагрузки страницы) и хранится только в памяти.
import type { AuthProvider, UserIdentity } from 'ra-core';
import type { HttpClient } from './api/http';
import type { SessionStore } from './api/session';
import type { Role, Session } from './api/types';

export interface BmIdentity extends UserIdentity {
  role: Role;
  departmentId: string | null;
}

/** Маршрут смены пароля (design/screens.md §1.2: /admin/#/password). Экран — в T043. */
export const PASSWORD_ROUTE = '/password';

const statusOf = (error: unknown): number | undefined =>
  error && typeof error === 'object' && 'status' in error ? Number((error as { status: unknown }).status) : undefined;

export function createAuthProvider(http: HttpClient, session: SessionStore): AuthProvider {
  async function current(): Promise<Session> {
    const known = session.get();
    if (known) return known;
    const { body } = await http.request<Session>('GET', '/auth/me');
    if (!body) throw new Error('auth/me: пустой ответ');
    session.set(body);
    return body;
  }

  return {
    async login({ username, password }: { username: string; password: string }) {
      session.set(null);
      const { body } = await http.request<Session>('POST', '/auth/login', { body: { login: username, password } });
      if (!body) throw new Error('auth/login: пустой ответ');
      session.set(body);
      return body.user.must_change_password ? { redirectTo: PASSWORD_ROUTE } : undefined;
    },

    async logout() {
      if (session.get()) {
        try {
          await http.request('POST', '/auth/logout');
        } catch {
          // Сессия уже могла истечь на сервере — выходим в любом случае.
        }
      }
      session.set(null);
    },

    async checkAuth() {
      await current();
    },

    async checkError(error: unknown) {
      if (statusOf(error) === 401) {
        session.set(null);
        throw error;
      }
      // 403 — нет прав на раздел: пользователь остаётся в админке и видит текст ошибки.
    },

    async getIdentity(): Promise<BmIdentity> {
      const { user } = await current();
      return { id: user.id, fullName: user.login, role: user.role, departmentId: user.department_id ?? null };
    },

    async getPermissions() {
      return (await current()).user.role;
    },
  };
}
