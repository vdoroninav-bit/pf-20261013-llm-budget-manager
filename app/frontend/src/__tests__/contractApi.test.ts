// Самопроверка фейкового API тестов: он обязан ловить запросы и ответы не по контракту,
// иначе тесты экранов «отправляет запрос по контракту» ничего не доказывают.
import { describe, expect, it } from 'vitest';
import { contractApi, fail, list, ok } from './support/contractApi';
import { department } from './support/fixtures';

const json = (method: string, body?: unknown, csrf = true): RequestInit => ({
  method,
  headers: { 'Content-Type': 'application/json', ...(csrf ? { 'X-CSRF-Token': 'csrf-test' } : {}) },
  body: body === undefined ? undefined : JSON.stringify(body),
});

describe('contractApi', () => {
  it('корректный запрос и ответ — без нарушений, вызов записан с operationId', async () => {
    const api = contractApi({ role: 'admin', handlers: { listDepartments: list([department()]) } });
    const res = await api.fetch('/api/v1/departments?page=1&page_size=50&sort=name&order=asc');
    expect(res.status).toBe(200);
    expect(api.violations).toEqual([]);
    expect(api.callsOf('listDepartments')[0]!.query).toEqual({ page: '1', page_size: '50', sort: 'name', order: 'asc' });
  });

  it('ловит путь вне контракта, лишний query, роль вне x-roles', async () => {
    const api = contractApi({ role: 'finance_viewer', handlers: { listEmployees: list([]) } });
    await api.fetch('/api/v1/nope');
    await api.fetch('/api/v1/employees?foo=1');
    expect(api.violations.join('\n')).toMatch(/нет в контракте/);
    expect(api.violations.join('\n')).toMatch(/query «foo»/);
    expect(api.violations.join('\n')).toMatch(/роль finance_viewer/);
  });

  it('ловит тело не по схеме (лишнее поле, неверный Money) и отсутствие CSRF', async () => {
    const api = contractApi({ role: 'admin', handlers: { putDepartmentLimits: ok({ day: null, week: null, month: null }) } });
    await api.fetch('/api/v1/departments/d/limits', json('PUT', { day: '5000', week: null, month: null, x: 1 }, false));
    const v = api.violations.join('\n');
    expect(v).toMatch(/\$\.day/);
    expect(v).toMatch(/\$\.x: поле не объявлено/);
    expect(v).toMatch(/нет X-CSRF-Token/);
  });

  it('ловит ответ фейка не по контракту: необъявленный статус и тело не той схемы', async () => {
    const api = contractApi({
      role: 'admin',
      handlers: { getDepartment: ok({ id: 'x' }), updateDepartment: fail(418, 'teapot') },
    });
    await api.fetch('/api/v1/departments/d');
    await api.fetch('/api/v1/departments/d', json('PATCH', { name: 'A' }));
    const v = api.violations.join('\n');
    expect(v).toMatch(/getDepartment ответ: \$\.name: обязательное поле/);
    expect(v).toMatch(/updateDepartment: статус 418 не объявлен/);
  });
});
