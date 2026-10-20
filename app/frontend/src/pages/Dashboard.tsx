// Экран 2 «Панель подразделения» (design/screens.md §2, T072). Маршруты /admin/#/ и /admin/#/dashboard/:department_id.
// Операции: listDepartments (выбор у А и Ф), getDepartment, getDepartmentBalance, listDepartmentBalanceItems,
// getSpendReport (group_by=model,source), getImportStatus (плашка; у В — сокращённый ответ, В1),
// listNotifications/markNotificationRead (лента у А и В). Автообновление раз в 60 с при видимой вкладке (В7).
import { useState, type ReactNode } from 'react';
import {
  Alert,
  Box,
  Button,
  Chip,
  Link as MuiLink,
  Paper,
  Skeleton,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  TextField as MuiTextField,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from '@mui/material';
import { useQuery } from '@tanstack/react-query';
import { Title, useDataProvider, useGetIdentity, useGetList, useGetOne, useRefresh } from 'react-admin';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { can } from '../access/permissions';
import type { Role, Schemas } from '../api/types';
import type { BmIdentity } from '../authProvider';
import type { BmDataProvider } from '../dataProvider';
import { balanceNotes } from '../budget/balanceMeter';
import { EmptyState, Forbidden, Mono, MoneyText, SlowHint, messageOf, statusOf, useRole } from '../components/common';
import { BalanceMeter } from '../components/PeriodBudgetMeter';
import { DASHBOARD_REFRESH_MS } from '../config/ui';
import { formatDate, formatDateTimeMsk, formatShortMsk, mondayOf, mskDateOf, todayMsk } from '../format/date';
import { formatMoney, formatPrice } from '../format/money';
import { ru } from '../i18n/ru';
import { importBannerLines } from '../import/importHealth';
import { PseudonymTag } from '../resources/employees';
import { IMPORT_STATUS_KEY } from './ImportStatus';

type LimitBalance = Schemas['LimitBalance'];
type PeriodKind = Schemas['PeriodKind'];
type SpendSource = Schemas['SpendSource'];
type Department = Schemas['Department'];

const t = ru.bm.dashboard;
const PERIODS: PeriodKind[] = ['day', 'week', 'month'];
const SOURCES: SpendSource[] = ['subscription', 'import', 'token', 'reservation'];
/** Обновление раз в 60 с; react-query не опрашивает скрытую вкладку (refetchIntervalInBackground = false). */
const live = { refetchInterval: DASHBOARD_REFRESH_MS, refetchIntervalInBackground: false, retry: false } as const;

export function Dashboard() {
  const role = useRole();
  const { identity, isPending } = useGetIdentity();
  const me = identity as BmIdentity | undefined;
  const { department_id: routeId } = useParams<{ department_id?: string }>();
  if (!role || isPending || !me) return null;
  if (role === 'budget_owner') {
    const id = routeId ?? me.departmentId;
    if (!id) return <Page><EmptyState message={t.no_department_owner} /></Page>;
    return <Page><DepartmentPanel id={id} role={role} /></Page>;
  }
  return <Page><ChooseDepartment routeId={routeId} role={role} /></Page>;
}

function Page({ children }: { children: ReactNode }) {
  return (
    <Box sx={{ p: 2, display: 'grid', gap: 2, maxWidth: 'var(--bm-size-content-max, 1440px)' }}>
      <Title title={t.title} />
      {children}
    </Box>
  );
}

/** А и Ф выбирают подразделение («Не распределено» здесь нет — это не бюджет). */
function ChooseDepartment({ routeId, role }: { routeId?: string; role: Role }) {
  const navigate = useNavigate();
  const { data, total, isPending, error, refetch } = useGetList<Department>('departments', {
    pagination: { page: 1, perPage: 200 },
    sort: { field: 'name', order: 'ASC' },
  });
  if (isPending) return <Skeleton variant="rectangular" height={56} />;
  if (error) return <ProblemBlock error={error} text={t.balance_error} onRetry={() => void refetch()} />;
  if (!total || !data?.length) {
    return role === 'admin' ? (
      <EmptyState
        message={t.no_departments_admin}
        actions={
          <>
            <Button component={Link} to="/org-import" variant="contained">
              {t.upload_ad}
            </Button>
            <Button component={Link} to="/departments/create">
              {t.create_department}
            </Button>
          </>
        }
      />
    ) : (
      <EmptyState message={t.no_departments} />
    );
  }
  const id = routeId ?? data[0]!.id;
  const selector = (
    <MuiTextField
      select
      size="small"
      label={t.department}
      value={id}
      onChange={(e) => navigate(`/dashboard/${e.target.value}`)}
      slotProps={{ select: { native: true }, inputLabel: { shrink: true } }}
      sx={{ minWidth: 240 }}
    >
      {data.map((d) => (
        <option key={d.id} value={d.id}>
          {d.name}
        </option>
      ))}
    </MuiTextField>
  );
  return <DepartmentPanel key={id} id={id} role={role} selector={selector} />;
}

function ProblemBlock({ error, text, onRetry }: { error: unknown; text: string; onRetry: () => void }) {
  if (statusOf(error) === 403) return <Forbidden />;
  return (
    <Alert
      severity="error"
      role="alert"
      action={
        <Button color="inherit" size="small" onClick={onRetry}>
          {ru.bm.state.retry}
        </Button>
      }
    >
      {text} {messageOf(error)}
    </Alert>
  );
}

function DepartmentPanel({ id, role, selector }: { id: string; role: Role; selector?: ReactNode }) {
  const dp = useDataProvider<BmDataProvider>();
  const refresh = useRefresh();
  const { data: dep } = useGetOne<Department>('departments', { id }, { retry: false });
  const balance = useQuery({
    queryKey: ['bm', 'getDepartmentBalance', id],
    queryFn: () => dp.getDepartmentBalance(id),
    ...live,
  });
  const limits = [...(balance.data?.limits ?? [])].sort((a, b) => PERIODS.indexOf(a.period) - PERIODS.indexOf(b.period));
  const available = limits.length ? limits.map((l) => l.period) : PERIODS;
  const [chosen, setChosen] = useState<PeriodKind | null>(null);
  const period: PeriodKind = chosen && available.includes(chosen) ? chosen : available.includes('month') ? 'month' : available.at(-1)!;
  const selected = limits.find((l) => l.period === period);

  return (
    <>
      <Box sx={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 3 }}>
        {selector ?? (
          <Typography variant="h5" component="h1">
            {dep?.name ?? ''}
          </Typography>
        )}
        {dep && <Typography>{t.mode.replace('%{mode}', ru.bm.policy_mode[dep.policy_mode])}</Typography>}
        {dep && <Typography>{t.fail_mode.replace('%{mode}', ru.bm.fail_mode[dep.fail_mode])}</Typography>}
        <Box sx={{ ml: 'auto', display: 'flex', alignItems: 'center', gap: 1 }}>
          {balance.data && (
            <Typography color="text.secondary">
              {t.as_of.replace('%{at}', formatDateTimeMsk(balance.data.as_of))}
            </Typography>
          )}
          <Button onClick={() => refresh()}>{t.refresh}</Button>
        </Box>
      </Box>

      {balance.isPending ? (
        <Box aria-busy="true">
          {PERIODS.map((p) => (
            <Skeleton key={p} variant="rectangular" height={48} sx={{ mb: 1 }} />
          ))}
          <SlowHint pending onRetry={() => void balance.refetch()} />
        </Box>
      ) : balance.isError ? (
        <ProblemBlock error={balance.error} text={t.balance_error} onRetry={() => void balance.refetch()} />
      ) : limits.length === 0 ? (
        <EmptyState
          message={t.no_limits}
          actions={
            can(role, 'putDepartmentLimits') ? (
              <Button component={Link} to={`/departments/${id}`} variant="contained">
                {t.set_limits}
              </Button>
            ) : undefined
          }
        />
      ) : (
        <Meters limits={limits} subsWarnPct={dep?.subs_warn_pct} mode={dep?.policy_mode ?? balance.data.policy_mode} />
      )}

      {!(balance.isError && statusOf(balance.error) === 403) && (
        <>
          <ToggleButtonGroup
            exclusive
            size="small"
            value={period}
            aria-label={t.breakdown_period}
            onChange={(_, v: PeriodKind | null) => v && setChosen(v)}
          >
            {available.map((p) => (
              <ToggleButton key={p} value={p}>
                {ru.bm.period_name[p]}
              </ToggleButton>
            ))}
          </ToggleButtonGroup>
          <Box sx={{ display: 'grid', gap: 2, gridTemplateColumns: { xs: '1fr', lg: '1fr 1fr' }, alignItems: 'start' }}>
            <Breakdown id={id} role={role} period={period} balance={selected} />
            <ByModels id={id} period={period} balance={selected} />
          </Box>
          <ImportBanner role={role} />
          {can(role, 'listNotifications') && <Feed currentId={id} role={role} />}
        </>
      )}
    </>
  );
}

