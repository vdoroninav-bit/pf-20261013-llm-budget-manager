// Фейковый API по 12_openapi.yaml для тестов экранов (вместо MSW, см. 14_dev_frontend.md, T043).
// Ответы задаются по operationId. Каждый запрос сверяется с контрактом:
// - путь и метод должны быть операцией контракта, роль сессии — входить в её x-roles;
// - query-параметры — только объявленные; тело JSON — по схеме requestBody; multipart — только объявленные поля;
// - на изменяющих запросах есть X-CSRF-Token;
// - ответ фейка (статус и тело) тоже сверяется с контрактом, чтобы моки не расходились с API.
// Нарушения копятся в `violations`; тест проверяет, что их нет.
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { parse } from 'yaml';
import type { Role, Session } from '../../api/types';
import { checkSchema, type Schema, type SchemaRoot } from './schemaCheck';

type Method = 'get' | 'post' | 'put' | 'patch' | 'delete';
interface Param {
  $ref?: string;
  name: string;
  in: string;
  required?: boolean;
}
interface MediaType {
  schema?: Schema;
}
interface Operation {
  operationId: string;
  security?: Array<Record<string, unknown>>;
  'x-roles'?: Role[];
  parameters?: Param[];
  requestBody?: { content: Record<string, MediaType> };
  responses: Record<string, { $ref?: string; content?: Record<string, MediaType> }>;
}
type PathItem = Partial<Record<Method, Operation>> & { parameters?: Param[] };

export interface Spec extends SchemaRoot {
  info: { version: string };
  paths: Record<string, PathItem>;
  components: SchemaRoot['components'] & {
    parameters: Record<string, Param>;
    responses: Record<string, { content?: Record<string, MediaType> }>;
  };
}

// Путь от корня vitest (app/frontend): в окружении jsdom import.meta.url вспомогательных модулей не file://.
export const spec = parse(readFileSync(resolve(process.cwd(), '../../12_openapi.yaml'), 'utf8')) as Spec;

export interface ApiCall {
  operationId: string;
  method: string;
  url: string;
  path: string;
  pathParams: Record<string, string>;
  query: Record<string, string>;
  headers: Record<string, string>;
  /** JSON — разобранный объект; multipart — объект полей (File для файлов). any — для удобства обработчиков в тестах. */
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  body: any;
}

export interface Reply {
  status: number;
  body?: unknown;
}
export type Handler = Reply | ((call: ApiCall) => Reply | Promise<Reply>);

export const ok = (body: unknown, status = 200): Reply => ({ status, body });
export const fail = (status: number, code: string, detail?: string, extra: object = {}): Reply => ({
  status,
  body: { type: 'about:blank', title: code, status, code, ...(detail ? { detail } : {}), ...extra },
});
export const list = (items: unknown[], total = items.length): Reply => ok({ items, total });

const API = '/api/v1';

interface Route {
  method: Method;
  template: string;
  regex: RegExp;
  names: string[];
  op: Operation;
  pathParams: Param[];
}

const routes: Route[] = Object.entries(spec.paths).flatMap(([template, item]) =>
  (['get', 'post', 'put', 'patch', 'delete'] as const)
    .filter((m) => item[m])
    .map((method) => {
      const names: string[] = [];
      const regex = new RegExp(
        `^${template.replace(/\{([a-z_]+)\}/g, (_, n: string) => {
          names.push(n);
          return '([^/]+)';
        })}$`,
      );
      return { method, template, regex, names, op: item[method]!, pathParams: item.parameters ?? [] };
    }),
);

const deref = (p: Param): Param => (p.$ref ? spec.components.parameters[p.$ref.split('/').pop()!]! : p);

function responseSchema(op: Operation, status: number): { declared: boolean; schema?: Schema } {
  let r = op.responses[String(status)];
  if (!r) return { declared: false };
  if (r.$ref) r = spec.components.responses[r.$ref.split('/').pop()!]!;
  const media = r.content?.['application/json'] ?? r.content?.['application/problem+json'];
  return { declared: true, schema: media?.schema };
}

export const session = (role: Role, extra: Partial<Session['user']> = {}): Session => ({
  user: {
    id: '00000000-0000-4000-8000-000000000001',
    login: role === 'admin' ? 'admin' : role === 'budget_owner' ? 'ivanova' : 'petrova',
    role,
    department_id: role === 'budget_owner' ? DEP_ID : null,
    active: true,
    must_change_password: false,
    ...extra,
  },
  csrf_token: 'csrf-test',
});

export const DEP_ID = '11111111-1111-4111-8111-111111111111';
export const DEP2_ID = '22222222-2222-4222-8222-222222222222';

export interface ContractApiOptions {
  role: Role;
  session?: Session;
  handlers: Record<string, Handler>;
}

