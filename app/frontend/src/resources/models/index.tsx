// Экран 4 «Каталог моделей и история цен» (design/screens.md §4).
import { useCallback, useEffect, useState, type FormEvent } from 'react';
import {
  Alert,
  Box,
  Button,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControlLabel,
  FormLabel,
  Radio,
  RadioGroup,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  TextField as MuiTextField,
} from '@mui/material';
import {
  BooleanInput,
  Create,
  CreateButton,
  Datagrid,
  Edit,
  FunctionField,
  List,
  NumberInput,
  RadioButtonGroupInput,
  SaveButton,
  Show,
  SimpleForm,
  TabbedShowLayout,
  TextField,
  TextInput,
  Toolbar,
  TopToolbar,
  maxLength,
  minValue,
  required,
  useDataProvider,
  useNotify,
  useRecordContext,
  useRefresh,
} from 'react-admin';
import { Link } from 'react-router-dom';
import { can } from '../../access/permissions';
import type { Schemas } from '../../api/types';
import type { BmDataProvider } from '../../dataProvider';
import { formatDateTimeMsk, formatInt, mskLocalToIso } from '../../format/date';
import { formatPrice, parsePriceInput } from '../../format/money';
import { ru } from '../../i18n/ru';
import {
  EmptyState,
  ListError,
  ListSlowHint,
  Mono,
  ProblemAlert,
  codeOf,
  messageOf,
  statusOf,
  useRole,
} from '../../components/common';
import { PriceInput, SwitchWithConfirm } from '../../components/inputs';
import {
  EditError,
  FxMissingAlert,
  Hint,
  SaveOnlyToolbar,
  ShowError,
  recordQueryOptions,
  useSaveErrorNotifier,
} from '../../components/records';

type Model = Schemas['Model'];
type ModelPrice = Schemas['ModelPrice'];
type Currency = Schemas['Currency'];
const t = ru.bm.models;
const yesNo = (v: boolean) => (v ? ru.bm.state.yes : ru.bm.state.no);
const CURRENCY_CHOICES = [
  { id: 'RUB', name: t.rub },
  { id: 'USD', name: t.usd },
];
const KIND_CHOICES = (['chat', 'embedding'] as const).map((id) => ({ id, name: ru.bm.model_kind[id] }));
const PROVIDERS = ['gigachat', 'yandexgpt', 'anthropic', 'openai', 'internal'];

function ModelsEmpty() {
  const role = useRole();
  if (!can(role, 'createModel')) return <EmptyState message={t.empty_other} />;
  return (
    <EmptyState
      message={t.empty_admin}
      actions={
        <Button component={Link} to="/models/create" variant="contained">
          {t.add}
        </Button>
      }
    />
  );
}

const ListActions = () => {
  const role = useRole();
  return <TopToolbar>{can(role, 'createModel') && <CreateButton label={t.add} />}</TopToolbar>;
};

export function ModelList() {
  const role = useRole();
  return (
    <List
      perPage={50}
      sort={{ field: 'id', order: 'ASC' }}
      actions={<ListActions />}
      empty={<ModelsEmpty />}
      error={<ListError />}
    >
      <ListSlowHint />
      <Datagrid rowClick={can(role, 'updateModel') ? 'edit' : 'show'} bulkActionButtons={false}>
        <FunctionField<Model> source="id" label={t.id_short} render={(r) => <Mono>{r.id}</Mono>} />
        <TextField source="provider" label={t.provider} />
        <FunctionField<Model> source="kind" label={t.kind} render={(r) => ru.bm.model_kind[r.kind]} />
        <FunctionField<Model>
          label={t.input}
          sortable={false}
          textAlign="right"
          render={(r) => formatPrice(r.current_price.input_per_1k, r.current_price.currency)}
        />
        <FunctionField<Model>
          label={t.output}
          sortable={false}
          textAlign="right"
          render={(r) => formatPrice(r.current_price.output_per_1k, r.current_price.currency)}
        />
        <FunctionField<Model> source="supports_tools" label={t.tools} render={(r) => yesNo(r.supports_tools)} />
        <FunctionField<Model> source="supports_json" label={t.json} render={(r) => yesNo(r.supports_json)} />
        <FunctionField<Model> source="max_context" label={t.context} textAlign="right" render={(r) => formatInt(r.max_context)} />
        <FunctionField<Model> source="tie_order" label={t.order} textAlign="right" render={(r) => r.tie_order} />
        <FunctionField<Model> source="active" label={t.status} render={(r) => (r.active ? t.on : t.off)} />
      </Datagrid>
    </List>
  );
}

