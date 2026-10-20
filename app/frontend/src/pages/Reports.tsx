// Экран 8.1 «Отчёты → Расход» (design/screens.md §8.1, T072): getSpendReport, getSpendCsv, listDepartments.
// Маршрут /admin/#/reports. В таблице и CSV нет данных сотрудников ни для одной роли (FR-028).
import { useState } from 'react';
import {
  Alert,
  Box,
  Button,
  Checkbox,
  FormControl,
  FormControlLabel,
  FormGroup,
  FormLabel,
  Paper,
  Skeleton,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  TextField as MuiTextField,
  Typography,
} from '@mui/material';
import { useQuery } from '@tanstack/react-query';
import { Title, useDataProvider, useGetList } from 'react-admin';
import { can } from '../access/permissions';
import type { Schemas } from '../api/types';
import type { BmDataProvider, SpendGroup } from '../dataProvider';
import { EmptyState, Forbidden, Mono, MoneyText, ProblemAlert, SlowHint, messageOf, useRole } from '../components/common';
import { NavTabs } from '../components/NavTabs';
import { Hint } from '../components/records';
import { currentMonthMsk, formatDate, lastDayOfMonth, mondayOf, previousMonth, todayMsk } from '../format/date';
import { ru } from '../i18n/ru';
import { LateChip, originalAmount } from './Dashboard';

const t = ru.bm.reports;
const GROUPS: SpendGroup[] = ['department', 'model', 'source', 'day', 'month'];
const GROUP_LABEL: Record<SpendGroup, string> = {
  department: t.g_department,
  model: t.g_model,
  source: t.g_source,
  day: t.g_day,
  month: t.g_month,
};

export function ReportTabs() {
  const role = useRole();
  return (
    <NavTabs
      label={t.tabs}
      tabs={[
        { to: '/reports', label: t.tab_spend },
        { to: '/reports/reconciliation', label: t.tab_recon, show: can(role, 'getReconciliation') },
      ]}
    />
  );
}

export function ReportsPage() {
  const role = useRole();
  if (role === undefined) return null;
  if (!can(role, 'getSpendReport')) return <Forbidden />;
  return <SpendReport showDepartment={role !== 'budget_owner'} />;
}

/** Добавляет BOM, если его нет: Excel узнаёт по нему UTF-8 (сервер отдаёт BOM, но fetch().text() его снимает). */
const withBom = (text: string) => (text.charCodeAt(0) === 0xfeff ? text : `﻿${text}`);

