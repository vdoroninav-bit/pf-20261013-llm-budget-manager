// HTTP-клиент админки: cookie-сессия (same-origin), X-CSRF-Token на изменяющих запросах,
// разбор application/problem+json в HttpError с русским текстом (12_openapi.yaml, «Общие правила»).
import { HttpError } from 'ra-core';
import { problemText } from './problemText';
import { ru } from '../i18n/ru';
import type { SessionStore } from './session';
import type { Problem } from './types';

export type FetchLike = (input: string, init?: RequestInit) => Promise<Response>;
export type HttpMethod = 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';
export type QueryValue = string | number | boolean | null | undefined;

export interface RequestOptions {
  query?: Record<string, QueryValue>;
  body?: unknown;
}

export interface HttpResponse<T> {
  status: number;
  body: T | undefined;
}

export interface HttpClient {
  request<T = unknown>(method: HttpMethod, path: string, options?: RequestOptions): Promise<HttpResponse<T>>;
}

export interface HttpClientDeps {
  fetch: FetchLike;
  session: SessionStore;
  /** Префикс API. Админка и API — один origin (12_arch_decision.md), поэтому путь относительный. */
  baseUrl?: string;
}

const SAFE_METHODS = new Set<HttpMethod>(['GET']);

export function buildQuery(query: Record<string, QueryValue> | undefined): string {
  if (!query) return '';
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === null || value === '') continue;
    params.append(key, String(value));
  }
  const s = params.toString();
  return s ? `?${s}` : '';
}

/** Problem.errors[] → { поле: текст, root: { serverError } } — формат серверной валидации react-admin. */
function formErrors(problem: Partial<Problem>, message: string) {
  if (!problem.errors?.length) return undefined;
  const errors: Record<string, unknown> = {};
  for (const e of problem.errors) {
    if (e.field && e.message) errors[e.field] = e.message;
  }
  errors.root = { serverError: message };
  return errors;
}

async function readBody(res: Response): Promise<unknown> {
  if (res.status === 204) return undefined;
  const type = res.headers.get('content-type') ?? '';
  // Текстовые ответы (getSpendCsv — text/csv) отдаются строкой как есть.
  if (res.ok && type.startsWith('text/')) return res.text();
  if (!type.includes('json')) return undefined;
  try {
    return await res.json();
  } catch {
    return undefined;
  }
}

export function createHttpClient({ fetch, session, baseUrl = '/api/v1' }: HttpClientDeps): HttpClient {
  return {
    async request<T>(method: HttpMethod, path: string, options: RequestOptions = {}) {
      const headers: Record<string, string> = { Accept: 'application/json' };
      let body: string | FormData | undefined;
      if (options.body instanceof FormData) {
        // multipart/form-data: границу частей выставляет сам fetch, Content-Type не задаём.
        body = options.body;
      } else if (options.body !== undefined) {
        headers['Content-Type'] = 'application/json';
        body = JSON.stringify(options.body);
      }
      const csrf = session.csrfToken();
      if (!SAFE_METHODS.has(method) && csrf) headers['X-CSRF-Token'] = csrf;

      let res: Response;
      try {
        res = await fetch(baseUrl + path + buildQuery(options.query), {
          method,
          headers,
          body,
          credentials: 'same-origin',
        });
      } catch {
        throw new HttpError(ru.bm.problem.network, 0, { code: 'network' });
      }

      const parsed = await readBody(res);
      if (!res.ok) {
        const problem = (parsed && typeof parsed === 'object' ? parsed : {}) as Partial<Problem>;
        const message = problemText({ status: res.status, code: problem.code, detail: problem.detail });
        throw new HttpError(message, res.status, {
          ...problem,
          ...(formErrors(problem, message) ? { errors: formErrors(problem, message) } : {}),
        });
      }
      return { status: res.status, body: parsed as T | undefined };
    },
  };
}
