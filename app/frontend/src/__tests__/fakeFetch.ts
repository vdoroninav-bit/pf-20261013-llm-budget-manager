// Фейк fetch для тестов: отвечает по очереди заранее заданными ответами и записывает запросы.
// Внешних сервисов тесты не требуют (engineering-standards, п. 2).

export interface RecordedCall {
  url: string;
  method: string;
  headers: Record<string, string>;
  credentials: RequestCredentials | undefined;
  body: unknown;
}

export interface FakeReply {
  status: number;
  body?: unknown;
  contentType?: string;
}

export const json = (status: number, body: unknown): FakeReply => ({ status, body });
export const problem = (status: number, code: string, detail?: string, extra: object = {}): FakeReply => ({
  status,
  contentType: 'application/problem+json',
  body: { type: 'about:blank', title: code, status, code, ...(detail ? { detail } : {}), ...extra },
});
export const noContent = (): FakeReply => ({ status: 204 });

export function fakeFetch(...replies: Array<FakeReply | Error>) {
  const queue = [...replies];
  const calls: RecordedCall[] = [];
  const fn = async (input: string, init: RequestInit = {}): Promise<Response> => {
    const headers: Record<string, string> = {};
    new Headers(init.headers).forEach((v, k) => {
      headers[k] = v;
    });
    let body: unknown = init.body;
    if (typeof init.body === 'string' && headers['content-type']?.includes('json')) {
      body = JSON.parse(init.body);
    }
    calls.push({ url: input, method: init.method ?? 'GET', headers, credentials: init.credentials, body });
    const next = queue.shift();
    if (next === undefined) throw new Error(`fakeFetch: нет ответа для ${init.method ?? 'GET'} ${input}`);
    if (next instanceof Error) throw next;
    if (next.status === 204 || next.body === undefined) return new Response(null, { status: next.status });
    return new Response(JSON.stringify(next.body), {
      status: next.status,
      headers: { 'content-type': next.contentType ?? 'application/json' },
    });
  };
  return { fetch: fn, calls, pending: () => queue.length };
}