const priceOrUndefined = (v: unknown) => {
  const p = parsePriceInput(String(v ?? ''));
  return p.ok ? p.value : undefined;
};

/** Тело ModelPriceCreate из полей формы (строки ввода → PriceDecimal, МСК → date-time). */
function toPriceCreate(p: Record<string, unknown>): Schemas['ModelPriceCreate'] {
  const cached = p.cached_input_per_1k ? priceOrUndefined(p.cached_input_per_1k) : undefined;
  const validFrom = typeof p.valid_from === 'string' && p.valid_from ? mskLocalToIso(p.valid_from) : undefined;
  return {
    currency: (p.currency as Currency) ?? 'RUB',
    input_per_1k: priceOrUndefined(p.input_per_1k) ?? '',
    output_per_1k: priceOrUndefined(p.output_per_1k) ?? '',
    ...(cached !== undefined ? { cached_input_per_1k: cached } : {}),
    ...(validFrom ? { valid_from: validFrom } : {}),
  };
}

function PriceFields({ prefix }: { prefix: string }) {
  return (
    <>
      <RadioButtonGroupInput source={`${prefix}currency`} label={t.currency} choices={CURRENCY_CHOICES} validate={required()} />
      <PriceInput source={`${prefix}input_per_1k`} label={t.price_input} helperText={t.price_input_hint} />
      <PriceInput source={`${prefix}output_per_1k`} label={t.price_output} />
      <PriceInput source={`${prefix}cached_input_per_1k`} label={t.price_cached} helperText={t.price_cached_hint} required={false} />
      <TextInput
        source={`${prefix}valid_from`}
        label={t.valid_from}
        helperText={t.valid_from_hint}
        type="datetime-local"
        slotProps={{ inputLabel: { shrink: true } }}
      />
    </>
  );
}

const CREATE_DEFAULTS = {
  kind: 'chat',
  supports_tools: false,
  supports_json: false,
  default_input_tokens: 1000,
  default_output_tokens: 500,
  tie_order: 100,
  price: { currency: 'RUB' },
};

export function ModelCreate() {
  const [fxMissing, setFxMissing] = useState(false);
  const notifyError = useSaveErrorNotifier({ conflict: t.conflict });
  const onError = (error: unknown) => {
    if (codeOf(error) === 'fx_rate_missing') setFxMissing(true);
    else notifyError(error);
  };
  return (
    <Create
      redirect="edit"
      mutationOptions={{ onError, onMutate: () => setFxMissing(false) }}
      transform={(d: Record<string, unknown>): Schemas['ModelCreate'] => ({
        id: String(d.id),
        provider: String(d.provider),
        kind: d.kind as Schemas['ModelKind'],
        supports_tools: !!d.supports_tools,
        supports_json: !!d.supports_json,
        max_context: Number(d.max_context),
        default_input_tokens: Number(d.default_input_tokens),
        default_output_tokens: Number(d.default_output_tokens),
        tie_order: Number(d.tie_order),
        price: toPriceCreate((d.price ?? {}) as Record<string, unknown>),
      })}
    >
      <SimpleForm
        defaultValues={CREATE_DEFAULTS}
        toolbar={
          <Toolbar>
            <SaveButton label={t.add} />
          </Toolbar>
        }
      >
        {fxMissing && <FxMissingAlert text={t.fx_missing} />}
        <TextInput source="id" label={t.id} helperText={t.id_hint} validate={[required(), maxLength(200)]} />
        <TextInput
          source="provider"
          label={t.provider}
          validate={[required(), maxLength(64)]}
          slotProps={{ htmlInput: { list: 'bm-providers' } }}
        />
        <datalist id="bm-providers">
          {PROVIDERS.map((p) => (
            <option key={p} value={p} />
          ))}
        </datalist>
        <RadioButtonGroupInput source="kind" label={t.kind} choices={KIND_CHOICES} helperText={t.kind_hint} />
        <CapabilityFields />
        <PriceFields prefix="price." />
      </SimpleForm>
    </Create>
  );
}

