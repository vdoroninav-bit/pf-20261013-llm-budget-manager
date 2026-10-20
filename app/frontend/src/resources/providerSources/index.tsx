// Экран 7.2 «Источники» (design/screens.md §7.2, T064): listProviderSources, createProviderSource,
// updateProviderSource, deleteProviderSource, listDepartments. Маршрут /admin/#/imports/sources, только администратор.
import { useState, type FormEvent } from 'react';
import {
  Alert,
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControl,
  FormControlLabel,
  FormHelperText,
  FormLabel,
  Radio,
  RadioGroup,
  TextField as MuiTextField,
} from '@mui/material';
import {
  Datagrid,
  FunctionField,
  List,
  TopToolbar,
  useDataProvider,
  useGetList,
  useNotify,
  useRefresh,
} from 'react-admin';
import { can } from '../../access/permissions';
import type { Schemas } from '../../api/types';
import { ConfirmDialog, EmptyState, Forbidden, ListError, ListSlowHint, Mono, messageOf, useRole } from '../../components/common';
import { Hint } from '../../components/records';
import { ru } from '../../i18n/ru';
import { providerLabel } from '../../import/importHealth';
import { useDepartmentNames } from '../departmentNames';
import { ImportTabs } from '../../pages/ImportStatus';

type Source = Schemas['ProviderSource'];
type Provider = Schemas['Provider'];
type Kind = Source['kind'];
type Mode = Source['mode'];

const t = ru.bm.sources;
const PROVIDERS: Provider[] = ['anthropic', 'openai'];
const KINDS: Kind[] = ['workspace', 'project', 'api_key'];
const MODES: Mode[] = ['import', 'decision_api'];

export function ProviderSourceList() {
  const role = useRole();
  const [dialog, setDialog] = useState<{ source?: Source } | null>(null);
  const [removing, setRemoving] = useState<Source | null>(null);
  const names = useDepartmentNames();
  if (role === undefined) return null;
  if (!can(role, 'listProviderSources')) return <Forbidden />;
  const addButton = (
    <Button variant="contained" onClick={() => setDialog({})}>
      {t.add}
    </Button>
  );
  return (
    <Box>
      <ImportTabs />
      <List
        resource="providerSources"
        perPage={200}
        pagination={false}
        actions={<TopToolbar>{addButton}</TopToolbar>}
        empty={false}
        error={<ListError />}
        title={ru.bm.imports.title}
        disableSyncWithLocation
      >
        <ListSlowHint />
        <Box sx={{ px: 2, pt: 1 }}>
          <Hint>{t.caption}</Hint>
        </Box>
        <Datagrid bulkActionButtons={false} rowClick={false} empty={<EmptyState message={t.empty} actions={addButton} />}>
          <FunctionField<Source> label={t.provider} sortable={false} render={(r) => providerLabel(r.provider)} />
          <FunctionField<Source> label={t.kind_col} sortable={false} render={(r) => ru.bm.source_kind[r.kind]} />
          <FunctionField<Source> label={t.ref} sortable={false} render={(r) => <Mono>{r.ref}</Mono>} />
          <FunctionField<Source> label={t.display_name} sortable={false} render={(r) => r.display_name ?? '—'} />
          <FunctionField<Source>
            label={t.department}
            sortable={false}
            render={(r) => (r.department_id ? (names.get(r.department_id) ?? <Mono>{r.department_id}</Mono>) : '—')}
          />
          <FunctionField<Source> label={t.mode} sortable={false} render={(r) => ru.bm.source_mode[r.mode]} />
          <FunctionField<Source>
            label={t.actions}
            sortable={false}
            render={(r) => (
              <Box sx={{ display: 'flex', gap: 1 }}>
                <Button size="small" onClick={() => setDialog({ source: r })}>
                  {t.edit}
                </Button>
                <Button size="small" color="error" onClick={() => setRemoving(r)}>
                  {t.remove}
                </Button>
              </Box>
            )}
          />
        </Datagrid>
      </List>
      {dialog && <ProviderSourceDialog source={dialog.source} onClose={() => setDialog(null)} />}
      {removing && <RemoveSourceDialog source={removing} onClose={() => setRemoving(null)} />}
    </Box>
  );
}

function RemoveSourceDialog({ source, onClose }: { source: Source; onClose: () => void }) {
  const dp = useDataProvider();
  const notify = useNotify();
  const refresh = useRefresh();
  return (
    <ConfirmDialog
      open
      danger
      title={t.remove_title}
      text={t.remove_text}
      confirm={t.remove_confirm}
      onClose={onClose}
      onConfirm={() => {
        onClose();
        dp.delete('providerSources', { id: source.id, previousData: { ...source } })
          .then(() => {
            notify(t.removed, { type: 'success' });
            refresh();
          })
          .catch((e: unknown) => notify(messageOf(e), { type: 'error' }));
      }}
    />
  );
}

export interface ProviderSourceDialogProps {
  /** Изменение существующего источника; без него — добавление. */
  source?: Source;
  /** Заполнение формы добавления: «Сопоставить» несопоставленный источник с экрана «Состояние». */
  initial?: { provider: Provider; ref: string };
  onClose: () => void;
}

type Errors = { ref?: string; department?: string };

