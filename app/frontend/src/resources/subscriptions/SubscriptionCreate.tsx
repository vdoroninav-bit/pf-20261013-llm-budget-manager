// Экран 5.2 «Ввод подписки» (design/screens.md §5.2; поток C в 13_design.md §5).
// Форма — управляемая, без react-admin SimpleForm: предпросмотру нужно тело запроса на каждое изменение,
// а сохранение и предпросмотр шлют одно и то же тело SubscriptionCreate.
import { useMemo, useState, type FormEvent, type ReactNode } from 'react';
import {
  Alert,
  Box,
  Button,
  FormControl,
  FormControlLabel,
  FormHelperText,
  FormLabel,
  Radio,
  RadioGroup,
  TextField as MuiTextField,
} from '@mui/material';
import { useQuery } from '@tanstack/react-query';
import { Title, useDataProvider, useNotify, useRedirect } from 'react-admin';
import { fetchAllPages } from '../../api/allPages';
import type { Schemas } from '../../api/types';
import { can } from '../../access/permissions';
import type { BmDataProvider } from '../../dataProvider';
import { Forbidden, codeOf, messageOf, statusOf, useRole } from '../../components/common';
import { FxMissingAlert, Hint } from '../../components/records';
import { parsePriceInput } from '../../format/money';
import { ru } from '../../i18n/ru';
import { useDepartmentNames } from '../departmentNames';
import { SubscriptionTabs } from './shared';
import { SubscriptionPreviewPanel, useSubscriptionPreview } from './SubscriptionPreviewPanel';

type Body = Schemas['SubscriptionCreate'];
type AssigneeType = Schemas['SubscriptionAssignee']['type'];
type Currency = Schemas['Currency'];
const t = ru.bm.subscriptions;

interface FormState {
  provider: string;
  plan: string;
  seat_price: string;
  currency: Currency;
  seats: string;
  start_date: string;
  end_date: string;
  type: AssigneeType;
  department: string;
  employee_department: string;
  employee: string;
}

/** Поля формы в порядке экрана: по нему выбирается первое ошибочное поле для фокуса. */
const FIELD_ORDER = ['provider', 'plan', 'seat_price', 'seats', 'start_date', 'end_date', 'ref'] as const;
type Field = (typeof FIELD_ORDER)[number];
type Errors = Partial<Record<Field, string>>;

const INITIAL: FormState = {
  provider: '',
  plan: '',
  seat_price: '',
  currency: 'RUB',
  seats: '',
  start_date: '',
  end_date: '',
  type: 'department',
  department: '',
  employee_department: '',
  employee: '',
};

const PROVIDERS = ['Anthropic', 'OpenAI'];
const DATE = /^\d{4}-\d{2}-\d{2}$/;

/** Проверка формы → ошибки полей и тело SubscriptionCreate (если ошибок нет). Без float: цена — строкой. */
function validateSubscription(f: FormState): { errors: Errors; body: Body | null } {
  const errors: Errors = {};
  const provider = f.provider.trim();
  const plan = f.plan.trim();
  if (!provider) errors.provider = t.err_required;
  else if (provider.length > 64) errors.provider = t.err_length.replace('%{max}', '64');
  if (!plan) errors.plan = t.err_required;
  else if (plan.length > 128) errors.plan = t.err_length.replace('%{max}', '128');
  const price = parsePriceInput(f.seat_price);
  if (!price.ok) errors.seat_price = ru.bm.money.price_error;
  const seats = f.seats.trim();
  if (!/^\d+$/.test(seats) || Number(seats) < 1) errors.seats = t.err_seats;
  if (!DATE.test(f.start_date)) errors.start_date = t.err_required;
  if (f.end_date && DATE.test(f.start_date) && f.end_date < f.start_date) errors.end_date = t.err_end_before_start;
  let ref: string | null = null;
  if (f.type === 'department') {
    if (!f.department) errors.ref = t.err_ref_department;
    ref = f.department;
  } else if (f.type === 'employee') {
    if (!f.employee) errors.ref = t.err_ref_employee;
    ref = f.employee;
  }
  if (Object.keys(errors).length || !price.ok) return { errors, body: null };
  return {
    errors,
    body: {
      provider,
      plan,
      seat_price: price.value,
      currency: f.currency,
      seats: Number(seats),
      start_date: f.start_date,
      end_date: f.end_date || null,
      assignee: { type: f.type, ref },
    },
  };
}