function Meters({ limits, subsWarnPct, mode }: { limits: LimitBalance[]; subsWarnPct?: number; mode?: Schemas['PolicyMode'] }) {
  return (
    <Paper component="section" aria-label={t.meters} sx={{ p: 2, display: 'grid', gap: 2 }}>
      {limits.map((l) => {
        const over = l.remaining_rub.startsWith('-');
        return (
          <Box key={l.period} role="group" aria-label={ru.bm.period_name[l.period]}>
            <Box
              sx={{
                display: 'grid',
                gridTemplateColumns: { xs: '1fr', md: '96px 1fr auto' },
                alignItems: 'center',
                columnGap: 2,
                rowGap: 1,
              }}
            >
              <Typography sx={{ fontWeight: 600 }} title={t.period_from.replace('%{date}', formatDate(mskDateOf(l.period_start)))}>
                {ru.bm.period_name[l.period]}
              </Typography>
              <BalanceMeter balance={l} />
              <Box sx={{ display: 'flex', gap: 2, alignItems: 'baseline', flexWrap: 'wrap', fontVariantNumeric: 'tabular-nums' }}>
                <Typography component="span" color={over ? 'error' : undefined}>
                  {over ? t.overspend : t.remaining}
                </Typography>
                <Typography component="span" sx={{ fontWeight: 600, fontSize: 'var(--bm-font-size-xl, 20px)' }}>
                  <MoneyText value={l.remaining_rub} />
                </Typography>
                <Typography component="span" color="text.secondary">
                  {t.limit.replace('%{value}', formatMoney(l.limit_rub))}
                </Typography>
                <Typography component="span" color="text.secondary">
                  {t.resets.replace('%{at}', formatShortMsk(l.resets_at))}
                </Typography>
              </Box>
            </Box>
            {balanceNotes(l, subsWarnPct, mode).map((n) => (
              <Alert key={n.text} severity={n.severity} sx={{ mt: 1 }}>
                {n.text}
              </Alert>
            ))}
          </Box>
        );
      })}
      <Box component="ul" aria-label={t.legend} sx={{ display: 'flex', flexWrap: 'wrap', gap: 2, m: 0, p: 0, listStyle: 'none' }}>
        {SOURCES.map((s) => (
          <Box component="li" key={s} sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
            <Box
              aria-hidden
              sx={{
                width: 12,
                height: 12,
                background: s === 'reservation' ? 'var(--bm-pattern-reservation-hatch)' : `var(--bm-color-src-${s})`,
              }}
            />
            <Typography variant="caption">{ru.bm.spend_source[s]}</Typography>
          </Box>
        ))}
        <Box component="li" sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
          <Box aria-hidden sx={{ width: 0, height: 12, borderLeft: '2px dashed var(--bm-color-meter-threshold)' }} />
          <Typography variant="caption">{t.threshold}</Typography>
        </Box>
      </Box>
    </Paper>
  );
}


