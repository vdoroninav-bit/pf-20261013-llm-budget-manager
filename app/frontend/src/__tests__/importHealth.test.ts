// Правила состояния импорта (design/screens.md §2 «Плашка импорта», §7.1; 13_design_rulings.md В1, В5, В7).
import { describe, expect, it } from 'vitest';
import {
  errorClassText,
  importBannerLines,
  isImportLagging,
  runsPollInterval,
  yesterdayUtc,
} from '../import/importHealth';
import type { Schemas } from '../api/types';

type ProviderStatus = Schemas['ImportStatus']['providers'][number];
const anthropic = (over: Partial<ProviderStatus> = {}): ProviderStatus => ({
  provider: 'anthropic',
  configured: true,
  last_success_day: '2026-10-13',
  last_run_at: '2026-10-14T09:00:00+03:00',
  lag_hours: 16,
  consecutive_failures: 0,
  last_error_class: null,
  unallocated_sources: [],
  ...over,
});

describe('SC-008: отставание импорта — одно правило для панели и экрана 7 (В7)', () => {
  it('«вчерашние сутки UTC» считаются от даты UTC, а не МСК', () => {
    // 14.10 02:00 МСК = 13.10 23:00 UTC → вчера по UTC = 12.10.
    expect(yesterdayUtc(new Date('2026-10-14T02:00:00+03:00'))).toBe('2026-10-12');
    expect(yesterdayUtc(new Date('2026-10-14T12:00:00+03:00'))).toBe('2026-10-13');
    expect(yesterdayUtc(new Date('2026-03-01T12:00:00Z'))).toBe('2026-02-28');
  });

  it('до 12:00 МСК — не отстаёт, даже если загрузок давно нет', () => {
    expect(isImportLagging('2026-10-01', new Date('2026-10-14T11:59:00+03:00'))).toBe(false);
    expect(isImportLagging('2026-10-01', new Date('2026-10-14T12:00:00+03:00'))).toBe(false);
  });

  it('после 12:00 МСК: загружено по вчера UTC — норма; раньше — отставание', () => {
    const now = new Date('2026-10-14T12:01:00+03:00');
    expect(isImportLagging('2026-10-13', now)).toBe(false);
    expect(isImportLagging('2026-10-14', now)).toBe(false);
    expect(isImportLagging('2026-10-12', now)).toBe(true);
  });

  it('загрузок не было ни разу — после 12:00 это отставание', () => {
    expect(isImportLagging(null, new Date('2026-10-14T16:20:00+03:00'))).toBe(true);
  });
});

describe('класс ошибки импорта (13_design.md §7.4, В5)', () => {
  it('четыре класса — тексты словаря; неизвестный — «Загрузка не удалась»', () => {
    expect(errorClassText('auth_rejected')).toBe('Провайдер отклонил ключ администратора. Проверьте ключ на сервере.');
    expect(errorClassText('provider_unavailable')).toBe('Провайдер не ответил. Загрузка повторится по расписанию.');
    expect(errorClassText('bad_response')).toBe(
      'Провайдер вернул данные в неожиданном формате. Нужна проверка коннектора.',
    );
    expect(errorClassText('internal')).toBe(
      'Загрузка не удалась из-за внутренней ошибки. Подробности — в журнале контейнера worker.',
    );
    expect(errorClassText('quota_exceeded')).toBe('Загрузка не удалась');
  });
});

describe('плашка импорта на панели (screens.md §2, В1)', () => {
  const noon = new Date('2026-10-14T16:20:00+03:00');

  it('импорт выключен — одна нейтральная строка', () => {
    expect(importBannerLines({ enabled: false, providers: [anthropic()] }, 'admin', noon)).toEqual([
      {
        severity: 'info',
        text: 'Импорт расхода провайдеров выключен. Расход Anthropic и OpenAI попадает в бюджет только через Decision API.',
        link: false,
      },
    ]);
  });

  it('норма, ключ не задан — по строке на провайдера', () => {
    const lines = importBannerLines(
      { enabled: true, providers: [anthropic(), anthropic({ provider: 'openai', configured: false })] },
      'finance_viewer',
      noon,
    );
    expect(lines).toEqual([
      { severity: 'info', text: 'Anthropic: загружено по 13.10 (сутки UTC), отставание 16 ч.', link: false },
      { severity: 'info', text: 'OpenAI: ключ администратора не задан.', link: false },
    ]);
  });

  it('отставание по SC-008 — warning «Данные за вчера ещё не загружены.»', () => {
    const [line] = importBannerLines(
      { enabled: true, providers: [anthropic({ last_success_day: '2026-10-12', lag_hours: 40 })] },
      'budget_owner',
      noon,
    );
    expect(line).toEqual({
      severity: 'warning',
      text: 'Anthropic: загружено по 12.10 (сутки UTC), отставание 40 ч. Данные за вчера ещё не загружены.',
      link: false,
    });
  });

  it('≥ 2 неудач подряд: А и Ф — «Подробности — в разделе…», ссылка только у А; В — «Администратор получил уведомление.»', () => {
    const status = {
      enabled: true,
      providers: [anthropic({ consecutive_failures: 2, last_success_day: '2026-10-11', last_error_class: null })],
    };
    const admin = importBannerLines(status, 'admin', noon)[0]!;
    expect(admin.severity).toBe('error');
    expect(admin.text).toBe(
      'Anthropic: 2 загрузки подряд не удались. Последняя успешная — 11.10. Подробности — в разделе «Импорт провайдеров».',
    );
    expect(admin.link).toBe(true);
    expect(importBannerLines(status, 'finance_viewer', noon)[0]!.link).toBe(false);
    expect(importBannerLines(status, 'budget_owner', noon)[0]).toEqual({
      severity: 'error',
      text: 'Anthropic: 2 загрузки подряд не удались. Последняя успешная — 11.10. Администратор получил уведомление.',
      link: false,
    });
  });

  it('5 неудач подряд и ни одной успешной — склонение и «успешных не было»', () => {
    const [line] = importBannerLines(
      { enabled: true, providers: [anthropic({ consecutive_failures: 5, last_success_day: null, lag_hours: null })] },
      'admin',
      noon,
    );
    expect(line!.text).toBe(
      'Anthropic: 5 загрузок подряд не удались. Успешных загрузок ещё не было. Подробности — в разделе «Импорт провайдеров».',
    );
  });

  it('загрузок ещё не было, отставание без числа', () => {
    const [line] = importBannerLines(
      { enabled: true, providers: [anthropic({ last_success_day: null, lag_hours: null, last_run_at: null })] },
      'admin',
      new Date('2026-10-14T10:00:00+03:00'),
    );
    expect(line).toEqual({ severity: 'info', text: 'Anthropic: загрузок ещё не было.', link: false });
  });
});

describe('опрос «Запусков» (В7): только пока есть queued или running', () => {
  const run = (status: Schemas['ImportRun']['status']) =>
    ({ id: 'x', provider: 'anthropic', status }) as Schemas['ImportRun'] & { id: string };

  it('есть queued/running — 10 с; нет — опрос остановлен; нет данных — не опрашиваем', () => {
    expect(runsPollInterval({ state: { data: { data: [run('ok'), run('queued')] } } })).toBe(10_000);
    expect(runsPollInterval({ state: { data: { data: [run('running')] } } })).toBe(10_000);
    expect(runsPollInterval({ state: { data: { data: [run('ok'), run('failed')] } } })).toBe(false);
    expect(runsPollInterval({ state: { data: undefined } })).toBe(false);
  });
});
