// Экран 7.1 «Импорт провайдеров → Состояние» (design/screens.md §7.1, T064): getImportStatus, runImport,
// listProviderSources (пустое состояние), createProviderSource («Сопоставить»). Маршрут /admin/#/imports.
// Видимость: администратор — всё; финансист — только чтение состояния; владелец бюджета — экрана нет (В1).
import { useState, type FormEvent, type ReactNode } from 'react';
import {
  Alert,
  Box,
  Button,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
  Paper,
  Skeleton,
  TextField as MuiTextField,
  Typography,
} from '@mui/material';
import { useQuery } from '@tanstack/react-query';
import { Title, useDataProvider, useGetList, useNotify } from 'react-admin';
import { useNavigate } from 'react-router-dom';
import { can, canSeeImportScreen } from '../access/permissions';
import type { Schemas } from '../api/types';
import type { BmDataProvider } from '../dataProvider';
import { EmptyState, Forbidden, Mono, ProblemAlert, SlowHint, codeOf, messageOf, useRole } from '../components/common';
import { NavTabs } from '../components/NavTabs';
import { formatDate, formatDateTimeMsk } from '../format/date';
import { ru } from '../i18n/ru';
import { errorClassText, isImportLagging, providerLabel } from '../import/importHealth';
import { ProviderSourceDialog } from '../resources/providerSources';

type ImportStatus = Schemas['ImportStatus'];
type ProviderStatus = ImportStatus['providers'][number];
type Provider = Schemas['Provider'];

const t = ru.bm.imports;
const ENV_KEY: Record<Provider, string> = { anthropic: 'ANTHROPIC_ADMIN_KEY', openai: 'OPENAI_ADMIN_KEY' };

/** Ключ запроса состояния импорта — общий для экрана 7 и плашки на панели. */
export const IMPORT_STATUS_KEY = ['bm', 'getImportStatus'] as const;

/** Вкладки экрана 7: «Источники» и «Запуски» — только администратору. */
export function ImportTabs() {
  const role = useRole();
  return (
    <NavTabs
      label={t.tabs}
      tabs={[
        { to: '/imports', label: t.tab_status },
        { to: '/imports/sources', label: t.tab_sources, show: can(role, 'listProviderSources') },
        { to: '/imports/runs', label: t.tab_runs, show: can(role, 'listImportRuns') },
      ]}
    />
  );
}

export function ImportStatusPage() {
  const role = useRole();
  if (role === undefined) return null;
  if (!canSeeImportScreen(role)) return <Forbidden />;
  return <ImportStatusView />;
}

function ImportStatusView() {
  const role = useRole();
  const dp = useDataProvider<BmDataProvider>();
  const status = useQuery({ queryKey: IMPORT_STATUS_KEY, queryFn: () => dp.getImportStatus(), retry: false });
  const [running, setRunning] = useState<Provider | null>(null);
  const [mapping, setMapping] = useState<{ provider: Provider; ref: string } | null>(null);

  return (
    <Box>
      <ImportTabs />
      <Title title={t.title} />
      {status.isPending ? (
        <Box sx={{ p: 2 }} aria-busy="true">
          <Skeleton variant="rectangular" height={160} />
          <SlowHint pending onRetry={() => void status.refetch()} />
        </Box>
      ) : status.isError ? (
        <ProblemAlert error={status.error} onRetry={() => void status.refetch()} />
      ) : (
        <Box sx={{ p: 2, display: 'grid', gap: 2, maxWidth: 960 }}>
          <EnabledLine enabled={status.data.enabled} />
          {!status.data.enabled && can(role, 'listProviderSources') && <NotUsedHint />}
          {status.data.providers.map((p) => (
            <ProviderPanel
              key={p.provider}
              status={p}
              enabled={status.data.enabled}
              canRun={can(role, 'runImport')}
              canMap={can(role, 'createProviderSource')}
              onRun={() => setRunning(p.provider)}
              onMap={(ref) => setMapping({ provider: p.provider, ref })}
            />
          ))}
        </Box>
      )}
      {running && <RunImportDialog provider={running} onClose={() => setRunning(null)} />}
      {mapping && <ProviderSourceDialog initial={mapping} onClose={() => setMapping(null)} />}
    </Box>
  );
}

function EnabledLine({ enabled }: { enabled: boolean }) {
  if (enabled) return <Chip color="success" label={t.enabled} sx={{ justifySelf: 'start' }} />;
  return (
    <Box>
      <Chip label={t.disabled} sx={{ mb: 1 }} />
      <Typography variant="body2" color="text.secondary">
        {t.disabled_hint}
      </Typography>
    </Box>
  );
}

/** Импорт выключен и справочник источников пуст — это нормальное состояние (screens.md §7 «Состояния»). */
function NotUsedHint() {
  const { total, isPending } = useGetList('providerSources', { pagination: { page: 1, perPage: 200 } });
  if (isPending || total !== 0) return null;
  return <EmptyState message={t.not_used} />;
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <>
      <Typography component="dt" color="text.secondary">
        {label}
      </Typography>
      <Box component="dd" sx={{ m: 0 }}>
        {children}
      </Box>
    </>
  );
}

interface ProviderPanelProps {
  status: ProviderStatus;
  enabled: boolean;
  canRun: boolean;
  canMap: boolean;
  onRun: () => void;
  onMap: (ref: string) => void;
}

