// Экран 5 «Подписки» (design/screens.md §5.1, §5.3): список с фильтрами и карточка с закрытием.
// Ввод с предпросмотром доли (§5.2) — SubscriptionCreate.tsx.
import { useState } from 'react';
import {
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControlLabel,
  Switch,
  TextField as MuiTextField,
} from '@mui/material';
import {
  CreateButton,
  Datagrid,
  FunctionField,
  List,
  Show,
  SimpleShowLayout,
  TopToolbar,
  useDataProvider,
  useNotify,
  useRecordContext,
  useRefresh,
} from 'react-admin';
import { Link } from 'react-router-dom';
import { can } from '../../access/permissions';
import type { Schemas } from '../../api/types';
import type { BmDataProvider } from '../../dataProvider';
import { ConfirmDialog, EmptyState, ListError, ListSlowHint, messageOf, useRole } from '../../components/common';
import { Hint, ShowError, recordQueryOptions } from '../../components/records';
import { formatDate, formatDateTimeMsk, todayMsk } from '../../format/date';
import { formatPrice } from '../../format/money';
import { ru } from '../../i18n/ru';
import { useDepartmentNames } from '../departmentNames';
import { AssigneeText, FxChip, MonthlyTotal, SubscriptionTabs, periodText } from './shared';

export { SubscriptionCreate } from './SubscriptionCreate';

type Subscription = Schemas['Subscription'] & { id: string };
const t = ru.bm.subscriptions;

function SubscriptionsEmpty() {
  const role = useRole();
  if (!can(role, 'createSubscription')) return <EmptyState message={t.empty_other} />;
  return (
    <EmptyState
      message={t.empty_admin}
      actions={
        <Button component={Link} to="/subscriptions/create" variant="contained">
          {t.add}
        </Button>
      }
    />
  );
}

const ListActions = () => {
  const role = useRole();
  return <TopToolbar>{can(role, 'createSubscription') && <CreateButton label={t.add} />}</TopToolbar>;
};

/**
 * Фильтры §5.1: подразделение и «Действует на дату» (по умолчанию сегодня по МСК); «Все, включая закрытые» снимает дату.
 * Фильтры держатся в состоянии экрана и передаются в List как постоянный filter: так переключатель
 * может убрать active_on полностью, а не подставить значение по умолчанию снова.
 */
export function SubscriptionList() {
  const role = useRole();
  const names = useDepartmentNames();
  const [department, setDepartment] = useState('');
  const [activeOn, setActiveOn] = useState(() => todayMsk());
  const [all, setAll] = useState(false);
  const filter = {
    ...(department ? { department_id: department } : {}),
    ...(all || !activeOn ? {} : { active_on: activeOn }),
  };
  return (
    <Box>
      <SubscriptionTabs />
      <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 2, alignItems: 'center', px: 2, pt: 2 }}>
        {role !== 'budget_owner' && (
          <MuiTextField
            select
            size="small"
            label={t.filter_department}
            value={department}
            onChange={(e) => setDepartment(e.target.value)}
            slotProps={{ select: { native: true }, inputLabel: { shrink: true } }}
            sx={{ minWidth: 220 }}
          >
            <option value="">{t.filter_all_departments}</option>
            {[...names].map(([id, name]) => (
              <option key={id} value={id}>
                {name}
              </option>
            ))}
          </MuiTextField>
        )}
        <MuiTextField
          type="date"
          size="small"
          label={t.filter_active_on}
          value={activeOn}
          disabled={all}
          onChange={(e) => setActiveOn(e.target.value)}
          slotProps={{ inputLabel: { shrink: true } }}
        />
        <FormControlLabel control={<Switch checked={all} onChange={(e) => setAll(e.target.checked)} />} label={t.filter_all} />
      </Box>
      <List
        resource="subscriptions"
        filter={filter}
        perPage={50}
        actions={<ListActions />}
        empty={<SubscriptionsEmpty />}
        error={<ListError />}
        title={ru.bm.menu.subscriptions}
      >
        <ListSlowHint />
        <Datagrid rowClick="show" bulkActionButtons={false}>
          <FunctionField<Subscription> label={t.provider} sortable={false} render={(r) => r.provider} />
          <FunctionField<Subscription> label={t.plan} sortable={false} render={(r) => r.plan} />
          <FunctionField<Subscription> label={t.seats} sortable={false} textAlign="right" render={(r) => r.seats} />
          <FunctionField<Subscription>
            label={t.seat_price}
            sortable={false}
            textAlign="right"
            render={(r) => formatPrice(r.seat_price, r.currency)}
          />
          <FunctionField<Subscription>
            label={t.monthly_total}
            sortable={false}
            textAlign="right"
            render={(r) => <MonthlyTotal value={r.monthly_total_rub} />}
          />
          <FunctionField<Subscription>
            label={t.assignee}
            sortable={false}
            render={(r) => <AssigneeText assignee={r.assignee} names={names} />}
          />
          <FunctionField<Subscription> label={t.period} sortable={false} render={(r) => periodText(r)} />
          <FunctionField<Subscription> label={t.fx} sortable={false} render={(r) => <FxChip warning={r.fx_warning} />} />
        </Datagrid>
      </List>
    </Box>
  );
}