export function contractApi({ role, session: s = session(role), handlers }: ContractApiOptions) {
  const calls: ApiCall[] = [];
  const violations: string[] = [];
  const all: Record<string, Handler> = { me: ok(s), ...handlers };

  async function fetch(input: string, init: RequestInit = {}): Promise<Response> {
    const method = (init.method ?? 'GET').toLowerCase() as Method;
    const url = new URL(input, 'http://localhost');
    const headers: Record<string, string> = {};
    new Headers(init.headers).forEach((v, k) => {
      headers[k] = v;
    });
    const where = `${method.toUpperCase()} ${url.pathname}`;
    const route = routes.find((r) => r.method === method && r.regex.test(url.pathname));
    if (!route || !url.pathname.startsWith(API)) {
      violations.push(`${where}: такой операции нет в контракте`);
      return problemResponse(404, 'not_found');
    }
    const op = route.op;
    const m = route.regex.exec(url.pathname)!;
    const pathParams = Object.fromEntries(route.names.map((n, i) => [n, decodeURIComponent(m[i + 1]!)]));
    const query = Object.fromEntries(url.searchParams.entries());

    // Роль и CSRF
    if (op['x-roles'] && !op['x-roles'].includes(s.user.role)) {
      violations.push(`${op.operationId}: роль ${s.user.role} не входит в x-roles ${op['x-roles'].join(',')}`);
    }
    const needsCsrf = op.security?.some((sec) => 'csrf' in sec);
    if (needsCsrf && headers['x-csrf-token'] !== s.csrf_token) violations.push(`${op.operationId}: нет X-CSRF-Token`);

    // Query
    const declared = [...route.pathParams, ...(op.parameters ?? [])].map(deref);
    const queryDeclared = declared.filter((p) => p.in === 'query');
    for (const name of Object.keys(query)) {
      if (!queryDeclared.some((p) => p.name === name)) violations.push(`${op.operationId}: query «${name}» не объявлен`);
    }
    for (const p of queryDeclared.filter((p) => p.required)) {
      if (!(p.name in query)) violations.push(`${op.operationId}: нет обязательного query «${p.name}»`);
    }

    // Тело
    let body: unknown;
    const content = op.requestBody?.content ?? {};
    if (init.body instanceof FormData) {
      const fields: Record<string, unknown> = {};
      init.body.forEach((v, k) => {
        fields[k] = v;
      });
      body = fields;
      const schema = content['multipart/form-data']?.schema;
      if (!schema) violations.push(`${op.operationId}: multipart не объявлен`);
      else {
        const props = (schema.properties ?? {}) as Record<string, unknown>;
        for (const k of Object.keys(fields)) if (!(k in props)) violations.push(`${op.operationId}: поле ${k} не объявлено`);
        for (const k of (schema.required ?? []) as string[]) {
          if (!(k in fields)) violations.push(`${op.operationId}: нет поля ${k}`);
        }
      }
    } else if (typeof init.body === 'string') {
      body = JSON.parse(init.body);
      const schema = content['application/json']?.schema;
      if (!schema) violations.push(`${op.operationId}: тело JSON не объявлено`);
      else violations.push(...checkSchema(spec, schema, body).map((e) => `${op.operationId} запрос: ${e}`));
    } else if (op.requestBody && (op.requestBody as { required?: boolean }).required) {
      violations.push(`${op.operationId}: нет обязательного тела`);
    }

    const call: ApiCall = {
      operationId: op.operationId,
      method: method.toUpperCase(),
      url: input,
      path: url.pathname,
      pathParams,
      query,
      headers,
      body,
    };
    calls.push(call);

    const handler = all[op.operationId];
    if (!handler) {
      violations.push(`${op.operationId}: в тесте нет ответа (${where})`);
      return problemResponse(500, 'server_error');
    }
    const reply = typeof handler === 'function' ? await handler(call) : handler;
    const declaredResp = responseSchema(op, reply.status);
    if (!declaredResp.declared) violations.push(`${op.operationId}: статус ${reply.status} не объявлен`);
    else if (declaredResp.schema && reply.body !== undefined) {
      violations.push(...checkSchema(spec, declaredResp.schema, reply.body).map((e) => `${op.operationId} ответ: ${e}`));
    }
    if (reply.status === 204 || reply.body === undefined) return new Response(null, { status: reply.status });
    const type = reply.status >= 400 ? 'application/problem+json' : 'application/json';
    return new Response(JSON.stringify(reply.body), { status: reply.status, headers: { 'content-type': type } });
  }

  return {
    fetch,
    calls,
    violations,
    /** Вызовы одной операции. */
    callsOf: (operationId: string) => calls.filter((c) => c.operationId === operationId),
  };
}

function problemResponse(status: number, code: string) {
  return new Response(JSON.stringify({ type: 'about:blank', title: code, status, code }), {
    status,
    headers: { 'content-type': 'application/problem+json' },
  });
}
