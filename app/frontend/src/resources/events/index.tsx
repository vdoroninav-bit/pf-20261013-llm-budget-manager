// Экран 11 «Журналы → События» (design/screens.md §11, T072): listEvents. Маршрут /admin/#/events.
// А — все подразделения (фильтр подразделения), В — своё (фильтр не показывается), Ф — журналов нет.
import { useEffect, useRef } from 'react';
import { Box, TextField as MuiTextField } from '@mui/material';
import { Datagrid, FunctionField, List, Pagination, useListContext } from 'react-admin';
import { can } from '../../access/permissions';
import type { Schemas } from '../../api/types';
import { EmptyState, Forbidden, ListError, ListSlowHint, Mono, MoneyText, useRole } from '../../components/common';
import { NavTabs } from '../../components/NavTabs';
import { formatDate, formatDateTimeMsk, formatInt, todayMsk } from '../../format/date';
import { ru } from '../../i18n/ru';
import { FlagChips } from '../../pages/Dashboard';
import { useDepartmentNames } from '../departmentNames';

type CallEvent = Schemas['CallEvent'];
const t = ru.bm.events;
const tj = ru.bm.journals;

/** Вкладки журналов: события и уведомления — А и В, аудит — только А. */
export function JournalTabs() {
  const role = useRole();
  return (
    <NavTabs
      label={tj.tabs}
      tabs={[
        { to: '/events', label: tj.tab_events, show: can(role, 'listEvents') },
        { to: '/audit', label: tj.tab_audit, show: can(role, 'listAudit') },
        { to: '/notifications', label: tj.tab_notifications, show: can(role, 'listNotifications') },
      ]}
    />
  );
}

const stable = (o: Record<string, unknown>) => JSON.stringify(Object.entries(o).sort(([a], [b]) => a.localeCompare(b)));

/**
 * Установка одного фильтра списка без потери соседних: фильтры могут смениться быстрее, чем список перерисуется,
 * поэтому следующее значение собирается от последнего установленного, а не от отрисованного.
 */
export type FilterSetter = ReturnType<typeof useSetFilter>;

/** Один экземпляр на панель фильтров списка — его получают все поля панели. */
export function useSetFilter() {
  const { filterValues, setFilters } = useListContext();
  const latest = useRef<Record<string, unknown>>(filterValues);
  // Установленное, но ещё не применённое списком значение (react-admin применяет фильтр в следующем тике).
  const pending = useRef<string | null>(null);
  useEffect(() => {
    const applied = stable(filterValues);
    if (pending.current === null || pending.current === applied) {
      pending.current = null;
      latest.current = filterValues;
    }
  }, [filterValues]);
  return {
    value: (source: string) => (filterValues[source] as string | undefined) ?? '',
    set: (source: string, value: string | boolean | undefined) => {
      const next = { ...latest.current, [source]: value };
      if (value === '' || value === undefined || value === false) delete next[source];
      latest.current = next;
      pending.current = stable(next);
      setFilters(next, {}, false);
    },
  };
}

/** Нативный выбор с пустым «Любое» для фильтра журнала. */
export function FilterSelect({
  filter,
  label,
  source,
  options,
}: {
  filter: FilterSetter;
  label: string;
  source: string;
  options: Array<[value: string, label: string]>;
}) {
  return (
    <MuiTextField
      select
      size="small"
      label={label}
      value={filter.value(source)}
      onChange={(e) => filter.set(source, e.target.value)}
      slotProps={{ select: { native: true }, inputLabel: { shrink: true } }}
      sx={{ minWidth: 180 }}
    >
      <option value="">{tj.any}</option>
      {options.map(([v, l]) => (
        <option key={v} value={v}>
          {l}
        </option>
      ))}
    </MuiTextField>
  );
}

/** Период журнала: from/to обязательны в контракте — пустое значение не принимается. */
export function PeriodFilter({ filter }: { filter: FilterSetter }) {
  // from и to обязательны в контракте — пустое значение не устанавливается.
  const set = (k: 'from' | 'to', v: string) => v && filter.set(k, v);
  return (
    <>
      <MuiTextField
        type="date"
        size="small"
        label={tj.from}
        value={filter.value('from')}
        onChange={(e) => set('from', e.target.value)}
        slotProps={{ inputLabel: { shrink: true } }}
      />
      <MuiTextField
        type="date"
        size="small"
        label={tj.to}
        value={filter.value('to')}
        onChange={(e) => set('to', e.target.value)}
        slotProps={{ inputLabel: { shrink: true } }}
      />
    </>
  );
}

