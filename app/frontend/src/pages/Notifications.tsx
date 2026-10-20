// Экран 11 «Журналы → Уведомления» (design/screens.md §11, FR-029, T072): listNotifications, markNotificationRead.
// А — все, В — своего подразделения (фильтрует сервер). Колонка «Внешний канал» скрыта, если у всех disabled.
import { Box, Button, Checkbox, FormControlLabel, Paper, Table, TableBody, TableCell, TableHead, TableRow } from '@mui/material';
import { List, Pagination, useDataProvider, useListContext, useNotify } from 'react-admin';
import { can } from '../access/permissions';
import type { Schemas } from '../api/types';
import type { BmDataProvider } from '../dataProvider';
import { EmptyState, Forbidden, ListError, ListSlowHint, messageOf, useRole } from '../components/common';
import { formatDate, formatDateTimeMsk } from '../format/date';
import { ru } from '../i18n/ru';
import { JournalTabs } from '../resources/events';
import { useDepartmentNames } from '../resources/departmentNames';

type Notification = Schemas['Notification'];
const t = ru.bm.notifications;

export function NotificationsPage() {
  const role = useRole();
  if (role === undefined) return null;
  if (!can(role, 'listNotifications')) return <Forbidden />;
  return (
    <Box>
      <JournalTabs />
      <List
        resource="notifications"
        perPage={50}
        actions={false}
        empty={false}
        error={<ListError />}
        title={t.title}
        disableSyncWithLocation
        pagination={<Pagination rowsPerPageOptions={[50, 100, 200]} />}
      >
        <ListSlowHint />
        <UnreadFilter />
        <NotificationTable />
      </List>
    </Box>
  );
}

function UnreadFilter() {
  const { filterValues, setFilters } = useListContext();
  return (
    <Box sx={{ px: 2, pt: 1 }}>
      <FormControlLabel
        control={
          <Checkbox
            checked={filterValues.unread === true}
            onChange={(e) => setFilters(e.target.checked ? { unread: true } : {}, {}, false)}
          />
        }
        label={t.unread_only}
      />
    </Box>
  );
}

function NotificationTable() {
  const { data, isPending, refetch } = useListContext<Notification & { id: string }>();
  const dp = useDataProvider<BmDataProvider>();
  const notify = useNotify();
  const names = useDepartmentNames();
  if (isPending) return null;
  const rows = data ?? [];
  if (!rows.length) return <EmptyState message={t.empty} />;
  const showWebhook = rows.some((n) => n.webhook_status && n.webhook_status !== 'disabled');
  const markRead = (n: Notification) =>
    dp
      .markNotificationRead(n.id)
      .then(() => {
        notify(t.marked, { type: 'success' });
        void refetch();
      })
      .catch((e: unknown) => notify(messageOf(e), { type: 'error' }));
  return (
    <Paper sx={{ m: 2, overflowX: 'auto' }}>
      <Table size="small" aria-label={t.title}>
        <TableHead>
          <TableRow>
            <TableCell scope="col">{t.col_at}</TableCell>
            <TableCell scope="col">{t.col_kind}</TableCell>
            <TableCell scope="col">{t.col_department}</TableCell>
            <TableCell scope="col">{t.col_period}</TableCell>
            <TableCell scope="col">{t.col_message}</TableCell>
            {showWebhook && <TableCell scope="col">{t.col_webhook}</TableCell>}
            <TableCell scope="col" />
          </TableRow>
        </TableHead>
        <TableBody>
          {rows.map((n) => (
            <TableRow key={n.id} sx={n.read ? undefined : { fontWeight: 600 }}>
              <TableCell>{formatDateTimeMsk(n.created_at)}</TableCell>
              <TableCell>{ru.bm.notification_kind[n.kind]}</TableCell>
              <TableCell>{n.department_id ? (names.get(n.department_id) ?? '—') : '—'}</TableCell>
              <TableCell>
                {n.period
                  ? `${ru.bm.period_name[n.period]}${n.period_start ? ` с ${formatDate(n.period_start)}` : ''}`
                  : n.period_start
                    ? formatDate(n.period_start)
                    : '—'}
              </TableCell>
              <TableCell>{n.message}</TableCell>
              {showWebhook && <TableCell>{n.webhook_status ? ru.bm.webhook_status[n.webhook_status] : '—'}</TableCell>}
              <TableCell>
                {!n.read && (
                  <Button size="small" onClick={() => void markRead(n)}>
                    {t.read}
                  </Button>
                )}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </Paper>
  );
}
