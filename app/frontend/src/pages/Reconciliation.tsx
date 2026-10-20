// Экран 8.2 «Отчёты → Сверка» (design/screens.md §8.2, T072): getReconciliation, listDepartments. Только А и Ф.
// Подсветка: |diff_pct| > 2 → «больше 2%» (SC-005), coverage_pct < 95 → «меньше 95%» — словами, не только цветом.
import { useState } from 'react';
import {
  Box,
  Link as MuiLink,
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
import { Title, useDataProvider } from 'react-admin';
import { Link } from 'react-router-dom';
import { can } from '../access/permissions';
import type { BmDataProvider } from '../dataProvider';
import { compareDecimal, formatPercent } from '../budget/subscriptionShare';
import { EmptyState, Forbidden, Mono, MoneyText, ProblemAlert, SlowHint, useRole } from '../components/common';
import { Hint } from '../components/records';
import { currentMonthMsk, previousMonth } from '../format/date';
import { ru } from '../i18n/ru';
import { providerLabel } from '../import/importHealth';
import { useDepartmentNames } from '../resources/departmentNames';
import { ReportTabs } from './Reports';

const t = ru.bm.recon;
const MONTH = /^\d{4}-(0[1-9]|1[0-2])$/;

/** |diff_pct| > 2 — расхождение больше допустимого (SC-005). */
export const diffTooBig = (pct: string | null | undefined) => !!pct && compareDecimal(pct.replace(/^-/, ''), '2') > 0;
/** coverage_pct < 95 — покрытие ниже порога Г2. */
export const coverageLow = (pct: string | null | undefined) => !!pct && compareDecimal(pct, '95') < 0;

export function ReconciliationPage() {
  const role = useRole();
  if (role === undefined) return null;
  if (!can(role, 'getReconciliation')) return <Forbidden />;
  return <Reconciliation canLinkImports={role === 'admin'} />;
}

function Reconciliation({ canLinkImports }: { canLinkImports: boolean }) {
  const dp = useDataProvider<BmDataProvider>();
  const names = useDepartmentNames();
  const [month, setMonth] = useState(() => previousMonth(currentMonthMsk()));
  const valid = MONTH.test(month);
  const q = useQuery({
    queryKey: ['bm', 'getReconciliation', month],
    queryFn: () => dp.getReconciliation(month),
    enabled: valid,
    retry: false,
  });
  const marked = (bad: boolean, text: string, severity: 'error' | 'warning') =>
    bad ? (
      <Typography component="span" variant="caption" color={severity} sx={{ ml: 1, fontWeight: 600 }}>
        {text}
      </Typography>
    ) : null;

  return (
    <Box>
      <ReportTabs />
      <Title title={ru.bm.reports.title} />
      <Box sx={{ p: 2, display: 'grid', gap: 2 }}>
        <MuiTextField
          type="month"
          size="small"
          label={t.month}
          value={month}
          onChange={(e) => setMonth(e.target.value)}
          slotProps={{ inputLabel: { shrink: true } }}
          sx={{ maxWidth: 220 }}
        />
        <Hint>{t.caption}</Hint>
        {!valid ? null : q.isPending ? (
          <Box aria-busy="true">
            <Skeleton variant="rectangular" height={120} />
            <SlowHint pending onRetry={() => void q.refetch()} />
          </Box>
        ) : q.isError ? (
          <ProblemAlert error={q.error} onRetry={() => void q.refetch()} />
        ) : q.data.length === 0 ? (
          <EmptyState
            message={t.empty}
            actions={
              canLinkImports ? (
                <MuiLink component={Link} to="/imports">
                  {t.empty_link}
                </MuiLink>
              ) : undefined
            }
          />
        ) : (
          <Paper sx={{ overflowX: 'auto' }}>
            <Table size="small" aria-label={ru.bm.reports.tab_recon}>
              <TableHead>
                <TableRow>
                  {[t.provider, t.source, t.department].map((h) => (
                    <TableCell key={h} scope="col">
                      {h}
                    </TableCell>
                  ))}
                  {[t.imported, t.decision_api, t.diff, t.diff_pct, t.coverage].map((h) => (
                    <TableCell key={h} scope="col" align="right">
                      {h}
                    </TableCell>
                  ))}
                </TableRow>
              </TableHead>
              <TableBody>
                {q.data.map((r) => {
                  const big = diffTooBig(r.diff_pct);
                  const low = coverageLow(r.coverage_pct);
                  return (
                    <TableRow key={`${r.provider}-${r.source_ref}`}>
                      <TableCell>{providerLabel(r.provider)}</TableCell>
                      <TableCell>
                        <Mono>{r.source_ref}</Mono>
                      </TableCell>
                      <TableCell>{r.department_id ? (names.get(r.department_id) ?? <Mono>{r.department_id}</Mono>) : '—'}</TableCell>
                      <TableCell align="right">
                        <MoneyText value={r.imported_rub} />
                      </TableCell>
                      <TableCell align="right">
                        <MoneyText value={r.decision_api_rub} />
                      </TableCell>
                      <TableCell align="right">
                        <MoneyText value={r.diff_rub} />
                      </TableCell>
                      <TableCell align="right" sx={big ? { bgcolor: 'var(--bm-color-danger-soft)' } : undefined}>
                        {formatPercent(r.diff_pct)}
                        {marked(big, t.diff_over, 'error')}
                      </TableCell>
                      <TableCell align="right" sx={low ? { bgcolor: 'var(--bm-color-warning-soft)' } : undefined}>
                        {formatPercent(r.coverage_pct)}
                        {marked(low, t.coverage_low, 'warning')}
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </Paper>
        )}
      </Box>
    </Box>
  );
}