function SpendReport({ showDepartment }: { showDepartment: boolean }) {
  const dp = useDataProvider<BmDataProvider>();
  const today = todayMsk();
  const [from, setFrom] = useState(`${today.slice(0, 8)}01`);
  const [to, setTo] = useState(today);
  const [department, setDepartment] = useState('');
  const [groups, setGroups] = useState<SpendGroup[]>(['department', 'model', 'source']);
  const [csv, setCsv] = useState<{ busy: boolean; error: unknown }>({ busy: false, error: null });
  const { data: departments } = useGetList<Schemas['Department']>(
    'departments',
    { pagination: { page: 1, perPage: 200 }, sort: { field: 'name', order: 'ASC' } },
    { enabled: showDepartment },
  );
  const invalid = !from || !to || from > to;
  const query = { from, to, ...(department ? { department_id: department } : {}) };
  const report = useQuery({
    queryKey: ['bm', 'getSpendReport', 'reports', query, groups],
    queryFn: () => dp.getSpendReport({ ...query, group_by: groups }),
    enabled: !invalid,
    retry: false,
  });

  function setRange(f: string, tt: string) {
    setFrom(f);
    setTo(tt);
  }
  const toggle = (g: SpendGroup) =>
    setGroups((cur) => GROUPS.filter((x) => (x === g ? !cur.includes(g) : cur.includes(x))));

  async function downloadCsv() {
    setCsv({ busy: true, error: null });
    try {
      const text = await dp.getSpendCsv(query);
      const url = URL.createObjectURL(new Blob([withBom(text)], { type: 'text/csv;charset=utf-8' }));
      const a = document.createElement('a');
      a.href = url;
      a.download = `spend_${from}_${to}.csv`;
      a.click();
      URL.revokeObjectURL(url);
      setCsv({ busy: false, error: null });
    } catch (error) {
      setCsv({ busy: false, error });
    }
  }

  const month = currentMonthMsk();
  const prev = previousMonth(month);
  const showPeriod = groups.includes('day') || groups.includes('month');

  return (
    <Box>
      <ReportTabs />
      <Title title={t.title} />
      <Box sx={{ p: 2, display: 'grid', gap: 2 }}>
        <Paper component="section" aria-label={t.filters} sx={{ p: 2, display: 'flex', flexWrap: 'wrap', gap: 2, alignItems: 'flex-start' }}>
          <MuiTextField
            type="date"
            size="small"
            label={t.from}
            value={from}
            onChange={(e) => setFrom(e.target.value)}
            error={invalid && !!from && !!to}
            helperText={invalid && from && to ? t.range_error : ' '}
            slotProps={{ inputLabel: { shrink: true } }}
          />
          <MuiTextField
            type="date"
            size="small"
            label={t.to}
            value={to}
            onChange={(e) => setTo(e.target.value)}
            helperText=" "
            slotProps={{ inputLabel: { shrink: true } }}
          />
          <Box sx={{ display: 'flex', gap: 1, pt: 0.5 }}>
            <Button size="small" onClick={() => setRange(`${month}-01`, today)}>
              {t.this_month}
            </Button>
            <Button size="small" onClick={() => setRange(`${prev}-01`, lastDayOfMonth(prev))}>
              {t.last_month}
            </Button>
            <Button size="small" onClick={() => setRange(mondayOf(today), today)}>
              {t.this_week}
            </Button>
          </Box>
          {showDepartment && (
            <MuiTextField
              select
              size="small"
              label={t.department}
              value={department}
              onChange={(e) => setDepartment(e.target.value)}
              slotProps={{ select: { native: true }, inputLabel: { shrink: true } }}
              sx={{ minWidth: 220 }}
            >
              <option value="">{t.all_departments}</option>
              {(departments ?? []).map((d) => (
                <option key={d.id} value={d.id}>
                  {d.name}
                </option>
              ))}
              <option value="unallocated">{t.unallocated}</option>
            </MuiTextField>
          )}
          <FormControl component="fieldset">
            <FormLabel component="legend">{t.group_by}</FormLabel>
            <FormGroup row>
              {GROUPS.map((g) => (
                <FormControlLabel
                  key={g}
                  control={<Checkbox size="small" checked={groups.includes(g)} onChange={() => toggle(g)} />}
                  label={GROUP_LABEL[g]}
                />
              ))}
            </FormGroup>
          </FormControl>
        </Paper>

        <Box sx={{ display: 'flex', gap: 2, alignItems: 'center', flexWrap: 'wrap' }}>
          <Button variant="contained" disabled={invalid || csv.busy} onClick={() => void downloadCsv()}>
            {csv.busy ? t.csv_busy : t.csv}
          </Button>
          <Hint>{t.csv_hint}</Hint>
        </Box>
        {csv.error !== null && (
          <Alert
            severity="error"
            role="alert"
            action={
              <Button color="inherit" size="small" onClick={() => void downloadCsv()}>
                {ru.bm.state.retry}
              </Button>
            }
          >
            {t.csv_error} {messageOf(csv.error)}
          </Alert>
        )}

        {invalid ? null : report.isPending ? (
          <Box aria-busy="true">
            <Skeleton variant="rectangular" height={160} />
            <SlowHint pending onRetry={() => void report.refetch()} />
          </Box>
        ) : report.isError ? (
          <ProblemAlert error={report.error} onRetry={() => void report.refetch()} />
        ) : report.data.items.length === 0 ? (
          <EmptyState message={t.empty} />
        ) : (
          <Paper sx={{ overflowX: 'auto' }}>
            <Table size="small" aria-label={t.tab_spend}>
              <TableHead>
                <TableRow>
                  {showPeriod && <TableCell scope="col">{t.col_period}</TableCell>}
                  {groups.includes('department') && <TableCell scope="col">{t.col_department}</TableCell>}
                  {groups.includes('model') && <TableCell scope="col">{t.col_model}</TableCell>}
                  {groups.includes('source') && <TableCell scope="col">{t.col_source}</TableCell>}
                  <TableCell scope="col" align="right">
                    {t.col_amount}
                  </TableCell>
                  <TableCell scope="col" align="right">
                    {t.col_original}
                  </TableCell>
                  <TableCell scope="col">{t.col_late}</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {report.data.items.map((r, i) => (
                  <TableRow key={i}>
                    {showPeriod && <TableCell>{formatDate(r.period_start)}</TableCell>}
                    {groups.includes('department') && (
                      <TableCell>{r.department_id === null || !r.department_name ? t.unallocated : r.department_name}</TableCell>
                    )}
                    {groups.includes('model') && <TableCell>{r.model_id ? <Mono>{r.model_id}</Mono> : '—'}</TableCell>}
                    {groups.includes('source') && <TableCell>{r.source ? ru.bm.spend_source[r.source] : '—'}</TableCell>}
                    <TableCell align="right">
                      <MoneyText value={r.amount_rub} />
                    </TableCell>
                    <TableCell align="right">{originalAmount(r)}</TableCell>
                    <TableCell>{r.late ? <LateChip /> : null}</TableCell>
                  </TableRow>
                ))}
                <TableRow sx={{ position: 'sticky', bottom: 0, bgcolor: 'background.paper' }}>
                  <TableCell
                    component="th"
                    scope="row"
                    colSpan={(showPeriod ? 1 : 0) + groups.filter((g) => g !== 'day' && g !== 'month').length || 1}
                  >
                    <Typography sx={{ fontWeight: 600 }}>{t.total}</Typography>
                  </TableCell>
                  <TableCell align="right" sx={{ fontWeight: 600 }}>
                    <MoneyText value={report.data.total_rub} />
                  </TableCell>
                  <TableCell colSpan={2} />
                </TableRow>
              </TableBody>
            </Table>
          </Paper>
        )}
      </Box>
    </Box>
  );
}