function ProviderPanel({ status: p, enabled, canRun, canMap, onRun, onMap }: ProviderPanelProps) {
  const name = providerLabel(p.provider);
  const lagging = isImportLagging(p.last_success_day);
  const blocked = !enabled
    ? ru.bm.problem.import_disabled
    : !p.configured
      ? t.key_missing_server.replace('%{provider}', name)
      : null;
  const titleId = `bm-import-${p.provider}`;
  return (
    <Paper component="section" aria-labelledby={titleId} sx={{ p: 2 }}>
      <Typography id={titleId} variant="h6" component="h2" sx={{ mb: 1 }}>
        {name}
      </Typography>
      <Box
        component="dl"
        sx={{ display: 'grid', gridTemplateColumns: 'max-content 1fr', columnGap: 3, rowGap: 1, m: 0 }}
      >
        <Row label={t.key}>{p.configured ? t.key_set : t.key_missing.replace('%{env}', ENV_KEY[p.provider])}</Row>
        <Row label={t.loaded_by}>
          {p.last_success_day ? t.utc_day.replace('%{date}', formatDate(p.last_success_day)) : t.never}
        </Row>
        <Row label={t.last_run}>{formatDateTimeMsk(p.last_run_at)}</Row>
        <Row label={t.lag}>
          <span>{p.lag_hours === null || p.lag_hours === undefined ? t.never : t.lag_value.replace('%{h}', String(p.lag_hours))}</span>
          {/* Единственный порог — SC-008, тот же, что на панели (В7). */}
          {enabled && p.configured && lagging && (
            <Alert severity="warning" sx={{ mt: 1 }}>
              {ru.bm.import_health.yesterday_missing.replace(/\.$/, '')}
            </Alert>
          )}
        </Row>
        {p.consecutive_failures > 0 && (
          <Row label={t.failures}>
            <span>{p.consecutive_failures}</span>
            {p.consecutive_failures >= 2 && (
              <Alert severity="error" sx={{ mt: 1 }}>
                {t.failures_notified}
              </Alert>
            )}
          </Row>
        )}
        {p.last_error_class && (
          <Row label={t.error}>
            {errorClassText(p.last_error_class)}{' '}
            <Typography component="span" variant="caption" color="text.secondary">
              <Mono>{p.last_error_class}</Mono>
            </Typography>
          </Row>
        )}
        {(p.unallocated_sources ?? []).length > 0 && (
          <Row label={t.unallocated}>
            <Box component="ul" sx={{ m: 0, pl: 0, listStyle: 'none', display: 'grid', gap: 0.5 }}>
              {(p.unallocated_sources ?? []).map((ref) => (
                <li key={ref}>
                  <Mono>{ref}</Mono>
                  {canMap && (
                    <Button size="small" sx={{ ml: 1 }} aria-label={`${t.map} ${ref}`} onClick={() => onMap(ref)}>
                      {t.map}
                    </Button>
                  )}
                </li>
              ))}
            </Box>
          </Row>
        )}
      </Box>
      {canRun && (
        <Box sx={{ mt: 2 }}>
          <Button
            variant="contained"
            disabled={!!blocked}
            onClick={onRun}
            aria-describedby={blocked ? `${titleId}-blocked` : undefined}
          >
            {t.run_now}
          </Button>
          {blocked && (
            <Typography id={`${titleId}-blocked`} variant="body2" color="text.secondary" sx={{ mt: 1 }}>
              {blocked}
            </Typography>
          )}
        </Box>
      )}
    </Paper>
  );
}

const DATE = /^\d{4}-\d{2}-\d{2}$/;

/** «Загрузить сейчас»: провайдер фиксирован, окно суток UTC необязательно (screens.md §7.1). */
function RunImportDialog({ provider, onClose }: { provider: Provider; onClose: () => void }) {
  const dp = useDataProvider<BmDataProvider>();
  const notify = useNotify();
  const navigate = useNavigate();
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const [rangeError, setRangeError] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const name = providerLabel(provider);

  async function submit(e: FormEvent) {
    e.preventDefault();
    const bad = DATE.test(from) && DATE.test(to) && from > to;
    setRangeError(bad);
    if (bad) return;
    setSaving(true);
    setFailure(null);
    try {
      await dp.runImport({
        provider,
        ...(DATE.test(from) ? { day_from: from } : {}),
        ...(DATE.test(to) ? { day_to: to } : {}),
      });
      notify(t.run_queued, { type: 'success' });
      onClose();
      navigate('/imports/runs');
    } catch (err) {
      setFailure(
        codeOf(err) === 'provider_key_missing' ? t.key_missing_server.replace('%{provider}', name) : messageOf(err),
      );
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog open onClose={onClose} aria-labelledby="bm-run-title" fullWidth maxWidth="xs">
      <Box component="form" noValidate onSubmit={(e) => void submit(e)}>
        <DialogTitle id="bm-run-title">{t.run_title.replace('%{provider}', name)}</DialogTitle>
        <DialogContent>
          <Box aria-live="polite">
            {failure && (
              <Alert severity="error" role="alert" sx={{ mb: 2 }}>
                {failure}
              </Alert>
            )}
          </Box>
          <DialogContentText sx={{ mb: 1 }}>
            {t.run_provider}: {name}
          </DialogContentText>
          <MuiTextField
            fullWidth
            margin="dense"
            type="date"
            label={t.run_from}
            value={from}
            onChange={(e) => setFrom(e.target.value)}
            error={rangeError}
            helperText={rangeError ? t.run_range_error : t.run_hint}
            slotProps={{ inputLabel: { shrink: true } }}
          />
          <MuiTextField
            fullWidth
            margin="dense"
            type="date"
            label={t.run_to}
            value={to}
            onChange={(e) => setTo(e.target.value)}
            helperText=" "
            slotProps={{ inputLabel: { shrink: true } }}
          />
        </DialogContent>
        <DialogActions>
          <Button onClick={onClose}>{ru.ra.action.cancel}</Button>
          <Button type="submit" variant="contained" disabled={saving}>
            {saving ? ru.bm.state.saving : t.run_submit}
          </Button>
        </DialogActions>
      </Box>
    </Dialog>
  );
}
