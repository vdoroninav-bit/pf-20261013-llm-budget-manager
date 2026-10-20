// Время в админке — по МСК (принцип IX; design/screens.md, «Общие правила»): «15.10.2026, 00:00 МСК».
const MSK = 'Europe/Moscow';

const dateTime = new Intl.DateTimeFormat('ru-RU', {
  timeZone: MSK,
  day: '2-digit',
  month: '2-digit',
  year: 'numeric',
  hour: '2-digit',
  minute: '2-digit',
  hourCycle: 'h23',
});

/** ISO date-time → «01.10.2026, 09:00 МСК». */
export function formatDateTimeMsk(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return `${dateTime.format(d)} МСК`;
}

/**
 * Значение <input type="datetime-local"> («2026-11-01T00:00») понимается как время МСК
 * и переводится в date-time контракта с явным смещением: «2026-11-01T00:00:00+03:00».
 * МСК — UTC+3 без перехода на летнее время (с 2014 г.).
 */
export function mskLocalToIso(local: string): string | undefined {
  const m = /^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})(?::(\d{2}))?$/.exec(local.trim());
  if (!m) return undefined;
  return `${m[1]}T${m[2]}:${m[3] ?? '00'}+03:00`;
}

/** Целое с разрядами: 128000 → «128 000». */
export function formatInt(n: number | null | undefined): string {
  if (n === null || n === undefined) return '—';
  return String(n).replace(/\B(?=(\d{3})+(?!\d))/g, ' ');
}

const MONTHS = [
  'январь', 'февраль', 'март', 'апрель', 'май', 'июнь',
  'июль', 'август', 'сентябрь', 'октябрь', 'ноябрь', 'декабрь',
] as const;
const MONTHS_GENITIVE = [
  'января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
  'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря',
] as const;

/** Дата контракта (format: date) «2026-10-01» → «01.10.2026». Без Date: дата не сдвигается часовым поясом. */
export function formatDate(date: string | null | undefined): string {
  if (!date) return '—';
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(date);
  return m ? `${m[3]}.${m[2]}.${m[1]}` : date;
}

const mskDate = new Intl.DateTimeFormat('en-CA', { timeZone: MSK, year: 'numeric', month: '2-digit', day: '2-digit' });

/** Сегодняшняя дата по МСК в формате контракта: «2026-10-14». */
export function todayMsk(now: Date = new Date()): string {
  return mskDate.format(now);
}

/** Текущий месяц по МСК: «2026-10». */
export const currentMonthMsk = (now: Date = new Date()) => todayMsk(now).slice(0, 7);

const monthIndex = (month: string) => Number(month.slice(5, 7)) - 1;

/** «2026-10» → «Октябрь 2026». */
export function formatMonth(month: string): string {
  const lower = monthLower(month);
  return lower.charAt(0).toUpperCase() + lower.slice(1);
}

/** «2026-10» → «октябрь 2026» (для «на октябрь 2026», «за октябрь 2026»). */
export function monthLower(month: string): string {
  const name = MONTHS[monthIndex(month)];
  return name ? `${name} ${month.slice(0, 4)}` : month;
}

/** «2026-09» → «сентября» (для «курс сентября»). */
export function monthGenitive(month: string): string {
  return MONTHS_GENITIVE[monthIndex(month)] ?? month;
}

/** «2026-01» → «2025-12». */
export function previousMonth(month: string): string {
  const y = Number(month.slice(0, 4));
  const m = Number(month.slice(5, 7));
  return m === 1 ? `${y - 1}-12` : `${y}-${String(m - 1).padStart(2, '0')}`;
}

const shortDateTime = new Intl.DateTimeFormat('ru-RU', {
  timeZone: MSK,
  day: '2-digit',
  month: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  hourCycle: 'h23',
});

/** ISO date-time → «01.11, 00:00 МСК» (сброс периода на панели, design/screens.md §2). */
export function formatShortMsk(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return `${shortDateTime.format(d)} МСК`;
}

/** Дата начала периода по МСК в формате контракта (format: date): «2026-10-01T00:00:00+03:00» → «2026-10-01». */
export function mskDateOf(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso.slice(0, 10) : todayMsk(d);
}

/** Сдвиг даты контракта на n суток: «2026-10-14», -6 → «2026-10-08». Без часовых поясов. */
export function addDays(date: string, n: number): string {
  const d = new Date(`${date}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}

/** Последний день месяца: «2026-09» → «2026-09-30». */
export function lastDayOfMonth(month: string): string {
  const y = Number(month.slice(0, 4));
  const m = Number(month.slice(5, 7));
  return new Date(Date.UTC(y, m, 0)).toISOString().slice(0, 10);
}

/** Понедельник недели даты (календарная неделя МСК начинается с понедельника). */
export function mondayOf(date: string): string {
  const day = new Date(`${date}T00:00:00Z`).getUTCDay(); // 0 — воскресенье
  return addDays(date, day === 0 ? -6 : 1 - day);
}
