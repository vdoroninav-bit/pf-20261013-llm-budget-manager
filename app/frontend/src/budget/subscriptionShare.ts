// Доля подписок в бюджете периода — без float (принцип VI).
// Признак «бюджет занят подписками» — по суммам в копейках, не по проценту (13_design_rulings.md, В3).
// Модуль общий для формы подписки (T052) и панели подразделения (T072).
import type { Schemas } from '../api/types';

type Money = string;
type Preview = Schemas['SubscriptionPreview'];
type PeriodKind = Schemas['PeriodKind'];

/** Десятичная строка → [знак, целая часть, дробная часть] без ведущих/хвостовых нулей. */
function parts(value: string): { neg: boolean; int: bigint; frac: string } {
  const neg = value.trim().startsWith('-');
  const [int = '0', frac = ''] = value.trim().replace(/^[-+]/, '').split('.');
  return { neg, int: BigInt(int || '0'), frac: frac.replace(/0+$/, '') };
}

/** Сравнение десятичных строк («62.5», «50», «-1.25») без перевода в число с плавающей точкой: -1, 0, 1. */
export function compareDecimal(a: string, b: string): -1 | 0 | 1 {
  const x = parts(a);
  const y = parts(b);
  const len = Math.max(x.frac.length, y.frac.length);
  const scaled = (p: typeof x) => {
    const v = p.int * 10n ** BigInt(len) + BigInt(p.frac.padEnd(len, '0') || '0');
    return p.neg ? -v : v;
  };
  const vx = scaled(x);
  const vy = scaled(y);
  return vx === vy ? 0 : vx > vy ? 1 : -1;
}

/**
 * В3: бюджет занят подписками, если подписки за период > 0 и ≥ лимита периода. Нет лимита — признака нет.
 * Совпадает с решением API (`budget_blocked_by_subscriptions`, 12_arch_decision.md §4.5 п. 8).
 */
export function isBudgetTakenBySubscriptions(subscriptionsRub: Money, limitRub: Money | null | undefined): boolean {
  if (limitRub === null || limitRub === undefined) return false;
  return compareDecimal(subscriptionsRub, '0') > 0 && compareDecimal(subscriptionsRub, limitRub) >= 0;
}

/** Процент строкой с сервера → «62,5%» (как в текстах design/screens.md). */
export function formatPercent(pct: string | null | undefined): string {
  if (pct === null || pct === undefined) return '—';
  return `${pct.replace('.', ',')}%`;
}

const kopecks = (money: Money): bigint => {
  const p = parts(money);
  const v = p.int * 100n + BigInt(p.frac.padEnd(2, '0').slice(0, 2) || '0');
  return p.neg ? -v : v;
};

/** Доля для ширины шкалы в тысячных (0–1000), целочисленно. Только для рисования, не для решений. */
export function sharePermille(valueRub: Money, limitRub: Money): number {
  const v = kopecks(valueRub);
  const l = kopecks(limitRub);
  if (v <= 0n) return 0;
  if (l <= 0n || v >= l) return 1000;
  return Number((v * 1000n) / l);
}

export interface PreviewSummary {
  /** В3 по суммам: хотя бы в одном периоде подписки займут весь лимит. */
  taken: boolean;
  /** Период для текста о превышении порога: наибольшая доля среди периодов с долей ≥ порога. */
  warnPeriod: { period: PeriodKind; share_pct: string } | null;
}

export function previewSummary(preview: Preview): PreviewSummary {
  const taken = preview.periods.some((p) => isBudgetTakenBySubscriptions(p.amortization_rub, p.limit_rub));
  let warnPeriod: PreviewSummary['warnPeriod'] = null;
  if (preview.warn) {
    const withShare = preview.periods.filter((p): p is typeof p & { share_pct: string } => !!p.share_pct);
    const over = withShare.filter((p) => compareDecimal(p.share_pct, String(preview.warn_threshold_pct)) >= 0);
    // Если сервер поднял warn, а ни один процент не дотянул до порога (округление), берём наибольшую долю.
    const pool = over.length ? over : withShare;
    for (const p of pool) {
      if (!warnPeriod || compareDecimal(p.share_pct, warnPeriod.share_pct) > 0) {
        warnPeriod = { period: p.period, share_pct: p.share_pct };
      }
    }
  }
  return { taken, warnPeriod };
}
