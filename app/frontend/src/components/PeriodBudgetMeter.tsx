// Шкала бюджета периода (13_design.md §6, PeriodBudgetMeter): compact — предпросмотр подписки (T052),
// BalanceMeter — полный вариант панели (T072): источники, резервы, перерасход, отметки 80% и лимита.
// Ширина сегмента считается в тысячных целыми числами (sharePermille), без float над деньгами.
import { Box } from '@mui/material';
import { sharePermille } from '../budget/subscriptionShare';
import { meterLabel, meterLayout, type MeterSource } from '../budget/balanceMeter';
import type { Schemas } from '../api/types';

export interface MeterSegment {
  source: 'subscription';
  value: string;
}

export interface PeriodBudgetMeterProps {
  /** Текст для скринридера, например «Месяц: лимит 12 000,00 ₽, подписки 7 500,00 ₽». */
  label: string;
  /** Лимит периода (Money). Без лимита шкала не рисуется. */
  limit: string | null | undefined;
  segments: MeterSegment[];
  variant?: 'compact';
}

const SOURCE_COLOR: Record<MeterSegment['source'], string> = {
  subscription: 'var(--bm-color-src-subscription)',
};

export function PeriodBudgetMeter({ label, limit, segments }: PeriodBudgetMeterProps) {
  if (limit === null || limit === undefined) return null;
  return (
    <Box
      role="img"
      aria-label={label}
      sx={{
        display: 'flex',
        // Зазор 2 px между сегментами (13_design.md §8, «Не только цвет»); цвет зазора — фон дорожки.
        gap: '2px',
        height: 'var(--bm-size-meter-height-compact, 12px)',
        minWidth: 96,
        borderRadius: 'var(--bm-radius-none, 0)',
        overflow: 'hidden',
        background: 'var(--bm-color-meter-track)',
      }}
    >
      {segments.map((s) => (
        <Box
          key={s.source}
          sx={{
            width: `${sharePermille(s.value, limit) / 10}%`,
            background: SOURCE_COLOR[s.source],
            transition: 'width var(--bm-motion-duration-base, 200ms) var(--bm-motion-easing, ease)',
          }}
        />
      ))}
    </Box>
  );
}

const FULL_COLOR: Record<MeterSource, string> = {
  subscription: 'var(--bm-color-src-subscription)',
  import: 'var(--bm-color-src-import)',
  token: 'var(--bm-color-src-token)',
  // Резерв — штриховка, не только цвет (13_design.md §8, WCAG 1.4.1).
  reservation: 'var(--bm-pattern-reservation-hatch)',
};

/**
 * Полная шкала периода (T072): подписки, импорт, токены, резервы (штриховка), отметка 80% и лимита,
 * перерасход — штриховка за отметкой лимита. Числа рядом выводит панель; шкала дублирует их в aria-label.
 */
export function BalanceMeter({ balance }: { balance: Schemas['LimitBalance'] }) {
  const layout = meterLayout(balance);
  const pct = (permille: number) => `${permille / 10}%`;
  return (
    <Box
      role="img"
      aria-label={meterLabel(balance)}
      sx={{
        position: 'relative',
        height: 'var(--bm-size-meter-height, 20px)',
        minWidth: 160,
        background: 'var(--bm-color-meter-track)',
      }}
    >
      <Box sx={{ display: 'flex', gap: 'var(--bm-border-meter-gap, 2px)', height: '100%' }}>
        {layout.segments
          .filter((s) => s.permille > 0)
          .map((s) => (
            <Box
              key={s.source}
              data-source={s.source}
              sx={{ width: pct(s.permille), background: FULL_COLOR[s.source], flex: '0 0 auto' }}
            />
          ))}
      </Box>
      {layout.overFrom !== null && (
        <Box
          data-over
          sx={{
            position: 'absolute',
            top: 0,
            bottom: 0,
            left: pct(layout.overFrom),
            right: 0,
            background: 'var(--bm-pattern-over-hatch)',
          }}
        />
      )}
      <Box
        data-threshold
        sx={{
          position: 'absolute',
          top: -2,
          bottom: -2,
          left: pct(layout.thresholdAt),
          borderLeft: '2px dashed var(--bm-color-meter-threshold)',
        }}
      />
      <Box
        data-limit
        sx={{ position: 'absolute', top: -4, bottom: -4, left: pct(layout.limitAt), borderLeft: '2px solid var(--bm-color-text)' }}
      />
    </Box>
  );
}