/** Поле ошибки сервера (Problem.errors[].field) → поле формы. */
const SERVER_FIELD: Record<string, Field> = {
  provider: 'provider',
  plan: 'plan',
  seat_price: 'seat_price',
  seats: 'seats',
  start_date: 'start_date',
  end_date: 'end_date',
  assignee: 'ref',
  'assignee.ref': 'ref',
  'assignee.type': 'ref',
};

const fieldId = (f: Field) => `bm-sub-${f}`;

export function SubscriptionCreate() {
  const role = useRole();
  if (role && !can(role, 'createSubscription')) return <Forbidden />;
  return <SubscriptionCreateForm />;
}

function SubscriptionCreateForm() {
  const dp = useDataProvider<BmDataProvider>();
  const notify = useNotify();
  const redirect = useRedirect();
  const names = useDepartmentNames();
  const [form, setForm] = useState<FormState>(INITIAL);
  const [submitted, setSubmitted] = useState(false);
  const [serverErrors, setServerErrors] = useState<Errors>({});
  const [failure, setFailure] = useState<unknown>(null);
  const [saving, setSaving] = useState(false);

  const { errors, body } = useMemo(() => validateSubscription(form), [form]);
  const preview = useSubscriptionPreview(body);
  const shown: Errors = submitted ? { ...errors, ...serverErrors } : serverErrors;

  const set = <K extends keyof FormState>(key: K, value: FormState[K]) => {
    setForm((f) => ({ ...f, [key]: value }));
    setServerErrors({});
  };
  const text = (key: keyof FormState) => (e: { target: { value: string } }) => set(key, e.target.value as never);

  // Все сотрудники подразделения, страницами по 200 до total (3Ф2): иначе № 201 нельзя назначить.
  const { data: employees, isPending: employeesPending } = useQuery({
    queryKey: ['bm', 'listEmployees', 'all', form.employee_department],
    queryFn: ({ signal }) =>
      fetchAllPages<Schemas['Employee'] & { id: string }>(
        dp,
        'employees',
        { department_id: form.employee_department },
        signal,
      ),
    enabled: form.type === 'employee' && !!form.employee_department,
  });
  const activeEmployees = (employees ?? []).filter((e) => e.active);

  const focusFirst = (errs: Errors) => {
    const first = FIELD_ORDER.find((f) => errs[f]);
    if (first) document.getElementById(fieldId(first))?.focus();
  };

  async function submit(e: FormEvent) {
    e.preventDefault();
    setSubmitted(true);
    setFailure(null);
    if (!body) {
      focusFirst(errors);
      return;
    }
    setSaving(true);
    try {
      const { data } = await dp.create('subscriptions', { data: body });
      notify(t.saved, { type: 'success' });
      redirect('show', 'subscriptions', data.id);
    } catch (err) {
      const fieldErrors = (err as { body?: { errors?: Record<string, unknown> } }).body?.errors;
      const mapped: Errors = {};
      for (const [k, v] of Object.entries(fieldErrors ?? {})) {
        const f = SERVER_FIELD[k];
        if (f && typeof v === 'string') mapped[f] = v;
      }
      if (statusOf(err) === 422 && codeOf(err) !== 'fx_rate_missing' && Object.keys(mapped).length) {
        setServerErrors(mapped);
        focusFirst(mapped);
      }
      setFailure(err);
    } finally {
      setSaving(false);
    }
  }

  const hasErrors = Object.keys(shown).length > 0;
  const fieldProps = (f: Field, hint?: string) => ({
    id: fieldId(f),
    error: !!shown[f],
    helperText: shown[f] ?? hint ?? ' ',
    fullWidth: true,
    margin: 'dense' as const,
  });

  return (
    <Box>
      <Title title={t.form_title} />
      <SubscriptionTabs />
      <Box
        sx={{
          display: 'grid',
          gap: 3,
          p: 2,
          gridTemplateColumns: { xs: '1fr', md: 'minmax(0, 720px) minmax(280px, 1fr)' },
        }}
      >
        <Box component="form" noValidate onSubmit={submit} aria-label={t.form_title}>
          <div aria-live="assertive">
            {submitted && hasErrors && (
              <Alert severity="error" sx={{ mb: 2 }}>
                {ru.bm.problem.validation_error}
              </Alert>
            )}
            {failure !== null &&
              (codeOf(failure) === 'fx_rate_missing' ? (
                <FxMissingAlert text={t.fx_missing} />
              ) : Object.keys(serverErrors).length ? null : (
                <Alert severity="error" sx={{ mb: 2 }}>
                  {messageOf(failure)}
                </Alert>
              ))}
          </div>
          <MuiTextField
            {...fieldProps('provider')}
            required
            label={t.f_provider}
            value={form.provider}
            onChange={text('provider')}
            slotProps={{ htmlInput: { list: 'bm-sub-providers', maxLength: 64 } }}
          />
          <datalist id="bm-sub-providers">
            {PROVIDERS.map((p) => (
              <option key={p} value={p} />
            ))}
          </datalist>
          <MuiTextField
            {...fieldProps('plan')}
            required
            label={t.f_plan}
            value={form.plan}
            onChange={text('plan')}
            slotProps={{ htmlInput: { maxLength: 128 } }}
          />
          <Box sx={{ display: 'flex', gap: 2, alignItems: 'flex-start', flexWrap: 'wrap' }}>
            <Box sx={{ flex: '1 1 220px' }}>
              <MuiTextField
                {...fieldProps('seat_price')}
                required
                label={t.f_seat_price}
                value={form.seat_price}
                onChange={text('seat_price')}
                slotProps={{ htmlInput: { inputMode: 'decimal', autoComplete: 'off' } }}
              />
            </Box>
            <Choice
              label={t.f_currency}
              value={form.currency}
              onChange={(v) => set('currency', v as Currency)}
              choices={[
                { id: 'RUB', name: t.rub },
                { id: 'USD', name: t.usd },
              ]}
            />
            <Box sx={{ flex: '0 1 120px' }}>
              <MuiTextField
                {...fieldProps('seats')}
                required
                label={t.f_seats}
                value={form.seats}
                onChange={text('seats')}
                slotProps={{ htmlInput: { inputMode: 'numeric', autoComplete: 'off' } }}
              />
            </Box>
          </Box>
          <Box sx={{ display: 'flex', gap: 2, flexWrap: 'wrap' }}>
            <Box sx={{ flex: '1 1 200px' }}>
              <MuiTextField
                {...fieldProps('start_date')}
                required
                type="date"
                label={t.f_start}
                value={form.start_date}
                onChange={text('start_date')}
                slotProps={{ inputLabel: { shrink: true } }}
              />
            </Box>
            <Box sx={{ flex: '1 1 200px' }}>
              <MuiTextField
                {...fieldProps('end_date', t.f_end_hint)}
                type="date"
                label={t.f_end}
                value={form.end_date}
                onChange={text('end_date')}
                slotProps={{ inputLabel: { shrink: true }, htmlInput: { min: form.start_date || undefined } }}
              />
            </Box>
          </Box>
          <Choice
            label={t.f_assignee}
            value={form.type}
            onChange={(v) => set('type', v as AssigneeType)}
            choices={[
              { id: 'department', name: t.type_department },
              { id: 'employee', name: t.type_employee },
              { id: 'unallocated', name: t.type_unallocated },
            ]}
            hint={form.type === 'employee' ? t.hint_employee : form.type === 'unallocated' ? t.hint_unallocated : undefined}
          />
          {form.type === 'department' && (
            <NativeSelect
              {...fieldProps('ref')}
              label={t.f_department}
              value={form.department}
              onChange={text('department')}
            >
              {[...names].map(([id, name]) => (
                <option key={id} value={id}>
                  {name}
                </option>
              ))}
            </NativeSelect>
          )}
          {form.type === 'employee' && (
            <>
              <NativeSelect
                id="bm-sub-employee-department"
                fullWidth
                margin="dense"
                helperText=" "
                label={t.f_employee_department}
                value={form.employee_department}
                onChange={(e) => {
                  setForm((f) => ({ ...f, employee_department: e.target.value, employee: '' }));
                  setServerErrors({});
                }}
              >
                {[...names].map(([id, name]) => (
                  <option key={id} value={id}>
                    {name}
                  </option>
                ))}
              </NativeSelect>
              {form.employee_department && (
                <NativeSelect
                  {...fieldProps(
                    'ref',
                    !employeesPending && activeEmployees.length === 0 ? t.employees_empty : undefined,
                  )}
                  label={t.f_employee}
                  value={form.employee}
                  onChange={text('employee')}
                  mono
                >
                  {activeEmployees.map((emp) => (
                    <option key={emp.pseudonym} value={emp.pseudonym}>
                      {emp.pseudonym}
                    </option>
                  ))}
                </NativeSelect>
              )}
            </>
          )}
          <Hint>{t.form_note}</Hint>
          <Box sx={{ mt: 2 }}>
            <Button type="submit" variant="contained" disabled={saving}>
              {saving ? ru.bm.state.saving : t.save}
            </Button>
          </Box>
        </Box>
        <SubscriptionPreviewPanel state={preview} />
      </Box>
    </Box>
  );
}

