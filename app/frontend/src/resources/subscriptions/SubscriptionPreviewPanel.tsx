// Панель «Доля бюджета» (13_design.md §6, SubscriptionPreviewPanel; design/screens.md §5.2).
// Состояния: ожидание ввода, пересчёт (прошлые значения приглушены), результат, warn, «займут весь бюджет», fx_warning, ошибка.
import { useEffect, useRef, useState } from 'react';
import { Alert, Box, Table, TableBody, TableCell, TableHead, TableRow, Typography } from '@mui/material';
import { useDataProvider } from 'react-admin';
import { Link } from 'react-router-dom';
import type { Schemas } from '../../api/types';
import type { BmDataProvider } from '../../dataProvider';
import { formatPercent, previewSummary } from '../../budget/subscriptionShare';
import { MoneyText } from '../../components/common';
import { PeriodBudgetMeter } from '../../components/PeriodBudgetMeter';
import { SUBSCRIPTION_PREVIEW_DEBOUNCE_MS } from '../../config/ui';
import { formatMoney } from '../../format/money';
import { ru } from '../../i18n/ru';

type Preview = Schemas['SubscriptionPreview'];
type Body = Schemas['SubscriptionCreate'];
const t = ru.bm.subscriptions;

export type PreviewState =
  | { status: 'idle' }
  | { status: 'loading'; last: Preview | null }
  | { status: 'ready'; preview: Preview }
  | { status: 'error' };

/**
 * previewSubscription через SUBSCRIPTION_PREVIEW_DEBOUNCE_MS после последнего изменения валидного тела.
 * Ответы устаревших запросов отбрасываются: показывается только ответ на последнее состояние формы.
 */
export function useSubscriptionPreview(body: Body | null): PreviewState {
  const dp = useDataProvider<BmDataProvider>();
  const [state, setState] = useState<PreviewState>({ status: 'idle' });
  const seq = useRef(0);
  const key = body ? JSON.stringify(body) : '';

  useEffect(() => {
    const id = ++seq.current;
    if (!key) {
      setState({ status: 'idle' });
      return;
    }
    const timer = setTimeout(async () => {
      setState((prev) => ({
        status: 'loading',
        last: prev.status === 'ready' ? prev.preview : prev.status === 'loading' ? prev.last : null,
      }));
      try {
        const preview = await dp.previewSubscription(JSON.parse(key) as Body);
        if (id === seq.current) setState({ status: 'ready', preview });
      } catch {
        if (id === seq.current) setState({ status: 'error' });
      }
    }, SUBSCRIPTION_PREVIEW_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [dp, key]);

  return state;
}

function FxLink() {
  return <Link to="/fx-rates">{ru.bm.models.fx_link}</Link>;
}

export function SubscriptionPreviewPanel({ state }: { state: PreviewState }) {
  const preview = state.status === 'ready' ? state.preview : state.status === 'loading' ? state.last : null;
  return (
    <Box
      component="section"
      aria-labelledby="bm-sub-preview-title"
      aria-live="polite"
      aria-busy={state.status === 'loading'}
      sx={{
        p: 2,
        border: 1,
        borderColor: 'divider',
        borderRadius: 'var(--bm-radius-md, 6px)',
        bgcolor: 'background.paper',
        alignSelf: 'start',
        minWidth: 0,
      }}
    >
      <Typography id="bm-sub-preview-title" variant="h6" component="h2" sx={{ mb: 1 }}>
        {t.preview_title}
      </Typography>
      {state.status === 'idle' && <Typography color="text.secondary">{t.preview_idle}</Typography>}
      {state.status === 'error' && (
        <Alert severity="warning" role="status">
          {t.preview_error}
        </Alert>
      )}
      {state.status === 'loading' && (
        <Typography variant="body2" color="text.secondary" role="status">
          {t.preview_loading}
        </Typography>
      )}
      {preview && <PreviewBody preview={preview} muted={state.status === 'loading'} />}
    </Box>
  );
}

function PreviewBody({ preview, muted }: { preview: Preview; muted: boolean }) {
  const { taken, warnPeriod } = previewSummary(preview);
  return (
    <Box
      sx={{
        opacity: muted ? 0.5 : 1,
        transition: 'opacity var(--bm-motion-duration-base, 200ms) var(--bm-motion-easing, ease)',
      }}
    >
      {preview.department_id === null && (
        <Alert severity="info" sx={{ mb: 1 }}>
          {t.preview_unallocated}
        </Alert>
      )}
      {preview.periods.length > 0 && (
        <Table size="small" aria-label={t.preview_title}>
          <TableHead>
            <TableRow>
              <TableCell scope="col">{t.preview_period}</TableCell>
              <TableCell scope="col" align="right" title={t.preview_amortization_hint}>
                {t.preview_amortization}
              </TableCell>
              <TableCell scope="col" align="right">
                {t.preview_limit}
              </TableCell>
              <TableCell scope="col" align="right">
                {t.preview_share}
              </TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {preview.periods.map((p) => {
              const name = ru.bm.period_name[p.period];
              return (
                <TableRow key={p.period}>
                  <TableCell scope="row">{name}</TableCell>
                  <TableCell align="right">
                    <MoneyText value={p.amortization_rub} />
                  </TableCell>
                  <TableCell align="right">
                    <MoneyText value={p.limit_rub ?? null} />
                  </TableCell>
                  <TableCell align="right">
                    <Box sx={{ fontVariantNumeric: 'tabular-nums' }}>{formatPercent(p.share_pct)}</Box>
                    <PeriodBudgetMeter
                      variant="compact"
                      limit={p.limit_rub}
                      segments={[{ source: 'subscription', value: p.amortization_rub }]}
                      label={t.meter_label
                        .replace('%{period}', name)
                        .replace('%{limit}', formatMoney(p.limit_rub ?? null))
                        .replace('%{subs}', formatMoney(p.amortization_rub))}
                    />
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      )}
      {taken && (
        <Alert severity="error" sx={{ mt: 1 }}>
          {t.preview_taken}
        </Alert>
      )}
      {warnPeriod && (
        <Alert severity="warning" sx={{ mt: 1 }}>
          {t.preview_warn
            .replace('%{share}', formatPercent(warnPeriod.share_pct))
            .replace('%{period}', ru.bm.period_adj[warnPeriod.period])
            .replace('%{threshold}', formatPercent(String(preview.warn_threshold_pct)))}
        </Alert>
      )}
      {preview.fx_warning === 'last_known_rate_used' && (
        <Alert severity="warning" sx={{ mt: 1 }}>
          {t.preview_fx_last_known} <FxLink />
        </Alert>
      )}
      {preview.fx_warning === 'no_rate' && (
        <Alert severity="error" sx={{ mt: 1 }}>
          {t.preview_fx_no_rate} <FxLink />
        </Alert>
      )}
    </Box>
  );
}
