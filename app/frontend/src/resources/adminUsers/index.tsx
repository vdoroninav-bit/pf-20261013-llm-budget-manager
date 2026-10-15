// Экран 10 «Пользователи админки» (design/screens.md §10). Только администратор.
// Защита последнего активного администратора (13_design_rulings.md, В6): в UI — выключенные поля, на сервере — 409 last_admin.
import {
  BooleanInput,
  Create,
  CreateButton,
  Datagrid,
  Edit,
  FormDataConsumer,
  FunctionField,
  List,
  PasswordInput,
  RadioButtonGroupInput,
  SelectInput,
  SimpleForm,
  TextInput,
  TopToolbar,
  maxLength,
  minLength,
  required,
  useGetList,
  useNotify,
  useRecordContext,
  useRefresh,
} from 'react-admin';
import { Typography } from '@mui/material';
import type { Role, Schemas } from '../../api/types';
import { ru } from '../../i18n/ru';
import { EmptyState, ListError, ListSlowHint, Mono, codeOf, messageOf } from '../../components/common';
import { EditError, SaveOnlyToolbar, recordQueryOptions, useSaveErrorNotifier } from '../../components/records';

type AdminUser = Schemas['AdminUser'];
const t = ru.bm.admin_users;
const ROLES: Role[] = ['admin', 'budget_owner', 'finance_viewer'];
const ROLE_CHOICES = ROLES.map((id) => ({ id, name: `${ru.bm.role[id]} — ${ru.bm.role_hint[id]}` }));
const yesNo = (v: boolean) => (v ? ru.bm.state.yes : ru.bm.state.no);

function useDepartmentNames() {
  const { data } = useGetList<Schemas['Department']>('departments', {
    pagination: { page: 1, perPage: 200 },
    sort: { field: 'name', order: 'ASC' },
  });
  return new Map((data ?? []).map((d) => [d.id, d.name]));
}

export function AdminUserList() {
  const names = useDepartmentNames();
  return (
    <List
      perPage={50}
      actions={
        <TopToolbar>
          <CreateButton label={t.create} />
        </TopToolbar>
      }
      empty={<EmptyState message={t.empty} actions={<CreateButton label={t.create} variant="contained" />} />}
      error={<ListError />}
    >
      <ListSlowHint />
      <Datagrid rowClick="edit" bulkActionButtons={false}>
        <FunctionField<AdminUser> source="login" label={t.login} render={(r) => <Mono>{r.login}</Mono>} />
        <FunctionField<AdminUser> source="role" label={t.role} render={(r) => ru.bm.role[r.role]} />
        <FunctionField<AdminUser>
          label={t.department}
          sortable={false}
          render={(r) => (r.department_id ? (names.get(r.department_id) ?? '…') : '—')}
        />
        <FunctionField<AdminUser> source="active" label={t.active} render={(r) => yesNo(r.active)} />
        <FunctionField<AdminUser>
          source="must_change_password"
          label={t.must_change_password}
          render={(r) => yesNo(r.must_change_password)}
        />
      </Datagrid>
    </List>
  );
}

/** Подразделение — только для владельца бюджета, тогда обязательно. */
function DepartmentForOwner() {
  const names = useDepartmentNames();
  return (
    <FormDataConsumer<{ role?: Role }>>
      {({ formData }) =>
        formData.role === 'budget_owner' ? (
          <SelectInput
            source="department_id"
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

const userBody = (d: Record<string, unknown>) => ({
  ...(d.role === 'budget_owner' ? { department_id: d.department_id } : { department_id: null }),
});

export function AdminUserCreate() {
  const onError = useSaveErrorNotifier({ conflict: t.conflict });
  return (
    <Create
      redirect="list"
      mutationOptions={{ onError }}
      transform={(d: Record<string, unknown>): Schemas['AdminUserCreate'] => {
        const role = d.role as Role;
        return {
          login: String(d.login),
          role,
          ...(role === 'budget_owner' ? { department_id: String(d.department_id) } : {}),
          password: String(d.password),
        };
      }}
    >
      <SimpleForm defaultValues={{ role: 'finance_viewer' }} toolbar={<SaveOnlyToolbar label={t.create} />}>
        <TextInput source="login" label={t.login} helperText={t.login_hint} validate={[required(), maxLength(128)]} />
        <RadioButtonGroupInput source="role" label={t.role} choices={ROLE_CHOICES} row={false} validate={required()} />
        <DepartmentForOwner />
        <PasswordInput
          source="password"
          label={t.password}
          helperText={t.password_hint}
          validate={[required(), minLength(12)]}
          autoComplete="new-password"
        />
      </SimpleForm>
    </Create>
  );
}

/** Единственный активный администратор — по полному списку пользователей (listAdminUsers без пагинации). */
function useIsLastAdmin(user: AdminUser | undefined): boolean {
  const { data } = useGetList<AdminUser & { id: string }>('admin-users', { pagination: { page: 1, perPage: 200 } });
  if (!user || user.role !== 'admin' || !user.active) return false;
  // Пока список не загружен, считаем пользователя последним администратором: поля не должны мигать доступными.
  if (!data) return true;
  return data.filter((u) => u.role === 'admin' && u.active).length <= 1;
}

function AdminUserEditForm() {
  const record = useRecordContext<AdminUser>();
  const lastAdmin = useIsLastAdmin(record);
  return (
    <SimpleForm toolbar={<SaveOnlyToolbar />} warnWhenUnsavedChanges>
      <Typography sx={{ mb: 1 }}>
        {t.login}: <Mono>{record?.login}</Mono>
      </Typography>
      <RadioButtonGroupInput
        source="role"
        label={t.role}
        choices={ROLE_CHOICES}
        row={false}
        disabled={lastAdmin}
        helperText={lastAdmin ? t.last_admin : undefined}
      />
      <DepartmentForOwner />
      <BooleanInput
        source="active"
        label={t.active_input}
        disabled={lastAdmin}
        helperText={lastAdmin ? t.last_admin : t.active_hint}
      />
      <PasswordInput
        source="reset_password"
        label={t.reset_password}
        helperText={t.reset_password_hint}
        validate={minLength(12)}
        autoComplete="new-password"
      />
    </SimpleForm>
  );
}

export function AdminUserEdit() {
  const notify = useNotify();
  const refresh = useRefresh();
  return (
    <Edit
      mutationMode="pessimistic"
      queryOptions={recordQueryOptions}
      mutationOptions={{
        onError: (error: unknown) => {
          notify(messageOf(error), { type: 'error' });
          // 409 last_admin: изменение не применено — перечитываем пользователя (screens.md §10).
          if (codeOf(error) === 'last_admin') refresh();
        },
      }}
      error={<EditError />}
      actions={false}
      transform={(d: Record<string, unknown>) => {
        const { reset_password: reset, ...rest } = d;
        return { ...rest, ...userBody(d), ...(reset ? { reset_password: reset } : {}) };
      }}
    >
      <AdminUserEditForm />
    </Edit>
  );
}

