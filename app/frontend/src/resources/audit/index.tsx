// Экран 11 «Журналы → Аудит» (design/screens.md §11, FR-031, T072): listAudit. Только администратор.
// Раскрытие строки — «Было / Стало» двумя колонками, изменённые ключи выделены и подписаны «изменено».
import { useState } from 'react';
import { Box, Button, Chip, Table, TableBody, TableCell, TableHead, TableRow, TextField as MuiTextField } from '@mui/material';
import { Datagrid, FunctionField, List, Pagination, useRecordContext } from 'react-admin';
import { can } from '../../access/permissions';
import type { Schemas } from '../../api/types';
import { EmptyState, Forbidden, ListError, ListSlowHint, Mono, useRole } from '../../components/common';
import { AUDIT_DEFAULT_DAYS } from '../../config/ui';
import { addDays, formatDateTimeMsk, todayMsk } from '../../format/date';
import { ru } from '../../i18n/ru';
import { JournalTabs, PeriodFilter, useSetFilter, type FilterSetter } from '../events';

type AuditEntry = Schemas['AuditEntry'];
const t = ru.bm.audit;

/** «caller:<id>» → «Система <id>», иначе логин админки. */
export const actorText = (actor: string) => (actor.startsWith('caller:') ? t.caller.replace('%{id}', actor.slice(7)) : actor);

function AuditFilters() {
  const filter = useSetFilter();
  return (
    <Box component="section" aria-label={ru.bm.journals.filters} sx={{ display: 'flex', flexWrap: 'wrap', gap: 2, px: 2, pt: 2 }}>
      <PeriodFilter filter={filter} />
      <ObjectTypeFilter filter={filter} />
    </Box>
  );
}

function ObjectTypeFilter({ filter }: { filter: FilterSetter }) {
  return (
    <MuiTextField
      size="small"
      label={t.object_type}
      value={filter.value('object_type')}
      onChange={(e) => filter.set('object_type', e.target.value.trim())}
      slotProps={{ htmlInput: { lang: 'en', translate: 'no' } }}
    />
  );
}

const json = (v: unknown) => (v === undefined ? '—' : JSON.stringify(v));

/**
 * «Было / Стало» (screens.md §11). Раскрывается кнопкой в строке, а не стрелкой Datagrid: у неё в react-admin 5
 * aria-hidden и tabIndex=-1, с клавиатуры её не открыть (13_design.md §8, «Клавиатура»).
 */
function AuditChanges() {
  const r = useRecordContext<AuditEntry>();
  const [open, setOpen] = useState(false);
  if (!r || (!r.before && !r.after)) return <>—</>;
  const id = `bm-audit-diff-${r.id}`;
  return (
    <Box>
      <Button size="small" aria-expanded={open} aria-controls={id} onClick={() => setOpen((v) => !v)}>
        {`${t.before} / ${t.after}`}
      </Button>
      {open && (
        <Box id={id}>
          <AuditDiff r={r} />
        </Box>
      )}
    </Box>
  );
}

function AuditDiff({ r }: { r: AuditEntry }) {
  const before = (r.before ?? {}) as Record<string, unknown>;
  const after = (r.after ?? {}) as Record<string, unknown>;
  const keys = [...new Set([...Object.keys(before), ...Object.keys(after)])];
  if (!keys.length) return <Box sx={{ p: 1, color: 'text.secondary' }}>—</Box>;
  return (
    <Table size="small" aria-label={`${t.before} / ${t.after}`}>
      <TableHead>
        <TableRow>
          <TableCell scope="col" />
          <TableCell scope="col">{t.before}</TableCell>
          <TableCell scope="col">{t.after}</TableCell>
          <TableCell scope="col" />
        </TableRow>
      </TableHead>
      <TableBody>
        {keys.map((k) => {
          const changed = json(before[k]) !== json(after[k]);
          return (
            <TableRow key={k} sx={changed ? { bgcolor: 'var(--bm-color-warning-soft)' } : undefined}>
              <TableCell component="th" scope="row">
                <Mono>{k}</Mono>
              </TableCell>
              <TableCell>
                <Mono>{json(before[k])}</Mono>
              </TableCell>
              <TableCell>
                <Mono>{json(after[k])}</Mono>
              </TableCell>
              <TableCell>{changed ? t.changed : ''}</TableCell>
            </TableRow>
          );
        })}
      </TableBody>
    </Table>
  );
}

export function AuditPage() {
  const role = useRole();
  if (role === undefined) return null;
  if (!can(role, 'listAudit')) return <Forbidden />;
  const today = todayMsk();
  return (
    <Box>
      <JournalTabs />
      <List
        resource="audit"
        perPage={50}
        actions={false}
        empty={false}
        error={<ListError />}
        title={t.title}
        disableSyncWithLocation
        // Период по умолчанию — последние AUDIT_DEFAULT_DAYS суток по МСК, включая сегодня (3Ф5).
        filterDefaultValues={{ from: addDays(today, 1 - AUDIT_DEFAULT_DAYS), to: today }}
        pagination={<Pagination rowsPerPageOptions={[50, 100, 200]} />}
      >
        <ListSlowHint />
        <AuditFilters />
        <Datagrid bulkActionButtons={false} rowClick={false} empty={<EmptyState message={t.empty} />}>
          <FunctionField<AuditEntry> label={t.col_at} sortable={false} render={(r) => formatDateTimeMsk(r.at)} />
          <FunctionField<AuditEntry> label={t.col_actor} sortable={false} render={(r) => actorText(r.actor)} />
          <FunctionField<AuditEntry>
            label={t.col_action}
            sortable={false}
            render={(r) => {
              const text = (t.action as Record<string, string>)[r.action];
              if (r.action === 'forbidden_access') return <Chip size="small" color="error" label={text} />;
              return text ?? <Mono>{r.action}</Mono>;
            }}
          />
          <FunctionField<AuditEntry>
            label={t.col_object}
            sortable={false}
            render={(r) => (
              <>
                <Mono>{r.object_type}</Mono>
                {r.object_id && (
                  <Box component="span" sx={{ ml: 1, color: 'text.secondary' }}>
                    <Mono>{r.object_id}</Mono>
                  </Box>
                )}
              </>
            )}
          />
          <FunctionField<AuditEntry> label={t.col_changes} sortable={false} render={() => <AuditChanges />} />
        </Datagrid>
      </List>
    </Box>
  );
}
