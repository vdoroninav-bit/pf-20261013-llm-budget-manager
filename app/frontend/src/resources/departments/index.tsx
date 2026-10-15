// Экран 3 «Подразделения и лимиты» (design/screens.md §3).
import { useEffect } from 'react';
import { Alert, Box, Button, Chip, Stack } from '@mui/material';
import {
  CheckboxGroupInput,
  Create,
  CreateButton,
  Datagrid,
  Edit,
  FormDataConsumer,
  FunctionField,
  List,
  NumberInput,
  SaveButton,
  Show,
  SimpleForm,
  TabbedShowLayout,
  TextField,
  TextInput,
  Toolbar,
  TopToolbar,
  maxLength,
  maxValue,
  minValue,
  required,
  useDataProvider,
  useGetList,
  useListContext,
  useRecordContext,
  useRedirect,
} from 'react-admin';
import { Link } from 'react-router-dom';
import { can } from '../../access/permissions';
import type { Schemas } from '../../api/types';
import type { BmDataProvider } from '../../dataProvider';
import { ru } from '../../i18n/ru';
import { EmptyState, ListError, ListSlowHint, MoneyText, Mono, useRole } from '../../components/common';
import { RadioWithConfirm } from '../../components/inputs';
import { LimitSetFields } from '../../components/LimitSetFields';
import { EditError, Hint, ShowError, recordQueryOptions, useSaveErrorNotifier } from '../../components/records';
import { EmployeesOfDepartment } from '../employees';

type Department = Schemas['Department'] & { id: string };
const t = ru.bm.departments;

const POLICY_CHOICES = (['observe', 'warn', 'enforce'] as const).map((id) => ({ id, name: ru.bm.policy_mode[id] }));
const FAIL_CHOICES = [
  { id: 'open', name: t.fail_open_recommended },
  { id: 'closed', name: ru.bm.fail_mode.closed },
];

function DepartmentsEmpty() {
  const role = useRole();
  if (role !== 'admin') return <EmptyState message={t.empty_other} />;
  return (
    <EmptyState
      message={t.empty_admin}
      actions={
        <>
          <Button component={Link} to="/org-import" variant="contained">
            {t.upload_ad}
          </Button>
          <Button component={Link} to="/departments/create" variant="outlined">
            {t.create}
          </Button>
        </>
      }
    />
  );
}

/** У владельца бюджета в списке одна строка — сразу открывается карточка (screens.md §3.1). */
function OwnerRedirect() {
  const role = useRole();
  const { data, total } = useListContext<Department>();
  const redirect = useRedirect();
  useEffect(() => {
    if (role === 'budget_owner' && total === 1 && data?.[0]) redirect('show', 'departments', data[0].id);
  }, [role, total, data, redirect]);
  return null;
}

const ListActions = () => {
  const role = useRole();
  return (
    <TopToolbar>
      {can(role, 'createDepartment') && <CreateButton label={t.create} />}
    </TopToolbar>
  );
};

const filters = [<TextInput key="q" source="q" label={t.search} alwaysOn resettable />];

export function DepartmentList() {
  const role = useRole();
  return (
    <List
      perPage={50}
      sort={{ field: 'name', order: 'ASC' }}
      filters={filters}
      actions={<ListActions />}
      empty={<DepartmentsEmpty />}
      error={<ListError />}
    >
      <OwnerRedirect />
      <ListSlowHint />
      <Datagrid rowClick={role === 'admin' ? 'edit' : 'show'} bulkActionButtons={false}>
        <TextField source="name" label={t.name} />
        <FunctionField<Department> label={t.ad_group} sortable={false} render={(r) => r.ad_group ?? '—'} />
        <FunctionField<Department>
          label={t.policy_mode}
          sortable={false}
          render={(r) => ru.bm.policy_mode[r.policy_mode]}
        />
        <FunctionField<Department> label={t.fail_mode} sortable={false} render={(r) => ru.bm.fail_mode[r.fail_mode]} />
        <FunctionField<Department> label={t.day} sortable={false} textAlign="right" render={(r) => <MoneyText value={r.limits.day} />} />
        <FunctionField<Department> label={t.week} sortable={false} textAlign="right" render={(r) => <MoneyText value={r.limits.week} />} />
        <FunctionField<Department> label={t.month} sortable={false} textAlign="right" render={(r) => <MoneyText value={r.limits.month} />} />
        <FunctionField<Department> label={t.employees_count} sortable={false} textAlign="right" render={(r) => r.employees_count} />
      </Datagrid>
    </List>
  );
}

/** Разрешённые модели: выбор из каталога (listModels), предупреждение при пустом выборе. */
function AllowedModelsInput() {
  const record = useRecordContext<Department>();
  const { data, isPending } = useGetList<Schemas['Model'] & { id: string }>('models', {
    pagination: { page: 1, perPage: 200 },
    sort: { field: 'id', order: 'ASC' },
  });
  if (isPending) return null;
  if (!data?.length) {
    return (
      <Alert severity="warning" sx={{ mb: 2 }}>
        {t.catalog_empty} <Link to="/models">{t.to_catalog}</Link>
      </Alert>
    );
  }
  return (
    <Box>
      <CheckboxGroupInput
        source="allowed_models"
        label={t.allowed_models}
        // Модели, которых уже нет в каталоге, остаются в выборе, чтобы их можно было снять.
        choices={[...new Set([...data.map((m) => m.id), ...(record?.allowed_models ?? [])])].map((id) => ({ id, name: id }))}
        row={false}
      />
      <FormDataConsumer<{ allowed_models?: string[] }>>
        {({ formData }) =>
          !formData.allowed_models?.length ? (
            <Alert severity="warning" sx={{ mb: 2 }}>
              {t.no_models_allowed}
            </Alert>
          ) : null
        }
      </FormDataConsumer>
    </Box>
  );
}

