// Деньги и цены — только строки, без float (принцип VI; design/screens.md, «Общие правила»).
// Money: '^-?[0-9]+\.[0-9]{2}$'; PriceDecimal: '^[0-9]+(\.[0-9]{1,6})?$' (12_openapi.yaml).

/** Узкий неразрывный пробел — разделитель разрядов и перед знаком валюты. */
export const NNBSP = ' ';
const MINUS = '−';

export type Currency = 'RUB' | 'USD';
const SYMBOL: Record<Currency, string> = { RUB: '₽', USD: '$' };

const groupDigits = (int: string) => int.replace(/\B(?=(\d{3})+(?!\d))/g, NNBSP);

function formatDecimal(value: string): string {
  const negative = value.startsWith('-');
  const [int = '0', frac] = (negative ? value.slice(1) : value).split('.');
  const body = groupDigits(int.replace(/^0+(?=\d)/, '')) + (frac !== undefined ? `,${frac}` : '');
  return (negative ? MINUS : '') + body;
}

/** "12345.67" → «12 345,67 ₽»; null → «нет лимита». */
export function formatMoney(value: string | null | undefined): string {
  if (value === null || value === undefined) return 'нет лимита';
  return `${formatDecimal(value)}${NNBSP}₽`;
}

/** Цена за 1 000 токенов в валюте модели: «0,75 ₽», «3,00 $». */
export function formatPrice(value: string | null | undefined, currency: Currency): string {
  if (value === null || value === undefined) return '—';
  return `${formatDecimal(value)}${NNBSP}${SYMBOL[currency]}`;
}

export type Parsed<T> = { ok: true; value: T } | { ok: false };

const normalize = (input: string) => input.replace(/[\s  ]/g, '').replace(',', '.');

/** Ввод суммы → Money. Пусто — null («нет лимита»). Запятая и точка допустимы, до 2 знаков. */
export function parseMoneyInput(input: string | null | undefined): Parsed<string | null> {
  const s = normalize(input ?? '');
  if (s === '') return { ok: true, value: null };
  const m = /^(\d+)(?:\.(\d{1,2}))?$/.exec(s);
  if (!m) return { ok: false };
  const int = m[1]!.replace(/^0+(?=\d)/, '');
  return { ok: true, value: `${int}.${(m[2] ?? '').padEnd(2, '0')}` };
}

/** Money → текст поля ввода: «5000,50»; null → ''. */
export function moneyToInput(value: string | null | undefined): string {
  return value === null || value === undefined ? '' : value.replace('.', ',');
}

/** Ввод цены → PriceDecimal (до 6 знаков). Пусто — ошибка: цена обязательна. */
export function parsePriceInput(input: string | null | undefined): Parsed<string> {
  const s = normalize(input ?? '');
  const m = /^(\d+)(?:\.(\d{1,6}))?$/.exec(s);
  if (!m) return { ok: false };
  const int = m[1]!.replace(/^0+(?=\d)/, '');
  return { ok: true, value: m[2] !== undefined ? `${int}.${m[2]}` : int };
}

/** PriceDecimal → текст поля ввода. */
export function priceToInput(value: string | null | undefined): string {
  return value === null || value === undefined ? '' : value.replace('.', ',');
}

const toKopecks = (money: string): bigint => {
  const negative = money.startsWith('-');
  const [int = '0', frac = '00'] = (negative ? money.slice(1) : money).split('.');
  const k = BigInt(int) * 100n + BigInt(frac.padEnd(2, '0').slice(0, 2));
  return negative ? -k : k;
};

/** Сравнение двух Money в целых копейках: -1, 0, 1. */
export function compareMoney(a: string, b: string): -1 | 0 | 1 {
  const x = toKopecks(a);
  const y = toKopecks(b);
  return x === y ? 0 : x > y ? 1 : -1;
}

/** Ввод курса USD/₽ → RateDecimal ('^[0-9]+(\.[0-9]{1,4})?$'). */
export function parseRateInput(input: string | null | undefined): Parsed<string> {
  const s = normalize(input ?? '');
  const m = /^(\d+)(?:\.(\d{1,4}))?$/.exec(s);
  if (!m) return { ok: false };
  const int = m[1]!.replace(/^0+(?=\d)/, '');
  return { ok: true, value: m[2] !== undefined ? `${int}.${m[2]}` : int };
}

/** RateDecimal → «92,5000 ₽ за 1 $» (всегда 4 знака, без float). */
export function formatRate(rate: string): string {
  const [int = '0', frac = ''] = rate.split('.');
  return `${formatDecimal(`${int}.${frac.padEnd(4, '0')}`)}${NNBSP}₽ за 1${NNBSP}$`;
}
