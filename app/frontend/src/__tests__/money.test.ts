import { describe, expect, it } from 'vitest';
import { formatMoney, formatPrice, parseMoneyInput, parsePriceInput, moneyToInput, compareMoney } from '../format/money';

const NNBSP = ' ';

describe('formatMoney (screens.md, общие правила): строка Money → «12 345,67 ₽» без float', () => {
  it('группирует разряды узким неразрывным пробелом и ставит запятую', () => {
    expect(formatMoney('12345.67')).toBe(`12${NNBSP}345,67${NNBSP}₽`);
    expect(formatMoney('5000.00')).toBe(`5${NNBSP}000,00${NNBSP}₽`);
    expect(formatMoney('0.00')).toBe(`0,00${NNBSP}₽`);
    expect(formatMoney('999.10')).toBe(`999,10${NNBSP}₽`);
  });

  it('минус — знак U+2212', () => {
    expect(formatMoney('-1120.40')).toBe(`−1${NNBSP}120,40${NNBSP}₽`);
  });

  it('не теряет копейки на больших суммах, где float уже неточен', () => {
    expect(formatMoney('90071992547409.93')).toBe(
      `90${NNBSP}071${NNBSP}992${NNBSP}547${NNBSP}409,93${NNBSP}₽`,
    );
  });

  it('null — «нет лимита»', () => {
    expect(formatMoney(null)).toBe('нет лимита');
  });
});

describe('parseMoneyInput: ввод → Money "5000.00"', () => {
  it.each([
    ['5000', '5000.00'],
    ['5000,5', '5000.50'],
    ['5 000,50', '5000.50'],
    ['5000.05', '5000.05'],
    [' 0 ', '0.00'],
    ['007', '7.00'],
  ])('%s → %s', (input, money) => {
    expect(parseMoneyInput(input)).toEqual({ ok: true, value: money });
  });

  it('пусто — нет лимита (null)', () => {
    expect(parseMoneyInput('')).toEqual({ ok: true, value: null });
    expect(parseMoneyInput('   ')).toEqual({ ok: true, value: null });
  });

  it.each(['-5', '5,555', 'abc', '5..0', '1e3'])('«%s» — ошибка формата', (input) => {
    expect(parseMoneyInput(input).ok).toBe(false);
  });

  it('moneyToInput показывает Money с запятой, null — пусто', () => {
    expect(moneyToInput('5000.50')).toBe('5000,50');
    expect(moneyToInput(null)).toBe('');
  });
});

describe('цены за 1 000 токенов (PriceDecimal, до 6 знаков)', () => {
  it('нормализует ввод без float', () => {
    expect(parsePriceInput('0,75')).toEqual({ ok: true, value: '0.75' });
    expect(parsePriceInput('1.123456')).toEqual({ ok: true, value: '1.123456' });
    expect(parsePriceInput('2')).toEqual({ ok: true, value: '2' });
    expect(parsePriceInput('1.1234567').ok).toBe(false);
    expect(parsePriceInput('').ok).toBe(false);
  });

  it('formatPrice: валюта рядом', () => {
    expect(formatPrice('0.75', 'RUB')).toBe(`0,75${NNBSP}₽`);
    expect(formatPrice('3.000000', 'USD')).toBe(`3,000000${NNBSP}$`);
  });
});

describe('compareMoney: сравнение в копейках без float', () => {
  it('сравнивает', () => {
    expect(compareMoney('20000.00', '5000.00')).toBe(1);
    expect(compareMoney('5000.00', '5000.00')).toBe(0);
    expect(compareMoney('99.99', '100.00')).toBe(-1);
    expect(compareMoney('90071992547409.93', '90071992547409.92')).toBe(1);
  });
});
