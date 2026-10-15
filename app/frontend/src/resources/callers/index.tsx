// Экран 9 «Ключи вызывающих систем» (design/screens.md §9). Только администратор.
import { useState, type MouseEvent } from 'react';
import { Button, Typography } from '@mui/material';
import {
  Create,
  CreateButton,
  Datagrid,
  Edit,
  FormDataConsumer,
  FunctionField,
  List,
  RadioButtonGroupInput,
  SelectInput,
  SimpleForm,
  TextInput,
  TopToolbar,
  maxLength,
  required,
  useDataProvider,
  useGetList,
  useNotify,
  useRecordContext,
  useRedirect,
  useRefresh,
} from 'react-admin';
import type { Schemas } from '../../api/types';
import type { BmDataProvider } from '../../dataProvider';
import { formatDateTimeMsk } from '../../format/date';
import { ru } from '../../i18n/ru';
import { ConfirmDialog, EmptyState, ListError, ListSlowHint, Mono, messageOf } from '../../components/common';
import { OneTimeSecretDialog } from '../../components/OneTimeSecretDialog';
import { EditError, Hint, SaveOnlyToolbar, recordQueryOptions, useSaveErrorNotifier } from '../../components/records';

type Caller = Schemas['Caller'];
type ScopeType = Schemas['SubjectScope']['type'];
const t = ru.bm.callers;

const SCOPE_CHOICES: Array<{ id: ScopeType; name: string }> = [
  { id: 'service', name: t.scope_service },
  { id: 'department_employees', name: t.scope_department_employees },
  { id: 'any_employee', name: t.scope_any_employee },
];

/** Справочник подразделений для подписей (listDepartments, до 200 записей — максимум page_size). */
function useDepartmentNames() {
  const { data } = useGetList<Schemas['Department']>('departments', {
    pagination: { page: 1, perPage: 200 },
    sort: { field: 'name', order: 'ASC' },
  });
  return new Map((data ?? []).map((d) => [d.id, d.name]));
}

function useSources(enabled = true) {
  const { data } = useGetList<Schemas['ProviderSource']>(
    'providerSources',
    { pagination: { page: 1, perPage: 200 } },
    { enabled },
  );
  return data ?? [];
}

const scopeText = (scope: Schemas['SubjectScope'], names: Map<string, string>) => {
  const name = scope.department_id ? (names.get(scope.department_id) ?? '…') : '';
  if (scope.type === 'service') return t.scope_service_of.replace('%{name}', name);
  if (scope.type === 'department_employees') return t.scope_department_employees_of.replace('%{name}', name);
  return t.scope_any_employee;
};

function SourceName({ id }: { id: string | null | undefined }) {
  const sources = useSources(!!id);
  if (!id) return <>—</>;
  const s = sources.find((x) => x.id === id);
  return s ? <Mono>{s.display_name ?? s.ref}</Mono> : <>…</>;
}

function RevokeButton() {
  const record = useRecordContext<Caller>();
  const dp = useDataProvider<BmDataProvider>();
  const notify = useNotify();
  const refresh = useRefresh();
  const [open, setOpen] = useState(false);
  if (!record || record.status !== 'active') return null;
  const stop = (e: MouseEvent) => e.stopPropagation();
  return (
    <span onClick={stop}>
      <Button
        size="small"
        color="error"
        aria-label={t.revoke_label.replace('%{name}', record.name)}
        onClick={() => setOpen(true)}
      >
        {t.revoke}
      </Button>
      <ConfirmDialog
        open={open}
        danger
        title={t.revoke_title}
        text={t.revoke_text.replace('%{name}', record.name)}
        confirm={t.revoke}
        onClose={() => setOpen(false)}
        onConfirm={async () => {
          setOpen(false);
          try {
            await dp.revokeCaller(record.id);
            notify(t.revoked, { type: 'success' });
            refresh();
          } catch (e) {
            notify(messageOf(e), { type: 'error' });
          }
        }}
      />
    </span>
  );
}

const ListActions = () => (
  <TopToolbar>
    <CreateButton label={t.create} />
  </TopToolbar>
);