function SettingsForm() {
  const record = useRecordContext<Department>();
  return (
    <SimpleForm
      warnWhenUnsavedChanges
      toolbar={
        <Toolbar>
          <SaveButton label={t.save} />
        </Toolbar>
      }
    >
      <TextInput source="name" label={t.name} validate={[required(), maxLength(200)]} />
      <Box sx={{ mb: 2 }}>
        <Hint>{t.ad_group}</Hint>
        {record?.ad_group ? <Mono>{record.ad_group}</Mono> : '—'}
      </Box>
      <RadioWithConfirm
        source="policy_mode"
        label={t.policy_mode}
        choices={POLICY_CHOICES}
        helperText={t.policy_hint}
        confirmFor={{ enforce: { title: t.enforce_title, text: t.enforce_text, confirm: t.enforce_confirm } }}
      />
      <RadioWithConfirm
        source="fail_mode"
        label={t.fail_mode}
        choices={FAIL_CHOICES}
        confirmFor={{ closed: { title: t.closed_title, text: t.closed_text, confirm: t.closed_confirm } }}
      />
      <AllowedModelsInput />
      <NumberInput
        source="subs_warn_pct"
        label={t.subs_warn_pct}
        helperText={t.subs_warn_hint}
        validate={[required(), minValue(1), maxValue(100)]}
        min={1}
        max={100}
        step={1}
      />
    </SimpleForm>
  );
}

function DepartmentLimits({ readOnly }: { readOnly?: boolean }) {
  const record = useRecordContext<Department>();
  const dp = useDataProvider<BmDataProvider>();
  if (!record) return null;
  return (
    <LimitSetFields
      title={ru.bm.limits.title}
      value={record.limits}
      readOnly={readOnly}
      onSave={(limits) => dp.putDepartmentLimits(record.id, limits)}
    />
  );
}

function DepartmentTitle() {
  const record = useRecordContext<Department>();
  return <span>{record ? record.name : ''}</span>;
}

function EmployeesTab() {
  const record = useRecordContext<Department>();
  return record ? <EmployeesOfDepartment /> : null;
}

export function DepartmentEdit() {
  const onError = useSaveErrorNotifier();
  return (
    <Edit
      title={<DepartmentTitle />}
      mutationMode="pessimistic"
      redirect={false}
      queryOptions={recordQueryOptions}
      mutationOptions={{ onError }}
      error={<EditError />}
      actions={false}
    >
      <TabbedShowLayout>
        <TabbedShowLayout.Tab label={t.tab_settings}>
          <div>
            <SettingsForm />
            <Box sx={{ px: 2, pb: 2 }}>
              <DepartmentLimits />
            </Box>
          </div>
        </TabbedShowLayout.Tab>
        <TabbedShowLayout.Tab label={t.tab_employees}>
          <div>
            <EmployeesTab />
          </div>
        </TabbedShowLayout.Tab>
      </TabbedShowLayout>
    </Edit>
  );
}

export function DepartmentShow() {
  const role = useRole();
  return (
    <Show title={<DepartmentTitle />} queryOptions={recordQueryOptions} error={<ShowError />} actions={false}>
      <TabbedShowLayout>
        <TabbedShowLayout.Tab label={t.tab_settings}>
          <TextField source="name" label={t.name} />
          <FunctionField<Department> label={t.ad_group} render={(r) => (r.ad_group ? <Mono>{r.ad_group}</Mono> : '—')} />
          <FunctionField<Department> label={t.policy_mode} render={(r) => ru.bm.policy_mode[r.policy_mode]} />
          <FunctionField<Department> label={t.fail_mode} render={(r) => ru.bm.fail_mode[r.fail_mode]} />
          <FunctionField<Department>
            label={t.allowed_models}
            render={(r) =>
              r.allowed_models.length ? (
                <Stack direction="row" spacing={1} useFlexGap sx={{ flexWrap: 'wrap' }}>
                  {r.allowed_models.map((m) => (
                    <Chip key={m} size="small" label={<Mono>{m}</Mono>} />
                  ))}
                </Stack>
              ) : (
                t.no_models_allowed
              )
            }
          />
          <FunctionField<Department> label={t.subs_warn_pct} render={(r) => r.subs_warn_pct} />
          <div>
            <DepartmentLimits readOnly />
          </div>
        </TabbedShowLayout.Tab>
        {can(role, 'listEmployees') && (
          <TabbedShowLayout.Tab label={t.tab_employees}>
            <div>
              <EmployeesTab />
            </div>
          </TabbedShowLayout.Tab>
        )}
      </TabbedShowLayout>
    </Show>
  );
}

export function DepartmentCreate() {
  const onError = useSaveErrorNotifier({ conflict: t.conflict });
  return (
    <Create
      redirect="edit"
      mutationOptions={{ onError }}
      transform={(d: { name: string; ad_group?: string | null }) => ({
        name: d.name,
        ...(d.ad_group ? { ad_group: d.ad_group } : {}),
      })}
    >
      <SimpleForm
        toolbar={
          <Toolbar>
            <SaveButton label={ru.ra.action.create} />
          </Toolbar>
        }
      >
        <Hint>{t.create_hint}</Hint>
        <TextInput source="name" label={t.name} validate={[required(), maxLength(200)]} />
        <TextInput source="ad_group" label={t.ad_group} />
      </SimpleForm>
    </Create>
  );
}
