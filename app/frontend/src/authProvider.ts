// authProvider: локальные учётные записи админки (FR-030), cookie bm_session + X-CSRF-Token.
// Операции: login, logout, me (12_openapi.yaml, tag auth). CSRF приходит в Session.csrf_token
// из login и из me (после перезагрузки страницы) и хранится только в памяти.
import type { AuthProvider, UserIdentity } from 'ra-core';
import type { HttpClient } from './api/http';
import type { SessionStore } from './api/session';
import type { Role, Schemas, Session } from './api/types';

export interface BmIdentity extends UserIdentity {
  role: Role;
  departmentId: string | null;
  mustChangePassword: boolean;
}

/** Маршрут смены пароля (design/screens.md §1.2: /admin/#/password). */
export const PASSWORD_ROUTE = '/password';

const statusOf = (error: unknown): number | undefined =>
  error && typeof error === 'object' && 'status' in error ? Number((error as { status: unknown }).status) : undefined;

const problemCode = (error: unknown): string | undefined => {
  const body = error && typeof error === 'object' ? (error as { body?: { code?: unknown } }).body : undefined;
  return typeof body?.code === 'string' ? body.code : undefined;
};

export interface BmAuthProvider extends AuthProvider {
  /** changePassword (204). После успеха сессия больше не требует смены пароля. */
  changePassword(body: Schemas['PasswordChange']): Promise<void>;
}

export function createAuthProvider(http: HttpClient, session: SessionStore): BmAuthProvider {
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
      // 403 password_change_required (контракт 1.0.2): временный пароль не сменён — на экран смены пароля, без выхода.
      if (statusOf(error) === 403 && problemCode(error) === 'password_change_required') {
        const known = session.get();
        if (known) session.set({ ...known, user: { ...known.user, must_change_password: true } });
        throw { redirectTo: PASSWORD_ROUTE, logoutUser: false, message: false };
      }
      // Остальные 403 — нет прав на раздел: пользователь остаётся в админке и видит состояние «Нет доступа».
    },

    async changePassword(body) {
      await http.request('POST', '/auth/password', { body });
      const known = session.get();
      if (known) session.set({ ...known, user: { ...known.user, must_change_password: false } });
    },

    async getIdentity(): Promise<BmIdentity> {
      const { user } = await current();
      return {
        id: user.id,
        fullName: user.login,
        role: user.role,
        departmentId: user.department_id ?? null,
        mustChangePassword: user.must_change_password,
      };
    },

    async getPermissions() {
      // Без сессии прав нет (null), а не ошибка: иначе react-admin ждёт повторов запроса прав,
      // прежде чем показать вход (ресурсы зависят от роли — App.tsx, resourcesFor).
      try {
        return (await current()).user.role;
      } catch (error) {
        if (statusOf(error) === 401) return null;
        throw error;
      }
    },
  };
}