const sumOf = (b: LimitBalance | undefined, s: SpendSource): string | undefined =>
  !b
    ? undefined
    : s === 'subscription'
      ? b.spent.subscriptions_rub
      : s === 'import'
        ? b.spent.import_rub
        : s === 'token'
          ? b.spent.token_rub
          : b.reserved_rub;

/** «Из чего сложился расход»: четыре суммы периода и строки по каждой (listDepartmentBalanceItems, FR-026). */
function Breakdown({ id, role, period, balance }: { id: string; role: Role; period: PeriodKind; balance?: LimitBalance }) {
  const [open, setOpen] = useState<SpendSource[]>([]);
  const toggle = (s: SpendSource) => setOpen((o) => (o.includes(s) ? o.filter((x) => x !== s) : [...o, s]));
  return (
    <Paper component="section" aria-label={t.breakdown.replace('%{period}', ru.bm.period_name[period])} sx={{ p: 2 }}>
      <Typography variant="h6" component="h2" sx={{ mb: 1 }}>
        {t.breakdown.replace('%{period}', ru.bm.period_name[period])}
      </Typography>
      <Table size="small">
        <TableBody>
          {SOURCES.map((s) => (
            <TableRow key={s}>
              <TableCell component="th" scope="row">
                {ru.bm.spend_source[s]}
              </TableCell>
              <TableCell align="right">{balance ? <MoneyText value={sumOf(balance, s)} /> : '—'}</TableCell>
              <TableCell align="right">
                <Button size="small" onClick={() => toggle(s)} aria-expanded={open.includes(s)}>
                  {open.includes(s) ? t.hide_rows : t.show_rows}
                </Button>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      {open.map((s) => (
        <BalanceRows key={`${period}-${s}`} id={id} role={role} period={period} source={s} />
      ))}
    </Paper>
  );
}

const ROWS_PAGE = 50;

function BalanceRows({ id, role, period, source }: { id: string; role: Role; period: PeriodKind; source: SpendSource }) {
  const dp = useDataProvider<BmDataProvider>();
  const q = useQuery({
    queryKey: ['bm', 'listDepartmentBalanceItems', id, period, source],
    queryFn: () => dp.listDepartmentBalanceItems(id, { period, source, page: 1, page_size: ROWS_PAGE }),
    retry: false,
  });
  // У финансиста subject_pseudonym = null — колонки «Сотрудник» нет.
  const showEmployee = role !== 'finance_viewer';
  const name = t.rows_of.replace('%{source}', ru.bm.spend_source[source]);
  if (q.isPending) return <Skeleton variant="rectangular" height={64} sx={{ mt: 2 }} />;
  if (q.isError) return <ProblemBlock error={q.error} text="" onRetry={() => void q.refetch()} />;
  const items = q.data.items;
  return (
    <Box sx={{ mt: 2 }}>
      <Typography variant="subtitle2" component="h3">
        {name}
      </Typography>
      {items.length === 0 ? (
        <Typography color="text.secondary">{t.rows_empty}</Typography>
      ) : (
        <Table size="small" aria-label={name}>
          <TableHead>
            <TableRow>
              <TableCell scope="col">{t.col_date}</TableCell>
              <TableCell scope="col">{t.col_source}</TableCell>
              <TableCell scope="col">{t.col_model}</TableCell>
              {showEmployee && <TableCell scope="col">{t.col_employee}</TableCell>}
              <TableCell scope="col" align="right">
                {t.col_amount}
              </TableCell>
              <TableCell scope="col">{t.col_flags}</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {items.map((it) => (
              <TableRow key={`${it.source}-${it.ref_id}`}>
                <TableCell>
                  {it.source === 'import' ? `${formatDate(it.date)} (${t.utc_day})` : formatDate(it.date)}
                </TableCell>
                <TableCell>{ru.bm.spend_source[it.source]}</TableCell>
                <TableCell>{it.model_id ? <Mono>{it.model_id}</Mono> : '—'}</TableCell>
                {showEmployee && (
                  <TableCell>{it.subject_pseudonym ? <PseudonymTag pseudonym={it.subject_pseudonym} /> : '—'}</TableCell>
                )}
                <TableCell align="right">
                  <MoneyText value={it.amount_rub} />
                </TableCell>
                <TableCell>
                  <FlagChips flags={it.flags} />
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
      {q.data.total > items.length && (
        <Typography variant="caption" color="text.secondary">
          {t.rows_more.replace('%{shown}', String(items.length)).replace('%{total}', String(q.data.total))}
        </Typography>
      )}
    </Box>
  );
}

export function FlagChips({ flags }: { flags: Schemas['EventFlag'][] | undefined }) {
  if (!flags?.length) return <>—</>;
  return (
    <Box sx={{ display: 'flex', gap: 0.5, flexWrap: 'wrap' }}>
      {flags.map((f) => (
        <Chip key={f} size="small" color="warning" variant="outlined" label={ru.bm.event_flag[f]} />
      ))}
    </Box>
  );
}

/** Начало периода по МСК: из LimitBalance, а без лимита — по календарю МСК (сутки, неделя с понедельника, месяц с 1-го). */
function periodStart(period: PeriodKind, balance?: LimitBalance): string {
  if (balance) return mskDateOf(balance.period_start);
  const today = todayMsk();
  return period === 'day' ? today : period === 'week' ? mondayOf(today) : `${today.slice(0, 8)}01`;
}

function ByModels({ id, period, balance }: { id: string; period: PeriodKind; balance?: LimitBalance }) {
  const dp = useDataProvider<BmDataProvider>();
  const from = periodStart(period, balance);
  const to = todayMsk();
  const q = useQuery({
    queryKey: ['bm', 'getSpendReport', 'dashboard', id, from, to],
    queryFn: () => dp.getSpendReport({ from, to, department_id: id, group_by: ['model', 'source'] }),
    ...live,
  });
  const title = t.by_models.replace('%{period}', ru.bm.period_lower[period]);
  return (
    <Paper component="section" aria-label={title} sx={{ p: 2 }}>
      <Typography variant="h6" component="h2" sx={{ mb: 1 }}>
        {title}
      </Typography>
      {q.isPending ? (
        <Skeleton variant="rectangular" height={96} />
      ) : q.isError ? (
        <ProblemBlock error={q.error} text={t.models_error} onRetry={() => void q.refetch()} />
      ) : q.data.items.length === 0 ? (
        <Typography color="text.secondary">{t.models_empty}</Typography>
      ) : (
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell scope="col">{t.col_model}</TableCell>
              <TableCell scope="col">{t.col_source}</TableCell>
              <TableCell scope="col" align="right">
                {t.col_amount}
              </TableCell>
              <TableCell scope="col" align="right">
                {t.col_original}
              </TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {q.data.items.map((r, i) => (
              <TableRow key={`${r.model_id}-${r.source}-${i}`}>
                <TableCell>{r.model_id ? <Mono>{r.model_id}</Mono> : '—'}</TableCell>
                <TableCell>
                  {r.source ? ru.bm.spend_source[r.source] : '—'}
                  {r.late && <LateChip />}
                </TableCell>
                <TableCell align="right">
                  <MoneyText value={r.amount_rub} />
                </TableCell>
                <TableCell align="right">{originalAmount(r)}</TableCell>
              </TableRow>
            ))}
            <TableRow>
              <TableCell component="th" scope="row" colSpan={2}>
                {t.total}
              </TableCell>
              <TableCell align="right" sx={{ fontWeight: 600 }}>
                <MoneyText value={q.data.total_rub} />
              </TableCell>
              <TableCell />
            </TableRow>
          </TableBody>
        </Table>
      )}
    </Paper>
  );
}

export function LateChip() {
  return <Chip size="small" sx={{ ml: 1 }} label={t.late} title={t.late_hint} />;
}

/** Исходная сумма в USD: «128,40 $»; для ₽ — пусто. */
export function originalAmount(r: Schemas['SpendRow']): string {
  return r.original_currency === 'USD' && r.original_amount ? formatPrice(r.original_amount, 'USD') : '';
}

/** Плашка импорта (screens.md §2): тексты общие, у В — без ссылки на экран 7 (В1). */
function ImportBanner({ role }: { role: Role }) {
  const dp = useDataProvider<BmDataProvider>();
  const q = useQuery({ queryKey: IMPORT_STATUS_KEY, queryFn: () => dp.getImportStatus(), ...live });
  const h = ru.bm.import_health;
  return (
    <Box component="section" aria-label={h.banner_label} sx={{ display: 'grid', gap: 1 }}>
      {q.isPending ? (
        <Skeleton variant="rectangular" height={40} />
      ) : q.isError ? (
        <ProblemBlock error={q.error} text={h.load_error} onRetry={() => void q.refetch()} />
      ) : (
        importBannerLines(q.data, role).map((line) => (
          <Alert key={line.text} severity={line.severity}>
            {line.text}
          </Alert>
        ))
      )}
      {role === 'admin' && (
        <MuiLink component={Link} to="/imports">
          {h.open_imports}
        </MuiLink>
      )}
    </Box>
  );
}

/** Лента непрочитанных уведомлений (А, В): «Прочитано» → markNotificationRead. */
function Feed({ currentId, role }: { currentId: string; role: Role }) {
  const dp = useDataProvider<BmDataProvider>();
  const { data, error, refetch, isPending } = useGetList<Schemas['Notification']>(
    'notifications',
    { filter: { unread: true }, pagination: { page: 1, perPage: 5 } },
    live,
  );
  return (
    <Paper component="section" aria-label={t.feed} sx={{ p: 2 }}>
      <Box sx={{ display: 'flex', alignItems: 'baseline', gap: 2, mb: 1 }}>
        <Typography variant="h6" component="h2">
          {t.feed}
        </Typography>
        <MuiLink component={Link} to="/notifications">
          {t.feed_all}
        </MuiLink>
      </Box>
      {isPending ? (
        <Skeleton variant="rectangular" height={40} />
      ) : error ? (
        <ProblemBlock error={error} text={t.feed_error} onRetry={() => void refetch()} />
      ) : !data?.length ? (
        <Typography color="text.secondary">{t.feed_empty}</Typography>
      ) : (
        <Box component="ul" sx={{ m: 0, p: 0, listStyle: 'none', display: 'grid', gap: 1 }}>
          {data.map((n) => (
            <Box component="li" key={n.id} sx={{ display: 'flex', flexWrap: 'wrap', gap: 2, alignItems: 'center' }}>
              <Typography>{n.message}</Typography>
              <Typography color="text.secondary">{formatDateTimeMsk(n.created_at)}</Typography>
              {role === 'admin' && n.department_id && n.department_id !== currentId && (
                <MuiLink component={Link} to={`/dashboard/${n.department_id}`}>
                  {t.feed_open}
                </MuiLink>
              )}
              <Button
                size="small"
                onClick={() => {
                  void dp.markNotificationRead(n.id).then(() => refetch());
                }}
              >
                {t.feed_read}
              </Button>
            </Box>
          ))}
        </Box>
      )}
    </Paper>
  );
}
