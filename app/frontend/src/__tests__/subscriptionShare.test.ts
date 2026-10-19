// Доля подписок без float (принцип VI) и признак «бюджет занят подписками» (13_design_rulings.md, В3).
import { describe, expect, it } from 'vitest';
import {
  compareDecimal,
  formatPercent,
  isBudgetTakenBySubscriptions,
  previewSummary,
  sharePermille,
} from '../budget/subscriptionShare';
import { formatDate, formatMonth, monthGenitive, monthLower, previousMonth, todayMsk } from '../format/date';
import { formatRate, parseRateInput } from '../format/money';

const NN = '\u202f';

describe('compareDecimal: сравнение десятичных строк без float', () => {
  it.each([
    ['62.5', '50', 1],
    ['50.0', '50', 0],
    ['49.99', '50', -1],
    ['100.0', '100', 0],
    ['99.9', '100', -1],
    ['0.10', '0.1', 0],
    ['-1.5', '1', -1],
    ['-1.5', '-1.25', -1],
    ['90071992547409.93', '90071992547409.92', 1],
  ] as const)('%s ? %s → %i', (a, b, r) => {
    expect(compareDecimal(a, b)).toBe(r);
  });
});

describe('isBudgetTakenBySubscriptions (В3): подписки > 0 и ≥ лимита, в копейках', () => {
  it('подписки равны лимиту — бюджет занят', () => {
    expect(isBudgetTakenBySubscriptions('60000.00', '60000.00')).toBe(true);
  });
  it('подписки больше лимита — занят', () => {
    expect(isBudgetTakenBySubscriptions('60000.01', '60000.00')).toBe(true);
  });
  it('на копейку меньше лимита — не занят, хотя процент округлится до 100.0', () => {
    expect(isBudgetTakenBySubscriptions('59999.99', '60000.00')).toBe(false);
  });
  it('лимит 0.00 без подписок — признака нет', () => {
    expect(isBudgetTakenBySubscriptions('0.00', '0.00')).toBe(false);
  });
  it('лимит 0.00 и подписки есть — занят', () => {
    expect(isBudgetTakenBySubscriptions('0.01', '0.00')).toBe(true);
  });
  it('нет лимита — признака нет', () => {
    expect(isBudgetTakenBySubscriptions('100.00', null)).toBe(false);
  });
});

describe('formatPercent и шкала', () => {
  it('«62.5» → «62,5%» без float (как в текстах screens.md)', () => {
    expect(formatPercent('62.5')).toBe('62,5%');
    expect(formatPercent('100.0')).toBe('100,0%');
    expect(formatPercent(null)).toBe('—');
  });
  it('sharePermille: доля в тысячных, целочисленно, не больше 1000', () => {
    expect(sharePermille('18540.00', '60000.00')).toBe(309);
    expect(sharePermille('70000.00', '60000.00')).toBe(1000);
    expect(sharePermille('1.00', '0.00')).toBe(1000);
    expect(sharePermille('0.00', '0.00')).toBe(0);
  });
});

describe('previewSummary: что показать по ответу previewSubscription', () => {
  const base = { department_id: 'd', warn_threshold_pct: 50, warn: false, fx_warning: null };
  it('warn: период с наибольшей долей не ниже порога', () => {
    const s = previewSummary({
      ...base,
      warn: true,
      periods: [
        { period: 'day', limit_rub: '5000.00', amortization_rub: '2000.00', share_pct: '40.0' },
        { period: 'month', limit_rub: '60000.00', amortization_rub: '37500.00', share_pct: '62.5' },
      ],
    });
    expect(s.warnPeriod).toEqual({ period: 'month', share_pct: '62.5' });
    expect(s.taken).toBe(false);
  });
  it('taken — по суммам: 99,99 % (share_pct «100.0») ещё не занят', () => {
    const s = previewSummary({
      ...base,
      periods: [{ period: 'month', limit_rub: '60000.00', amortization_rub: '59994.00', share_pct: '100.0' }],
    });
    expect(s.taken).toBe(false);
  });
  it('taken — хотя бы один период, где подписки ≥ лимита', () => {
    const s = previewSummary({
      ...base,
      periods: [
        { period: 'day', limit_rub: '100.00', amortization_rub: '120.00', share_pct: '120.0' },
        { period: 'month', limit_rub: null, amortization_rub: '3600.00', share_pct: null },
      ],
    });
    expect(s.taken).toBe(true);
  });
});

describe('даты и месяцы по МСК', () => {
  it('formatDate: 2026-10-01 → 01.10.2026', () => {
    expect(formatDate('2026-10-01')).toBe('01.10.2026');
    expect(formatDate(null)).toBe('—');
  });
  it('todayMsk: 23:30 UTC 13 октября — уже 14 октября по МСК', () => {
    expect(todayMsk(new Date('2026-10-13T21:30:00Z'))).toBe('2026-10-14');
    expect(todayMsk(new Date('2026-10-13T20:59:00Z'))).toBe('2026-10-13');
  });
  it('месяцы: «Октябрь 2026», «октябрь 2026», «сентября»', () => {
    expect(formatMonth('2026-10')).toBe('Октябрь 2026');
    expect(monthLower('2026-10')).toBe('октябрь 2026');
    expect(monthGenitive('2026-09')).toBe('сентября');
    expect(previousMonth('2026-01')).toBe('2025-12');
  });
});

describe('курс USD/₽: RateDecimal до 4 знаков', () => {
  it.each([
    ['92,5', '92.5'],
    ['92.5000', '92.5000'],
    [' 101 ', '101'],
  ])('%s → %s', (input, out) => {
    expect(parseRateInput(input)).toEqual({ ok: true, value: out });
  });
  it.each(['', 'abc', '92,12345', '-1'])('«%s» — ошибка', (input) => {
    expect(parseRateInput(input).ok).toBe(false);
  });
  it('formatRate: «92,5000 ₽ за 1 $»', () => {
    expect(formatRate('92.5')).toBe(`92,5000${NN}₽ за 1${NN}$`);
    expect(formatRate('101')).toBe(`101,0000${NN}₽ за 1${NN}$`);
  });
});
