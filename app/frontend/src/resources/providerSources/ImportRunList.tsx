// Экран 7.3 «Запуски» (design/screens.md §7.3, T064): listImportRuns с фильтром провайдера.
// Опрос раз в 10 с только пока есть queued/running, затем останавливается (13_design_rulings.md, В7).
import { Box, TextField as MuiTextField, Typography } from '@mui/material';
import { Datagrid, FunctionField, List, Pagination, useListContext } from 'react-admin';
import { can } from '../../access/permissions';
import type { Schemas } from '../../api/types';
import { EmptyState, Forbidden, ListError, ListSlowHint, Mono, useRole } from '../../components/common';
import { formatDate, formatDateTimeMsk } from '../../format/date';
import { ru } from '../../i18n/ru';
import { errorClassText, providerLabel, runsPollInterval } from '../../import/importHealth';
import { ImportTabs } from '../../pages/ImportStatus';

type Run = Schemas['ImportRun'];
const t = ru.bm.runs;

export function ImportRunList() {
  const role = useRole();
  if (role === undefined) return null;
  if (!can(role, 'listImportRuns')) return <Forbidden />;
  return (
    <Box>
      <ImportTabs />
      <List
        resource="importRuns"
        perPage={50}
        actions={false}
        empty={false}
        error={<ListError />}
        title={ru.bm.imports.title}
        disableSyncWithLocation
        pagination={<Pagination rowsPerPageOptions={[50, 100, 200]} />}
        queryOptions={{ refetchInterval: runsPollInterval as never }}
      >
        <ListSlowHint />
        <ProviderFilter />
        <Datagrid bulkActionButtons={false} rowClick={false} empty={<EmptyState message={t.empty} />}>
          <FunctionField<Run> label={t.provider} sortable={false} render={(r) => providerLabel(r.provider)} />
          <FunctionField<Run> label={t.status} sortable={false} render={(r) => ru.bm.run_status[r.status]} />
          <FunctionField<Run> label={t.started} sortable={false} render={(r) => formatDateTimeMsk(r.started_at)} />
          <FunctionField<Run> label={t.finished} sortable={false} render={(r) => formatDateTimeMsk(r.finished_at)} />
          <FunctionField<Run>
            label={t.days}
            sortable={false}
            render={(r) => (r.day_from ? `${formatDate(r.day_from)} – ${formatDate(r.day_to)}` : '—')}
          />
          <FunctionField<Run> label={t.charged} sortable={false} textAlign="right" render={(r) => r.lines_charged ?? '—'} />
          <FunctionField<Run> label={t.reconcile} sortable={false} textAlign="right" render={(r) => r.lines_reconcile ?? '—'} />
          <FunctionField<Run>
            label={t.unallocated}
            sortable={false}
            textAlign="right"
            render={(r) => r.lines_unallocated ?? '—'}
          />
          <FunctionField<Run> label={t.changed} sortable={false} textAlign="right" render={(r) => r.lines_changed ?? '—'} />
          <FunctionField<Run>
            label={t.error}
            sortable={false}
            render={(r) =>
              r.error_class ? (
                <>
                  {errorClassText(r.error_class)}{' '}
                  <Typography component="span" variant="caption" color="text.secondary">
                    <Mono>{r.error_class}</Mono>
                  </Typography>
                </>
              ) : (
                '—'
              )
            }
          />
        </Datagrid>
      </List>
    </Box>
  );
}

function ProviderFilter() {
  const { filterValues, setFilters } = useListContext();
  const value = (filterValues.provider as string | undefined) ?? '';
  return (
    <Box sx={{ px: 2, pt: 2 }}>
      <MuiTextField
        select
        size="small"
        label={t.provider}
        value={value}
        onChange={(e) => setFilters(e.target.value ? { provider: e.target.value } : {}, {}, false)}
        slotProps={{ select: { native: true }, inputLabel: { shrink: true } }}
        sx={{ minWidth: 220 }}
      >
        <option value="">{t.all_providers}</option>
        <option value="anthropic">{providerLabel('anthropic')}</option>
        <option value="openai">{providerLabel('openai')}</option>
      </MuiTextField>
    </Box>
  );
}
