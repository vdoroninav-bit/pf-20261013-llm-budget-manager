// Вкладки экрана как навигация по маршрутам (nav + aria-current), как SubscriptionTabs (T052).
// Используются экранами 7 (импорт), 8 (отчёты) и 11 (журналы). Одна доступная вкладка — панель не показывается.
import { Box, Link as MuiLink } from '@mui/material';
import { Link, useLocation } from 'react-router-dom';

export interface NavTab {
  to: string;
  label: string;
  show?: boolean;
}

export function NavTabs({ label, tabs }: { label: string; tabs: NavTab[] }) {
  const { pathname } = useLocation();
  const visible = tabs.filter((x) => x.show !== false);
  if (visible.length < 2) return null;
  // Текущая — самая длинная вкладка, с которой начинается путь (/imports и /imports/runs не путаются).
  const current = visible
    .filter((tab) => pathname === tab.to || pathname.startsWith(`${tab.to}/`))
    .sort((a, b) => b.to.length - a.to.length)[0]?.to;
  return (
    <Box
      component="nav"
      aria-label={label}
      sx={{ display: 'flex', gap: 3, px: 2, pt: 2, borderBottom: 1, borderColor: 'divider' }}
    >
      {visible.map((tab) => {
        const active = tab.to === current;
        return (
          <MuiLink
            key={tab.to}
            component={Link}
            to={tab.to}
            aria-current={active ? 'page' : undefined}
            underline="none"
            sx={{
              pb: 1,
              color: active ? 'primary.main' : 'text.secondary',
              fontWeight: active ? 600 : 400,
              borderBottom: 2,
              borderColor: active ? 'primary.main' : 'transparent',
            }}
          >
            {tab.label}
          </MuiLink>
        );
      })}
    </Box>
  );
}
