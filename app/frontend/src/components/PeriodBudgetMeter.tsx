// Шкала бюджета периода (13_design.md §6, PeriodBudgetMeter). В T052 — вариант compact с сегментом подписок
// для предпросмотра; полный вариант (импорт, токены, резервы, перерасход, отметка 80%, легенда) добавит T072.
// Ширина сегмента считается в тысячных целыми числами (sharePermille), без float над деньгами.
import { Box } from '@mui/material';
import { sharePermille } from '../budget/subscriptionShare';

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
