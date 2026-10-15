// Экран 3.5 «Сотрудники» (design/screens.md §3.5): сотрудники под псевдонимами, личные лимиты, раскрытие псевдонима.
import { useState } from 'react';
import { Alert, Box, Button, Typography } from '@mui/material';
import {
  Datagrid,
  FunctionField,
  Pagination,
  ReferenceField,
  ReferenceManyField,
  Show,
  SimpleShowLayout,
  TextField,
  useDataProvider,
  useRecordContext,
} from 'react-admin';
import { Link } from 'react-router-dom';
import { can } from '../../access/permissions';
import type { Schemas } from '../../api/types';
import type { BmDataProvider } from '../../dataProvider';
import { ru } from '../../i18n/ru';
import { ConfirmDialog, EmptyState, MoneyText, Mono, messageOf, useRole } from '../../components/common';
import { LimitSetFields } from '../../components/LimitSetFields';
import { Hint, ShowError, recordQueryOptions } from '../../components/records';

type Employee = Schemas['Employee'] & { id: string };
const t = ru.bm.employees;

function EmployeesEmpty() {
  const role = useRole();
  return (
    <EmptyState
      message={t.empty}
      actions={
        can(role, 'importOrg') ? (
          <Button component={Link} to="/org-import" variant="outlined">
            {ru.bm.departments.upload_ad}
          </Button>
        ) : undefined
      }
    />
  );
}

/** Вкладка «Сотрудники» карточки подразделения: listEmployees?department_id=<id записи-подразделения>. */
export function EmployeesOfDepartment() {
  return (
    <Box>
      <Hint>{t.caption}</Hint>
      <ReferenceManyField
        reference="employees"
        target="department_id"
        perPage={50}
        pagination={<Pagination rowsPerPageOptions={[50, 100, 200]} />}
        empty={<EmployeesEmpty />}
      >
        <Datagrid rowClick={(id) => `/employees/${encodeURIComponent(String(id))}/show`} bulkActionButtons={false}>
          <FunctionField<Employee> label={t.pseudonym} render={(r) => <Mono>{r.pseudonym}</Mono>} />
          <FunctionField<Employee> label={t.active} render={(r) => (r.active ? ru.bm.state.yes : t.removed)} />
          <FunctionField<Employee> label={t.limit_day} textAlign="right" render={(r) => <MoneyText value={r.limits.day} />} />
          <FunctionField<Employee> label={t.limit_week} textAlign="right" render={(r) => <MoneyText value={r.limits.week} />} />
          <FunctionField<Employee> label={t.limit_month} textAlign="right" render={(r) => <MoneyText value={r.limits.month} />} />
        </Datagrid>
      </ReferenceManyField>
    </Box>
  );
}

/** PseudonymTag (13_design.md §6): у администратора — «Кто это?» с записью в аудит. Значение не кэшируется. */
function PseudonymTag({ pseudonym }: { pseudonym: string }) {
  const role = useRole();
  const dp = useDataProvider<BmDataProvider>();
  const [asking, setAsking] = useState(false);
  const [identity, setIdentity] = useState<Schemas['EmployeeIdentity'] | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function reveal() {
    setAsking(false);
    setError(null);
    try {
      setIdentity(await dp.revealPseudonym(pseudonym));
    } catch (e) {
      setError(messageOf(e));
    }
  }

  return (
    <Box>
      <Mono>{pseudonym}</Mono>
      {can(role, 'revealPseudonym') && !identity && (
        <Button size="small" sx={{ ml: 1 }} onClick={() => setAsking(true)}>
          {t.reveal}
        </Button>
      )}
      {identity && (
        <Box component="dl" aria-live="polite" sx={{ display: 'grid', gridTemplateColumns: 'max-content auto', columnGap: 2, m: 0, mt: 1 }}>
          <Typography component="dt" color="text.secondary">
            {t.revealed_login}
          </Typography>
          <Typography component="dd" sx={{ m: 0 }}>
            <Mono>{identity.login}</Mono>
          </Typography>
          <Typography component="dt" color="text.secondary">
            {t.revealed_name}
          </Typography>
          <Typography component="dd" sx={{ m: 0 }}>
            {identity.display_name ?? '—'}
          </Typography>
        </Box>
      )}
      {error && (
        <Alert severity="error" role="alert" sx={{ mt: 1 }}>
          {error}
        </Alert>
      )}
      <ConfirmDialog
        open={asking}
        title={t.reveal_title}
        text={t.reveal_text.replace('%{pseudonym}', pseudonym)}
        confirm={t.reveal_confirm}
        onClose={() => setAsking(false)}
        onConfirm={() => void reveal()}
      />
    </Box>
  );
}

function EmployeeLimits() {
  const record = useRecordContext<Employee>();
  const role = useRole();
  const dp = useDataProvider<BmDataProvider>();
  if (!record) return null;
  return (
    <Box>
      <LimitSetFields
        title={ru.bm.limits.employee_title}
        value={record.limits}
        readOnly={!can(role, 'putEmployeeLimits')}
        onSave={(limits) => dp.putEmployeeLimits(record.pseudonym, limits)}
      />
      <Box sx={{ mt: 2 }}>
        <Hint>{t.limits_hint}</Hint>
        <Hint>{t.balance_hint}</Hint>
      </Box>
    </Box>
  );
}

const EmployeeTitle = () => {
  const record = useRecordContext<Employee>();
  return <span>{record ? `${ru.bm.employees.pseudonym} ${record.pseudonym}` : ''}</span>;
};

/** Карточка сотрудника: getEmployee; администратор меняет личные лимиты, владелец бюджета — только читает. */
export function EmployeeCard() {
  return (
    <Show title={<EmployeeTitle />} queryOptions={recordQueryOptions} error={<ShowError />} actions={false}>
      <SimpleShowLayout>
        <FunctionField<Employee> label={t.pseudonym} render={(r) => <PseudonymTag pseudonym={r.pseudonym} />} />
        <ReferenceField source="department_id" reference="departments" label={t.department} link={false} emptyText="—">
          <TextField source="name" />
        </ReferenceField>
        <FunctionField<Employee> label={t.active} render={(r) => (r.active ? ru.bm.state.yes : t.removed)} />
        <div>
          <EmployeeLimits />
        </div>
      </SimpleShowLayout>
    </Show>
  );
}
