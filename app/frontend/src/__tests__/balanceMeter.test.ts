// Шкала бюджета периода, вариант full (13_design.md §6, §8; design/screens.md §2): геометрия без float над деньгами,
// подпись для скринридера, признаки под шкалой (В3 — «занят подписками», доля подписок против порога).
import { describe, expect, it } from 'vitest';
import { balanceNotes, meterLabel, meterLayout } from '../budget/balanceMeter';
import { NNBSP as NN } from '../format/money';
import { limitBalance } from './support/fixtures';

describe('meterLayout: масштаб max(лимит, расход + резервы), ширины в тысячных', () => {
  it('без перерасхода: сегменты по доле лимита, отметка лимита у правого края, 80% — 800', () => {
    const l = meterLayout(
      limitBalance({
        limit_rub: '1000.00',
        spent: { subscriptions_rub: '100.00', import_rub: '200.00', token_rub: '300.00' },
        reserved_rub: '50.00',
        remaining_rub: '350.00',
      }),
    );
    expect(l.segments).toEqual([
      { source: 'subscription', permille: 100 },
      { source: 'import', permille: 200 },
      { source: 'token', permille: 300 },
      { source: 'reservation', permille: 50 },
    ]);
    expect(l.limitAt).toBe(1000);
    expect(l.thresholdAt).toBe(800);
    expect(l.overFrom).toBeNull();
  });

  it('перерасход: масштаб — весь расход с резервами, лимит и 80% сдвигаются влево, хвост за лимитом заштрихован', () => {
    const l = meterLayout(
      limitBalance({
        limit_rub: '1000.00',
        spent: { subscriptions_rub: '500.00', import_rub: '0.00', token_rub: '600.00' },
        reserved_rub: '150.00',
        remaining_rub: '-250.00',
      }),
    );
    // Всего 1250 ₽ → 1000 тысячных; лимит 1000 ₽ → 800; 80% лимита (800 ₽) → 640.
    expect(l.segments.map((s) => s.permille)).toEqual([400, 0, 480, 120]);
    expect(l.limitAt).toBe(800);
    expect(l.thresholdAt).toBe(640);
    expect(l.overFrom).toBe(800);
  });

  it('копейки не теряются через float: 0,01 ₽ из 0,03 ₽ — 333 тысячных', () => {
    const l = meterLayout(
      limitBalance({
        limit_rub: '0.03',
        spent: { subscriptions_rub: '0.00', import_rub: '0.00', token_rub: '0.01' },
        reserved_rub: '0.00',
        remaining_rub: '0.02',
      }),
    );
    expect(l.segments.find((s) => s.source === 'token')!.permille).toBe(333);
  });

  it('лимит 0 и расхода нет — пустая шкала без деления на ноль', () => {
    const l = meterLayout(
      limitBalance({
        limit_rub: '0.00',
        spent: { subscriptions_rub: '0.00', import_rub: '0.00', token_rub: '0.00' },
        reserved_rub: '0.00',
        remaining_rub: '0.00',
      }),
    );
    expect(l.segments.every((s) => s.permille === 0)).toBe(true);
    expect(l.overFrom).toBeNull();
  });
});

describe('meterLabel — подпись шкалы для скринридера (13_design.md §8)', () => {
  it('месяц с перерасходом: лимит, источники, резервы, использовано, перерасход, сброс по МСК', () => {
    expect(meterLabel(limitBalance())).toBe(
      `Месяц: лимит 60${NN}000,00${NN}₽, подписки 18${NN}540,00${NN}₽, импорт 12${NN}007,30${NN}₽, ` +
        `токены 29${NN}950,10${NN}₽, резервы 623,00${NN}₽, использовано 100,8%, перерасход 1${NN}120,40${NN}₽, ` +
        'сброс 01.11, 00:00 МСК',
    );
  });

  it('без перерасхода — без слова «перерасход»', () => {
    expect(meterLabel(limitBalance({ remaining_rub: '120.00', over_limit: false }))).not.toContain('перерасход');
  });
});

describe('balanceNotes — пометки под шкалой', () => {
  it('В3: подписки ≥ лимита → «Бюджет занят подписками…» вместо доли', () => {
    const notes = balanceNotes(
      limitBalance({
        limit_rub: '5000.00',
        spent: { subscriptions_rub: '5000.00', import_rub: '0.00', token_rub: '0.00' },
        subscriptions_share_pct: '100.0',
      }),
      50,
      'enforce',
    );
    expect(notes).toContainEqual({ severity: 'error', text: 'Бюджет занят подписками: платные вызовы получают отказ' });
    expect(notes.some((n) => n.text.startsWith('Подписки занимают'))).toBe(false);
  });

  it('В3: доля 100,0% при сумме на копейку меньше лимита — признака нет, есть доля ≥ порога', () => {
    const notes = balanceNotes(
      limitBalance({
        limit_rub: '5000.00',
        spent: { subscriptions_rub: '4999.99', import_rub: '0.00', token_rub: '0.00' },
        reserved_rub: '0.00',
        remaining_rub: '0.01',
        over_limit: false,
        subscriptions_share_pct: '100.0',
      }),
      50,
      'enforce',
    );
    expect(notes).toEqual([{ severity: 'warning', text: 'Подписки занимают 100,0% бюджета периода' }]);
  });

  it('В3: лимит 0,00 без подписок — признака нет', () => {
    const notes = balanceNotes(
      limitBalance({
        limit_rub: '0.00',
        spent: { subscriptions_rub: '0.00', import_rub: '0.00', token_rub: '0.00' },
        reserved_rub: '0.00',
        remaining_rub: '0.00',
        over_limit: false,
        subscriptions_share_pct: '0.0',
      }),
      50,
      'enforce',
    );
    expect(notes).toEqual([]);
  });

  it('доля подписок ниже порога — пометки нет; ровно порог — есть (сравнение строк без float)', () => {
    const at = (pct: string) =>
      balanceNotes(limitBalance({ remaining_rub: '1.00', over_limit: false, subscriptions_share_pct: pct }), 50, 'enforce');
    expect(at('49.9')).toEqual([]);
    expect(at('50.0')).toEqual([{ severity: 'warning', text: 'Подписки занимают 50,0% бюджета периода' }]);
  });

  it('перерасход в режимах «Наблюдать» и «Предупреждать» — пояснение; в «Ограничивать» — нет', () => {
    const over = limitBalance({ subscriptions_share_pct: '10.0' });
    expect(balanceNotes(over, 50, 'observe')).toEqual([
      { severity: 'info', text: 'Режим «Наблюдать»: вызовы не блокируются, превышения записываются в журнал.' },
    ]);
    expect(balanceNotes(over, 50, 'warn')).toEqual([
      { severity: 'info', text: 'Режим «Предупреждать»: вызовы не блокируются, превышения записываются в журнал.' },
    ]);
    expect(balanceNotes(over, 50, 'enforce')).toEqual([]);
  });
});
