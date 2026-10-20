// Шкала бюджета периода на панели (13_design.md §6 PeriodBudgetMeter full, §8; design/screens.md §2).
// Деньги — строки Money; доли считаются в целых копейках через BigInt, ширины — в тысячных (принцип VI).
import type { Schemas } from '../api/types';
import { formatMoney } from '../format/money';
import { formatShortMsk } from '../format/date';
import { ru } from '../i18n/ru';
import { compareDecimal, formatPercent, isBudgetTakenBySubscriptions } from './subscriptionShare';

type LimitBalance = Schemas['LimitBalance'];
type PolicyMode = Schemas['PolicyMode'];
export type MeterSource = 'subscription' | 'import' | 'token' | 'reservation';

const kopecks = (money: string): bigint => {
  const neg = money.startsWith('-');
  const [int = '0', frac = ''] = (neg ? money.slice(1) : money).split('.');
  const v = BigInt(int || '0') * 100n + BigInt(frac.padEnd(2, '0').slice(0, 2) || '0');
  return neg ? -v : v;
};

const permille = (value: bigint, scale: bigint): number => {
  if (value <= 0n || scale <= 0n) return 0;
  if (value >= scale) return 1000;
  return Number((value * 1000n) / scale);
};

export interface MeterLayout {
  segments: Array<{ source: MeterSource; permille: number }>;
  /** Положение отметки лимита, тысячные ширины шкалы. */
  limitAt: number;
  /** Отметка 80% лимита (порог FR-029). */
  thresholdAt: number;
  /** Начало перерасхода (штриховка до правого края) или null. */
  overFrom: number | null;
}

/** Шкала в масштабе max(лимит, расход + резервы): перерасход виден как выход за отметку лимита. */
export function meterLayout(b: LimitBalance): MeterLayout {
  const parts: Array<[MeterSource, bigint]> = [
    ['subscription', kopecks(b.spent.subscriptions_rub)],
    ['import', kopecks(b.spent.import_rub)],
    ['token', kopecks(b.spent.token_rub)],
    ['reservation', kopecks(b.reserved_rub)],
  ];
  const total = parts.reduce((s, [, v]) => s + (v > 0n ? v : 0n), 0n);
  const limit = kopecks(b.limit_rub);
  const scale = total > limit ? total : limit;
  return {
    segments: parts.map(([source, v]) => ({ source, permille: permille(v, scale) })),
    limitAt: scale > 0n ? permille(limit, scale) : 1000,
    thresholdAt: scale > 0n ? permille((limit * 8n) / 10n, scale) : 800,
    overFrom: total > limit ? permille(limit, scale) : null,
  };
}

const tm = ru.bm.meter;
const negative = (money: string) => money.startsWith('-');

/** «Месяц: лимит 60 000,00 ₽, подписки …, импорт …, токены …, резервы …, использовано 100,8%, перерасход …, сброс 01.11, 00:00 МСК». */
export function meterLabel(b: LimitBalance): string {
  const src = ru.bm.spend_source;
  const lower = (s: string) => s.split(' ')[0]!.toLowerCase();
  const parts = [
    tm.limit.replace('%{value}', formatMoney(b.limit_rub)),
    `${lower(src.subscription)} ${formatMoney(b.spent.subscriptions_rub)}`,
    `${lower(src.import)} ${formatMoney(b.spent.import_rub)}`,
    `${lower(src.token)} ${formatMoney(b.spent.token_rub)}`,
    `${lower(src.reservation)} ${formatMoney(b.reserved_rub)}`,
    tm.used.replace('%{pct}', formatPercent(b.used_pct)),
    ...(negative(b.remaining_rub) ? [tm.over.replace('%{value}', formatMoney(b.remaining_rub.slice(1)))] : []),
    tm.resets.replace('%{at}', formatShortMsk(b.resets_at)),
  ];
  return `${ru.bm.period_name[b.period]}: ${parts.join(', ')}`;
}

export interface BalanceNote {
  severity: 'info' | 'warning' | 'error';
  text: string;
}

const td = ru.bm.dashboard;

/**
 * Пометки под шкалой (screens.md §2): «занят подписками» по суммам (В3) вместо доли; доля ≥ subs_warn_pct;
 * при перерасходе в режимах «Наблюдать»/«Предупреждать» — что вызовы не блокируются.
 */
export function balanceNotes(b: LimitBalance, subsWarnPct: number | undefined, mode: PolicyMode | undefined): BalanceNote[] {
  const notes: BalanceNote[] = [];
  if (isBudgetTakenBySubscriptions(b.spent.subscriptions_rub, b.limit_rub)) {
    notes.push({ severity: 'error', text: td.subs_taken });
  } else if (
    b.subscriptions_share_pct &&
    subsWarnPct !== undefined &&
    compareDecimal(b.subscriptions_share_pct, '0') > 0 &&
    compareDecimal(b.subscriptions_share_pct, String(subsWarnPct)) >= 0
  ) {
    notes.push({ severity: 'warning', text: td.subs_share.replace('%{pct}', formatPercent(b.subscriptions_share_pct)) });
  }
  if (b.over_limit && (mode === 'observe' || mode === 'warn')) {
    notes.push({ severity: 'info', text: td.observe_over.replace('%{mode}', ru.bm.policy_mode[mode]) });
  }
  return notes;
}
