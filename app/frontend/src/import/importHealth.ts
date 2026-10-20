// Состояние импорта провайдеров: правило отставания SC-008, тексты классов ошибок, строки плашки на панели.
// Источники: design/screens.md §2 «Плашка импорта», §7.1; 13_design.md §7.4; 13_design_rulings.md В1, В5, В7.
// Одно правило для панели (экран 2) и экрана 7 — других порогов отставания нет.
import type { Role, Schemas } from '../api/types';
import { IMPORT_RUNS_POLL_MS } from '../config/ui';
import { ru } from '../i18n/ru';

type ImportStatus = Schemas['ImportStatus'];
type ProviderStatus = ImportStatus['providers'][number];
type Provider = Schemas['Provider'];

const t = ru.bm.import_health;

const MSK_OFFSET_MS = 3 * 60 * 60 * 1000; // МСК — UTC+3 без перехода на летнее время.
const NOON_MS = 12 * 60 * 60 * 1000;
const DAY_MS = 24 * 60 * 60 * 1000;

export const providerLabel = (p: Provider | string): string =>
  (ru.bm.provider as Record<string, string>)[p] ?? p;

/** Вчерашние сутки UTC относительно момента now: «2026-10-13». */
export function yesterdayUtc(now: Date = new Date()): string {
  return new Date(now.getTime() - DAY_MS).toISOString().slice(0, 10);
}

/**
 * SC-008: если сейчас позже 12:00 МСК, а last_success_day раньше вчерашних суток UTC — данные за вчера не загружены.
 * Загрузок не было ни разу (null) — после полудня это тоже отставание.
 */
export function isImportLagging(lastSuccessDay: string | null | undefined, now: Date = new Date()): boolean {
  const sinceMskMidnight = (now.getTime() + MSK_OFFSET_MS) % DAY_MS;
  if (sinceMskMidnight <= NOON_MS) return false;
  if (!lastSuccessDay) return true;
  // Даты ISO сравниваются строкой.
  return lastSuccessDay < yesterdayUtc(now);
}

const ERROR_CLASSES = ['auth_rejected', 'provider_unavailable', 'bad_response', 'internal'] as const;

/** Текст класса ошибки по 13_design.md §7.4; значение вне перечня (сервер новее клиента) — «Загрузка не удалась». */
export function errorClassText(errorClass: string): string {
  return (ERROR_CLASSES as readonly string[]).includes(errorClass)
    ? t.error[errorClass as (typeof ERROR_CLASSES)[number]]
    : t.error_unknown;
}

/** «13.10» из «2026-10-13». */
const dayMonth = (date: string) => `${date.slice(8, 10)}.${date.slice(5, 7)}`;

/** 2 загрузки, 5 загрузок, 21 загрузка. */
export function failuresText(n: number): string {
  const mod10 = n % 10;
  const mod100 = n % 100;
  const form =
    mod10 === 1 && mod100 !== 11 ? 'one' : mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14) ? 'few' : 'many';
  return t.failures[form].replace('%{n}', String(n));
}

export interface BannerLine {
  severity: 'info' | 'warning' | 'error';
  text: string;
  /** Ссылка на экран 7 — только у администратора (В1). */
  link: boolean;
}

/** «Anthropic: загружено по 13.10 (сутки UTC), отставание 16 ч.» */
function loadedText(p: ProviderStatus): string {
  if (!p.last_success_day) return t.never_loaded;
  const lag = p.lag_hours === null || p.lag_hours === undefined ? '' : t.lag.replace('%{h}', String(p.lag_hours));
  return t.loaded.replace('%{day}', dayMonth(p.last_success_day)) + lag + '.';
}

/** Строки плашки импорта на панели (screens.md §2). Для budget_owner — без ссылки и с «Администратор получил уведомление». */
export function importBannerLines(status: ImportStatus, role: Role | undefined, now: Date = new Date()): BannerLine[] {
  if (!status.enabled) return [{ severity: 'info', text: t.disabled, link: false }];
  return status.providers.map((p) => {
    const name = providerLabel(p.provider);
    if (!p.configured) return { severity: 'info', text: `${name}: ${t.key_missing}`, link: false };
    if (p.consecutive_failures >= 2) {
      const last = p.last_success_day ? t.last_success.replace('%{day}', dayMonth(p.last_success_day)) : t.no_success;
      const tail = role === 'budget_owner' ? t.admin_notified : t.see_imports;
      return {
        severity: 'error',
        text: `${name}: ${failuresText(p.consecutive_failures)} ${last} ${tail}`,
        link: role === 'admin',
      };
    }
    const lagging = isImportLagging(p.last_success_day, now);
    const text = `${name}: ${loadedText(p)}`;
    return lagging
      ? { severity: 'warning', text: `${text} ${t.yesterday_missing}`, link: false }
      : { severity: 'info', text, link: false };
  });
}

/**
 * refetchInterval для списка «Запусков» (В7): 10 с, пока есть queued или running; затем опрос останавливается.
 * Аргумент — запрос react-query со списком react-admin ({ data: ImportRun[] }).
 */
export function runsPollInterval(query: { state: { data?: { data?: Array<{ status: string }> } } }): number | false {
  const rows = query.state.data?.data ?? [];
  return rows.some((r) => r.status === 'queued' || r.status === 'running') ? IMPORT_RUNS_POLL_MS : false;
}