export function CallerList() {
  const names = useDepartmentNames();
  return (
    <List
      perPage={50}
      actions={<ListActions />}
      empty={
        <EmptyState
          message={t.empty}
          actions={
            <CreateButton label={t.create} variant="contained" />
          }
        />
      }
      error={<ListError />}
    >
      <ListSlowHint />
      <Datagrid rowClick="edit" bulkActionButtons={false}>
        <FunctionField<Caller> source="name" label={t.name} render={(r) => r.name} />
        <FunctionField<Caller> label={t.key_prefix} sortable={false} render={(r) => <Mono>{r.key_prefix}</Mono>} />
        <FunctionField<Caller> label={t.scope} sortable={false} render={(r) => scopeText(r.subject_scope, names)} />
        <FunctionField<Caller> label={t.source} sortable={false} render={(r) => <SourceName id={r.provider_source_id} />} />
        <FunctionField<Caller>
          label={t.status}
          sortable={false}
          render={(r) =>
            r.status === 'active' ? t.active : t.revoked_at.replace('%{at}', formatDateTimeMsk(r.revoked_at))
          }
        />
        <FunctionField<Caller> source="created_at" label={t.created_at} render={(r) => formatDateTimeMsk(r.created_at)} />
        <FunctionField label="" sortable={false} render={() => <RevokeButton />} />
      </Datagrid>
    </List>
  );
}

function DepartmentSelect() {
  const names = useDepartmentNames();
  return (
    <FormDataConsumer<{ subject_scope?: { type?: ScopeType } }>>
      {({ formData }) =>
        formData.subject_scope?.type && formData.subject_scope.type !== 'any_employee' ? (
          <SelectInput
            source="subject_scope.department_id"
            label={t.department}
            choices={[...names].map(([id, name]) => ({ id, name }))}
            validate={(v: unknown) => (v ? undefined : t.department_required)}
            emptyText="—"
          />
        ) : null
      }
    </FormDataConsumer>
  );
}

function SourceSelect() {
  const sources = useSources();
  return (
    <SelectInput
      source="provider_source_id"
      label={t.source_input}
      helperText={t.source_hint}
      choices={sources.map((s) => ({ id: s.id, name: `${s.provider}: ${s.display_name ?? s.ref}` }))}
      emptyText="—"
    />
  );
}

export function CallerCreate() {
  const dp = useDataProvider<BmDataProvider>();
  const notify = useNotify();
  const redirect = useRedirect();
  const [secret, setSecret] = useState<string | null>(null);

  // Создание идёт мимо кэша react-admin: ответ с api_key не должен оседать в кэше запросов (13_design.md §10).
  async function submit(values: Record<string, unknown>) {
    const scope = (values.subject_scope ?? {}) as { type?: ScopeType; department_id?: string | null };
    const body: Schemas['CallerCreate'] = {
      name: String(values.name ?? ''),
      subject_scope: {
        type: scope.type ?? 'service',
        ...(scope.type !== 'any_employee' && scope.department_id ? { department_id: scope.department_id } : {}),
      },
      ...(values.provider_source_id ? { provider_source_id: String(values.provider_source_id) } : {}),
    };
    try {
      const { data } = await dp.create('callers', { data: body });
      setSecret(String((data as { api_key?: string }).api_key ?? ''));
    } catch (e) {
      const errors = (e as { body?: { errors?: Record<string, unknown> } }).body?.errors;
      if (errors) return errors;
      notify(messageOf(e), { type: 'error' });
    }
    return undefined;
  }

  return (
    <Create>
      <SimpleForm
        onSubmit={submit}
        defaultValues={{ subject_scope: { type: 'service' } }}
        toolbar={<SaveOnlyToolbar label={t.create} />}
      >
        <TextInput source="name" label={t.name} validate={[required(), maxLength(200)]} />
        <RadioButtonGroupInput source="subject_scope.type" label={t.scope} choices={SCOPE_CHOICES} row={false} />
        <DepartmentSelect />
        <SourceSelect />
      </SimpleForm>
      {secret !== null && (
        <OneTimeSecretDialog
          title={t.secret_title}
          secret={secret}
          onDone={() => {
            setSecret(null);
            redirect('list', 'callers');
          }}
        />
      )}
    </Create>
  );
}

export function CallerEdit() {
  const onError = useSaveErrorNotifier();
  return (
    <Edit
      mutationMode="pessimistic"
      queryOptions={recordQueryOptions}
      mutationOptions={{ onError }}
      error={<EditError />}
      actions={false}
      transform={(d: Record<string, unknown>) => ({ ...d, provider_source_id: d.provider_source_id || null })}
    >
      <SimpleForm toolbar={<SaveOnlyToolbar />} warnWhenUnsavedChanges>
        <Hint>{t.edit_hint}</Hint>
        <CallerInfo />
        <TextInput source="name" label={t.name} validate={[required(), maxLength(200)]} />
        <SourceSelect />
      </SimpleForm>
    </Edit>
  );
}

function CallerInfo() {
  const record = useRecordContext<Caller>();
  const names = useDepartmentNames();
  if (!record) return null;
  return (
    <Typography sx={{ my: 1 }}>
      <Mono>{record.key_prefix}</Mono> — {scopeText(record.subject_scope, names)}
    </Typography>
  );
}
