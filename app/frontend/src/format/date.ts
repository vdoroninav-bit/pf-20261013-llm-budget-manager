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