function CapabilityFields() {
  return (
    <>
      <BooleanInput source="supports_tools" label={t.supports_tools} />
      <BooleanInput source="supports_json" label={t.supports_json} />
      <NumberInput source="max_context" label={t.max_context} validate={[required(), minValue(1)]} min={1} step={1} />
      <NumberInput
        source="default_input_tokens"
        label={t.default_input_tokens}
        helperText={t.default_tokens_hint}
        validate={[required(), minValue(0)]}
        min={0}
        step={1}
      />
      <NumberInput
        source="default_output_tokens"
        label={t.default_output_tokens}
        helperText={t.default_tokens_hint}
        validate={[required(), minValue(0)]}
        min={0}
        step={1}
      />
      <NumberInput source="tie_order" label={t.tie_order} helperText={t.tie_order_hint} validate={required()} step={1} />
    </>
  );
}

function CurrentPrice() {
  const record = useRecordContext<Model>();
  if (!record) return null;
  const p = record.current_price;
  return (
    <Box sx={{ mb: 2 }}>
      <FormLabel>{t.current_price}</FormLabel>
      <Box>
        {t.price_in}: {formatPrice(p.input_per_1k, p.currency)}; {t.price_out}: {formatPrice(p.output_per_1k, p.currency)}
      </Box>
      <Hint>{t.price_readonly}</Hint>
    </Box>
  );
}

const ModelTitle = () => {
  const record = useRecordContext<Model>();
  return <span>{record ? `${ru.bm.models.id_short} ${record.id}` : ''}</span>;
};

function ModelEditForm() {
  const record = useRecordContext<Model>();
  return (
    <SimpleForm warnWhenUnsavedChanges toolbar={<SaveOnlyToolbar />}>
      <Box sx={{ mb: 2 }}>
        <FormLabel>{t.id}</FormLabel>
        <Box>
          <Mono>{record?.id}</Mono> · {record?.provider} · {record ? ru.bm.model_kind[record.kind] : ''}
        </Box>
      </Box>
      <SwitchWithConfirm
        source="active"
        label={t.active}
        confirmOff={{
          title: t.disable_title.replace('%{id}', record?.id ?? ''),
          text: t.disable_text,
          confirm: t.disable_confirm,
        }}
      />
      <CapabilityFields />
      <CurrentPrice />
    </SimpleForm>
  );
}

export function ModelEdit() {
  const onError = useSaveErrorNotifier();
  return (
    <Edit
      title={<ModelTitle />}
      mutationMode="pessimistic"
      redirect={false}
      queryOptions={recordQueryOptions}
      mutationOptions={{ onError }}
      error={<EditError />}
      actions={false}
    >
      <TabbedShowLayout>
        <TabbedShowLayout.Tab label={t.tab_card}>
          <div>
            <ModelEditForm />
          </div>
        </TabbedShowLayout.Tab>
        <TabbedShowLayout.Tab label={t.tab_prices}>
          <div>
            <PriceHistory />
          </div>
        </TabbedShowLayout.Tab>
      </TabbedShowLayout>
    </Edit>
  );
}