/** Форма источника (screens.md §7.2). Провайдер, тип и ref после создания не меняются. */
export function ProviderSourceDialog({ source, initial, onClose }: ProviderSourceDialogProps) {
  const editing = !!source;
  const dp = useDataProvider();
  const notify = useNotify();
  const refresh = useRefresh();
  const { data: departments } = useGetList<Schemas['Department']>('departments', {
    pagination: { page: 1, perPage: 200 },
    sort: { field: 'name', order: 'ASC' },
  });
  const [provider, setProvider] = useState<Provider>(source?.provider ?? initial?.provider ?? 'anthropic');
  const [kind, setKind] = useState<Kind>(source?.kind ?? 'workspace');
  const [ref, setRef] = useState(source?.ref ?? initial?.ref ?? '');
  const [displayName, setDisplayName] = useState(source?.display_name ?? '');
  const [departmentId, setDepartmentId] = useState(source?.department_id ?? '');
  const [mode, setMode] = useState<Mode>(source?.mode ?? 'import');
  const [errors, setErrors] = useState<Errors>({});
  const [failure, setFailure] = useState<unknown>(null);
  const [saving, setSaving] = useState(false);

  // Anthropic отдаёт расход только по workspace (unsupported_source_kind).
  const kindAllowed = (k: Kind) => provider !== 'anthropic' || k === 'workspace';

  function chooseProvider(p: Provider) {
    setProvider(p);
    if (p === 'anthropic') setKind('workspace');
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    const trimmedRef = ref.trim();
    const next: Errors = {
      ...(!editing && (trimmedRef.length < 1 || trimmedRef.length > 200) ? { ref: t.ref_error } : {}),
      ...(!departmentId ? { department: t.department_error } : {}),
    };
    setErrors(next);
    if (next.ref || next.department) return;
    const name = displayName.trim() || null;
    setSaving(true);
    setFailure(null);
    try {
      if (source) {
        const patch: Schemas['ProviderSourcePatch'] = {
          ...(name !== (source.display_name ?? null) ? { display_name: name } : {}),
          ...(departmentId !== source.department_id ? { department_id: departmentId } : {}),
          ...(mode !== source.mode ? { mode } : {}),
        };
        if (Object.keys(patch).length) {
          await dp.update('providerSources', { id: source.id, data: patch, previousData: {} as never });
        }
        notify(t.saved, { type: 'success' });
      } else {
        const body: Schemas['ProviderSourceCreate'] = {
          provider,
          kind,
          ref: trimmedRef,
          display_name: name,
          department_id: departmentId,
          mode,
        };
        await dp.create('providerSources', { data: body });
        notify(t.created, { type: 'success' });
      }
      refresh();
      onClose();
    } catch (err) {
      setFailure(err);
    } finally {
      setSaving(false);
    }
  }

  const title = source ? t.edit_title.replace('%{ref}', source.ref) : t.add;
  return (
    <Dialog open onClose={onClose} aria-labelledby="bm-source-title" fullWidth maxWidth="sm">
      <Box component="form" noValidate onSubmit={(e) => void submit(e)}>
        <DialogTitle id="bm-source-title">{title}</DialogTitle>
        <DialogContent>
          {failure !== null && (
            <Alert severity="error" role="alert" sx={{ mb: 2 }}>
              {messageOf(failure)}
            </Alert>
          )}
          {!editing && (
            <>
              <FormControl component="fieldset" margin="dense" fullWidth>
                <FormLabel component="legend">{t.provider}</FormLabel>
                <RadioGroup row value={provider} onChange={(e) => chooseProvider(e.target.value as Provider)}>
                  {PROVIDERS.map((p) => (
                    <FormControlLabel key={p} value={p} control={<Radio />} label={providerLabel(p)} />
                  ))}
                </RadioGroup>
              </FormControl>
              <FormControl component="fieldset" margin="dense" fullWidth>
                <FormLabel component="legend">{t.kind}</FormLabel>
                <RadioGroup row value={kind} onChange={(e) => setKind(e.target.value as Kind)}>
                  {KINDS.map((k) => (
                    <FormControlLabel
                      key={k}
                      value={k}
                      control={<Radio />}
                      label={ru.bm.source_kind[k]}
                      disabled={!kindAllowed(k)}
                    />
                  ))}
                </RadioGroup>
                {provider === 'anthropic' && <FormHelperText>{t.kind_anthropic_hint}</FormHelperText>}
              </FormControl>
              <MuiTextField
                fullWidth
                required
                margin="dense"
                label={t.ref}
                value={ref}
                onChange={(e) => setRef(e.target.value)}
                error={!!errors.ref}
                helperText={errors.ref ?? t.ref_hint}
                slotProps={{ htmlInput: { maxLength: 200, lang: 'en', translate: 'no', autoComplete: 'off' } }}
              />
            </>
          )}
          <MuiTextField
            fullWidth
            margin="dense"
            label={t.display_name}
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
            helperText={t.display_name_hint}
            slotProps={{ htmlInput: { maxLength: 200 } }}
          />
          <MuiTextField
            select
            fullWidth
            required
            margin="dense"
            label={t.department}
            value={departmentId}
            onChange={(e) => setDepartmentId(e.target.value)}
            error={!!errors.department}
            helperText={errors.department ?? ' '}
            slotProps={{ select: { native: true }, inputLabel: { shrink: true } }}
          >
            <option value="" />
            {(departments ?? []).map((d) => (
              <option key={d.id} value={d.id}>
                {d.name}
              </option>
            ))}
          </MuiTextField>
          <FormControl component="fieldset" margin="dense" fullWidth>
            <FormLabel component="legend">{t.mode}</FormLabel>
            <RadioGroup value={mode} onChange={(e) => setMode(e.target.value as Mode)}>
              {MODES.map((m) => (
                <FormControlLabel key={m} value={m} control={<Radio />} label={ru.bm.source_mode[m]} />
              ))}
            </RadioGroup>
          </FormControl>
        </DialogContent>
        <DialogActions>
          <Button onClick={onClose}>{ru.ra.action.cancel}</Button>
          <Button type="submit" variant="contained" disabled={saving}>
            {saving ? ru.bm.state.saving : t.save}
          </Button>
        </DialogActions>
      </Box>
    </Dialog>
  );
}