const SubscriptionTitle = () => {
  const record = useRecordContext<Subscription>();
  return <span>{record ? t.card_title.replace('%{plan}', record.plan) : ''}</span>;
};

/** Карточка §5.3: все поля только для чтения, у администратора — «Закрыть с даты» и «Закрыть сегодня». */
export function SubscriptionShow() {
  const names = useDepartmentNames();
  return (
    <Box>
      <SubscriptionTabs />
      <Show
        resource="subscriptions"
        title={<SubscriptionTitle />}
        queryOptions={recordQueryOptions}
        error={<ShowError />}
        actions={false}
      >
        <SimpleShowLayout>
          <FunctionField<Subscription> label={t.provider} render={(r) => r.provider} />
          <FunctionField<Subscription> label={t.plan} render={(r) => r.plan} />
          <FunctionField<Subscription> label={t.seats} render={(r) => r.seats} />
          <FunctionField<Subscription> label={t.seat_price} render={(r) => formatPrice(r.seat_price, r.currency)} />
          <FunctionField<Subscription> label={t.monthly_total} render={(r) => <MonthlyTotal value={r.monthly_total_rub} />} />
          <FunctionField<Subscription> label={t.assignee} render={(r) => <AssigneeText assignee={r.assignee} names={names} />} />
          <FunctionField<Subscription> label={t.period} render={(r) => periodText(r)} />
          <FunctionField<Subscription> label={t.fx} render={(r) => (r.fx_warning ? <FxChip warning={r.fx_warning} /> : ru.bm.state.dash)} />
          <FunctionField<Subscription> label={t.created_at} render={(r) => formatDateTimeMsk(r.created_at)} />
          <CloseActions />
        </SimpleShowLayout>
      </Show>
    </Box>
  );
}

function CloseActions() {
  const record = useRecordContext<Subscription>();
  const role = useRole();
  const dp = useDataProvider<BmDataProvider>();
  const notify = useNotify();
  const refresh = useRefresh();
  const [fromDate, setFromDate] = useState(false);
  const [today, setToday] = useState(false);
  if (!record) return null;
  const todayDate = todayMsk();
  // Подписка, закрытая датой в прошлом, уже не списывается — закрывать нечего.
  const closed = !!record.end_date && record.end_date < todayDate;
  if (!can(role, 'endSubscription') || closed) return <Hint>{t.immutable}</Hint>;

  return (
    <Box>
      <Hint>{t.immutable}</Hint>
      <Box sx={{ display: 'flex', gap: 1, mt: 2 }}>
        <Button variant="outlined" onClick={() => setFromDate(true)}>
          {t.close_from}
        </Button>
        {can(role, 'deleteSubscription') && (
          <Button variant="outlined" color="error" onClick={() => setToday(true)}>
            {t.close_today}
          </Button>
        )}
      </Box>
      {fromDate && (
        <CloseFromDateDialog
          record={record}
          onClose={() => setFromDate(false)}
          onSubmit={async (endDate) => {
            await dp.endSubscription(record.id, endDate);
            setFromDate(false);
            notify(t.closed_from, { type: 'success' });
            refresh();
          }}
        />
      )}
      <ConfirmDialog
        open={today}
        danger
        title={t.close_title}
        text={t.close_today_text.replace('%{date}', formatDate(todayDate))}
        confirm={t.close_confirm}
        onClose={() => setToday(false)}
        onConfirm={async () => {
          setToday(false);
          try {
            await dp.delete('subscriptions', { id: record.id, previousData: record });
            notify(t.closed_today, { type: 'success' });
            refresh();
          } catch (e) {
            notify(messageOf(e), { type: 'error' });
          }
        }}
      />
    </Box>
  );
}

interface CloseFromDateDialogProps {
  record: Subscription;
  onClose: () => void;
  onSubmit: (endDate: string) => Promise<void>;
}

function CloseFromDateDialog({ record, onClose, onSubmit }: CloseFromDateDialogProps) {
  const [value, setValue] = useState(() => todayMsk());
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  return (
    <Dialog open onClose={onClose} aria-labelledby="bm-sub-close-title" fullWidth maxWidth="xs">
      <Box
        component="form"
        noValidate
        onSubmit={async (e) => {
          e.preventDefault();
          if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return setError(t.err_required);
          if (value < record.start_date) return setError(t.err_end_before_start);
          setSaving(true);
          setError(null);
          try {
            await onSubmit(value);
          } catch (err) {
            setError(messageOf(err));
          } finally {
            setSaving(false);
          }
        }}
      >
        <DialogTitle id="bm-sub-close-title">{t.close_title}</DialogTitle>
        <DialogContent>
          <MuiTextField
            fullWidth
            margin="dense"
            type="date"
            label={t.close_last_day}
            value={value}
            onChange={(e) => setValue(e.target.value)}
            error={!!error}
            helperText={error ?? ' '}
            slotProps={{ inputLabel: { shrink: true }, htmlInput: { min: record.start_date } }}
          />
        </DialogContent>
        <DialogActions>
          <Button onClick={onClose}>{ru.ra.action.cancel}</Button>
          <Button type="submit" variant="contained" disabled={saving}>
            {saving ? ru.bm.state.saving : t.close_confirm}
          </Button>
        </DialogActions>
      </Box>
    </Dialog>
  );
}