export function ModelShow() {
  return (
    <Show title={<ModelTitle />} queryOptions={recordQueryOptions} error={<ShowError />} actions={false}>
      <TabbedShowLayout>
        <TabbedShowLayout.Tab label={t.tab_card}>
          <FunctionField<Model> label={t.id} render={(r) => <Mono>{r.id}</Mono>} />
          <TextField source="provider" label={t.provider} />
          <FunctionField<Model> label={t.kind} render={(r) => ru.bm.model_kind[r.kind]} />
          <FunctionField<Model> label={t.supports_tools} render={(r) => yesNo(r.supports_tools)} />
          <FunctionField<Model> label={t.supports_json} render={(r) => yesNo(r.supports_json)} />
          <FunctionField<Model> label={t.max_context} render={(r) => formatInt(r.max_context)} />
          <FunctionField<Model> label={t.default_input_tokens} render={(r) => formatInt(r.default_input_tokens)} />
          <FunctionField<Model> label={t.default_output_tokens} render={(r) => formatInt(r.default_output_tokens)} />
          <FunctionField<Model> label={t.tie_order} render={(r) => r.tie_order} />
          <FunctionField<Model> label={t.status} render={(r) => (r.active ? t.on : t.off)} />
          <div>
            <CurrentPrice />
          </div>
        </TabbedShowLayout.Tab>
        <TabbedShowLayout.Tab label={t.tab_prices}>
          <div>
            <PriceHistory />
          </div>
        </TabbedShowLayout.Tab>
      </TabbedShowLayout>
    </Show>
  );
}

