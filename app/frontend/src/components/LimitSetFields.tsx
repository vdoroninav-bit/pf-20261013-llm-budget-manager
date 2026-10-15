// LimitSetFields (13_design.md §6): три поля MoneyInput — сутки, неделя, месяц. Пусто — нет лимита (null).
// Отправляется LimitSet со всеми тремя полями (putDepartmentLimits / putEmployeeLimits — PUT целиком).
import { useEffect, useId, useState, type FormEvent } from 'react';
import { Alert, Box, Button, Stack, TextField, Typography } from '@mui/material';
import { useNotify } from 'react-admin';
import type { Schemas } from '../api/types';
import { compareMoney, moneyToInput, parseMoneyInput } from '../format/money';
import { ru } from '../i18n/ru';
import { MoneyText, messageOf } from './common';

type LimitSet = Schemas['LimitSet'];
type Period = keyof LimitSet;
const PERIODS: Period[] = ['day', 'week', 'month'];
const t = ru.bm.limits;

export interface LimitSetFieldsProps {
  title: string;
  value: LimitSet;
  /** Нет права менять: показываются значения, без полей ввода. */
  readOnly?: boolean;
  onSave?: (limits: LimitSet) => Promise<LimitSet>;
}

const toInputs = (v: LimitSet) => ({ day: moneyToInput(v.day), week: moneyToInput(v.week), month: moneyToInput(v.month) });

/** Мягкие предупреждения о порядке лимитов (screens.md §3.3). */
export function limitOrderWarnings(v: Partial<Record<Period, string | null>>): string[] {
  const out: string[] = [];
  if (v.day && v.week && compareMoney(v.day, v.week) > 0) out.push(t.day_over_week);
  if (v.week && v.month && compareMoney(v.week, v.month) > 0) out.push(t.week_over_month);
  return out;
}

export function LimitSetFields({ title, value, readOnly, onSave }: LimitSetFieldsProps) {
  const notify = useNotify();
  const hintId = useId();
  const [inputs, setInputs] = useState(() => toInputs(value));
  const [errors, setErrors] = useState<Partial<Record<Period, string>>>({});
  const [saving, setSaving] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);

  useEffect(() => setInputs(toInputs(value)), [value]);

  if (readOnly || !onSave) {
    return (
      <Box component="section" aria-label={title} sx={{ mt: 2 }}>
        <Typography component="h3" variant="subtitle1" sx={{ fontWeight: 600 }}>
          {title}
        </Typography>
        <Box component="dl" sx={{ display: 'grid', gridTemplateColumns: 'max-content auto', columnGap: 2, rowGap: 0.5 }}>
          {PERIODS.map((p) => (
            <Box key={p} sx={{ display: 'contents' }}>
              <Typography component="dt" color="text.secondary">
                {t[p]}
              </Typography>
              <Typography component="dd" sx={{ m: 0 }}>
                <MoneyText value={value[p]} />
              </Typography>
            </Box>
          ))}
        </Box>
        <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
          {t.hint}
        </Typography>
      </Box>
    );
  }

  const parsed = Object.fromEntries(PERIODS.map((p) => [p, parseMoneyInput(inputs[p])])) as Record<
    Period,
    ReturnType<typeof parseMoneyInput>
  >;
  const warnings = limitOrderWarnings(
    Object.fromEntries(PERIODS.map((p) => [p, parsed[p].ok ? parsed[p].value : null])) as Record<Period, string | null>,
  );

  async function submit(e: FormEvent) {
    e.preventDefault();
    const nextErrors: Partial<Record<Period, string>> = {};
    for (const p of PERIODS) if (!parsed[p].ok) nextErrors[p] = ru.bm.money.format_error;
    setErrors(nextErrors);
    if (Object.keys(nextErrors).length) {
      document.getElementById(`${hintId}-${PERIODS.find((p) => nextErrors[p])}`)?.focus();
      return;
    }
    const body = Object.fromEntries(PERIODS.map((p) => [p, parsed[p].ok ? parsed[p].value : null])) as LimitSet;
    setSaving(true);
    setFailure(null);
    try {
      const saved = await onSave!(body);
      setInputs(toInputs(saved));
      notify(t.saved, { type: 'success' });
    } catch (error) {
      setFailure(messageOf(error));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Box component="form" noValidate onSubmit={submit} aria-label={title} sx={{ mt: 2 }}>
      <Typography component="h3" variant="subtitle1" sx={{ fontWeight: 600 }}>
        {title}
      </Typography>
      <Typography id={hintId} variant="body2" color="text.secondary" sx={{ mb: 1 }}>
        {t.hint}
      </Typography>
      <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2}>
        {PERIODS.map((p) => (
          <TextField
            key={p}
            id={`${hintId}-${p}`}
            label={t[p]}
            value={inputs[p]}
            onChange={(e) => setInputs((s) => ({ ...s, [p]: e.target.value }))}
            error={!!errors[p]}
            helperText={errors[p] ?? ' '}
            disabled={saving}
            slotProps={{
              input: { endAdornment: <span aria-hidden>₽</span> },
              htmlInput: { inputMode: 'decimal', autoComplete: 'off' },
            }}
            size="small"
          />
        ))}
      </Stack>
      {warnings.map((w) => (
        <Alert key={w} severity="warning" sx={{ mt: 1 }}>
          {w}
        </Alert>
      ))}
      {failure && (
        <Alert severity="error" role="alert" sx={{ mt: 1 }}>
          {failure}
        </Alert>
      )}
      <Button type="submit" variant="contained" disabled={saving} sx={{ mt: 1 }}>
        {saving ? ru.bm.state.saving : t.save}
      </Button>
    </Box>
  );
}