const entries = <K extends string>(dict: Record<K, string>) => Object.entries(dict) as Array<[K, string]>;

function DepartmentFilter({ filter }: { filter: FilterSetter }) {
  const names = useDepartmentNames();
  return <FilterSelect filter={filter} label={t.department} source="department_id" options={[...names.entries()]} />;
}

function EventFilters({ showDepartment }: { showDepartment: boolean }) {
  const filter = useSetFilter();
  return (
    <Box component="section" aria-label={tj.filters} sx={{ display: 'flex', flexWrap: 'wrap', gap: 2, px: 2, pt: 2 }}>
      <PeriodFilter filter={filter} />
      {showDepartment && <DepartmentFilter filter={filter} />}
      <FilterSelect
        filter={filter}
        label={t.outcome}
        source="outcome"
        // Outcome контракта: allow, redirect, deny («Без решения» — только в событиях, не в фильтре).
        options={entries(ru.bm.outcome).filter(([v]) => v !== 'none')}
      />
      <FilterSelect filter={filter} label={t.code} source="code" options={entries(ru.bm.deny_code)} />
      <FilterSelect filter={filter} label={t.flag} source="flag" options={entries(ru.bm.event_flag)} />
    </Box>
  );
}

export function EventsPage() {
  const role = useRole();
  if (role === undefined) return null;
  if (!can(role, 'listEvents')) return <Forbidden />;
  const today = todayMsk();
  return (
    <Box>
      <JournalTabs />
      <List
        resource="events"
        perPage={50}
        actions={false}
        empty={false}
        error={<ListError />}
        title={t.title}
        disableSyncWithLocation
        filterDefaultValues={{ from: today, to: today }}
        pagination={<Pagination rowsPerPageOptions={[50, 100, 200]} />}
      >
        <ListSlowHint />
        <EventFilters showDepartment={role === 'admin'} />
        <Datagrid bulkActionButtons={false} rowClick={false} empty={<EmptyState message={t.empty} />}>
          <FunctionField<CallEvent> label={t.col_time} sortable={false} render={(r) => formatDateTimeMsk(r.created_at)} />
          <FunctionField<CallEvent> label={t.col_caller} sortable={false} render={(r) => r.caller_name} />
          <FunctionField<CallEvent>
            label={t.col_employee}
            sortable={false}
            render={(r) => (r.subject_pseudonym ? <Mono>{r.subject_pseudonym}</Mono> : '—')}
          />
          <FunctionField<CallEvent> label={t.col_requested} sortable={false} render={(r) => <Mono>{r.requested_model}</Mono>} />
          <FunctionField<CallEvent>
            label={t.col_final}
            sortable={false}
            render={(r) => (r.final_model ? <Mono>{r.final_model}</Mono> : '—')}
          />
          <FunctionField<CallEvent>
            label={t.col_outcome}
            sortable={false}
            render={(r) => (
              <>
                <span>{ru.bm.outcome[r.outcome]}</span>
                {r.code && (
                  <Box component="span" sx={{ display: 'block', color: 'text.secondary' }}>
                    {ru.bm.deny_code[r.code]}
                  </Box>
                )}
              </>
            )}
          />
          <FunctionField<CallEvent> label={t.col_reserve} sortable={false} textAlign="right" render={(r) => <MoneyText value={r.reserve_rub ?? null} />} />
          <FunctionField<CallEvent>
            label={t.col_fact}
            sortable={false}
            textAlign="right"
            render={(r) => (r.fact_rub ? <MoneyText value={r.fact_rub} /> : '—')}
          />
          <FunctionField<CallEvent>
            label={t.col_tokens}
            sortable={false}
            textAlign="right"
            render={(r) =>
              r.tokens ? `${formatInt(r.tokens.input)} / ${formatInt(r.tokens.output)} / ${formatInt(r.tokens.cached ?? 0)}` : '—'
            }
          />
          <FunctionField<CallEvent> label={t.col_status} sortable={false} render={(r) => ru.bm.event_status[r.status]} />
          <FunctionField<CallEvent> label={t.col_flags} sortable={false} render={(r) => <FlagChips flags={r.flags} />} />
          <FunctionField<CallEvent> label={t.col_expense} sortable={false} render={(r) => formatDate(r.expense_date)} />
        </Datagrid>
      </List>
    </Box>
  );
}