/** История цен (screens.md §4.3): listModelPrices по убыванию valid_from; у администратора — «Новая цена». */
function PriceHistory() {
  const record = useRecordContext<Model>();
  const role = useRole();
  const dp = useDataProvider<BmDataProvider>();
  const [prices, setPrices] = useState<ModelPrice[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [adding, setAdding] = useState(false);
  const modelId = record?.id;

  const load = useCallback(async () => {
    if (!modelId) return;
    setError(null);
    try {
      setPrices(await dp.listModelPrices(modelId));
    } catch (e) {
      setError(e);
    }
  }, [dp, modelId]);

  useEffect(() => {
    void load();
  }, [load]);

  if (!record) return null;
  return (
    <Box>
      <Hint>{t.prices_hint}</Hint>
      {can(role, 'createModelPrice') && (
        <Button variant="outlined" sx={{ my: 1 }} onClick={() => setAdding(true)}>
          {t.new_price}
        </Button>
      )}
      {error ? (
        <ProblemAlert error={error} onRetry={() => void load()} />
      ) : prices === null ? null : prices.length === 0 ? (
        <EmptyState message={t.prices_empty} />
      ) : (
        <Table size="small" aria-label={t.tab_prices}>
          <TableHead>
            <TableRow>
              <TableCell scope="col">{t.price_from}</TableCell>
              <TableCell scope="col" align="right">
                {t.price_in}
              </TableCell>
              <TableCell scope="col" align="right">
                {t.price_out}
              </TableCell>
              <TableCell scope="col" align="right">
                {t.price_cache}
              </TableCell>
              <TableCell scope="col">{t.price_currency}</TableCell>
              <TableCell scope="col" />
            </TableRow>
          </TableHead>
          <TableBody>
            {prices.map((p) => (
              <TableRow key={p.id}>
                <TableCell>{formatDateTimeMsk(p.valid_from)}</TableCell>
                <TableCell align="right">{formatPrice(p.input_per_1k, p.currency)}</TableCell>
                <TableCell align="right">{formatPrice(p.output_per_1k, p.currency)}</TableCell>
                <TableCell align="right">{p.cached_input_per_1k ? formatPrice(p.cached_input_per_1k, p.currency) : '—'}</TableCell>
                <TableCell>{p.currency === 'RUB' ? t.rub : t.usd}</TableCell>
                <TableCell>{p.id === record.current_price.id && <Chip size="small" color="success" label={t.price_current} />}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
      {adding && (
        <NewPriceDialog
          modelId={record.id}
          currency={record.current_price.currency}
          onClose={() => setAdding(false)}
          onSaved={() => {
            setAdding(false);
            void load();
          }}
        />
      )}
    </Box>
  );
}

interface NewPriceDialogProps {
  modelId: string;
  currency: Currency;
  onClose: () => void;
  onSaved: () => void;
}

function NewPriceDialog({ modelId, currency: initialCurrency, onClose, onSaved }: NewPriceDialogProps) {
  const dp = useDataProvider<BmDataProvider>();
  const notify = useNotify();
  const refresh = useRefresh();
  const [form, setForm] = useState({ currency: initialCurrency, input: '', output: '', cached: '', validFrom: '' });
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [failure, setFailure] = useState<unknown>(null);
  const [saving, setSaving] = useState(false);
  const set = (k: keyof typeof form) => (e: { target: { value: string } }) => setForm((f) => ({ ...f, [k]: e.target.value }));

  async function submit(e: FormEvent) {
    e.preventDefault();
    const next: Record<string, string> = {};
    if (!parsePriceInput(form.input).ok) next.input = ru.bm.money.price_error;
    if (!parsePriceInput(form.output).ok) next.output = ru.bm.money.price_error;
    if (form.cached && !parsePriceInput(form.cached).ok) next.cached = ru.bm.money.price_error;
    setErrors(next);
    if (Object.keys(next).length) return;
    setSaving(true);
    setFailure(null);
    try {
      await dp.createModelPrice(
        modelId,
        toPriceCreate({
          currency: form.currency,
          input_per_1k: form.input,
          output_per_1k: form.output,
          cached_input_per_1k: form.cached,
          valid_from: form.validFrom,
        }),
      );
      notify(t.price_saved, { type: 'success' });
      refresh();
      onSaved();
    } catch (err) {
      const fieldErrors = (err as { body?: { errors?: Record<string, unknown> } }).body?.errors;
      if (statusOf(err) === 422 && fieldErrors && 'valid_from' in fieldErrors) setErrors({ validFrom: t.price_past });
      else setFailure(err);
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog open onClose={onClose} aria-labelledby="bm-new-price-title" fullWidth maxWidth="sm">
      <Box component="form" noValidate onSubmit={submit}>
        <DialogTitle id="bm-new-price-title">{t.new_price}</DialogTitle>
        <DialogContent>
          {failure !== null &&
            (codeOf(failure) === 'fx_rate_missing' ? (
              <FxMissingAlert text={t.fx_missing} />
            ) : (
              <Alert severity="error" role="alert" sx={{ mb: 2 }}>
                {messageOf(failure)}
              </Alert>
            ))}
          <FormLabel id="bm-new-price-currency">{t.currency}</FormLabel>
          <RadioGroup row aria-labelledby="bm-new-price-currency" value={form.currency} onChange={set('currency')}>
            {CURRENCY_CHOICES.map((c) => (
              <FormControlLabel key={c.id} value={c.id} control={<Radio />} label={c.name} />
            ))}
          </RadioGroup>
          {(
            [
              ['input', t.price_input, t.price_input_hint],
              ['output', t.price_output, undefined],
              ['cached', t.price_cached, t.price_cached_hint],
            ] as const
          ).map(([k, label, hint]) => (
            <MuiTextField
              key={k}
              fullWidth
              margin="dense"
              label={label}
              value={form[k]}
              onChange={set(k)}
              error={!!errors[k]}
              helperText={errors[k] ?? hint ?? ' '}
              slotProps={{ htmlInput: { inputMode: 'decimal', autoComplete: 'off' } }}
            />
          ))}
          <MuiTextField
            fullWidth
            margin="dense"
            type="datetime-local"
            label={t.valid_from}
            value={form.validFrom}
            onChange={set('validFrom')}
            error={!!errors.validFrom}
            helperText={errors.validFrom ?? t.valid_from_hint}
            slotProps={{ inputLabel: { shrink: true } }}
          />
        </DialogContent>
        <DialogActions>
          <Button onClick={onClose}>{ru.ra.action.cancel}</Button>
          <Button type="submit" variant="contained" disabled={saving}>
            {saving ? ru.bm.state.saving : t.save_price}
          </Button>
        </DialogActions>
      </Box>
    </Dialog>
  );
}
