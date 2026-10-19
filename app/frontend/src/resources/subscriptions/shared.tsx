// Общее для экрана 5 (design/screens.md §5): вкладки «Подписки» / «Курсы USD/₽» и подписи значений подписки.
import { Box, Chip, Link as MuiLink } from '@mui/material';
import { Link, useLocation } from 'react-router-dom';
import { can } from '../../access/permissions';
import type { Schemas } from '../../api/types';
import { Mono, MoneyText, useRole } from '../../components/common';
import { formatDate } from '../../format/date';
import { ru } from '../../i18n/ru';

type Subscription = Schemas['Subscription'];
const t = ru.bm.subscriptions;

/** Вкладки экрана 5. «Курсы» — только ролям с listFxRates (у владельца бюджета их нет). */
export function SubscriptionTabs() {
  const role = useRole();
  const { pathname } = useLocation();
  const tabs = [
    { to: '/subscriptions', label: t.tab_subscriptions, show: true },
    { to: '/fx-rates', label: t.tab_fx, show: can(role, 'listFxRates') },
  ].filter((x) => x.show);
  if (tabs.length < 2) return null;
  return (
    <Box
      component="nav"
      aria-label={t.tabs}
      sx={{ display: 'flex', gap: 3, px: 2, pt: 2, borderBottom: 1, borderColor: 'divider' }}
    >
      {tabs.map((tab) => {
        const current = pathname === tab.to || pathname.startsWith(`${tab.to}/`);
        return (
          <MuiLink
            key={tab.to}
            component={Link}
            to={tab.to}
            aria-current={current ? 'page' : undefined}
            underline="none"
            sx={{
              pb: 1,
              color: current ? 'primary.main' : 'text.secondary',
              fontWeight: current ? 600 : 400,
              borderBottom: 2,
              borderColor: current ? 'primary.main' : 'transparent',
            }}
          >
            {tab.label}
          </MuiLink>
        );
      })}
    </Box>
  );
}

/** «Подразделение «Разработка»», «Сотрудник emp-…», «Не распределено». */
export function AssigneeText({ assignee, names }: { assignee: Subscription['assignee']; names: Map<string, string> }) {
  if (assignee.type === 'unallocated') return <>{t.assignee_unallocated}</>;
  if (assignee.type === 'employee') {
    return (
      <>
        {t.assignee_employee} <Mono>{assignee.ref}</Mono>
      </>
    );
  }
  const name = (assignee.ref && names.get(assignee.ref)) ?? null;
  return name ? <>{t.assignee_department.replace('%{name}', name)}</> : <Mono>{assignee.ref}</Mono>;
}

/** «01.10.2026 – 31.12.2026» или «с 01.10.2026, бессрочно». */
export function periodText(s: Pick<Subscription, 'start_date' | 'end_date'>): string {
  return s.end_date
    ? t.period_range.replace('%{from}', formatDate(s.start_date)).replace('%{to}', formatDate(s.end_date))
    : t.period_open.replace('%{from}', formatDate(s.start_date));
}

/** Чип курса по fx_warning (13_design.md §7.2): прошлый месяц — warning, нет курса — danger. */
export function FxChip({ warning }: { warning: Subscription['fx_warning'] | undefined }) {
  if (warning === 'last_known_rate_used') return <Chip size="small" color="warning" label={t.fx_last_known} />;
  if (warning === 'no_rate') return <Chip size="small" color="error" label={t.fx_no_rate} />;
  return null;
}

/** «В месяц, ₽»: сумма или чип «Нет курса», если пересчитать нечем. */
export function MonthlyTotal({ value }: { value: string | null | undefined }) {
  if (value === null || value === undefined) return <Chip size="small" color="error" label={t.fx_no_rate} />;
  return <MoneyText value={value} />;
}
