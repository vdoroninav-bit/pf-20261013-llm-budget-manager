import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import englishMessages from 'ra-language-english';
import { parse } from 'yaml';
import { ru } from '../i18n/ru';
import { i18nProvider } from '../i18n';
import { problemText } from '../api/problemText';

const leafKeys = (obj: object, prefix = ''): string[] =>
  Object.entries(obj).flatMap(([k, v]) =>
    v !== null && typeof v === 'object' ? leafKeys(v as object, `${prefix}${k}.`) : [`${prefix}${k}`],
  );

const get = (obj: unknown, path: string): unknown =>
  path.split('.').reduce<unknown>((o, k) => (o as Record<string, unknown> | undefined)?.[k], obj);

describe('словарь ru для react-admin', () => {
  it('переводит каждый ключ ядра react-admin', () => {
    const missing = leafKeys(englishMessages).filter((k) => typeof get(ru, k) !== 'string');
    expect(missing).toEqual([]);
  });

  it('не оставляет английских строк ядра (кроме пустых)', () => {
    const same = leafKeys(englishMessages).filter((k) => {
      const en = get(englishMessages, k) as string;
      // Строки из одних подстановок (например, '%{name}') переводить нечего.
      const hasWords = /[A-Za-z]/.test(en.replace(/%\{\w+\}/g, ''));
      return hasWords && get(ru, k) === en;
    });
    expect(same).toEqual([]);
  });

  it('i18nProvider по умолчанию русский и склоняет по правилам русского языка', () => {
    expect(i18nProvider.getLocale()).toBe('ru');
    expect(i18nProvider.translate('ra.auth.sign_in')).toBe('Войти');
    const deleted = (n: number) => i18nProvider.translate('ra.notification.deleted', { smart_count: n });
    expect([deleted(1), deleted(3), deleted(5), deleted(21)]).toEqual([
      '1 запись удалена',
      '3 записи удалены',
      '5 записей удалены',
      '21 запись удалена',
    ]);
  });
});

describe('тексты ошибок Problem (13_design.md §7.1)', () => {
  const spec = parse(readFileSync(fileURLToPath(new URL('../../../../12_openapi.yaml', import.meta.url)), 'utf8'));
  const adminCodes: string[] = (spec.components.schemas.Problem.properties.code.examples as string[]).filter(
    // Коды Decision API в админке не встречаются (13_design.md §7.1).
    (c) => !['subject_out_of_scope', 'decision_not_found', 'batch_too_large'].includes(c),
  );

  it('у каждого кода админки из контракта есть русский текст', () => {
    const missing = adminCodes.filter((c) => !ru.bm.problem[c as keyof typeof ru.bm.problem]);
    expect(missing).toEqual([]);
  });

  it('detail с сервера важнее словаря', () => {
    expect(problemText({ status: 403, code: 'forbidden', detail: 'Только своё подразделение' })).toBe(
      'Только своё подразделение',
    );
  });

  it('неизвестный код: 5xx и 4xx получают общие тексты', () => {
    expect(problemText({ status: 502 })).toBe(ru.bm.problem.server_error);
    expect(problemText({ status: 400, code: 'something_new' })).toBe(ru.bm.problem.unknown);
  });
});
