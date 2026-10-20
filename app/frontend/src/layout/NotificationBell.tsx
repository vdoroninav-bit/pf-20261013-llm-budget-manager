// Колокольчик в шапке (design/screens.md §11; 13_design.md §8 «Живые области»): у А и В — число непрочитанных
// из listNotifications?unread=true&page_size=1 (total), опрос раз в 60 с только при видимой вкладке (В7).
import { Badge, IconButton } from '@mui/material';
import NotificationsIcon from '@mui/icons-material/Notifications';
import { useGetList } from 'react-admin';
import { Link } from 'react-router-dom';
import { can } from '../access/permissions';
import { useRole } from '../components/common';
import { DASHBOARD_REFRESH_MS } from '../config/ui';
import { ru } from '../i18n/ru';

const t = ru.bm.notifications;

export function NotificationBell() {
  const role = useRole();
  if (!can(role, 'listNotifications')) return null;
  return <Bell />;
}

function Bell() {
  const { total } = useGetList(
    'notifications',
    { filter: { unread: true }, pagination: { page: 1, perPage: 1 } },
    { refetchInterval: DASHBOARD_REFRESH_MS, refetchIntervalInBackground: false },
  );
  const n = total ?? 0;
  return (
    <IconButton
      component={Link}
      to="/notifications"
      color="inherit"
      aria-label={n ? t.bell.replace('%{n}', String(n)) : t.bell_none}
    >
      <Badge badgeContent={n} color="error" max={99}>
        <NotificationsIcon />
      </Badge>
    </IconButton>
  );
}
