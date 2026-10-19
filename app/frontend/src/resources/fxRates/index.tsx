// Экран 5.4 «Курсы USD/₽» (design/screens.md §5.4): listFxRates, putFxRate. Маршрут /admin/#/fx-rates.
import { useState, type FormEvent } from 'react';
import { Alert, Box, Button, Dialog, DialogActions, DialogContent, DialogTitle, TextField as MuiTextField } from '@mui/material';
import {
  Datagrid,
  FunctionField,
  List,
  TopToolbar,
  useDataProvider,
  useGetList,
  useListContext,
  useNotify,
  useRefresh,
} from 'react-admin';
import { can } from '../../access/permissions';
import type { Schemas } from '../../api/types';
import type { BmDataProvider } from '../../dataProvider';
import { ConfirmDialog, EmptyState, ListError, ListSlowHint, messageOf, useRole } from '../../components/common';
import {
  currentMonthMsk,
  formatDateTimeMsk,
  formatMonth,
  monthGenitive,
  monthLower,
  todayMsk,
} from '../../format/date';
import { formatRate, parseRateInput } from '../../format/money';
import { ru } from '../../i18n/ru';
import { SubscriptionTabs } from '../subscriptions/shared';

type FxRate = Schemas['FxRate'] & { id: string };
const t = ru.bm.fx_rates;

export function FxRateList() {
  const role = useRole();
  const [entering, setEntering] = useState(false);
  const canPut = can(role, 'putFxRate');
  const addButton = canPut ? (
    <Button variant="contained" onClick={() => setEntering(true)}>
      {t.add}
    </Button>
  ) : undefined;
  return (
    <Box>
      <SubscriptionTabs />
      <List
        resource="fx-rates"
        sort={{ field: 'month', order: 'DESC' }}
        perPage={200}
        pagination={false}
        actions={<TopToolbar>{addButton}</TopToolbar>}
        empty={false}
        error={<ListError />}
        title={ru.bm.subscriptions.tab_fx}
      >
        <ListSlowHint />
        <NoCurrentRateWarning />
        <Datagrid
          bulkActionButtons={false}
          rowClick={false}
          empty={<EmptyState message={t.empty} actions={addButton} />}
        >
          <FunctionField<FxRate> label={t.month} sortable={false} render={(r) => formatMonth(r.month)} />
          <FunctionField<FxRate> label={t.rate} sortable={false} textAlign="right" render={(r) => formatRate(r.usd_rub)} />
          <FunctionField<FxRate> label={t.entered_by} sortable={false} render={(r) => r.entered_by} />
          <FunctionField<FxRate> label={t.entered_at} sortable={false} render={(r) => formatDateTimeMsk(r.entered_at)} />
        </Datagrid>
        {entering && <FxRateDialog onClose={() => setEntering(false)} />}
      </List>
    </Box>
  );
}

/**
 * «Нет курса на октябрь 2026. Используется курс сентября.» — если курса на текущий месяц МСК нет, а действующие
 * подписки в USD есть (признак — из listSubscriptions, отдельного поля в контракте нет; screens.md §5.4, [Гипотеза]).
 */
function NoCurrentRateWarning() {
  const { data } = useListContext<FxRate>();
  const month = currentMonthMsk();
  const hasCurrent = (data ?? []).some((r) => r.month === month);
  const previous = (data ?? [])
    .map((r) => r.month)
    .filter((m) => m < month)
    .sort()
    .at(-1);
  const { data: usd } = useGetList<Schemas['Subscription'] & { id: string }>(
    'subscriptions',
    { filter: { active_on: todayMsk() }, pagination: { page: 1, perPage: 200 } },
    { enabled: !hasCurrent && !!previous },
  );
  if (hasCurrent || !previous || !(usd ?? []).some((s) => s.currency === 'USD')) return null;
  return (
    <Alert severity="warning" sx={{ m: 2 }}>
      {t.no_current.replace('%{month}', monthLower(month)).replace('%{previous}', monthGenitive(previous))}
    </Alert>
  );
}

const MONTH = /^\d{4}-(0[1-9]|1[0-2])$/;

function FxRateDialog({ onClose }: { onClose: () => void }) {
  // Диалог живёт внутри List: курс за месяц, который уже есть, требует подтверждения замены.
  const { data } = useListContext<FxRate>();
  const existing = data ?? [];
  const dp = useDataProvider<BmDataProvider>();
  const notify = useNotify();
  const refresh = useRefresh();
  const [month, setMonth] = useState(() => currentMonthMsk());
  const [rate, setRate] = useState('');
  const [errors, setErrors] = useState<{ month?: string; rate?: string }>({});
  const [failure, setFailure] = useState<unknown>(null);
  const [confirmReplace, setConfirmReplace] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  async function save(value: string) {
    setSaving(true);
    setFailure(null);
    try {
      await dp.putFxRate(month, value);
      notify(t.saved, { type: 'success' });
      refresh();
      onClose();
    } catch (err) {
      setFailure(err);
    } finally {
      setSaving(false);
    }
  }

  function submit(e: FormEvent) {
    e.preventDefault();
    const parsed = parseRateInput(rate);
    const next = {
      ...(MONTH.test(month) ? {} : { month: t.month_error }),
      ...(parsed.ok ? {} : { rate: t.rate_error }),
    };
    setErrors(next);
    if (!parsed.ok || next.month) return;
    if (existing.some((r) => r.month === month)) setConfirmReplace(parsed.value);
    else void save(parsed.value);
  }

  return (
    <>
      <Dialog open onClose={onClose} aria-labelledby="bm-fx-title" fullWidth maxWidth="xs">
        <Box component="form" noValidate onSubmit={submit}>
          <DialogTitle id="bm-fx-title">{t.add}</DialogTitle>
          <DialogContent>
            {failure !== null && (
              <Alert severity="error" role="alert" sx={{ mb: 2 }}>
                {messageOf(failure)}
              </Alert>
            )}
            <MuiTextField
              fullWidth
              required
              margin="dense"
              type="month"
              label={t.f_month}
              value={month}
              onChange={(e) => setMonth(e.target.value)}
              error={!!errors.month}
              helperText={errors.month ?? ' '}
              slotProps={{ inputLabel: { shrink: true } }}
            />
            <MuiTextField
              fullWidth
              required
              margin="dense"
              label={t.f_rate}
              value={rate}
              onChange={(e) => setRate(e.target.value)}
              error={!!errors.rate}
              helperText={errors.rate ?? t.f_rate_hint}
              slotProps={{ htmlInput: { inputMode: 'decimal', autoComplete: 'off' } }}
            />
          </DialogContent>
          <DialogActions>
            <Button onClick={onClose}>{ru.ra.action.cancel}</Button>
            <Button type="submit" variant="contained" disabled={saving}>
              {saving ? ru.bm.state.saving : t.save}
            </Button>
          </DialogActions>
        </Box>
      </Dialog>
      <ConfirmDialog
        open={confirmReplace !== null}
        title={t.replace_title}
        text={t.replace_text.replace('%{month}', MONTH.test(month) ? monthLower(month) : month)}
        confirm={t.replace_confirm}
        onClose={() => setConfirmReplace(null)}
        onConfirm={() => {
          const value = confirmReplace;
          setConfirmReplace(null);
          if (value) void save(value);
        }}
      />
    </>
  );
}