interface ChoiceProps {
  label: string;
  value: string;
  onChange: (value: string) => void;
  choices: Array<{ id: string; name: string }>;
  hint?: string;
}

/** Радиокнопки в fieldset/legend (13_design.md §8, «Формы»). */
function Choice({ label, value, onChange, choices, hint }: ChoiceProps) {
  const id = `bm-sub-choice-${choices.map((c) => c.id).join('-')}`;
  return (
    <FormControl component="fieldset" margin="dense" sx={{ display: 'block' }}>
      <FormLabel component="legend" id={`${id}-label`}>
        {label}
      </FormLabel>
      <RadioGroup
        row
        aria-labelledby={`${id}-label`}
        aria-describedby={hint ? `${id}-hint` : undefined}
        value={value}
        onChange={(e) => onChange(e.target.value)}
      >
        {choices.map((c) => (
          <FormControlLabel key={c.id} value={c.id} control={<Radio />} label={c.name} />
        ))}
      </RadioGroup>
      {hint && <FormHelperText id={`${id}-hint`}>{hint}</FormHelperText>}
    </FormControl>
  );
}

interface NativeSelectProps {
  id: string;
  label: string;
  value: string;
  onChange: (e: { target: { value: string } }) => void;
  children: ReactNode;
  error?: boolean;
  helperText?: string;
  fullWidth?: boolean;
  margin?: 'dense';
  mono?: boolean;
}

/** Нативный select: доступен с клавиатуры и скринридеру без дополнительной разметки. */
function NativeSelect({ children, mono, ...props }: NativeSelectProps) {
  return (
    <MuiTextField
      {...props}
      select
      required
      slotProps={{
        select: { native: true },
        inputLabel: { shrink: true },
        htmlInput: mono ? { lang: 'en', translate: 'no', style: { fontFamily: 'var(--bm-font-mono, monospace)' } } : undefined,
      }}
    >
      <option value="">{t.choose}</option>
      {children}
    </MuiTextField>
  );
}
