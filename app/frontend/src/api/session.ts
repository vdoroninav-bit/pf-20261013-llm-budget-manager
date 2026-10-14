// Сессия админки живёт только в памяти вкладки: csrf_token не кладётся ни в localStorage, ни в sessionStorage
// (13_design.md §10). После перезагрузки страницы сессию и CSRF заново даёт GET /auth/me.
import type { Session } from './types';

export interface SessionStore {
  get(): Session | null;
  set(session: Session | null): void;
  csrfToken(): string | null;
}

export function createSessionStore(): SessionStore {
  let current: Session | null = null;
  return {
    get: () => current,
    set: (session) => {
      current = session;
    },
    csrfToken: () => current?.csrf_token ?? null,
  };
}
